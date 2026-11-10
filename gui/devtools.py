"""开发者面板：运行流程、实时日志、问题列表、环境信息、诊断包。

外观和主窗口/设置一致：无边框、圆角、同一套阴影与标题栏。
入口藏在设置里；打开后挂一个日志 Handler 和一个 trace 监听器，关掉全部摘掉。
"""
from __future__ import annotations

import logging
import os
import platform
import queue
import shutil
import subprocess
import threading
import time
import zipfile
from collections import deque
from pathlib import Path

from PyQt5.QtCore import QSize, QTimer, Qt, QUrl
from PyQt5.QtGui import QColor, QDesktopServices, QFont
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui import icons
from gui.combo import style_combo
from gui.console import ConsolePanel
from gui.dialog import RoundDialog, SHADOW
from gui.style import QSS
from ncm import runlog as runlog_mod
from ncm import trace as trace_mod
from ncm.paths import data_dir

MAX_LOG = 3000
MAX_ERRORS = 800
FLUSH_MS = 140
# 状态页最多画多少行队列。再多的部分只给汇总数字：批量下几百首时
# 逐行刷新会白白卡界面，完整进度本来就在主窗口的表格里
MAX_QUEUE_ROWS = 40
ERROR_BANNER_SEC = 300

_STATUS = {"ok": "完成", "fail": "失败", "skip": "跳过", "run": "进行中"}
_LEVEL_NAME = {logging.INFO: "信息", logging.WARNING: "警告", logging.ERROR: "错误"}
# 设置页要打码的键名（脱敏正则本身在 ncm/runlog.py）
_SECRET_KEY = ("cookie", "token", "music_u", "csrf", "password", "auth", "session")
_SCOPES = ("全部", "搜索", "缓存", "下载", "播放", "接口", "任务")
# 日志页里标出「面板打开前」和「实时」的分界
_HISTORY_SEP = "──── 以上为启动以来的记录 · 以下实时 ────"

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
QPushButton[nav="true"] {
    background: #242428; color: #96969e;
    border: 1px solid #34343a; border-radius: 6px; padding: 5px 12px;
}
QPushButton[nav="true"]:hover { color: #d8d8dc; }
QPushButton[nav="true"]:checked {
    background: #2e2e34; color: #e8e8ea; border: 1px solid #5a5a64;
}
QPushButton[nav="true"][danger="true"] { color: #e8554a; }
QComboBox, QLineEdit {
    background: #242428; color: #d8d8dc; border: 1px solid #3a3a42;
    border-radius: 4px; padding: 3px 6px;
}
QCheckBox { color: #b9b9c2; }
"""

# 状态页两条进度条：正常绿色，失败红色。QSS 里用动态属性切，不重建控件
_STATUS_BAR_CSS = """
QProgressBar { background: #242428; border: none; border-radius: 3px; }
QProgressBar::chunk { background: #4caf7d; border-radius: 3px; }
QProgressBar[fail="true"]::chunk { background: #e8554a; }
QProgressBar[dim="true"]::chunk { background: #5a5a64; }
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


# 脱敏规则只有一份，在 ncm/runlog.py；日志、面板、诊断包共用
_redact = runlog_mod.redact


class DevWindow(RoundDialog):
    def __init__(self, window):
        # 父窗口传 None：QDialog 一旦有 owner，在 Windows 上就是「被拥有的窗口」，
        # 会永远压在主窗口上面——点主窗口也翻不到前面。这里做成独立顶层窗口，
        # 主窗口激活时它能正常退到后面；位置由 open_devtools 负责居中。
        super().__init__(None, "开发者面板")
        self._win = window
        self.setStyleSheet(QSS + _DEV_CSS)
        self.resize(1120 + SHADOW * 2, 680 + SHADOW * 2)

        self._logs: deque[tuple[int, str, str, float]] = deque(maxlen=MAX_LOG)
        self._errors: deque[str] = deque(maxlen=MAX_ERRORS)
        self._steps: deque[dict] = deque(maxlen=1500)
        # 面板关着的时候 runlog 照样在记：打开时按时间戳把「没见过的」补进来，
        # 这些水位线也保证反复开关面板不会重复灌同一条记录
        self._seen_log_ts = 0.0
        self._seen_event_ts = 0.0
        self._seen_error_ts = 0.0
        self._attach_ts = 0.0
        self._sep_done = False
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
        self._replaying = False
        self._cmd_q: queue.Queue = queue.Queue()
        # 工作线程不能直接碰控件：把回调塞进这个队列，由 _flush 在主线程执行
        self._ui_q: queue.Queue = queue.Queue()
        self._cmd_history: list[str] = []
        self._cmd_pos = 0

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
        # 和主窗口的下拉框用同一套浮层处理：不加就会闪一帧浅色、圆角外露直角
        style_combo(self.scope_box)
        self.scope_box.currentTextChanged.connect(self._on_scope_changed)
        filters.addWidget(self.scope_box)
        self.only_fail = QCheckBox("只看失败")
        self.only_fail.stateChanged.connect(self._on_scope_changed)
        filters.addWidget(self.only_fail)
        self.count_label = QLabel("0 步")
        self.count_label.setStyleSheet("color:#96969e;")
        filters.addWidget(self.count_label)
        filters.addStretch(1)
        self.replay_btn = QPushButton("导入回放")
        self.replay_btn.setToolTip("读取诊断包里的 steps.txt，离线查看别人的现场")
        self.replay_btn.clicked.connect(self._toggle_replay)
        filters.addWidget(self.replay_btn)
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
        style_combo(self.level_box)
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

        self.status_view = self._build_status_page()
        self.log_view = self._make_view()
        err_panel = QWidget()
        ev = QVBoxLayout(err_panel)
        ev.setContentsMargins(0, 0, 0, 0)
        ev.setSpacing(4)
        # 「上次异常退出」横幅：常驻记录发现上次没收干净时提示，配合崩溃现场文件
        self.err_banner = QLabel("")
        self.err_banner.setWordWrap(True)
        self.err_banner.setStyleSheet(
            "QLabel { background:#2a2023; border:1px solid #5a3a3a; border-radius:6px;"
            " color:#e8a09a; padding:6px 10px; }"
        )
        self.err_banner.hide()
        ev.addWidget(self.err_banner)
        err_bar = QHBoxLayout()
        err_bar.setSpacing(6)
        err_hint = QLabel("警告以上会进这里；程序启动即开始采集，面板晚开也能看到之前的问题。")
        err_hint.setStyleSheet("color:#7f7f88;")
        err_hint.setWordWrap(True)
        err_bar.addWidget(err_hint, 1)
        btn_copy_ctx = QPushButton("复制现场")
        btn_copy_ctx.setToolTip("版本、音源、勾选、队列、最近事件——贴出来就能复现大半问题")
        btn_copy_ctx.clicked.connect(self._copy_context)
        err_bar.addWidget(btn_copy_ctx)
        btn_open_dump = QPushButton("打开崩溃现场")
        btn_open_dump.setToolTip("打开 diagnostics 目录里的 last-crash-*.txt")
        btn_open_dump.clicked.connect(self._open_crash_dump)
        err_bar.addWidget(btn_open_dump)
        ev.addLayout(err_bar)
        self.err_list = QListWidget()
        self.err_list.currentRowChanged.connect(self._show_error_detail)
        self.err_view = self._make_view()
        ev.addWidget(self.err_list, 1)
        ev.addWidget(self.err_view, 1)
        self.env_view = self._make_view()

        # 顶部分段按钮 + 页面栈。之前用 QTabWidget，6 个标签在默认宽度下放不下，
        # Qt 会把第一个「状态」挤掉并出现 ◄► 滚动箭头；按钮行放得下，以后加页也不会再挤
        self.stack = QStackedWidget()
        self.nav_buttons: list[QPushButton] = []
        nav_group = QButtonGroup(self)
        nav_group.setExclusive(True)
        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)
        for index, (title, page) in enumerate(
            (
                ("状态", self.status_view),
                ("日志", self.log_view),
                ("问题", err_panel),
                ("组件", self._build_components()),
                ("命令行", self._build_console()),
                ("环境", self.env_view),
            )
        ):
            self.stack.addWidget(page)
            btn = QPushButton(title)
            btn.setCheckable(True)
            btn.setProperty("nav", True)
            btn.setChecked(index == 0)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _c, i=index: self.stack.setCurrentIndex(i))
            nav_group.addButton(btn, index)
            nav_row.addWidget(btn)
            self.nav_buttons.append(btn)
        nav_row.addStretch(1)
        self.help_btn = QPushButton()
        self.help_btn.setIcon(icons.icon("circle-help", "#96969e", 16))
        self.help_btn.setIconSize(QSize(16, 16))
        self.help_btn.setFixedSize(30, 26)
        self.help_btn.setToolTip("使用说明（可搜索功能名、按钮名）")
        self.help_btn.setCursor(Qt.PointingHandCursor)
        self.help_btn.setFocusPolicy(Qt.NoFocus)
        self.help_btn.clicked.connect(self.open_help)
        nav_row.addWidget(self.help_btn)
        rv.addLayout(nav_row)
        self.stack.setCurrentIndex(0)
        rv.addWidget(self.stack, 1)
        self.stack.currentChanged.connect(lambda _i: self._refresh_status())

        # QDialog 里回车会点中 autoDefault 按钮：在命令行页按回车执行命令的同时，
        # 还会把「导入回放」点一下。命令行的按钮一律不当默认按钮
        for btn in self.findChildren(QPushButton):
            btn.setAutoDefault(False)
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
        self._refresh_crash_banner()

    # ---------------- 挂载 ----------------

    def _attach(self) -> None:
        """挂到 root logger 和 trace 上；重复调用无副作用。"""
        from ncm.runlog import runlog

        runlog.install()
        self._seed(runlog)
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

    def _seed(self, runlog) -> None:
        """把面板没开时攒下的历史灌进来，并留一条分隔线标出「实时」从哪里开始。"""
        snap = runlog.snapshot(0.0)
        logs = [item for item in snap["logs"] if item[3] > self._seen_log_ts]
        events = [item for item in snap["events"] if item.get("t", 0) > self._seen_event_ts]
        errors = [text for ts, text in snap["errors"] if ts > self._seen_error_ts]
        if logs:
            self._seen_log_ts = max(self._seen_log_ts, logs[-1][3])
        if events:
            self._seen_event_ts = max(self._seen_event_ts, events[-1]["t"])
        if errors:
            self._seen_error_ts = time.time()
        if not (logs or events or errors):
            return
        self._logs.extend(logs)
        self._errors.extend(errors)
        self._steps.extend(events)
        # 分隔线只在「本次面板第一次打开」处画一次；之后关掉再开补进来的记录
        # 本来就比它新，会落在它下面，不用再画第二条
        if not self._attach_ts:
            self._attach_ts = time.time()
        self._sep_done = False
        self._rebuild_log()
        self._rebuild_errors()
        if not self._replaying:
            self._rebuild_steps()
        self.count_label.setText(f"{len(self._steps)} 步")
        self._update_problem_badge(len(self._errors))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._attach()
        self._refresh_env()
        self._refresh_crash_banner()
        # 按钮挂进 QDialog 的那一刻 Qt 会把 autoDefault 重新打开（实测），回车
        # 就会点中「导入回放」这类按钮。布局挂完之后这里再关一遍才真正生效
        for btn in self.findChildren(QPushButton):
            btn.setAutoDefault(False)

    def open_help(self) -> None:
        """打开使用说明。复用同一个窗口，避免反复创建。"""
        win = getattr(self, "_help_window", None)
        if win is None:
            from gui.devhelp import DevHelpDialog

            win = DevHelpDialog(self)
            self._help_window = win
        win.show()
        win.raise_()
        win.activateWindow()

    def _detach(self) -> None:
        self._timer.stop()
        self._status_timer.stop()
        if self._handler is not None:
            logging.getLogger().removeHandler(self._handler)
            self._handler = None
        trace_mod.remove_listener(self._on_trace)
        # 帮助窗口是非模态的独立窗口，面板关掉后它会留在屏幕上变成孤儿：
        # 那时它已经没有父窗口可依附，用户只能自己找关闭按钮
        help_win = getattr(self, "_help_window", None)
        if help_win is not None and help_win.isVisible():
            help_win.close()

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
        if self._replaying:
            return
        self._event_q.put(event)

    def _on_pause(self, state) -> None:
        self._paused = state == Qt.Checked
        if not self._paused:
            # 暂停期间数据一直往队列里攒，只是没渲染。恢复时按数据重建一遍，
            # 否则这段时间的日志和步骤再也不会出现（除非去改过滤条件）
            self._rebuild_log()
            self._rebuild_errors()
            if not self._replaying:
                self._rebuild_steps()

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
        # 清空只清「已显示的」，水位线推到当前，历史不会被再灌回来
        now = time.time()
        self._seen_log_ts = self._seen_event_ts = self._seen_error_ts = now
        self._attach_ts = 0.0
        self._sep_done = False
        self.tree.clear()
        self.log_view.clear()
        self.err_view.clear()
        self.err_list.clear()
        self._update_problem_badge(0)
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
            # 水位线跟上实时流，之后再打开面板不会把这段重复补一遍
            self._seen_log_ts = max(self._seen_log_ts, created)
            if level >= logging.WARNING:
                self._seen_error_ts = max(self._seen_error_ts, created)
                line = f"{time.strftime('%H:%M:%S', time.localtime(created))} {_LEVEL_NAME.get(level, '信息')} {name}: {message}"
                self._errors.append(_redact(line))
                self._last_error_ts = time.time()
                # 有过滤时只加匹配项，否则列表里混进不匹配的行，
                # 点它取详情会按过滤后的下标去 _errors 里取，显示成别人的内容
                if not self._paused and (
                    not self._keyword or self._keyword.lower() in self._errors[-1].lower()
                ):
                    self.err_list.addItem(QListWidgetItem(self._errors[-1]))
                    while self.err_list.count() > MAX_ERRORS:
                        self.err_list.takeItem(0)
                dirty_err = True
            if self._match(level, message) and not self._paused:
                if self._attach_ts and not self._sep_done:
                    # 第一条实时记录之前，把「历史 / 实时」的分界画出来
                    self.log_view.appendPlainText(_HISTORY_SEP)
                    self._sep_done = True
                self.log_view.appendPlainText(self._fmt(level, name, message, created))
                dirty_log = True
        if dirty_err:
            self._update_problem_badge(len(self._errors))
        while True:
            try:
                text = self._cmd_q.get_nowait()
            except queue.Empty:
                break
            # 命令行输出统一走共享的 console，面板和独立窗口都能看到
            self._win.console.emit_block(text)
        while True:
            try:
                callback = self._ui_q.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:  # noqa: BLE001
                # 回调只用来刷状态栏/标签，失败不影响面板本身
                pass
        appended = False
        if not self._paused:
            for _ in range(200):
                try:
                    event = self._event_q.get_nowait()
                except queue.Empty:
                    break
                self._steps.append(event)
                self._seen_event_ts = max(self._seen_event_ts, event.get("t", 0.0))
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
        rows: list[str] = []
        marked = not self._attach_ts
        for lv, nm, msg, ts in self._logs:
            if not self._match(lv, msg):
                continue
            if not marked and ts >= self._attach_ts:
                # 这条之后是打开面板时才实时收到的，与历史分开
                rows.append(_HISTORY_SEP)
                marked = True
                self._sep_done = True
            rows.append(self._fmt(lv, nm, msg, ts))
        if rows:
            self.log_view.setPlainText("\n".join(rows[-MAX_LOG:]))
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

    def _copy_context(self) -> None:
        """把当前现场 + 最近问题复制走，方便直接贴给我或记进 issue。"""
        from ncm.runlog import runlog

        parts = [runlog.context(), "", "== 最近问题 =="]
        parts.extend(list(self._errors)[-12:] or ["（无）"])
        QApplication.clipboard().setText(_redact("\n".join(parts)))
        self.status.setText("现场已复制到剪贴板（含 %d 条问题）" % len(self._errors))

    def _open_crash_dump(self) -> None:
        """打开最近一份崩溃现场；没有就打开目录并说明。"""
        folder = data_dir() / "diagnostics"
        try:
            files = sorted(folder.glob("last-crash-*.txt"))
        except OSError:
            files = []
        if not files:
            self.status.setText("还没有崩溃现场（正常退出不会生成）")
            self._open_dir(folder)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(files[-1])))
        self.status.setText(f"已打开 {files[-1].name}")

    def _refresh_crash_banner(self) -> None:
        from ncm.runlog import runlog

        prev = runlog.previous
        if not prev or prev.get("clean"):
            self.err_banner.hide()
            return
        started = float(prev.get("started_at") or 0)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(started)) if started else "未知时间"
        self.err_banner.setText(
            "上次运行没有正常退出（启动于 %s，版本 %s）。点「打开崩溃现场」看当时的日志与调用栈。"
            % (when, prev.get("version") or "?")
        )
        self.err_banner.show()

    def _update_problem_badge(self, count: int) -> None:
        """「问题」按钮兼当徽标：有未处理错误时整个按钮变红，不用翻页也能看见。"""
        btn = self.nav_buttons[2]
        btn.setText(f"问题 {count}" if count else "问题")
        danger = count > 0
        if btn.property("danger") != danger:
            btn.setProperty("danger", danger)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # ---------------- 状态 / 快捷动作 ----------------

    def _refresh_status(self) -> None:
        """定时器入口：只在面板可见且停在状态页时才重渲染，省开销。"""
        if not self.isVisible() or self.stack.currentIndex() != 0:
            return
        self._render_status()

    def _status_section(self, title: str) -> QWidget:
        """状态页的小节标题：灰字 + 一条细分隔线。"""
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        label = QLabel(title)
        label.setStyleSheet("color:#96969e;font-size:11px;")
        lay.addWidget(label)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background:#2a2a30;border:0;")
        lay.addWidget(line)
        box.title_label = label
        return box

    def _make_queue_slot(self) -> dict:
        """队列里的一行：名称 / 进度条 / 状态词。行控件预先建好反复用。"""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        name = QLabel("")
        name.setStyleSheet("color:#d8d8dc;font-size:11px;")
        name.setMinimumWidth(40)
        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.setTextVisible(False)
        bar.setFixedHeight(4)
        bar.setFixedWidth(96)
        bar.setStyleSheet(_STATUS_BAR_CSS)
        tail = QLabel("")
        tail.setStyleSheet("color:#96969e;font-size:11px;")
        tail.setFixedWidth(52)
        tail.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(name, 1)
        h.addWidget(bar)
        h.addWidget(tail)
        row.setVisible(False)
        return {"row": row, "name": name, "bar": bar, "tail": tail, "_tail": None}

    def _build_status_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(7)

        self.st_player_head = self._status_section("播放器")
        lay.addWidget(self.st_player_head)
        self.st_song = QLabel("还没有播放")
        self.st_song.setStyleSheet("color:#e8e8ea;font-size:13px;")
        self.st_song.setWordWrap(True)
        lay.addWidget(self.st_song)

        self.st_bar = QProgressBar()
        self.st_bar.setRange(0, 1000)
        self.st_bar.setTextVisible(False)
        self.st_bar.setFixedHeight(6)
        self.st_bar.setStyleSheet(_STATUS_BAR_CSS)
        lay.addWidget(self.st_bar)

        state_row = QHBoxLayout()
        state_row.setContentsMargins(0, 0, 0, 0)
        state_row.setSpacing(8)
        self.st_state = QLabel("已停止 · mediaStatus 1")
        self.st_state.setStyleSheet("color:#96969e;font-size:11px;")
        self.st_time = QLabel("— / —")
        self.st_time.setStyleSheet("color:#96969e;font-size:11px;")
        state_row.addWidget(self.st_state)
        state_row.addStretch(1)
        state_row.addWidget(self.st_time)
        lay.addLayout(state_row)

        self.st_meta = QLabel("兼容播放 —")
        self.st_meta.setStyleSheet("color:#7a7a82;font-size:11px;")
        self.st_meta.setWordWrap(True)
        lay.addWidget(self.st_meta)

        lay.addWidget(self._status_section("任务"))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)
        self.st_tasks: dict[str, QLabel] = {}
        for index, (key, title) in enumerate(
            (("batch", "批量下载"), ("search", "搜索"), ("scan", "缓存扫描"))
        ):
            name = QLabel(title)
            name.setStyleSheet("color:#96969e;font-size:11px;")
            name.setFixedWidth(56)
            value = QLabel("空闲")
            value.setStyleSheet("color:#d8d8dc;font-size:11px;")
            value.setWordWrap(True)
            grid.addWidget(name, index, 0, Qt.AlignTop)
            grid.addWidget(value, index, 1)
            self.st_tasks[key] = value
        lay.addLayout(grid)

        self.st_queue_head = self._status_section("队列")
        lay.addWidget(self.st_queue_head)
        holder = QWidget()
        holder_lay = QVBoxLayout(holder)
        holder_lay.setContentsMargins(0, 0, 0, 0)
        holder_lay.setSpacing(4)
        self.st_queue_rows: list[dict] = []
        for _ in range(MAX_QUEUE_ROWS):
            slot = self._make_queue_slot()
            holder_lay.addWidget(slot["row"])
            self.st_queue_rows.append(slot)
        self.st_queue_empty = QLabel("空")
        self.st_queue_empty.setStyleSheet("color:#6a6a72;font-size:11px;")
        holder_lay.addWidget(self.st_queue_empty)
        holder_lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(holder)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:0;}"
            "QScrollArea>QWidget>QWidget{background:transparent;}"
        )
        scroll.setMinimumHeight(70)
        lay.addWidget(scroll, 1)

        self.st_queue_more = QLabel("")
        self.st_queue_more.setStyleSheet("color:#7a7a82;font-size:11px;")
        self.st_queue_more.setVisible(False)
        lay.addWidget(self.st_queue_more)

        self.st_error = QLabel("")
        self.st_error.setWordWrap(True)
        self.st_error.setStyleSheet(
            "background:#241a1b;border-left:2px solid #e8554a;border-radius:4px;"
            "padding:5px 8px;color:#f09595;font-size:11px;"
        )
        self.st_error.setVisible(False)
        lay.addWidget(self.st_error)

        self._status_plain = "== 播放器 ==\n  未初始化"
        self._last_error_ts = 0.0
        return page

    @staticmethod
    def _set_bar_state(bar: QProgressBar, state: str) -> None:
        """改动态属性切颜色，比每次 setStyleSheet 便宜。state: normal/fail/dim。"""
        want_fail = state == "fail"
        want_dim = state == "dim"
        if bar.property("fail") == want_fail and bar.property("dim") == want_dim:
            return
        bar.setProperty("fail", want_fail)
        bar.setProperty("dim", want_dim)
        bar.style().unpolish(bar)
        bar.style().polish(bar)

    def _last_event(self, scope: str, step: str) -> dict | None:
        """从面板已经收到的事件里翻最近一条，用来显示「上次跑了什么」。"""
        for event in reversed(self._steps):
            if event["scope"] == scope and event["step"] == step:
                return event
        return None

    def _render_status(self) -> None:
        win = self._win
        player = getattr(win, "player", None)
        plain: list[str] = ["== 播放器 =="]

        if player is None:
            self.st_song.setText("播放器未初始化")
            self.st_state.setText("—")
            self.st_time.setText("— / —")
            self.st_meta.setText("")
            self.st_bar.setValue(0)
            self._set_bar_state(self.st_bar, "dim")
            plain.append("  未初始化")
        else:
            qp = player._player
            state = {0: "已停止", 1: "播放中", 2: "已暂停"}.get(
                int(qp.state()), str(int(qp.state()))
            )
            status = int(qp.mediaStatus())
            dur, pos = qp.duration(), qp.position()
            song = player.song
            self.st_song.setText(
                f"{song.name} - {song.artist_text}" if song is not None else "还没有播放"
            )
            self.st_state.setText(f"{state} · mediaStatus {status}")
            if dur > 0:
                pos = max(0, min(pos, dur))
                self.st_bar.setValue(int(pos * 1000 / dur))
                self.st_time.setText(f"{self._mmss(pos)} / {self._mmss(dur)}")
                prog = f"{self._mmss(pos)} / {self._mmss(dur)}"
                self._set_bar_state(self.st_bar, "normal")
            else:
                self.st_bar.setValue(0)
                self.st_time.setText("— / —")
                prog = "— / —"
                # mediaStatus 8 = InvalidMedia：进度条变红，一眼看出放不了
                self._set_bar_state(self.st_bar, "fail" if status == 8 else "dim")
            meta: list[str] = []
            cur = player._current_path
            if cur is not None:
                size = (
                    f"{cur.stat().st_size / 1048576:.1f} MB" if cur.exists() else "文件不存在"
                )
                src = "本地" if getattr(player, "_used_local", False) else "网络"
                meta.append(f"{src} {cur.name} · {size}")
            meta.append(f"兼容播放 {'开' if player.compat_play else '关'}")
            # 针对当前这一首的判断，不是格式清单：进程里实测过这个后缀放不出来才是「是」。
            # 没有文件时不适用，显示破折号，别硬说「否」
            if cur is None:
                verdict = "—"
            else:
                verdict = "是" if cur.suffix.lower() in player._bad_formats else "否"
            meta.append(f"无法直接播放：{verdict}")
            self.st_meta.setText(" · ".join(meta))
            plain.append(f"  状态 {state} · mediaStatus {status} · 进度 {prog}")
            if song is not None:
                plain.append(f"  曲目 {song.name} - {song.artist_text}")
            if cur is not None:
                plain.append(f"  文件 {cur.name}")

        batch = getattr(win, "batch", None)
        total = getattr(win, "_batch_total", 0)
        done = getattr(win, "_batch_done", 0)
        if batch is not None:
            paused = " · 已暂停" if getattr(win, "_paused", False) else ""
            batch_text = f"进行中 {done}/{total} · 并发 {getattr(win, 'workers', '?')}{paused}"
        else:
            batch_text = "空闲" + (f" · 上批 {total} 首" if total else "")
        self.st_tasks["batch"].setText(batch_text)

        busy = getattr(win, "task", None) is not None
        search_text = "进行中" if busy else "空闲"
        last_search = None if busy else self._last_event("搜索", "完成")
        if last_search:
            count = last_search["detail"].get("count")
            if count:
                search_text += f" · 上次 {count} 首"
        self.st_tasks["search"].setText(search_text)

        scan_text = "空闲"
        last_scan = self._last_event("缓存", "扫描")
        if last_scan:
            detail = last_scan["detail"]
            files = detail.get("files", "?")
            complete = detail.get("complete", "?")
            head = (
                f"上次 {last_scan['ms']:.0f} ms · " if last_scan.get("ms") else "上次 "
            )
            scan_text = f"{head}{files} 个文件（完整 {complete}）"
        self.st_tasks["scan"].setText(scan_text)

        plain.append("")
        plain.append("== 任务 ==")
        plain.append(f"  批量下载 {batch_text}")
        plain.append(f"  搜索 {search_text}")
        plain.append(f"  缓存扫描 {scan_text}")

        songs = list(getattr(win, "_queue_songs", []))
        bars = getattr(win, "bars", {})
        failed_ids = {s.id for s in getattr(win, "_failed", [])}
        self.st_queue_head.title_label.setText(
            f"队列 · {len(songs)} 首" if songs else "队列"
        )
        self.st_queue_empty.setVisible(not songs)
        plain.append("")
        plain.append("== 队列 ==")
        for index, slot in enumerate(self.st_queue_rows):
            if index >= len(songs):
                slot["row"].setVisible(False)
                continue
            song = songs[index]
            bar = bars.get(song.id)
            slot["name"].setText(song.name)
            slot["name"].setToolTip(f"{song.name} - {song.artist_text}")
            if bar is None:
                slot["bar"].setValue(0)
                self._set_bar_state(slot["bar"], "dim")
                tail, color = "等待", "#7a7a82"
            elif song.id in failed_ids:
                slot["bar"].setValue(1000)
                self._set_bar_state(slot["bar"], "fail")
                tail, color = "失败", "#e8554a"
            else:
                mx, val = bar.maximum(), bar.value()
                if mx <= 0:
                    slot["bar"].setValue(0)
                    self._set_bar_state(slot["bar"], "dim")
                    tail, color = "取址中", "#7a7a82"
                elif val >= mx:
                    slot["bar"].setValue(1000)
                    self._set_bar_state(slot["bar"], "normal")
                    tail, color = "完成", "#4caf7d"
                else:
                    slot["bar"].setValue(int(min(val, mx) * 1000 / mx))
                    self._set_bar_state(slot["bar"], "normal")
                    tail, color = f"{val * 100 // mx}%", "#96969e"
            if slot["_tail"] != (tail, color):
                slot["_tail"] = (tail, color)
                slot["tail"].setText(tail)
                slot["tail"].setStyleSheet(f"color:{color};font-size:11px;")
            slot["row"].setVisible(True)
            plain.append(f"  {song.name[:28]:<28} {tail}")

        extra = len(songs) - MAX_QUEUE_ROWS
        if extra > 0:
            self.st_queue_more.setText(f"另有 {extra} 首未显示，完整进度看主窗口")
            self.st_queue_more.setVisible(True)
            plain.append(f"  …另有 {extra} 首")
        else:
            self.st_queue_more.setVisible(False)

        # 最近一条错误：只在 5 分钟内显示，否则旧的会一直挂着误导人
        fresh = bool(self._errors) and (time.time() - self._last_error_ts) < ERROR_BANNER_SEC
        self.st_error.setVisible(fresh)
        if fresh:
            self.st_error.setText(self._errors[-1])

        self._status_plain = "\n".join(plain)

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
            try:
                item.setText(3, took)
                item.setText(4, f"{detail} ×{count}".strip())
            except RuntimeError:
                # 节点已经被容量上限淘汰掉，退化成新增一行
                self._last_step = None
            else:
                self._last_step = (key, item, count, event["t"])
                return
        if group:
            # 这里不能复用 key：合并判断用的是「步骤+结果+说明」的元组，
            # 一旦被换成「范围|歌名」，同一首歌的取址/下载/写标签会被当成
            # 同一个事件合并成一行 ×3，步骤名就全错了
            group_key = f"{event['scope']}|{group}"
            top = self._groups.get(group_key)
            if top is None:
                top = QTreeWidgetItem([clock, f"{event['scope']} · {group}", "", "", ""])
                font = top.font(1)
                font.setBold(True)
                top.setFont(1, font)
                self.tree.addTopLevelItem(top)
                self._groups[group_key] = top
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
                # _last_step 存的是 item 引用：它随节点一起被销毁后，下一次
                # 合并还会去 setText，直接 RuntimeError。这里一并清掉
                if self._last_step is not None:
                    target = self._last_step[1]
                    if target is gone or gone.indexOfChild(target) >= 0:
                        self._last_step = None
                for key, value in list(self._groups.items()):
                    if value is gone:
                        del self._groups[key]

    # ---------------- 组件 ----------------

    def _build_components(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(10)

        tip = QLabel("改动立即生效；播放后端是进程级设置，切换后需要重启程序。")
        tip.setStyleSheet("color:#96969e;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.ffmpeg_label = QLabel()
        self.ffmpeg_label.setWordWrap(True)
        lay.addWidget(self.ffmpeg_label)
        row = QHBoxLayout()
        row.setSpacing(6)
        for label, slot in (
            ("更换…", self._change_ffmpeg),
            ("测试", self._test_current_ffmpeg),
            ("恢复默认", self._reset_ffmpeg),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch(1)
        lay.addLayout(row)

        back_row = QHBoxLayout()
        back_row.setSpacing(8)
        back_row.addWidget(QLabel("播放后端"))
        self.backend_box = QComboBox()
        for label, value in (
            ("Media Foundation（推荐）", "windows,dsengine"),
            ("仅 Media Foundation", "windows"),
            ("仅 DirectShow", "dsengine"),
        ):
            self.backend_box.addItem(label, value)
        style_combo(self.backend_box)
        self.backend_box.currentIndexChanged.connect(self._on_backend_changed)
        back_row.addWidget(self.backend_box, 1)
        lay.addLayout(back_row)
        self.backend_hint = QLabel()
        self.backend_hint.setStyleSheet("color:#96969e;")
        self.backend_hint.setWordWrap(True)
        lay.addWidget(self.backend_hint)

        self.compat_check = QCheckBox("兼容播放（无法直接播放时转 WAV）")
        self.compat_check.stateChanged.connect(self._on_compat_changed)
        lay.addWidget(self.compat_check)
        self.decode_label = QLabel()
        self.decode_label.setStyleSheet("color:#96969e;")
        self.decode_label.setWordWrap(True)
        lay.addWidget(self.decode_label)
        lay.addStretch(1)
        self._refresh_components()
        return page

    def _refresh_components(self) -> None:
        try:
            from ncm import transcode

            path = transcode.ffmpeg_path()
        except Exception:  # noqa: BLE001
            path = None
        self.ffmpeg_label.setText(f"ffmpeg：{path or '未找到'}")
        self.ffmpeg_label.setStyleSheet(
            "color:#d8d8dc;" if path else "color:#e8554a;"
        )
        saved = (
            self._win.settings.value("playback_backend", "", type=str)
            or os.environ.get("QT_MULTIMEDIA_PREFERRED_PLUGINS", "")
            or "windows,dsengine"
        )
        if saved not in ("windows,dsengine", "windows", "dsengine"):
            saved = "windows,dsengine"
        self.backend_box.blockSignals(True)
        self.backend_box.setCurrentIndex(max(0, self.backend_box.findData(saved)))
        self.backend_box.blockSignals(False)
        self.backend_hint.setText(
            f"当前进程使用 {os.environ.get('QT_MULTIMEDIA_PREFERRED_PLUGINS', '默认')}；"
            "改完点关闭后重新打开程序生效。"
        )
        self.compat_check.blockSignals(True)
        self.compat_check.setChecked(bool(getattr(self._win.player, "compat_play", False)))
        self.compat_check.blockSignals(False)
        bad = sorted(getattr(self._win.player, "_bad_formats", []) or [])
        self.decode_label.setText(
            "本机无法直接播放的格式：" + ("、".join(bad) if bad else "暂无（还没遇到过）")
        )

    def _test_ffmpeg_async(self, path: str, on_done) -> None:
        """ffmpeg -version 放后台跑：卡住的 exe 会让界面僵住好几秒。"""

        def work() -> None:
            ok, text = self._test_ffmpeg(path)
            self._ui_q.put(lambda: on_done(ok, text))

        threading.Thread(target=work, daemon=True).start()

    def _test_ffmpeg(self, path: str) -> tuple[bool, str]:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(
                [path, "-version"], capture_output=True, text=True,
                timeout=6, creationflags=flags,
            )
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)[:80]
        line = (proc.stdout or proc.stderr or "").strip().splitlines()
        return proc.returncode == 0, (line[0][:90] if line else "无输出")

    def _change_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 ffmpeg", "", "ffmpeg (ffmpeg*.exe);;可执行文件 (*.exe)"
        )
        if not path:
            return
        if "ffmpeg" not in Path(path).name.lower():
            self.status.setText("文件名里没有 ffmpeg，已拒绝")
            return
        self.status.setText("正在校验 ffmpeg…")

        def done(ok: bool, text: str) -> None:
            if not ok:
                self.status.setText(f"不可用：{text}")
                return
            self._win.settings.setValue("ffmpeg_path", path)
            from ncm import transcode

            transcode.set_ffmpeg_override(path)
            self._refresh_components()
            self.status.setText(f"已替换：{text}")

        self._test_ffmpeg_async(path, done)

    def _test_current_ffmpeg(self) -> None:
        from ncm import transcode

        path = transcode.ffmpeg_path()
        if not path:
            self.status.setText("没有可用的 ffmpeg")
            return
        self.status.setText("正在测试 ffmpeg…")
        self._test_ffmpeg_async(
            path,
            lambda ok, text: self.status.setText(("可用：" if ok else "不可用：") + text),
        )

    def _reset_ffmpeg(self) -> None:
        self._win.settings.setValue("ffmpeg_path", "")
        from ncm import transcode

        transcode.set_ffmpeg_override(None)
        self._refresh_components()
        self.status.setText("已恢复默认查找顺序")

    def _on_backend_changed(self, *_args) -> None:
        value = self.backend_box.currentData()
        self._win.settings.setValue("playback_backend", value)
        self.status.setText(f"已记录 {value}，重启程序后生效")

    def _on_compat_changed(self, state) -> None:
        enabled = state == Qt.Checked
        self._win.settings.setValue("compat_play", enabled)
        self._win.player.compat_play = enabled
        self.status.setText("兼容播放已" + ("开启" if enabled else "关闭"))

    # ---------------- 命令行 ----------------

    def _build_console(self) -> QWidget:
        """命令行页 = 共享的 ConsolePanel，和独立窗口是同一份输出与历史。"""
        return ConsolePanel(self._win.console, self)

    def _status_text(self) -> str:
        self._render_status()
        return self._status_plain

    def reload_style(self) -> None:
        """命令行 reload 之后把面板自己的样式表也重套一遍。"""
        self.setStyleSheet(QSS + _DEV_CSS)


    # ---------------- 回放 ----------------

    def _toggle_replay(self) -> None:
        if self._replaying:
            self._exit_replay()
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "导入回放", "", "诊断包 (*.zip);;文本 (*.txt)"
        )
        if not path:
            return
        try:
            if path.lower().endswith(".zip"):
                with zipfile.ZipFile(path) as zf:
                    text = zf.read("steps.txt").decode("utf-8", "replace")
            else:
                text = Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"读取失败：{exc}")
            return
        events = self._parse_steps(text)
        if not events:
            self.status.setText("文件里没有可回放的步骤")
            return
        self._replaying = True
        self._steps.clear()
        self._groups.clear()
        self.tree.clear()
        self._last_step = None
        for event in events:
            self._steps.append(event)
        self._rebuild_steps()
        self.replay_btn.setText("退出回放")
        self.status.setText(f"回放中 · {len(events)} 步（实时事件已暂停）")

    def _exit_replay(self) -> None:
        self._replaying = False
        self.replay_btn.setText("导入回放")
        self._steps.clear()
        self._groups.clear()
        self.tree.clear()
        self._last_step = None
        self.count_label.setText("0 步")
        self.status.setText("已退出回放，恢复实时事件")

    @staticmethod
    def _parse_steps(text: str) -> list[dict]:
        events: list[dict] = []
        now = time.localtime()
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("---"):
                continue
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            clock, scope, rest = parts
            try:
                hh, mm, ss = (int(x) for x in clock.split(":"))
            except ValueError:
                continue
            group = ""
            if rest.startswith("[") and "]" in rest:
                group, rest = rest[1:].split("]", 1)
                rest = rest.strip()
            tokens = rest.split()
            if len(tokens) < 2:
                continue
            step, status = tokens[0], tokens[1]
            idx = 2
            ms = None
            if idx < len(tokens) and tokens[idx].endswith("ms"):
                try:
                    ms = float(tokens[idx][:-2])
                except ValueError:
                    ms = None
                idx += 1
            detail = {}
            for token in tokens[idx:]:
                if "=" in token:
                    key, value = token.split("=", 1)
                    detail[key] = value
            events.append(
                {
                    "t": time.mktime(
                        (now.tm_year, now.tm_mon, now.tm_mday, hh, mm, ss, 0, 0, -1)
                    ),
                    "scope": scope,
                    "step": step,
                    "status": status,
                    "ms": ms,
                    "detail": detail,
                    "group": group,
                }
            )
        return events

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
                lines.append(f"{key} = {self._settings_value_text(settings, key)}")
        return _redact("\n".join(lines)) + "\n"

    def _settings_text(self) -> str:
        settings = getattr(self._win, "settings", None)
        if settings is None:
            return "(无)"
        rows = []
        for key in settings.allKeys():
            rows.append(f"{key} = {self._settings_value_text(settings, key)}")
        return _redact("\n".join(rows)) + "\n"

    @staticmethod
    def _settings_value_text(settings, key: str) -> str:
        """配置值转文本：敏感项打码，二进制（搜索历史是 QVariant）别原样倒出来。"""
        if any(word in key.lower() for word in _SECRET_KEY):
            return "***"
        value = settings.value(key)
        if isinstance(value, (bytes, bytearray)):
            return f"<{len(value)} 字节二进制>"
        text = str(value)
        if len(text) > 200:
            text = text[:200] + "…"
        return text

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
        try:
            QApplication.clipboard().setText(body)
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"复制失败：{exc}")
            return
        self.status.setText(f"诊断信息已复制（{len(body)} 字）")

    def _export_diagnostics(self) -> None:
        try:
            from ncm.log import log_path

            target_dir = data_dir() / "diagnostics"
            target_dir.mkdir(parents=True, exist_ok=True)
            path = target_dir / f"诊断包-{time.strftime('%Y%m%d-%H%M%S')}.zip"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("env.txt", self._env_full_text())
                # 现场：版本、音源、勾选、队列、最近事件。面板只开了几分钟也能带上
                zf.writestr("context.txt", runlog_mod.runlog.context())
                zf.writestr("settings.txt", self._settings_text())
                zf.writestr("steps.txt", self._steps_text())
                zf.writestr("errors.txt", "\n".join(self._errors) + "\n")
                # 上一次崩溃的现场（如果有）
                dumps = sorted((data_dir() / "diagnostics").glob("last-crash-*.txt"))
                if dumps:
                    zf.write(dumps[-1], "last-crash.txt")
                log_file = Path(log_path())
                if log_file.exists():
                    # 日志可能已经几十 MB，只带尾部 512 KB，够定位问题又不至于传不动
                    with open(log_file, "rb") as fh:
                        fh.seek(0, os.SEEK_END)
                        size = fh.tell()
                        fh.seek(max(0, size - 512 * 1024))
                        tail = fh.read()
                    zf.writestr("app.log", tail)
            self.status.setText(f"已导出：{path}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"导出失败：{exc}")

    def _open_log_file(self) -> None:
        try:
            from ncm.log import log_path

            path = Path(log_path())
            if not path.exists():
                self.status.setText("日志文件还没生成")
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"打不开日志：{exc}")
