"""开发者面板：运行流程、实时日志、问题列表、诊断包。

入口藏在设置里，不打扰普通用户。打开后挂一个日志 Handler 和一个 trace
监听器，关掉就全部摘掉，文件日志不受影响。
"""
from __future__ import annotations

import logging
import platform
import queue
import re
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
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

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

_CSS = """
#devRoot { background: #1c1c1f; }
QLabel { color: #d8d8dc; }
#envBar {
    background: #242428; border: 1px solid #34343a; border-radius: 6px;
    color: #b9b9c2; padding: 8px 10px;
}
QTreeWidget, QPlainTextEdit {
    background: #17171a; border: 1px solid #34343a; border-radius: 4px;
    color: #d8d8dc; selection-background-color: #33333a;
}
QTreeWidget::item { height: 22px; }
QHeaderView::section {
    background: #222226; color: #96969e; border: 0; border-bottom: 1px solid #34343a;
    padding: 4px 6px;
}
QTabWidget::pane { border: 1px solid #34343a; border-radius: 4px; }
QTabBar::tab {
    background: #242428; color: #96969e; padding: 5px 12px;
    border: 1px solid #34343a; border-bottom: 0; border-top-left-radius: 4px; border-top-right-radius: 4px;
}
QTabBar::tab:selected { background: #17171a; color: #e8e8ea; }
QPushButton {
    background: #2b2b31; color: #d8d8dc; border: 1px solid #3a3a42;
    border-radius: 4px; padding: 5px 12px;
}
QPushButton:hover { background: #34343c; }
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


class DevWindow(QWidget):
    def __init__(self, window):
        super().__init__(None)
        self._win = window
        self.setObjectName("devRoot")
        self.setWindowTitle("开发者面板")
        self.resize(1120, 660)
        self.setStyleSheet(_CSS)

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

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.env = QLabel()
        self.env.setObjectName("envBar")
        self.env.setTextFormat(Qt.RichText)
        self.env.setWordWrap(True)
        root.addWidget(self.env)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["时间", "步骤", "结果", "耗时", "说明"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.tree.setColumnWidth(0, 104)
        self.tree.setIndentation(12)
        self.tree.setColumnWidth(2, 58)
        self.tree.setColumnWidth(3, 66)
        split.addWidget(self.tree)

        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(6)

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
        self.pause = QCheckBox("暂停滚动")
        self.pause.stateChanged.connect(self._on_pause)
        bar.addWidget(self.pause)
        btn_clear = QPushButton("清空")
        btn_clear.clicked.connect(self._clear_views)
        bar.addWidget(btn_clear)
        rlay.addLayout(bar)

        self.tabs = QTabWidget()
        self.log_view = self._make_view()
        self.err_view = self._make_view()
        self.tabs.addTab(self.log_view, "日志")
        self.tabs.addTab(self.err_view, "问题 0")
        rlay.addWidget(self.tabs, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([640, 460])

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
        root.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setInterval(FLUSH_MS)
        self._timer.timeout.connect(self._flush)
        self._timer.start()

        self._handler = _QueueHandler(self._log_q)
        self._handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(name)s: %(message)s", "%H:%M:%S"))
        logging.getLogger().addHandler(self._handler)
        trace_mod.add_listener(self._on_trace)
        self._refresh_env()

    # ---------------- 构建 ----------------

    @staticmethod
    def _make_view() -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setMaximumBlockCount(MAX_LOG)
        view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        font = QFont("Consolas")
        font.setPointSize(9)
        view.setFont(font)
        return view

    def _refresh_env(self) -> None:
        from gui import __version__

        pieces = [f"<b>v{__version__}</b>"]
        try:
            from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR

            pieces.append(f"Python {platform.python_version()}")
            pieces.append(f"PyQt5 {PYQT_VERSION_STR} / Qt {QT_VERSION_STR}")
        except Exception:  # noqa: BLE001
            pass
        backend = __import__("os").environ.get("QT_MULTIMEDIA_PREFERRED_PLUGINS", "default")
        pieces.append(f"播放后端 {backend}")
        try:
            from ncm import transcode

            ff = transcode.ffmpeg_path()
            pieces.append("ffmpeg " + ("就绪" if ff else "<span style='color:#e8554a'>未找到</span>"))
        except Exception:  # noqa: BLE001
            pieces.append("ffmpeg 未知")
        try:
            entries = self._win._cache_map()
            done = sum(1 for e in entries.values() if e.complete)
            pieces.append(f"缓存 {len(entries)} 个（完整 {done}）")
        except Exception:  # noqa: BLE001
            pass
        cache_dir = getattr(self._win, "cache_dir", None)
        out_dir = getattr(self._win, "out_dir", None)
        extra = []
        if cache_dir:
            extra.append(f"缓存目录 {cache_dir}")
        if out_dir:
            extra.append(f"保存目录 {out_dir}")
        text = " · ".join(pieces)
        if extra:
            text += "<br><span style='color:#7f7f88'>" + "<br>".join(extra) + "</span>"
        self.env.setText(text)

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

    def _clear_views(self) -> None:
        self._logs.clear()
        self._errors.clear()
        self._steps.clear()
        self._groups.clear()
        self.tree.clear()
        self.log_view.clear()
        self.err_view.clear()
        self.tabs.setTabText(1, "问题 0")

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
                dirty_err = True
            if self._match(level, message) and not self._paused:
                self.log_view.appendPlainText(self._fmt(level, name, message, created))
                dirty_log = True
        if dirty_err:
            self.err_view.appendPlainText(self._errors[-1])
            self.tabs.setTabText(1, f"问题 {len(self._errors)}")
        appended = False
        if not self._paused:
            for _ in range(200):
                try:
                    event = self._event_q.get_nowait()
                except queue.Empty:
                    break
                self._append_step(event)
                appended = True
            if appended:
                self.tree.scrollToBottom()
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
        if self._keyword and self._keyword.lower() not in message.lower():
            return False
        return True

    def _rebuild_log(self) -> None:
        self.log_view.clear()
        lines = [self._fmt(lv, nm, msg, ts) for lv, nm, msg, ts in self._logs if self._match(lv, msg)]
        if lines:
            self.log_view.setPlainText("\n".join(lines[-MAX_LOG:]))
            self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _rebuild_errors(self) -> None:
        self.err_view.clear()
        lines = [line for line in self._errors if not self._keyword or self._keyword.lower() in line.lower()]
        if lines:
            self.err_view.setPlainText("\n".join(lines))

    def _append_step(self, event: dict) -> None:
        self._steps.append(event)
        clock = time.strftime("%H:%M:%S", time.localtime(event["t"]))
        status = _STATUS.get(event["status"], event["status"])
        took = f"{event['ms']:.0f} ms" if event.get("ms") is not None else ""
        detail = " ".join(f"{k}={v}" for k, v in (event.get("detail") or {}).items())
        group = event.get("group") or ""
        if group:
            key = f"{event['scope']}|{group}"
            top = self._groups.get(key)
            if top is None:
                top = QTreeWidgetItem([clock, f"{event['scope']} · {group}", "", "", ""])
                top.setFirstColumnSpanned(False)
                font = top.font(1)
                font.setBold(True)
                top.setFont(1, font)
                self.tree.addTopLevelItem(top)
                self._groups[key] = top
            child = QTreeWidgetItem([clock, event["step"], status, took, detail])
            child.setForeground(2, QColor({"ok": "#6fbf8f", "fail": "#e8554a", "skip": "#c9a45c"}.get(event["status"], "#96969e")))
            top.addChild(child)
            top.setExpanded(True)
        else:
            item = QTreeWidgetItem([clock, f"{event['scope']} · {event['step']}", status, took, detail])
            item.setForeground(2, QColor({"ok": "#6fbf8f", "fail": "#e8554a", "skip": "#c9a45c"}.get(event["status"], "#96969e")))
            self.tree.addTopLevelItem(item)
        if self.tree.topLevelItemCount() > 800:
            for _ in range(200):
                gone = self.tree.takeTopLevelItem(0)
                if gone is None:
                    break
                for key, value in list(self._groups.items()):
                    if value is gone:
                        del self._groups[key]

    # ---------------- 诊断 ----------------

    def _env_text(self) -> str:
        from gui import __version__

        lines = [f"版本 {__version__}", f"Python {platform.python_version()}", f"系统 {platform.platform()}"]
        try:
            from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR

            lines.append(f"PyQt5 {PYQT_VERSION_STR} / Qt {QT_VERSION_STR}")
        except Exception:  # noqa: BLE001
            pass
        import os

        lines.append(f"播放后端 {os.environ.get('QT_MULTIMEDIA_PREFERRED_PLUGINS', 'default')}")
        try:
            from ncm import transcode

            lines.append(f"ffmpeg {transcode.ffmpeg_path() or '未找到'}")
        except Exception:  # noqa: BLE001
            pass
        for key in ("cache_dir", "out_dir"):
            value = getattr(self._win, key, None)
            if value:
                lines.append(f"{key} {value}")
        try:
            entries = self._win._cache_map()
            done = sum(1 for e in entries.values() if e.complete)
            lines.append(f"缓存条目 {len(entries)}（完整 {done}）")
        except Exception:  # noqa: BLE001
            pass
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
        body = self._env_text() + "\n--- 最近日志 ---\n"
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
                zf.writestr("env.txt", self._env_text())
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

    # ---------------- 生命周期 ----------------

    def closeEvent(self, event) -> None:
        self._timer.stop()
        if self._handler is not None:
            logging.getLogger().removeHandler(self._handler)
            self._handler = None
        trace_mod.remove_listener(self._on_trace)
        super().closeEvent(event)
