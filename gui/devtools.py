"""开发者面板：运行流程、实时日志、问题列表、环境信息、诊断包。

外观和主窗口/设置一致：无边框、圆角、同一套阴影与标题栏。
入口藏在设置里；打开后挂一个日志 Handler 和一个 trace 监听器，关掉全部摘掉。
"""
from __future__ import annotations

import logging
import os
import platform
import queue
import re
import shutil
import time
import zipfile
from collections import deque
from pathlib import Path

from PyQt5.QtCore import QTimer, Qt, QUrl
from PyQt5.QtGui import QColor, QDesktopServices, QFont
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizeGrip,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.dialog import RoundDialog, SHADOW
from gui.style import QSS
from ncm import trace as trace_mod
from ncm.paths import data_dir

MAX_LOG = 3000
MAX_ERRORS = 800
FLUSH_MS = 140

_STATUS = {"ok": "完成", "fail": "失败", "skip": "跳过", "run": "进行中"}
_LEVEL_NAME = {logging.INFO: "信息", logging.WARNING: "警告", logging.ERROR: "错误"}
_SECRET_KEY = ("cookie", "token", "music_u", "csrf", "password", "auth", "session")
_SECRET_TEXT = (
    re.compile(r"MUSIC_U=[^;\s\"']+", re.I),
    re.compile(r"__csrf=[^;\s\"']+", re.I),
    re.compile(r"(?i)(cookie|token|authorization)\s*[:=]\s*\S+"),
)
_SCOPES = ("全部", "搜索", "缓存", "下载", "播放", "接口", "任务")

_DEV_CSS = """
QTreeWidget, QPlainTextEdit {
    background: #17171a; border: 1px solid #34343a; border-radius: 6px;
    color: #d8d8dc; selection-background-color: #33333a;
}
QTreeWidget::item { height: 22px; }
QHeaderView::section {
    background: #222226; color: #96969e; border: 0;
    border-bottom: 1px solid #34343a; padding: 4px 6px;
}
QTabBar { background: transparent; }
QTabWidget::pane {
    top: -1px; background: #17171a;
    border: 1px solid #34343a; border-radius: 6px;
}
QTabBar::tab {
    background: #242428; color: #96969e; padding: 5px 14px;
    min-width: 58px; min-height: 18px;
    border: 1px solid #34343a; border-bottom: 0;
    border-top-left-radius: 5px; border-top-right-radius: 5px;
}
QTabBar::tab:selected { background: #17171a; color: #e8e8ea; }
QTabBar::tab:hover { color: #d8d8dc; }
QComboBox, QLineEdit {
    background: #242428; color: #d8d8dc; border: 1px solid #3a3a42;
    border-radius: 4px; padding: 3px 6px;
}
QCheckBox { color: #b9b9c2; }
"""


class _QueueHandler(logging.Handler):
    """工作线程里只入队，UI 定时批量取，避免刷屏把主线程拖卡。"""

    def __init__(self, sink: queue.Queue):
        super().__init__()
        self._sink = sink
        self.setLevel(logging.INFO)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + logging.Formatter().formatException(record.exc_info)
            self._sink.put((record.levelno, record.name, message, record.created))
        except Exception:  # noqa: BLE001
            pass


def _redact(text: str) -> str:
    for pattern in _SECRET_TEXT:
        text = pattern.sub("***", text)
    return text


class DevWindow(RoundDialog):
    def __init__(self, window):
        super().__init__(window, "开发者面板")
        self._win = window
        self.setStyleSheet(QSS + _DEV_CSS)
        self.resize(1120 + SHADOW * 2, 680 + SHADOW * 2)

        self._logs: deque[tuple[int, str, str, float]] = deque(maxlen=MAX_LOG)
        self._errors: deque[str] = deque(maxlen=MAX_ERRORS)
        self._steps: deque[dict] = deque(maxlen=1500)
        self._log_q: queue.Queue = queue.Queue()
        self._event_q: queue.Queue = queue.Queue()
        self._groups: dict[str, QTreeWidgetItem] = {}
        self._paused = False
        self._handler: _QueueHandler | None = None
        self._filter_level = logging.INFO
        self._keyword = ""
        self._scope = "全部"
        self._only_fail = False
        self._last_step: tuple | None = None  # (key, item, count, ts)

        body = self.body_layout
        body.setContentsMargins(14, 4, 14, 12)
        body.setSpacing(8)

        self.env = QLabel()
        self.env.setObjectName("envBar")
        self.env.setTextFormat(Qt.RichText)
        self.env.setWordWrap(True)
        self.env.setStyleSheet(
            "#envBar { background: #202024; border: 1px solid #34343a;"
            " border-radius: 6px; color: #b9b9c2; padding: 7px 10px; }"
        )
        body.addWidget(self.env)

        split = QSplitter(Qt.Horizontal)
        body.addWidget(split, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)

        filters = QHBoxLayout()
        filters.setSpacing(6)
        self.scope_box = QComboBox()
        self.scope_box.addItems(_SCOPES)
        self.scope_box.currentTextChanged.connect(self._on_scope_changed)
        filters.addWidget(self.scope_box)
        self.only_fail = QCheckBox("只看失败")
        self.only_fail.stateChanged.connect(self._on_scope_changed)
        filters.addWidget(self.only_fail)
        self.count_label = QLabel("0 步")
        self.count_label.setStyleSheet("color:#96969e;")
        filters.addWidget(self.count_label)
        filters.addStretch(1)
        self.follow = QCheckBox("自动跟随")
        self.follow.setToolTip("关闭后新事件不再把列表拉到底，方便往回翻")
        self.follow.setChecked(True)
        filters.addWidget(self.follow)
        btn_clear_steps = QPushButton("清空")
        btn_clear_steps.clicked.connect(self._clear_views)
        filters.addWidget(btn_clear_steps)
        lv.addLayout(filters)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["时间", "步骤", "结果", "耗时", "说明"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.tree.setColumnWidth(0, 104)
        self.tree.setColumnWidth(2, 58)
        self.tree.setColumnWidth(3, 66)
        self.tree.setIndentation(12)
        lv.addWidget(self.tree, 1)
        split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.level_box = QComboBox()
        for label, value in (("信息以上", logging.INFO), ("只看警告", logging.WARNING), ("只看错误", logging.ERROR)):
            self.level_box.addItem(label, value)
        self.level_box.currentIndexChanged.connect(self._on_filter_changed)
        bar.addWidget(self.level_box)
        self.search = QLineEdit()
        self.search.setPlaceholderText("过滤关键字")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_filter_changed)
        bar.addWidget(self.search, 1)
        self.pause = QCheckBox("暂停")
        self.pause.setToolTip("暂停滚动和新事件，方便看现场")
        self.pause.stateChanged.connect(self._on_pause)
        bar.addWidget(self.pause)
        rv.addLayout(bar)

        self.tabs = QTabWidget()
        # 全局 QSS 里没有 tab 规则，Fusion 默认会画成白底浅字；这里单独钉死
        self.tabs.setStyleSheet(
            "QTabBar::tab { background:#242428; color:#96969e; padding:6px 16px;"
            " min-width:56px; border:1px solid #34343a; border-bottom:0;"
            " border-top-left-radius:5px; border-top-right-radius:5px; }"
            "QTabBar::tab:selected { background:#17171a; color:#e8e8ea; }"
            "QTabBar::tab:hover { color:#d8d8dc; }"
            "QTabWidget::pane { background:#17171a; border:1px solid #34343a;"
            " border-radius:6px; top:-1px; }"
        )
        self.status_view = self._make_view()
        self.log_view = self._make_view()
        err_panel = QWidget()
        ev = QVBoxLayout(err_panel)
        ev.setContentsMargins(0, 0, 0, 0)
        ev.setSpacing(4)
        self.err_list = QListWidget()
        self.err_list.currentRowChanged.connect(self._show_error_detail)
        self.err_view = self._make_view()
        ev.addWidget(self.err_list, 1)
        ev.addWidget(self.err_view, 1)
        self.env_view = self._make_view()
        self.tabs.addTab(self.status_view, "状态")
        self.tabs.addTab(self.log_view, "日志")
        self.tabs.addTab(err_panel, "问题 0")
        self.tabs.addTab(self.env_view, "环境")
        self.tabs.currentChanged.connect(lambda _i: self._refresh_status())
        rv.addWidget(self.tabs, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([620, 440])

        quick = QHBoxLayout()
        quick.setSpacing(6)
        for label, slot in (
            ("打开缓存目录", lambda: self._open_dir(getattr(self._win, "cache_dir", None))),
            ("打开下载目录", lambda: self._open_dir(getattr(self._win, "out_dir", None))),
            ("打开配置目录", lambda: self._open_dir(data_dir())),
            ("清空播放缓存", self._clear_play_cache),
            ("重新扫描缓存", self._rescan_cache),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            quick.addWidget(btn)
        quick.addStretch(1)
        body.addLayout(quick)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.status = QLabel("准备就绪")
        self.status.setStyleSheet("color:#96969e;")
        bottom.addWidget(self.status, 1)
        for label, slot in (
            ("打开日志文件", self._open_log_file),
            ("复制诊断信息", self._copy_diagnostics),
            ("导出诊断包", self._export_diagnostics),
            ("关闭", self.close),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            bottom.addWidget(btn)
        bottom.addWidget(QSizeGrip(self._root), 0, Qt.AlignBottom | Qt.AlignRight)
        body.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setInterval(FLUSH_MS)
        self._timer.timeout.connect(self._flush)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()
        self._attach()
        self._refresh_env()

    # ---------------- 挂载 ----------------

    def _attach(self) -> None:
        """挂到 root logger 和 trace 上；重复调用无副作用。"""
        if self._handler is None:
            self._handler = _QueueHandler(self._log_q)
            self._handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)-5s %(name)s: %(message)s", "%H:%M:%S")
            )
            logging.getLogger().addHandler(self._handler)
        trace_mod.add_listener(self._on_trace)
        if not self._timer.isActive():
            self._timer.start()
        if not self._status_timer.isActive():
            self._status_timer.start()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._attach()
        self._refresh_env()

    def _detach(self) -> None:
        self._timer.stop()
        self._status_timer.stop()
        if self._handler is not None:
            logging.getLogger().removeHandler(self._handler)
            self._handler = None
        trace_mod.remove_listener(self._on_trace)

    def done(self, result: int) -> None:
        # QDialog 的关闭按钮走 done()，不经过 closeEvent；两条路都要摘干净
        self._detach()
        super().done(result)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._detach()
        super().closeEvent(event)

    # ---------------- 构建 ----------------

    @staticmethod
    def _make_view() -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setMaximumBlockCount(MAX_LOG)
        view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        # 单独写一份：全局 QSS 的 QWidget 规则会把底色让出去，Fusion 默认底是白的
        view.setStyleSheet(
            "QPlainTextEdit { background:#17171a; color:#d8d8dc;"
            " border:1px solid #34343a; border-radius:6px; padding:4px; }"
        )
        font = QFont("Consolas")
        font.setPointSize(9)
        view.setFont(font)
        return view

    def _refresh_env(self) -> None:
        from gui import __version__

        pieces = [f"<b>v{__version__}</b>"]
        pieces.append(f"Python {platform.python_version()}")
        try:
            from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR

            pieces.append(f"PyQt5 {PYQT_VERSION_STR} / Qt {QT_VERSION_STR}")
        except Exception:  # noqa: BLE001
            pass
        pieces.append(f"后端 {os.environ.get('QT_MULTIMEDIA_PREFERRED_PLUGINS', '默认')}")
        try:
            from ncm import transcode

            ok = bool(transcode.ffmpeg_path())
            pieces.append("ffmpeg " + ("就绪" if ok else "<span style='color:#e8554a'>未找到</span>"))
        except Exception:  # noqa: BLE001
            pieces.append("ffmpeg 未知")
        try:
            entries = self._win._cache_map()
            done = sum(1 for e in entries.values() if e.complete)
            pieces.append(f"缓存 {len(entries)} 个（完整 {done}）")
        except Exception:  # noqa: BLE001
            pass
        extra = []
        if getattr(self._win, "cache_dir", None):
            extra.append(f"缓存目录 {self._win.cache_dir}")
        if getattr(self._win, "out_dir", None):
            extra.append(f"保存目录 {self._win.out_dir}")
        text = " · ".join(pieces)
        if extra:
            text += "<br><span style='color:#7f7f88'>" + "<br>".join(extra) + "</span>"
        self.env.setText(text)
        self.env_view.setPlainText(self._env_full_text())

    # ---------------- 数据入口 ----------------

    def _on_trace(self, event: dict) -> None:
        self._event_q.put(event)

    def _on_pause(self, state) -> None:
        self._paused = state == Qt.Checked

    def _on_filter_changed(self, *_args) -> None:
        self._filter_level = self.level_box.currentData()
        self._keyword = self.search.text().strip()
        self._rebuild_log()
        self._rebuild_errors()

    def _on_scope_changed(self, *_args) -> None:
        self._scope = self.scope_box.currentText()
        self._only_fail = self.only_fail.isChecked()
        self._rebuild_steps()

    def _clear_views(self) -> None:
        self._logs.clear()
        self._errors.clear()
        self._steps.clear()
        self._groups.clear()
        self.tree.clear()
        self.log_view.clear()
        self.err_view.clear()
        self.err_list.clear()
        self.tabs.setTabText(2, "问题 0")
        self.count_label.setText("0 步")
        self._last_step = None

    def _flush(self) -> None:
        dirty_log = False
        dirty_err = False
        for _ in range(400):
            try:
                level, name, message, created = self._log_q.get_nowait()
            except queue.Empty:
                break
            self._logs.append((level, name, message, created))
            if level >= logging.WARNING:
                line = f"{time.strftime('%H:%M:%S', time.localtime(created))} {_LEVEL_NAME.get(level, '信息')} {name}: {message}"
                self._errors.append(_redact(line))
                self.err_list.addItem(QListWidgetItem(self._errors[-1]))
                while self.err_list.count() > MAX_ERRORS:
                    self.err_list.takeItem(0)
                dirty_err = True
            if self._match(level, message) and not self._paused:
                self.log_view.appendPlainText(self._fmt(level, name, message, created))
                dirty_log = True
        if dirty_err:
            self.tabs.setTabText(2, f"问题 {len(self._errors)}")
        appended = False
        if not self._paused:
            for _ in range(200):
                try:
                    event = self._event_q.get_nowait()
                except queue.Empty:
                    break
                self._steps.append(event)
                if self._step_match(event):
                    self._append_step(event)
                    appended = True
            if appended:
                if self.follow.isChecked():
                    self.tree.scrollToBottom()
                self.count_label.setText(f"{len(self._steps)} 步")
        else:
            while True:
                try:
                    self._event_q.get_nowait()
                except queue.Empty:
                    break
        if dirty_log and self.log_view.verticalScrollBar().value() >= self.log_view.verticalScrollBar().maximum() - 4:
            self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())
        if dirty_err and self.err_view.verticalScrollBar().value() >= self.err_view.verticalScrollBar().maximum() - 4:
            self.err_view.verticalScrollBar().setValue(self.err_view.verticalScrollBar().maximum())

    @staticmethod
    def _fmt(level: int, name: str, message: str, created: float) -> str:
        short = name.replace("ncm.", "")
        text = f"{time.strftime('%H:%M:%S', time.localtime(created))} {_LEVEL_NAME.get(level, '信息'):<2} {short}: {message}"
        return _redact(text)

    def _match(self, level: int, message: str) -> bool:
        if level < self._filter_level:
            return False
        return not (self._keyword and self._keyword.lower() not in message.lower())

    def _step_match(self, event: dict) -> bool:
        if self._scope != "全部" and event["scope"] != self._scope:
            return False
        if self._only_fail and event["status"] != "fail":
            return False
        if self._keyword:
            blob = f"{event['scope']} {event['step']} {event.get('detail')}"
            if self._keyword.lower() not in blob.lower():
                return False
        return True

    def _rebuild_log(self) -> None:
        self.log_view.clear()
        lines = [self._fmt(lv, nm, msg, ts) for lv, nm, msg, ts in self._logs if self._match(lv, msg)]
        if lines:
            self.log_view.setPlainText("\n".join(lines[-MAX_LOG:]))
            self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _rebuild_errors(self) -> None:
        self.err_list.clear()
        self.err_view.clear()
        for line in self._errors:
            if not self._keyword or self._keyword.lower() in line.lower():
                self.err_list.addItem(QListWidgetItem(line))

    def _show_error_detail(self, row: int) -> None:
        lines = [line for line in self._errors if not self._keyword or self._keyword.lower() in line.lower()]
        self.err_view.setPlainText(lines[row] if 0 <= row < len(lines) else "")

    # ---------------- 状态 / 快捷动作 ----------------

    def _refresh_status(self) -> None:
        """每秒刷新一次「此刻在干什么」，比翻时间线快得多。"""
        if not self.isVisible() or self.tabs.currentIndex() != 0:
            return
        win = self._win
        player = getattr(win, "player", None)
        out: list[str] = ["== 播放器 =="]
        if player is None:
            out.append("  未初始化")
        else:
            qp = player._player
            state = {0: "已停止", 1: "播放中", 2: "已暂停"}.get(int(qp.state()), str(int(qp.state())))
            dur, pos = qp.duration(), qp.position()
            # 没有媒体时 Qt 会给出离谱的 position（几千万毫秒），别直接显示
            prog = f"{self._mmss(min(pos, dur))} / {self._mmss(dur)}" if dur > 0 else "— / —"
            out.append(f"  状态 {state} · mediaStatus {qp.mediaStatus()} · 进度 {prog}")
            song = player.song
            if song is not None:
                out.append(f"  曲目 {song.name} - {song.artist_text}")
            cur = player._current_path
            if cur is not None:
                size = f"{cur.stat().st_size / 1048576:.1f} MB" if cur.exists() else "文件不存在"
                out.append(f"  文件 {cur.name} · {size}")
            bad = ", ".join(sorted(player._bad_formats)) or "无"
            out.append(
                f"  兼容播放 {'开' if player.compat_play else '关'} · 已判定放不了的格式 {bad}"
            )
        out.append("")
        out.append("== 任务 ==")
        batch = getattr(win, "batch", None)
        total = getattr(win, "_batch_total", 0)
        done = getattr(win, "_batch_done", 0)
        if batch is not None:
            paused = "是" if getattr(win, "_paused", False) else "否"
            out.append(f"  批量下载 进行中 {done}/{total} · 并发 {getattr(win, 'workers', '?')} · 已暂停 {paused}")
        else:
            out.append("  批量下载 空闲")
        out.append(f"  搜索 {'进行中' if getattr(win, 'task', None) is not None else '空闲'}")
        out.append("")
        out.append("== 队列 ==")
        songs = list(getattr(win, "_queue_songs", []))
        bars = getattr(win, "bars", {})
        if not songs:
            out.append("  空")
        else:
            for song in songs[:12]:
                bar = bars.get(song.id)
                if bar is None:
                    continue
                mx, val = bar.maximum(), bar.value()
                pct = f"{val * 100 // mx:>3}%" if mx else "  —"
                out.append(f"  {song.name[:24]:<24} {pct}")
        self.status_view.setPlainText("\n".join(out))

    @staticmethod
    def _mmss(ms: int) -> str:
        sec = max(0, int(ms) // 1000)
        return f"{sec // 60}:{sec % 60:02d}"

    def _open_dir(self, path) -> None:
        if not path:
            self.status.setText("这个目录还没设置")
            return
        target = Path(path)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _clear_play_cache(self) -> None:
        from ncm import cache as cache_mod

        count, freed = cache_mod.clear_player_cache()
        self.status.setText(f"已清理播放缓存 {count} 个（{freed / 1048576:.0f} MB）")
        trace_mod.trace("面板", "清空播放缓存", "ok", None, {"files": count})

    def _rescan_cache(self) -> None:
        try:
            self._win.on_load_cache()
            self.status.setText("已触发缓存重扫")
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"重扫失败：{exc}")

    def _rebuild_steps(self) -> None:
        self.tree.clear()
        self._groups.clear()
        self._last_step = None
        for event in self._steps:
            if self._step_match(event):
                self._append_step(event)
        self.count_label.setText(f"{len(self._steps)} 步")

    def _append_step(self, event: dict) -> None:
        clock = time.strftime("%H:%M:%S", time.localtime(event["t"]))
        status = _STATUS.get(event["status"], event["status"])
        took = f"{event['ms']:.0f} ms" if event.get("ms") is not None else ""
        detail = " ".join(f"{k}={v}" for k, v in (event.get("detail") or {}).items())
        color = {"ok": "#6fbf8f", "fail": "#e8554a", "skip": "#c9a45c"}.get(event["status"], "#96969e")
        group = event.get("group") or ""
        key = (event["scope"], event["step"], event["status"], detail, group)
        last = self._last_step
        if last is not None and last[0] == key and event["t"] - last[3] <= 8:
            # 自动刷新这类重复事件：不新增行，改成在原行上标次数
            item, count = last[1], last[2] + 1
            item.setText(3, took)
            item.setText(4, f"{detail} ×{count}".strip())
            self._last_step = (key, item, count, event["t"])
            return
        if group:
            key = f"{event['scope']}|{group}"
            top = self._groups.get(key)
            if top is None:
                top = QTreeWidgetItem([clock, f"{event['scope']} · {group}", "", "", ""])
                font = top.font(1)
                font.setBold(True)
                top.setFont(1, font)
                self.tree.addTopLevelItem(top)
                self._groups[key] = top
            child = QTreeWidgetItem([clock, event["step"], status, took, detail])
            child.setForeground(2, QColor(color))
            top.addChild(child)
            top.setExpanded(True)
            item = child
        else:
            item = QTreeWidgetItem([clock, f"{event['scope']} · {event['step']}", status, took, detail])
            item.setForeground(2, QColor(color))
            self.tree.addTopLevelItem(item)
        self._last_step = (key, item, 1, event["t"])
        if self.tree.topLevelItemCount() > 800:
            for _ in range(200):
                gone = self.tree.takeTopLevelItem(0)
                if gone is None:
                    break
                for key, value in list(self._groups.items()):
                    if value is gone:
                        del self._groups[key]

    # ---------------- 诊断 ----------------

    def _env_full_text(self) -> str:
        from gui import __version__

        lines = [
            f"版本 {__version__}",
            f"Python {platform.python_version()}",
            f"系统 {platform.platform()}",
        ]
        try:
            from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR

            lines.append(f"PyQt5 {PYQT_VERSION_STR} / Qt {QT_VERSION_STR}")
        except Exception:  # noqa: BLE001
            pass
        lines.append(f"播放后端 {os.environ.get('QT_MULTIMEDIA_PREFERRED_PLUGINS', '默认')}")
        try:
            from ncm import transcode

            lines.append(f"ffmpeg {transcode.ffmpeg_path() or '未找到'}")
        except Exception:  # noqa: BLE001
            pass
        lines.append(f"数据目录 {data_dir()}")
        for key, label in (("cache_dir", "缓存目录"), ("out_dir", "保存目录")):
            value = getattr(self._win, key, None)
            if value:
                lines.append(f"{label} {value}")
        try:
            from ncm.log import log_path

            lines.append(f"日志文件 {log_path()}")
        except Exception:  # noqa: BLE001
            pass
        try:
            entries = self._win._cache_map()
            done = sum(1 for e in entries.values() if e.complete)
            lines.append(f"缓存条目 {len(entries)}（完整 {done}，未完成 {len(entries) - done}）")
        except Exception:  # noqa: BLE001
            pass
        try:
            out = Path(getattr(self._win, "out_dir", data_dir()))
            while not out.exists() and out != out.parent:
                out = out.parent  # 保存目录可能还没建，往上一层层找到存在的
            usage = shutil.disk_usage(str(out))
            lines.append(
                f"磁盘剩余 {usage.free / 1073741824:.1f} GB / 共 {usage.total / 1073741824:.1f} GB"
            )
        except Exception:  # noqa: BLE001
            pass
        lines.append("")
        lines.append("---- 配置项 ----")
        settings = getattr(self._win, "settings", None)
        if settings is not None:
            for key in settings.allKeys():
                value = settings.value(key)
                if any(word in key.lower() for word in _SECRET_KEY):
                    value = "***"
                lines.append(f"{key} = {value}")
        return _redact("\n".join(lines)) + "\n"

    def _settings_text(self) -> str:
        settings = getattr(self._win, "settings", None)
        if settings is None:
            return "(无)"
        rows = []
        for key in settings.allKeys():
            value = settings.value(key)
            if any(word in key.lower() for word in _SECRET_KEY):
                value = "***"
            rows.append(f"{key} = {value}")
        return _redact("\n".join(rows)) + "\n"

    def _steps_text(self) -> str:
        rows = []
        for event in self._steps:
            clock = time.strftime("%H:%M:%S", time.localtime(event["t"]))
            took = f" {event['ms']:.0f}ms" if event.get("ms") is not None else ""
            detail = " ".join(f"{k}={v}" for k, v in (event.get("detail") or {}).items())
            group = f"[{event['group']}] " if event.get("group") else ""
            rows.append(f"{clock} {event['scope']} {group}{event['step']} {event['status']}{took} {detail}")
        return _redact("\n".join(rows)) + "\n"

    def _copy_diagnostics(self) -> None:
        body = self._env_full_text() + "\n--- 最近日志 ---\n"
        body += "\n".join(self._fmt(lv, nm, msg, ts) for lv, nm, msg, ts in list(self._logs)[-120:])
        body += "\n\n--- 问题 ---\n" + "\n".join(list(self._errors)[-40:])
        QApplication.clipboard().setText(body)
        self.status.setText("诊断信息已复制到剪贴板")

    def _export_diagnostics(self) -> None:
        try:
            from ncm.log import log_path

            target_dir = data_dir() / "diagnostics"
            target_dir.mkdir(parents=True, exist_ok=True)
            path = target_dir / f"诊断包-{time.strftime('%Y%m%d-%H%M%S')}.zip"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("env.txt", self._env_full_text())
                zf.writestr("settings.txt", self._settings_text())
                zf.writestr("steps.txt", self._steps_text())
                zf.writestr("errors.txt", "\n".join(self._errors) + "\n")
                log_file = Path(log_path())
                if log_file.exists():
                    zf.write(log_file, "app.log")
            self.status.setText(f"已导出：{path}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"导出失败：{exc}")

    def _open_log_file(self) -> None:
        try:
            from ncm.log import log_path

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_path())))
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"打不开日志：{exc}")
