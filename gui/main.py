"""主窗口。"""
from __future__ import annotations

import logging
import os
from collections import Counter
from pathlib import Path

from PyQt5.QtCore import (
    QEasingCurve,
    QFileSystemWatcher,
    QPropertyAnimation,
    QRectF,
    QSettings,
    QSize,
    Qt,
    QTimer,
    QUrl,
)
from PyQt5.QtGui import (
    QColor,
    QCursor,
    QDesktopServices,
    QIcon,
    QKeySequence,
    QPainter,
    QPalette,
    QPixmap,
    QRegion,
)
from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QProgressBar,
    QPushButton,
    QShortcut,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ncm import LocalBackend, Store
from ncm import auth
from ncm import cache as cache_mod
from ncm.downloader import (
    CANCELLED,
    CACHE_MISS,
    FAILED,
    NO_COPYRIGHT,
    OK,
    SKIPPED,
    SOURCE_LABEL,
    SOURCE_SHORT,
    STATUS_TEXT,
    TRIAL,
    Downloader,
    TaskResult,
)
from ncm.models import QUALITY_LABEL, QUALITY_ORDER, Song
from ncm.store import (
    FOLDER_LABELS,
    NAME_LABELS,
    build_template,
    render_template,
)
from ncm.targets import parse_target
from ncm.transcode import ffmpeg_path
from gui import icons
from gui.combo import style_combo
from gui.dialog import RoundDialog
from gui.help import HelpDialog
from gui.inputs import HistoryLineEdit
from gui.shadow import clip_outside_window, render_shadow
from gui.style import BG, MUTED, PANEL, QSS
from gui.table import FluidTable
from gui.player import PlayerEngine, TopBar
from gui.workers import BatchTask, Task

HEADERS = ["", "曲名", "艺人", "专辑", "时长"]

log = logging.getLogger("ncm.gui")

STATUS_COLOR = {
    OK: "#4caf7d",
    SKIPPED: MUTED,
    CANCELLED: MUTED,
    NO_COPYRIGHT: "#d9a13b",
    TRIAL: "#d9a13b",
    CACHE_MISS: "#d9a13b",
    FAILED: "#e8554a",
}


def _fmt_size(num: int) -> str:
    if not num:
        return ""
    return f"{num / 1048576:.1f} MB"


SORT_KEYS = {
    "title": lambda s: s.name.lower(),
    "artist": lambda s: s.artist_text.lower(),
    "album": lambda s: (s.album.lower(), s.track_no),
    "duration": lambda s: s.duration_ms,
}

# 界面上只给三种明确选择，「自动」留在命令行用
GUI_SOURCES = ("client", "web", "cache")

# 接口单次返回上限，翻页也按这个步长走
PAGE_SIZE = 50


def default_download_dir() -> Path:
    """默认落盘位置。

    不能拿 Path.cwd() 当基准：打包成 exe 后，cwd 可能是桌面、也可能是
    点击快捷方式时的工作目录，文件会散落到意想不到的地方。
    """
    return Path.home() / "Music" / "网易云下载"

# 底部操作栏按钮统一高度：设置、选择器、「下载选中」共用一个值，避免一高一低
ACTION_BTN_H = 32
# 紧凑模式下把两个选择器压到刚够显示最长选项（Hi-Res / 仅缓存），给按钮让出宽度
MINI_LEVEL_W = 80
MINI_SOURCE_W = 80
# QSS 里的 min-width 会把 setFixedWidth 顶回去（78 + padding + 边框 = 96），
# 紧凑模式用控件级样式临时解除，退出时置空即回到全局规则
_NARROW_COMBO_QSS = "QComboBox { min-width: 0; }"


def _bar_qss(color: str) -> str:
    # 直接 setStyleSheet 会覆盖掉全局样式，背景与圆角必须一起带上
    return (
        "QProgressBar { background: #2e2e34; border: none; border-radius: 3px; }"
        f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
    )


class SettingsDialog(RoundDialog):
    """账号登录与下载参数。登录后音质上限由账号权限决定。"""

    def __init__(
        self,
        settings: QSettings,
        out_dir: Path,
        cache_dir: Path | None = None,
        parent=None,
    ):
        super().__init__(parent, "设置", width=548)
        self._settings = settings
        self.out_dir = Path(out_dir)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.changed = False

        layout = self.body_layout
        form = QFormLayout()
        form.setSpacing(10)

        account_row = QHBoxLayout()
        account_row.setSpacing(8)
        self.account_label = QLabel()
        account_row.addWidget(self.account_label, 1)
        btn_login = QPushButton("扫码登录")
        btn_login.clicked.connect(self._login)
        account_row.addWidget(btn_login)
        self.btn_logout = QPushButton("退出")
        self.btn_logout.clicked.connect(self._logout)
        account_row.addWidget(self.btn_logout)
        form.addRow("账号", account_row)

        self.workers = QSpinBox()
        self.workers.setRange(1, 8)
        self.workers.setValue(settings.value("workers", 4, type=int))
        form.addRow("并发数", self.workers)

        self.to_mp3 = QCheckBox("把 m4a / flac 转成 mp3")
        self.to_mp3.setChecked(settings.value("to_mp3", False, type=bool))
        self.to_mp3.setToolTip(
            "转码按源码率上浮一档（如 96k AAC → 160k mp3），需要系统装有 ffmpeg"
        )
        form.addRow("输出格式", self.to_mp3)

        # 转码能不能用直接写出来，别等用户勾了才发现失败
        self.ffmpeg_state = QLabel()
        self.ffmpeg_state.setObjectName("muted")
        form.addRow("", self.ffmpeg_state)
        self._refresh_ffmpeg_state()

        self.name_box = QComboBox()
        for key, label in NAME_LABELS.items():
            self.name_box.addItem(label, key)
        self.name_box.setCurrentIndex(
            max(0, self.name_box.findData(settings.value("name_pattern", "artist_title", type=str)))
        )
        style_combo(self.name_box)
        form.addRow("文件名", self.name_box)

        self.folder_box = QComboBox()
        for key, label in FOLDER_LABELS.items():
            self.folder_box.addItem(label, key)
        self.folder_box.setCurrentIndex(
            max(0, self.folder_box.findData(settings.value("folder_pattern", "artist_album", type=str)))
        )
        style_combo(self.folder_box)
        form.addRow("分类", self.folder_box)

        self.preview = QLabel()
        self.preview.setObjectName("muted")
        self.preview.setWordWrap(True)
        form.addRow("", self.preview)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        self.dir_value = QLabel()
        self.dir_value.setObjectName("muted")
        dir_row.addWidget(self.dir_value, 1)
        btn_pick = QPushButton("更改")
        btn_pick.setFixedHeight(30)
        btn_pick.setMinimumWidth(58)
        btn_pick.clicked.connect(self._pick_dir)
        dir_row.addWidget(btn_pick)
        form.addRow("保存目录", dir_row)

        cache_row = QHBoxLayout()
        cache_row.setSpacing(8)
        self.cache_value = QLabel()
        self.cache_value.setObjectName("muted")
        cache_row.addWidget(self.cache_value, 1)
        btn_detect = QPushButton("自动检测")
        btn_detect.setFixedHeight(30)
        btn_detect.setToolTip("重新在默认位置查找客户端的播放缓存")
        btn_detect.clicked.connect(self._detect_cache)
        cache_row.addWidget(btn_detect)
        btn_cache = QPushButton("选择")
        btn_cache.setFixedHeight(30)
        btn_cache.setMinimumWidth(58)
        btn_cache.clicked.connect(self._pick_cache)
        cache_row.addWidget(btn_cache)
        form.addRow("缓存目录", cache_row)

        # 未登录时听 VIP 歌会缓存成试听片段，登录后仍是残片；给个入口清掉
        play_row = QHBoxLayout()
        play_row.setSpacing(8)
        self.play_cache_state = QLabel("播放时解密出的临时文件")
        self.play_cache_state.setObjectName("muted")
        play_row.addWidget(self.play_cache_state, 1)
        btn_play_cache = QPushButton("清理")
        btn_play_cache.setFixedHeight(30)
        btn_play_cache.setMinimumWidth(58)
        btn_play_cache.setToolTip(
            "清掉播放缓存。若某首歌还停在试听片段，清完重新播放会取完整版"
        )
        btn_play_cache.clicked.connect(self._clear_play_cache)
        play_row.addWidget(btn_play_cache)
        form.addRow("播放缓存", play_row)

        self.startup_view = QComboBox()
        self.startup_view.addItem("标准窗口", "full")
        self.startup_view.addItem("紧凑模式", "mini")
        saved_view = settings.value("startup_view", "full", type=str)
        self.startup_view.setCurrentIndex(
            max(0, self.startup_view.findData(saved_view))
        )
        self.startup_view.setToolTip(
            "只决定下次启动时用哪种窗口；进来以后仍可用 Ctrl+M 随时切换"
        )
        style_combo(self.startup_view)
        form.addRow("启动视图", self.startup_view)

        history_row = QHBoxLayout()
        history_row.setSpacing(8)
        self.history_state = QLabel("最近 10 条搜索记录")
        self.history_state.setObjectName("muted")
        history_row.addWidget(self.history_state, 1)
        btn_history = QPushButton("清空")
        btn_history.setFixedHeight(30)
        btn_history.setMinimumWidth(58)
        btn_history.clicked.connect(self._clear_history)
        history_row.addWidget(btn_history)
        form.addRow("搜索历史", history_row)

        layout.addLayout(form)
        self._refresh_dir_text()
        self._refresh_cache_text()

        self.name_box.currentIndexChanged.connect(self._refresh_preview)
        self.folder_box.currentIndexChanged.connect(self._refresh_preview)
        self._refresh_preview()

        hint = QLabel(
            "请求以客户端身份发出，因此能用上账号的客户端权益（例如看广告换免费听）。"
            "实际音质由权益决定：免费曲目最高 320k，VIP 曲目通常是 128k～192k，"
            "无损 / Hi-Res 需要会员。转 mp3 只是换容器，不会提升音质。"
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self
        )
        ok = buttons.button(QDialogButtonBox.Ok)
        ok.setText("保存")
        ok.setObjectName("primary")
        # objectName 改在样式应用之后，必须重新 polish 才会命中 #primary 规则
        ok.style().unpolish(ok)
        ok.style().polish(ok)
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_account()

    def _refresh_account(self) -> None:
        if not auth.load_cookie():
            self.account_label.setText("未登录（匿名身份）")
            self.account_label.setStyleSheet(f"color: {MUTED};")
            self.btn_logout.setVisible(False)
            return
        account = auth.saved_account()
        name = account.nickname or "已登录账号"
        self.account_label.setText(f"{name} · {account.vip_text}")
        self.account_label.setStyleSheet("")
        self.btn_logout.setVisible(True)

    def _login(self) -> None:
        from gui.login_dialog import LoginDialog

        dialog = LoginDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.changed = True
            self._refresh_account()

    def _logout(self) -> None:
        auth.clear_login()
        self.changed = True
        self._refresh_account()

    def _refresh_preview(self) -> None:
        name_key = self.name_box.currentData()
        folder_key = self.folder_box.currentData()
        template = build_template(name_key, folder_key)
        demo = Song(0, "够钟", ["周柏豪"], "Follow")
        text = f"示例：{render_template(template, demo)}.mp3"
        if folder_key != "none" and name_key in ("artist_title", "title_artist"):
            text += "　歌手重复，可改用「歌曲名」"
        self.preview.setText(text)

    def _clear_history(self) -> None:
        self._settings.setValue("search_history", [])
        self.history_state.setText("已清空")
        self.history_state.setStyleSheet("color:#4caf7d;")

    def _refresh_ffmpeg_state(self) -> None:
        exe = ffmpeg_path()
        if exe:
            name = Path(exe).name
            where = "内置" if "imageio_ffmpeg" in exe.replace("\\", "/") else "系统"
            self.ffmpeg_state.setText(f"转码引擎：已就绪（{where} {name}）")
            self.ffmpeg_state.setStyleSheet("color:#4caf7d;")
            self.ffmpeg_state.setToolTip(exe)
        else:
            self.ffmpeg_state.setText("转码引擎：未找到，转 mp3 不可用")
            self.ffmpeg_state.setStyleSheet("color:#d9a13b;")
            self.ffmpeg_state.setToolTip(
                "把 ffmpeg.exe 放到程序目录，或安装后加入 PATH"
            )

    def _refresh_dir_text(self) -> None:
        text = str(self.out_dir)
        if len(text) > 42:
            text = "…" + text[-41:]
        self.dir_value.setText(text)
        self.dir_value.setToolTip(str(self.out_dir))

    def _pick_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "选择保存目录", str(self.out_dir)
        )
        if chosen:
            self.out_dir = Path(chosen)
            self._refresh_dir_text()

    def _refresh_cache_text(self) -> None:
        path = self.cache_dir
        if path is None or not Path(path).is_dir():
            self.cache_value.setText("未找到，请手动指定")
            self.cache_value.setToolTip(
                "客户端「设置 → 下载设置」里能看到缓存位置"
            )
            return
        text = str(path)
        if len(text) > 38:
            text = "…" + text[-37:]
        self.cache_value.setText(text)
        # 选错目录时给个提示，不然「仅缓存」一直是空的没人知道原因
        if cache_mod.looks_like_cache_dir(path):
            self.cache_value.setToolTip(str(path))
        else:
            self.cache_value.setToolTip(
                f"{path}\n这个目录里没有 .uc 缓存文件，可能选错了"
            )

    def _clear_play_cache(self) -> None:
        from ncm import cache as cache_mod

        count, total = cache_mod.clear_player_cache()
        if count:
            self.play_cache_state.setText(
                f"已清理 {count} 个文件，释放 {total / 1048576:.1f} MB"
            )
            self.play_cache_state.setStyleSheet("color:#4caf7d;")
        else:
            self.play_cache_state.setText("没有可清理的播放缓存")
            self.play_cache_state.setStyleSheet("")
        self.changed = True

    def _detect_cache(self) -> None:
        self.cache_dir = cache_mod.default_cache_dir()
        self._refresh_cache_text()

    def _pick_cache(self) -> None:
        start = str(self.cache_dir) if self.cache_dir else ""
        chosen = QFileDialog.getExistingDirectory(self, "选择网易云缓存目录", start)
        if chosen:
            self.cache_dir = Path(chosen)
            self._refresh_cache_text()


class MainWindow(QMainWindow):
    SHADOW = 12    # 圆角窗口外留出的阴影/缩放边距
    RADIUS = 12
    EDGE = 6       # 边缘多少像素内触发缩放

    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings("ncm", "downloader")
        self.backend = LocalBackend(cookie=auth.load_cookie())
        self.workers = self.settings.value("workers", 4, type=int)
        self.to_mp3 = self.settings.value("to_mp3", False, type=bool)
        self.name_pattern = self.settings.value("name_pattern", "artist_title", type=str)
        self.folder_pattern = self.settings.value(
            "folder_pattern", "artist_album", type=str
        )
        self.songs: list[Song] = []
        # 每个音源各存一份结果，切换音源互不覆盖
        # source -> (完整列表, 标题, 是否缓存视图)
        self._pools: dict[str, tuple[list[Song], str, bool]] = {}
        self.sort_key = self.settings.value("sort_key", "default", type=str)
        self.sort_desc = self.settings.value("sort_desc", False, type=bool)
        # 搜索结果翻页：记住关键词与下一页偏移，只对接口搜索有效
        self._search_keyword = ""
        self._search_offset = 0
        self._has_more = False
        # 缓存扫描状态：用于表里那句「正在扫描…」以及失败后停掉自动刷新
        self._scanning_cache = False
        self._cache_scan_failed = False
        self._mini = False
        self._full_geometry = None
        self._shadow_px = 12
        self.SHADOW = 12
        self.playlist: list[Song] = []
        self.play_index = -1
        self._repeat = self.settings.value("repeat", "list", type=str)
        self._playing_id: int | None = None
        self._queue_songs: list[Song] = []
        self._batch_done = 0
        self._batch_total = 0
        self._audio_store: Store | None = None
        self._cache_entries: dict[int, object] | None = None
        self._shadow: QPixmap | None = None
        self._shadow_timer = QTimer(self)
        self._shadow_timer.setSingleShot(True)
        self._shadow_timer.setInterval(150)
        self._shadow_timer.timeout.connect(self._rebuild_shadow)
        self._row_paths: dict[int, Path] = {}
        self._cache_rates: dict[int, str] = {}
        self.store: Store | None = None
        self.downloader: Downloader | None = None
        self.task: Task | None = None
        self.batch: BatchTask | None = None
        self.queue_rows: dict[int, int] = {}
        self.bars: dict[int, QProgressBar] = {}
        self._failed: list[Song] = []
        self.out_dir = Path(
            self.settings.value("out_dir", str(default_download_dir()), type=str)
        )
        # 缓存目录：设置里指定过就用它，否则自动探测客户端默认位置
        self.cache_dir = self._load_cache_dir()

        self.setWindowTitle("网易云音乐下载器")
        icon = Path(__file__).parent / "assets" / "app.ico"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        # 无边框自绘：圆角、阴影、标题栏按钮都由自己控制
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setMinimumSize(840, 600)
        # 窗口位置不再记忆：每次都在主屏居中，固定 1000×740。
        # 记住位置在多显示器切换、改分辨率之后经常把窗口丢到屏幕外，
        # 用户既找不到也没法拉回来，索性每次从固定位置开始。
        self._center_window(1000, 740)
        self.setStyleSheet(QSS)
        self._build_ui()
        self._refresh_account_label()
        # 盯着缓存目录：在客户端边听边缓存时列表要跟着变，攒 2.5 秒再扫一次，
        # 因为播放过程中 .uc/.idx 是连续写入的，每次都扫会一直重排表格
        self._cache_watcher = QFileSystemWatcher(self)
        self._cache_watcher.directoryChanged.connect(self._on_cache_dir_changed)
        self._cache_timer = QTimer(self)
        self._cache_timer.setSingleShot(True)
        self._cache_timer.setInterval(2500)
        self._cache_timer.timeout.connect(self._auto_refresh_cache)
        self._watch_cache_dir(self.cache_dir)
        # 启动视图为紧凑模式：先记下完整窗口的几何（Ctrl+M 要还原到这儿），
        # 再把窗口缩到紧凑尺寸，避免 show 出来先大后小闪一下。
        # 真正的状态切换放到事件循环里做，那时窗口已有的几何才是有效的。
        if self.settings.value("startup_view", "full", type=str) == "mini":
            self._full_geometry = self.geometry()
            # 先换成紧凑的边距与最小尺寸，否则 resize 会被 840 的最小宽顶回去，
            # show 出来先 840 再跳到 400，会明显闪一下
            self._shadow_px = 8
            self.SHADOW = 8
            self.setMinimumSize(360, 420)
            self._apply_window_frame()
            self.resize(400, 620)
            QTimer.singleShot(0, self._enter_mini_from_startup)
        # 上次停在「仅本地缓存」时，启动就把列表载入
        if self._current_source() == "cache":
            self.on_load_cache()

    # ---------------- 界面搭建 ----------------

    def _build_ui(self) -> None:
        # 外壳透明，只为给圆角窗口留出阴影边距
        shell = QWidget()
        shell.setObjectName("shell")
        shell.setMouseTracking(True)
        shell.installEventFilter(self)
        self.setCentralWidget(shell)
        self._shell_layout = QVBoxLayout(shell)
        self._shell_layout.setContentsMargins(
            self._shadow_px, self._shadow_px, self._shadow_px, self._shadow_px
        )
        self._shell_layout.setSpacing(0)

        self._root = QWidget()
        self._root.setObjectName("windowRoot")
        self._root.setMouseTracking(True)
        self._root.installEventFilter(self)
        self._shell_layout.addWidget(self._root)

        stack = QVBoxLayout(self._root)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)

        self.topbar = TopBar(self, "网易云音乐下载器", self._root)
        stack.addWidget(self.topbar)

        body = QWidget()
        body.setObjectName("body")
        outer = QVBoxLayout(body)
        # 底部必须留白：操作栏是最后一个元素，留 0 会直接压在状态栏分隔线上，
        # 与上方 spacing 不对称，看着整体下坠
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(10)

        outer.addLayout(self._build_search_bar())

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_result_panel())
        self.queue_panel = self._build_queue_panel()
        splitter.addWidget(self.queue_panel)
        splitter.setSizes([340, 300])
        outer.addWidget(splitter, 1)

        outer.addLayout(self._build_action_bar())
        stack.addWidget(body, 1)
        stack.addWidget(self._build_status_bar())
        self._apply_window_frame()
        self._setup_shortcuts()
        self._init_player()
        # 边缘缩放靠事件冒泡到 _root 来判定，中间任何一层不开 mouseTracking，
        # 鼠标掠过时就不会产生 MouseMove，边缘带等于失效
        for child in self.findChildren(QWidget):
            child.setMouseTracking(True)

    def _apply_window_frame(self) -> None:
        """最大化时去掉边距和圆角，否则四角会露出桌面。"""
        maxed = self.isMaximized()
        margin = 0 if maxed else self.SHADOW
        self._shell_layout.setContentsMargins(margin, margin, margin, margin)
        radius = 0 if maxed else self.RADIUS
        border = "transparent" if maxed else "#2c2c33"
        self._root.setStyleSheet(
            f"QWidget#windowRoot {{ background: {BG};"
            f" border-radius: {radius}px; border: 1px solid {border}; }}"
        )

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.WindowStateChange:
            if hasattr(self, "_root"):
                self._apply_window_frame()
            if hasattr(self, "topbar"):
                self.topbar.sync_window()
        super().changeEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.isMaximized():
            return
        if self._shadow is None:
            self._rebuild_shadow()
        if self._shadow is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        clip_outside_window(painter, self.rect(), self.SHADOW, self.RADIUS)
        self._paint_shadow(painter)
        painter.end()

    def _paint_shadow(self, painter: QPainter) -> None:
        """把阴影贴到窗口外圈。

        尺寸一致就直接贴位图；拖动改大小期间位图与当前窗口对不上，九宫格
        在接缝处仍会漏出未绘制区域（横向拉伸时表现为白条），所以这段时间
        改用四条实心半透明边兜底——形状不完美，但每一帧都必定覆盖满。
        """
        pixmap = self._shadow
        width, height = self.width(), self.height()
        if pixmap.width() == width and pixmap.height() == height:
            painter.drawPixmap(0, 0, pixmap)
            return

        margin = self.SHADOW
        # 比真实阴影最外缘略深一点，拖动时边缘更清楚；松手后会被真阴影替换
        tint = QColor(0, 0, 0, 50)
        painter.fillRect(QRectF(0, 0, width, margin), tint)
        painter.fillRect(QRectF(0, height - margin, width, margin), tint)
        painter.fillRect(QRectF(0, margin, margin, height - margin * 2), tint)
        painter.fillRect(
            QRectF(width - margin, margin, margin, height - margin * 2), tint
        )

    def _rebuild_shadow(self) -> None:
        self._shadow = render_shadow(
            self.width(), self.height(), self.SHADOW, self.RADIUS
        )
        # 只刷外圈：阴影只在窗口四边可见，整窗 update 会让表格跟着重绘一次，
        # 拖动改完尺寸时看起来就是"闪一下"
        margin = self.SHADOW
        ring = QRegion(self.rect()).subtracted(
            QRegion(self.rect().adjusted(margin, margin, -margin, -margin))
        )
        self.update(ring)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 透明窗口在尺寸变化时只失效新露出的区域，而系统 resize 与 Qt 重绘
        # 是异步的，中间会有一帧没内容——表现为拖动时闪出白条。
        # 这里同步重绘一次，把「尺寸已变、内容未画」的窗口期直接消掉。
        self.repaint()
        if hasattr(self, "_shadow_timer"):
            self._shadow_timer.start()

    def _center_window(self, width: int, height: int) -> None:
        """在主屏可用区域居中，尺寸超出时退回屏幕尺寸。"""
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen else None
        if area is None:
            self.resize(width, height)
            return
        w = min(width, area.width())
        h = min(height, area.height())
        x = area.x() + (area.width() - w) // 2
        y = area.y() + (area.height() - h) // 2
        self.setGeometry(x, y, w, h)

    def _edges_at(self, pos) -> Qt.Edges:
        if self.isMaximized():
            return Qt.Edges()
        edges = Qt.Edges()
        # 视觉上的窗口边缘在阴影边距之内（_shadow_px），不是窗口矩形的 0。
        # 命中区以这条边为基准内外各留 EDGE，否则得把鼠标移到窗口外才有缩放光标。
        m = self._shadow_px
        if pos.x() <= m + self.EDGE:
            edges |= Qt.LeftEdge
        elif pos.x() >= self.width() - m - self.EDGE:
            edges |= Qt.RightEdge
        if pos.y() <= m + self.EDGE:
            edges |= Qt.TopEdge
        elif pos.y() >= self.height() - m - self.EDGE:
            edges |= Qt.BottomEdge
        return edges

    @staticmethod
    def _cursor_for(edges: Qt.Edges) -> QCursor:
        left = bool(edges & Qt.LeftEdge)
        right = bool(edges & Qt.RightEdge)
        top = bool(edges & Qt.TopEdge)
        bottom = bool(edges & Qt.BottomEdge)
        if (left and top) or (right and bottom):
            return QCursor(Qt.SizeFDiagCursor)
        if (right and top) or (left and bottom):
            return QCursor(Qt.SizeBDiagCursor)
        if left or right:
            return QCursor(Qt.SizeHorCursor)
        if top or bottom:
            return QCursor(Qt.SizeVerCursor)
        return QCursor(Qt.ArrowCursor)

    def eventFilter(self, obj, event) -> bool:
        """无边框后边缘缩放要自己处理。

        阴影边距和视觉窗口本体是两个控件（shell / _root），事件只会发给命中的那一个，
        所以两边都要装过滤器，判之前先把坐标换算到窗口坐标系。
        """
        # 构造期间 shell 先装过滤器、_root 还没建好，这里必须容错：
        # PyQt 的虚函数里抛出未捕获异常会直接 abort 进程
        root = getattr(self, "_root", None)
        if obj is self.centralWidget() or (root is not None and obj is root):
            kind = event.type()
            # 只有鼠标事件有 pos()，别的事件（Leave/Resize 等）先取坐标会抛异常
            if kind == QEvent.MouseMove and not self.isMaximized():
                pos = obj.mapTo(self, event.pos())
                self.setCursor(self._cursor_for(self._edges_at(pos)))
            elif kind == QEvent.Leave:
                self.unsetCursor()
            elif kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                pos = obj.mapTo(self, event.pos())
                edges = self._edges_at(pos)
                if edges:
                    handle = self.windowHandle()
                    if handle is not None and hasattr(handle, "startSystemResize"):
                        if handle.startSystemResize(edges):
                            return True
        return super().eventFilter(obj, event)

    # ---------------- 播放 ----------------

    def _init_player(self) -> None:
        self.player = PlayerEngine(self)
        self.player.local_provider = self._local_audio
        self.player.remote_provider = self._remote_audio
        self.player.started.connect(self._on_play_started)
        self.player.state_changed.connect(self.topbar.set_state)
        self.player.position.connect(self.topbar.set_position)
        self.player.cover_ready.connect(self.topbar.set_cover)
        self.player.finished.connect(self._on_play_finished)
        self.player.failed.connect(self._on_play_failed)

        bar = self.topbar
        bar.play_pause.connect(self.player.toggle)
        bar.prev.connect(self._play_prev)
        bar.next.connect(self._play_next)
        bar.seeked.connect(self.player.seek)
        bar.volume_changed.connect(self._on_volume_changed)
        bar.repeat_changed.connect(self._on_repeat_changed)
        bar.toggle_mini.connect(self.toggle_mini)
        bar.stop_requested.connect(self._stop_playback)

        volume = self.settings.value("volume", 70, type=int)
        bar.set_volume(volume)
        bar.set_repeat(self._repeat)
        self.player.set_volume(volume)

    def _load_cache_dir(self) -> Path | None:
        """设置里指定过就用它，否则自动探测；失效时再探测一次。"""
        saved = self.settings.value("cache_dir", "", type=str)
        if saved and Path(saved).is_dir():
            return Path(saved)
        return cache_mod.default_cache_dir()

    def _watch_cache_dir(self, path) -> None:
        """只盯当前缓存目录，换目录时先把旧的摘掉。"""
        existing = self._cache_watcher.directories()
        if existing:
            self._cache_watcher.removePaths(existing)
        if path and Path(path).is_dir():
            self._cache_watcher.addPath(str(path))

    def _on_cache_dir_changed(self, _path: str) -> None:
        self._cache_timer.start()  # 防抖：连续写入只触发最后一次

    def _auto_refresh_cache(self) -> None:
        """缓存目录变了就静默重扫，但不打断用户正在做的事。"""
        if self.task is not None or self._current_source() != "cache":
            return
        if self._cache_scan_failed:
            return  # 上次就失败了，等用户手动点「本地缓存」再试
        if self._selected_songs():
            return  # 正勾着歌单，重排会清掉勾选，等下次
        self.on_load_cache()

    def _cache_map(self) -> dict[int, object]:
        """客户端缓存只在第一次需要时扫一遍。"""
        if self._cache_entries is None:
            entries: dict[int, object] = {}
            root = self.cache_dir
            if root is None or not Path(root).is_dir():
                root = cache_mod.default_cache_dir()
            if root and Path(root).is_dir():
                for entry in cache_mod.scan(root):
                    if not entry.complete or entry.size <= 200_000:
                        continue
                    old = entries.get(entry.song_id)
                    if old is None or entry.rate > old.rate:
                        entries[entry.song_id] = entry
            self._cache_entries = entries
        return self._cache_entries

    def _ensure_audio_store(self) -> None:
        if self._audio_store is not None:
            return
        db = self.out_dir / ".ncm-index.db"
        if not db.exists():
            return
        try:
            self._audio_store = Store(db)
        except Exception:  # noqa: BLE001
            self._audio_store = None

    def _local_audio(self, song: Song) -> Path | None:
        """按「已下载 → 客户端缓存 → 没有」的顺序找本地文件。"""
        self._ensure_audio_store()
        if self._audio_store is not None:
            row = self._audio_store.get(song.id)
            if row:
                path = Path(row["path"])
                if path.exists():
                    return path

        entry = self._cache_map().get(song.id)
        if entry is None:
            return None
        from ncm import cache as cache_mod

        # 未登录时听 VIP 歌，客户端只缓存 30 秒试听片段；之后登录了再播，
        # 吃的还是这段残片。用「码率 × 体积」估一下时长，明显短于歌曲时长
        # 就当作试听片段跳过，让调用方走网络取完整版。
        if entry.rate and entry.size and song.duration_ms:
            cached_ms = entry.size * 8 / (entry.rate * 1000) * 1000
            if cached_ms < song.duration_ms * 0.6:
                return None

        target_dir = cache_mod.player_cache_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        scratch = target_dir / f"{song.id}.bin"
        try:
            ext = cache_mod.decode(entry.path, scratch)
        except Exception:  # noqa: BLE001
            # 缓存文件半截、被占用、或换了客户端版本解不开——别直接失败，
            # 交回上层走网络，用户至少还能听
            scratch.unlink(missing_ok=True)
            return None
        final = target_dir / f"{song.id}.{ext}"
        os.replace(scratch, final)
        return final

    def _remote_audio(self, song: Song) -> tuple[str, str] | None:
        try:
            src = self.backend.song_url(song.id, self.level_box.currentData())
        except Exception:  # noqa: BLE001
            return None
        if not src.url:
            return None
        return (src.url, src.ext or "mp3")

    def _play_from_list(self, songs: list[Song], index: int) -> None:
        if not songs or not (0 <= index < len(songs)):
            return
        self.playlist = list(songs)
        self.play_index = index
        self._play_current()

    def _play_current(self) -> None:
        if 0 <= self.play_index < len(self.playlist):
            self.player.play_song(self.playlist[self.play_index])

    def _play_row(self, index) -> None:
        row = index.row() if hasattr(index, "row") else int(index)
        self._play_from_list(self.songs, row)

    def _stop_playback(self) -> None:
        """停止当前播放，回到「没有在播」的干净状态。"""
        player = getattr(self, "player", None)
        if player is None:
            return
        previous = self._playing_id
        player.stop()
        self._playing_id = None
        self.playlist = []
        self.play_index = 0
        self.topbar.set_song(None)
        self.topbar.set_state("stopped")
        self.topbar.set_position(0, 0)
        self._refresh_playing_rows(previous)
        self.status_label.setText("已停止播放")

    def _play_next(self) -> None:
        if not self.playlist:
            return
        nxt = self.play_index + 1
        if nxt >= len(self.playlist):
            nxt = 0 if self._repeat == "list" else len(self.playlist) - 1
        self.play_index = nxt
        self._play_current()

    def _play_prev(self) -> None:
        if not self.playlist:
            return
        # 播放超过 3 秒时先回到开头，符合常见播放器习惯
        if self.player and self.topbar.seek._value > 3000:
            self.player.seek(0)
            return
        prev = self.play_index - 1
        if prev < 0:
            prev = len(self.playlist) - 1 if self._repeat == "list" else 0
        self.play_index = prev
        self._play_current()

    def _refresh_playing_rows(self, previous: int | None) -> None:
        """只重绘受影响的两行，保留用户的勾选状态。"""
        from PyQt5.QtGui import QBrush

        for row, item_song in enumerate(self.songs):
            if item_song.id not in (previous, self._playing_id):
                continue
            active = item_song.id == self._playing_id
            for col in range(5):
                cell = self.result_table.item(row, col)
                if cell is not None:
                    cell.setBackground(
                        QColor("#2c2c36") if active else QBrush()
                    )
            name = self.result_table.item(row, 1)
            if name is not None:
                name.setForeground(
                    QColor("#ffffff") if active else QColor("#d8d8dc")
                )
                font = name.font()
                font.setBold(active)
                name.setFont(font)

    def _on_play_started(self, song: Song) -> None:
        previous = self._playing_id
        self._playing_id = song.id
        self.topbar.set_song(song)
        self._refresh_playing_rows(previous)
        row = next((i for i, s in enumerate(self.songs) if s.id == song.id), -1)
        if row >= 0:
            self.result_table.scrollToItem(
                self.result_table.item(row, 1), QAbstractItemView.PositionAtCenter
            )
        self.status_label.setText(f"正在播放 {song.name} - {song.artist_text}")

    def _on_play_finished(self) -> None:
        if self._repeat == "one" and self.player.song is not None:
            self.player.play_song(self.player.song)
            return
        nxt = self.play_index + 1
        if nxt < len(self.playlist):
            self.play_index = nxt
            self._play_current()
            return
        if self._repeat == "list" and self.playlist:
            self.play_index = 0
            self._play_current()
            return
        previous = self._playing_id
        self._playing_id = None
        self.topbar.set_state("stopped")
        self._refresh_playing_rows(previous)

    def _on_play_failed(self, message: str) -> None:
        previous = self._playing_id
        self._playing_id = None
        log.warning("播放失败：%s", message)
        self.status_label.setText(f"播放失败：{message}")
        self._refresh_playing_rows(previous)

    def _on_volume_changed(self, value: int) -> None:
        self.player.set_volume(value)
        self.settings.setValue("volume", value)

    def _on_repeat_changed(self, mode: str) -> None:
        self._repeat = mode
        self.settings.setValue("repeat", mode)
        self.status_label.setText(f"播放模式：{ {'list': '列表循环', 'one': '单曲循环', 'off': '顺序播放'}[mode] }")

    # ---------------- 紧凑模式 ----------------

    def toggle_mini(self) -> None:
        if self._mini:
            self._exit_mini()
        else:
            self._enter_mini()

    def _enter_mini(self, keep_full_geometry: bool = False) -> None:
        if self._mini:
            return
        self._was_maximized = self.isMaximized()
        # 启动即紧凑时，_full_geometry 在构造里已经记好，这里不能再覆盖成紧凑尺寸
        if not keep_full_geometry:
            self._full_geometry = self.geometry()
        if self._was_maximized:
            self.showNormal()
        self._mini = True

        self.queue_panel.hide()
        for col in (2, 3):
            self.result_table.setColumnHidden(col, True)
        self.result_table.setColumnWidth(4, 56)
        # 音源/音质只留选择器本身，标签藏起来省宽度
        self.level_label.hide()
        self.source_label.hide()
        for box, width in (
            (self.level_box, MINI_LEVEL_W),
            (self.source_box, MINI_SOURCE_W),
        ):
            box.setStyleSheet(_NARROW_COMBO_QSS)
            box.setFixedWidth(width)
        self._action_layout.setSpacing(6)
        self.btn_settings.setFixedSize(32, ACTION_BTN_H)
        self._update_download_button()
        self.btn_search.setText("")
        self.btn_search.setIcon(icons.icon("search", "#ffffff", 16))
        self.btn_search.setFixedWidth(38)
        self.input.setPlaceholderText("搜索歌名或粘贴链接")
        # 状态栏三段并排会挤到截字，紧凑时让账号信息让位
        self.account_label.hide()
        self.btn_help.hide()
        self.btn_folder.hide()
        self.topbar.set_compact(True)

        self._shadow_px = 8
        self.SHADOW = 8
        self.setMinimumSize(360, 420)
        self._apply_window_frame()
        self._rebuild_shadow()
        self.resize(400, max(520, min(self.height(), 620)))
        self.status_label.setText("紧凑模式 · Ctrl+M 还原")

    def _enter_mini_from_startup(self) -> None:
        """设置里选了「启动视图 = 紧凑模式」时走这条路径。"""
        self._enter_mini(keep_full_geometry=True)
        self._center_window(self.width(), self.height())

    def _exit_mini(self) -> None:
        if not self._mini:
            return
        self._mini = False

        self.queue_panel.show()
        for col in (2, 3):
            self.result_table.setColumnHidden(col, False)
        self.result_table.setColumnWidth(2, 160)
        self.result_table.setColumnWidth(3, 200)
        self.result_table.setColumnWidth(4, 88)
        self.level_label.show()
        self.source_label.show()
        # 解除紧凑时的定宽与样式覆盖，回到 QSS 的 min-width 与内容自适应
        for box in (self.level_box, self.source_box):
            box.setStyleSheet("")
            box.setMinimumWidth(0)
            box.setMaximumWidth(16777215)
        self.source_box.setMinimumWidth(84)
        self._action_layout.setSpacing(8)
        self.btn_settings.setFixedSize(36, ACTION_BTN_H)
        self._update_download_button()
        self.btn_search.setText("搜索")
        self.btn_search.setIcon(icons.icon("search", "#ffffff", 18))
        self.btn_search.setMinimumWidth(88)
        self.btn_search.setMaximumWidth(16777215)
        self.input.setPlaceholderText("搜索歌名，或粘贴歌曲 / 歌单链接")
        self.account_label.show()
        self.btn_help.show()
        self.btn_folder.show()
        self.topbar.set_compact(False)

        self._shadow_px = 12
        self.SHADOW = 12
        self.setMinimumSize(840, 600)
        self._apply_window_frame()
        if self._full_geometry is not None:
            self.setGeometry(self._full_geometry)
        if getattr(self, "_was_maximized", False):
            self.showMaximized()
        self._rebuild_shadow()
        self.status_label.setText("已还原窗口")

    def _toggle_play(self) -> None:
        self.player.toggle()

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)
        QShortcut(QKeySequence("F1"), self, activated=self._open_help)
        QShortcut(QKeySequence("Esc"), self, activated=self._escape)
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self.toggle_mini)
        QShortcut(QKeySequence("Ctrl+Left"), self, activated=self._play_prev)
        QShortcut(QKeySequence("Ctrl+Right"), self, activated=self._play_next)

    def keyPressEvent(self, event) -> None:
        # 空格只在焦点不在输入类控件时才切播放，避免打不出空格
        if event.key() == Qt.Key_Space and not isinstance(
            QApplication.focusWidget(), (HistoryLineEdit, QSpinBox, QComboBox)
        ):
            self.player.toggle()
            event.accept()
            return
        super().keyPressEvent(event)

    def _focus_search(self) -> None:
        self.input.setFocus()
        self.input.selectAll()

    def _escape(self) -> None:
        if self.input.text():
            self.input.clear()
        else:
            self.result_table.clearSelection()

    def _build_search_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.input = HistoryLineEdit()
        self.input.setPlaceholderText("搜索歌名，或粘贴歌曲 / 歌单链接")
        self.input.setClearButtonEnabled(True)
        self.input.returnPressed.connect(self.on_search)
        self.input.textChanged.connect(self._on_input_changed)
        self.input.set_history(self._load_history())
        bar.addWidget(self.input, 1)

        self.btn_search = QPushButton("搜索")
        self.btn_search.setObjectName("primary")
        self.btn_search.setIcon(icons.icon("search", "#ffffff", 18))
        self.btn_search.setIconSize(QSize(15, 15))
        self.btn_search.setMinimumWidth(88)
        self.btn_search.clicked.connect(self.on_search)
        bar.addWidget(self.btn_search)
        return bar

    def _build_result_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.result_info = QLabel("输入关键词开始搜索")
        self.result_info.setObjectName("muted")
        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 0, 0, 0)
        info_row.setSpacing(8)
        info_row.addWidget(self.result_info, 1)
        # 接口一次最多给 50 条，热门歌手得靠这个继续往下翻
        self.btn_more = QPushButton("加载更多")
        self.btn_more.setObjectName("moreButton")
        self.btn_more.setFixedHeight(24)
        self.btn_more.setFocusPolicy(Qt.NoFocus)
        self.btn_more.setCursor(Qt.PointingHandCursor)
        self.btn_more.clicked.connect(self._load_more)
        self.btn_more.hide()
        info_row.addWidget(self.btn_more)
        layout.addLayout(info_row)

        # 所有列都能拖动；窗口变宽时余量给曲名（见 FluidTable）
        self.result_table = FluidTable(0, 5, fluid_col=1)
        self.result_table.setHorizontalHeaderLabels(HEADERS)
        self._style_table(self.result_table)
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        self.result_table.setColumnWidth(0, 30)
        self.result_table.setColumnWidth(1, 300)
        self.result_table.setColumnWidth(2, 160)
        self.result_table.setColumnWidth(3, 200)
        # 缓存视图下时长列带档位（如 3:48 · 320k），需要更宽
        self.result_table.setColumnWidth(4, 88)
        self._align_header(self.result_table, {4})
        # 排序挂在表头：点第一列切全选，点其余列排序（升 → 降 → 取消）
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(False)
        header.sectionClicked.connect(self._on_header_clicked)
        self.result_table.itemChanged.connect(self._update_download_button)
        self.result_table.doubleClicked.connect(self._play_row)
        self.result_table.setTextElideMode(Qt.ElideRight)
        self.result_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.result_table.customContextMenuRequested.connect(self._show_context_menu)

        # 空列表时表格就是一块黑，盖一层居中说明告诉用户下一步做什么
        self.empty_hint = QLabel("输入歌名、歌手开始搜索\n也可以直接粘贴歌曲 / 歌单链接")
        self.empty_hint.setObjectName("emptyHint")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setWordWrap(True)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.addWidget(self.result_table, 0, 0)
        grid.addWidget(self.empty_hint, 0, 0, Qt.AlignCenter)
        layout.addLayout(grid, 1)

        self._restore_column_widths()
        self._update_header_sort_style()
        return panel

    def _on_header_clicked(self, col: int) -> None:
        if col == 0:
            self._toggle_all()
            return
        key = {1: "title", 2: "artist", 3: "album", 4: "duration"}.get(col)
        if key is None:
            return

        if self.sort_key == key and not self.sort_desc:
            self.sort_desc = True
        elif self.sort_key == key and self.sort_desc:
            self.sort_key = "default"
            self.sort_desc = False
        else:
            self.sort_key = key
            self.sort_desc = False

        self.settings.setValue("sort_key", self.sort_key)
        self.settings.setValue("sort_desc", self.sort_desc)
        # 没有数据时 _apply_view 会提前返回，箭头状态必须单独刷新
        self._update_header_sort_style()
        self._apply_view()

    def _update_header_sort_style(self) -> None:
        """排序箭头直接写在表头文字里，跟着列宽走，不会被挤压或错位。"""
        col_of = {"title": 1, "artist": 2, "album": 3, "duration": 4}
        active = col_of.get(self.sort_key, -1)
        arrow = ""
        if active >= 0:
            arrow = "\u25bc" if self.sort_desc else "\u25b2"
        for col, base in enumerate(HEADERS):
            item = self.result_table.horizontalHeaderItem(col)
            if item is None:
                item = QTableWidgetItem()
                self.result_table.setHorizontalHeaderItem(col, item)
            item.setText(f"{base} {arrow}" if col == active else base)
            font = item.font()
            font.setBold(col == active)
            font.setPointSizeF(9.0 if col == active else 9.0)
            item.setFont(font)
            item.setForeground(QColor("#e8e8ea" if col == active else MUTED))

    def _fade_in(self, widget, duration: int = 170, start: float = 0.5) -> None:
        """数据刷新时做一次淡入，避免内容硬切换。"""
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(duration)
        anim.setStartValue(start)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(lambda: widget.setGraphicsEffect(None))
        self._anim = anim
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    def _toggle_all(self) -> None:
        if not self.songs:
            return
        checked = sum(
            1
            for row in range(self.result_table.rowCount())
            if self.result_table.item(row, 0)
            and self.result_table.item(row, 0).checkState() == Qt.Checked
        )
        self._set_all(checked < len(self.songs))

    def _build_queue_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.queue_info = QLabel("下载队列")
        self.queue_info.setObjectName("muted")
        layout.addWidget(self.queue_info)

        self.queue_table = FluidTable(0, 4, fluid_col=0)
        self.queue_table.setHorizontalHeaderLabels(["曲名", "状态", "进度", "大小"])
        self._style_table(self.queue_table)
        header = self.queue_table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        self.queue_table.setColumnWidth(0, 300)
        self.queue_table.setColumnWidth(1, 70)
        self.queue_table.setColumnWidth(2, 170)
        self.queue_table.setColumnWidth(3, 80)
        self._align_header(self.queue_table, {3})
        self.queue_table.verticalHeader().setDefaultSectionSize(30)
        self.queue_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue_table.customContextMenuRequested.connect(self._show_queue_menu)
        # 结果表双击能播，队列也该能，两边交互保持一致
        self.queue_table.doubleClicked.connect(self._play_queue_row)
        layout.addWidget(self.queue_table, 1)
        return panel

    def _play_queue_row(self, index) -> None:
        row = index.row()
        if not (0 <= row < len(self._queue_songs)):
            return
        path = self._row_paths.get(row)
        if not path or not path.exists():
            self.status_label.setText("这首歌还没下载完")
            return
        self.playlist = list(self._queue_songs)
        self.play_index = row
        self.player.play_local(self._queue_songs[row], path)

    def _build_action_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._action_layout = bar

        self.level_label = QLabel("音质")
        self.level_label.setObjectName("muted")
        bar.addWidget(self.level_label)

        self.level_box = QComboBox()
        for level in QUALITY_ORDER:
            self.level_box.addItem(f"{QUALITY_LABEL[level]}", level)
        saved = self.settings.value("level", "exhigh", type=str)
        if saved in QUALITY_ORDER:
            self.level_box.setCurrentIndex(QUALITY_ORDER.index(saved))
        self.level_box.setToolTip("音质")
        self.level_box.setFixedHeight(ACTION_BTN_H)
        style_combo(self.level_box)
        self.level_box.currentIndexChanged.connect(self._on_level_changed)
        bar.addWidget(self.level_box)

        bar.addSpacing(10)
        self.source_label = QLabel("音源")
        self.source_label.setObjectName("muted")
        bar.addWidget(self.source_label)

        self.source_box = QComboBox()
        for key in GUI_SOURCES:
            self.source_box.addItem(SOURCE_SHORT[key], key)
        self.source_box.setMinimumWidth(84)
        saved_src = self.settings.value("source", "client", type=str)
        if saved_src not in GUI_SOURCES:
            saved_src = "client"
        self.source_box.setCurrentIndex(GUI_SOURCES.index(saved_src))
        self.source_box.setToolTip(
            "客户端接口：以桌面客户端身份发起请求，能使用账号在客户端享有的权益；"
            "移动端开启免费听歌后，VIP 音乐也能下载。\n"
            "网页接口：以网页身份请求，区别在于音质和无法使用免费听歌，"
            "不是 VIP 无法下载完整版。\n"
            "仅本地缓存：完全不发请求，只列出客户端已经缓存的歌曲，没网络也能用。"
        )
        style_combo(self.source_box)
        self.source_box.setFixedHeight(ACTION_BTN_H)
        self.source_box.currentIndexChanged.connect(self._on_source_changed)
        bar.addWidget(self.source_box)

        bar.addStretch(1)

        self.btn_settings = QPushButton()
        self.btn_settings.setObjectName("iconOnly")
        self.btn_settings.setIcon(icons.icon("settings"))
        self.btn_settings.setIconSize(QSize(18, 18))
        # 高度与「下载选中」严格一致，宽度收到 36 才不至于比图标胖一圈
        self.btn_settings.setFixedSize(36, ACTION_BTN_H)
        self.btn_settings.setToolTip("设置")
        self.btn_settings.clicked.connect(self._open_settings)
        bar.addWidget(self.btn_settings)

        self.btn_download = QPushButton("下载选中")
        self.btn_download.setObjectName("primary")
        self.btn_download.setIcon(icons.icon("download", "#ffffff", 18))
        self.btn_download.setIconSize(QSize(16, 16))
        self.btn_download.setMinimumWidth(126)
        self.btn_download.setFixedHeight(ACTION_BTN_H)
        self.btn_download.setEnabled(False)
        self.btn_download.clicked.connect(self.on_download)
        bar.addWidget(self.btn_download)
        return bar

    def _restore_column_widths(self) -> None:
        raw = self.settings.value("col_widths", "", type=str)
        if not raw:
            return
        try:
            widths = [int(x) for x in raw.split(",")]
        except ValueError:
            return
        if len(widths) != self.result_table.columnCount():
            return
        # 只还原用户可能手动调整过的两列；曲名是弹性列，时长列由界面固定
        for col in (2, 3):
            if col < len(widths) and widths[col] > 0:
                self.result_table.setColumnWidth(col, widths[col])

    def _save_column_widths(self) -> None:
        widths = [
            self.result_table.columnWidth(c)
            for c in range(self.result_table.columnCount())
        ]
        self.settings.setValue("col_widths", ",".join(str(w) for w in widths))

    def _build_status_bar(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("statusBar")
        panel.setFixedHeight(28)
        bar = QHBoxLayout(panel)
        bar.setContentsMargins(8, 0, 8, 0)
        bar.setSpacing(4)
        self.status_label = QLabel("就绪")
        self.summary_label = QLabel("")
        self.account_label = QLabel("")
        self.account_label.setObjectName("muted")
        bar.addWidget(self.status_label, 1)
        bar.addWidget(self.account_label)
        bar.addWidget(self.summary_label)
        # 汇总里的「完成 N」「失败 N」是可点的
        self.summary_label.setOpenExternalLinks(False)
        self.summary_label.linkActivated.connect(self._on_summary_link)
        # 状态栏里的「缓存目录缺失」也做成可点，直接跳设置
        self.status_label.setOpenExternalLinks(False)
        self.status_label.linkActivated.connect(self._on_status_link)

        self.btn_help = QPushButton()
        self.btn_help.setObjectName("helpBtn")
        self.btn_help.setIcon(icons.stateful("circle-help", "#8b8b95", "#ffffff", 14))
        self.btn_help.setIconSize(QSize(14, 14))
        self.btn_help.setFixedSize(22, 20)
        self.btn_help.setToolTip("帮助（F1）")
        self.btn_help.setFocusPolicy(Qt.NoFocus)
        self.btn_help.clicked.connect(self._open_help)
        bar.addWidget(self.btn_help)

        self.btn_folder = QPushButton()
        self.btn_folder.setObjectName("helpBtn")
        self.btn_folder.setIcon(icons.stateful("folder-open", "#8b8b95", "#ffffff", 14))
        self.btn_folder.setIconSize(QSize(14, 14))
        self.btn_folder.setFixedSize(22, 20)
        self.btn_folder.setToolTip("打开下载目录")
        self.btn_folder.setFocusPolicy(Qt.NoFocus)
        self.btn_folder.clicked.connect(self._open_dir)
        bar.addWidget(self.btn_folder)
        return panel

    def _open_help(self) -> None:
        HelpDialog(self).exec_()

    def _on_status_link(self, link: str) -> None:
        if link == "cache":
            self._open_settings()

    def _on_summary_link(self, link: str) -> None:
        if link == "retry":
            self._retry_failed()
        elif link == "open":
            self._open_dir()

    @staticmethod
    def _style_table(table: QTableWidget) -> None:
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setAlternatingRowColors(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setFocusPolicy(Qt.NoFocus)
        # hover 高亮需要鼠标跟踪
        table.setMouseTracking(True)
        table.viewport().setMouseTracking(True)
        # 滚动条常驻，否则内容变化时出现/消失会让列宽来回跳
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        # 横向滚动条绝不能出现：往回拖窄时列宽还没跟上（有防抖），
        # 列宽总和会短暂超过视口，一旦冒出横向滚动条就会挤掉一行高度、
        # 整块重排，看起来就是往回拉时闪的那一下。超出的部分直接裁掉。
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.horizontalHeader().setHighlightSections(False)
        # viewport 在尺寸变化时会用 palette.base 填新露出的区域，默认是白底，
        # 横向拖动时若有一帧没跟上就是一条白条；显式压成面板色
        palette = table.viewport().palette()
        palette.setColor(QPalette.Base, QColor(PANEL))
        table.viewport().setPalette(palette)
        table.viewport().setAutoFillBackground(True)

    @staticmethod
    def _align_header(table: QTableWidget, right_cols: set[int]) -> None:
        """表头默认居中，与左对齐的内容对不齐，这里按列手动指定。"""
        for col in range(table.columnCount()):
            item = table.horizontalHeaderItem(col)
            if item is None:
                item = QTableWidgetItem("")
                table.setHorizontalHeaderItem(col, item)
            if col in right_cols:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            else:
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            item.setForeground(QColor(MUTED))

    # ---------------- 搜索 / 解析 ----------------

    def _current_source(self) -> str:
        return self.source_box.currentData()

    def _pool(self) -> tuple[list[Song], str, bool] | None:
        return self._pools.get(self._current_source())

    def _is_cache_view(self) -> bool:
        pool = self._pool()
        return bool(pool and pool[2])

    def on_search(self) -> None:
        text = self.input.text().strip()
        if self.task is not None:
            return
        if not text:
            self._apply_view()
            return
        if self._looks_like_link(text):
            self._resolve(text)
            return
        pool = self._pool()
        if pool is not None and pool[2]:
            # 缓存视图下不发请求，只在本地列表里筛
            self._apply_view()
            return
        if self._current_source() == "cache" and pool is None:
            # 音源是纯缓存但还没载入列表，先载入再按关键词筛
            self.on_load_cache()
            return
        self._search(text)

    def _sorted(self, songs: list[Song]) -> list[Song]:
        key = SORT_KEYS.get(self.sort_key)
        if not key:
            return list(songs)
        return sorted(songs, key=key, reverse=self.sort_desc)

    def _apply_view(self) -> None:
        """按当前关键词与排序方式，把当前音源的数据渲染到表格。"""
        pool = self._pool()
        if pool is None:
            self.songs = []
            self._fill_result_table()
            self.result_info.setText("输入关键词开始搜索")
            self._update_empty_hint()
            self._update_download_button()
            return

        songs, title, is_cache = pool
        kw = self.input.text().strip().lower()
        # 只有缓存视图才在本地按关键词筛：接口搜索的结果本来就是匹配过的，
        # 再筛一遍会把「搜到的但标题里没这个词」的歌误删
        filtering = is_cache and bool(kw) and not self._looks_like_link(kw)
        items = songs
        if filtering:
            items = [
                song
                for song in songs
                if kw in song.name.lower()
                or kw in song.artist_text.lower()
                or kw in song.album.lower()
            ]
        items = self._sorted(items)

        self.songs = items
        self._fill_result_table()
        if filtering:
            self.result_info.setText(f"{title} · 匹配 {len(items)} 首")
        else:
            self.result_info.setText(f"{title} · 共 {len(items)} 首")
        # 「加载更多」只对接口搜索有意义：缓存视图和链接解析出来的列表是完整的
        self.btn_more.setVisible(self._has_more and not is_cache and not filtering)
        self._update_empty_hint()
        self._update_header_sort_style()
        self._update_download_button()

    def _update_empty_hint(self) -> None:
        if self._scanning_cache:
            self.empty_hint.setText("正在扫描本地缓存…")
            self.empty_hint.show()
            return
        if self.songs:
            self.empty_hint.hide()
            return
        keyword = self.input.text().strip()
        if self._is_cache_view():
            if self._cache_scan_failed:
                self.empty_hint.setText(
                    "没能读到本地缓存\n"
                    "到「设置 → 缓存目录」确认路径是否正确"
                )
            else:
                self.empty_hint.setText(
                    "客户端里还没有可用的缓存\n"
                    "在网易云播放几首，这里会自动出现"
                )
        elif keyword and not self._looks_like_link(keyword):
            self.empty_hint.setText(
                f"没搜到「{keyword}」\n换个关键词，或者换个音源试试"
            )
        else:
            self.empty_hint.setText(
                "输入歌名、歌手开始搜索\n也可以直接粘贴歌曲 / 歌单链接"
            )
        self.empty_hint.show()

    def _load_history(self) -> list[str]:
        value = self.settings.value("search_history", [], type=list)
        return [str(x) for x in value][:10]

    def _record_history(self, keyword: str) -> None:
        if not keyword or self._looks_like_link(keyword):
            return
        history = [keyword] + [h for h in self._load_history() if h != keyword]
        history = history[:10]
        self.settings.setValue("search_history", history)
        self.input.set_history(history)

    def _on_input_changed(self, text: str) -> None:
        # 只有缓存视图才边打字边过滤，接口搜索结果不这么做
        if not self._is_cache_view():
            return
        if self._looks_like_link(text.strip()):
            return
        self._apply_view()

    def _clear_current(self) -> None:
        """清空当前音源的结果，不影响其他音源已载入的内容。"""
        source = self._current_source()
        self._pools.pop(source, None)
        self.input.blockSignals(True)
        self.input.clear()
        self.input.blockSignals(False)
        self._apply_view()
        self.status_label.setText(f"已清空 {SOURCE_LABEL.get(source, source)} 的结果")

    @staticmethod
    def _looks_like_link(text: str) -> bool:
        return text.isdigit() or "music.163.com" in text or "163cn.tv" in text

    def _search(self, keyword: str) -> None:
        self._record_history(keyword)
        self._search_keyword = keyword
        self._search_offset = 0
        self._has_more = False
        self.btn_more.hide()
        self._set_busy(True, f"搜索「{keyword}」…")
        self.task = Task(lambda: ("搜索结果", self.backend.search(keyword, limit=PAGE_SIZE)))
        self.task.ok.connect(self._on_songs_loaded)
        self.task.fail.connect(self._on_task_error)
        self.task.finished.connect(lambda: self._set_busy(False, None))
        self.task.start()

    def _load_more(self) -> None:
        if self.task is not None or not self._search_keyword:
            return
        keyword, offset = self._search_keyword, self._search_offset
        self.btn_more.setText("加载中…")
        self.btn_more.setEnabled(False)
        self.task = Task(
            lambda: ("搜索结果", self.backend.search(keyword, limit=PAGE_SIZE, offset=offset))
        )
        self.task.ok.connect(self._on_more_loaded)
        self.task.fail.connect(self._on_more_failed)
        self.task.finished.connect(lambda: self._set_busy(False, None))
        self.task.start()

    def _on_more_loaded(self, payload: tuple[str, list[Song]]) -> None:
        _, songs = payload
        source = self._current_source()
        pool = self._pools.get(source)
        if pool is None or not songs:
            self._has_more = False
        else:
            existing, title, _ = pool
            seen = {song.id for song in existing}
            added = [song for song in songs if song.id not in seen]
            self._pools[source] = (existing + added, title, False)
            self._search_offset += len(songs)
            self._has_more = len(songs) >= PAGE_SIZE
        self.btn_more.setText("加载更多")
        self.btn_more.setEnabled(True)
        self._apply_view()

    def _on_more_failed(self, message: str) -> None:
        self.btn_more.setText("加载更多")
        self.btn_more.setEnabled(True)
        self._on_task_error(message)

    def _resolve(self, text: str) -> None:
        try:
            kind, ident = parse_target(text)
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return

        # 链接/歌单解析出来的列表是完整的，没有下一页可翻
        self._search_keyword = ""
        self._has_more = False
        self.btn_more.hide()

        if kind == "song":

            def load() -> tuple[str, list[Song]]:
                song = self.backend.song_detail(ident)
                return ("单曲", [song] if song else [])

            self._set_busy(True, "解析歌曲…")
        else:

            def load() -> tuple[str, list[Song]]:
                playlist = self.backend.playlist(ident)
                return (playlist.name, playlist.songs)

            self._set_busy(True, "解析歌单…")

        self.task = Task(load)
        self.task.ok.connect(self._on_songs_loaded)
        self.task.fail.connect(self._on_task_error)
        self.task.finished.connect(lambda: self._set_busy(False, None))
        self.task.start()

    def _on_songs_loaded(self, payload: tuple[str, list[Song]]) -> None:
        title, songs = payload
        self._pools[self._current_source()] = (list(songs), title, False)
        # 只有关键词搜索才有下一页；链接/歌单解析出来的是全量
        if self._search_keyword:
            self._search_offset = len(songs)
            self._has_more = len(songs) >= PAGE_SIZE
        self._apply_view()
        self._fade_in(self.result_table)
        if songs:
            self.status_label.setText(f"已载入 {len(songs)} 首")
        else:
            self.result_info.setText("没有结果")

    def on_load_cache(self) -> None:
        """扫描客户端缓存并列出结果。

        这里自己负责把音源切到「仅缓存」，并且屏蔽信号——否则 setCurrentIndex
        会再触发一次 _on_source_changed → on_load_cache，变成一次点击跑两个
        扫描任务，看起来就是「一直在扫缓存」。
        """
        if self.task is not None:
            if self._scanning_cache:
                # 已经在扫了：把旧结果清掉、保持扫描态就行，别让用户以为没反应
                self._pools.pop("cache", None)
                self.songs = []
                self._fill_result_table()
                self.status_label.setText("正在扫描本地缓存…")
                self._update_empty_hint()
                self._update_download_button()
                return
            self.status_label.setText("上一个任务还没结束，稍等一下再扫描")
            return

        idx = GUI_SOURCES.index("cache")
        if self.source_box.currentIndex() != idx:
            self.source_box.blockSignals(True)
            self.source_box.setCurrentIndex(idx)
            self.source_box.blockSignals(False)
            self.settings.setValue("source", "cache")

        # 先把上一个音源的结果清干净，再进扫描态，避免旧结果和「扫描中」同屏
        self._pools.pop("cache", None)
        self.songs = []
        self._fill_result_table()
        self._scanning_cache = True
        self._cache_scan_failed = False
        self._update_empty_hint()
        self._update_download_button()

        self._set_busy(True, "扫描本地缓存…")
        self.task = Task(self._scan_cache)
        self.task.ok.connect(self._on_cache_loaded)
        self.task.fail.connect(self._on_cache_failed)
        self.task.finished.connect(lambda: self._set_busy(False, None))
        self.task.start()

    def _on_cache_loaded(self, payload: tuple[str, list[Song]]) -> None:
        title, songs = payload
        self._scanning_cache = False
        self._pools["cache"] = (list(songs), title, True)
        self._has_more = False
        self.btn_more.hide()
        self._apply_view()
        self._fade_in(self.result_table)
        if songs:
            self.status_label.setText(
                f"已载入本地缓存 {len(songs)} 首，搜索框只会在缓存内筛选"
            )
        else:
            self.status_label.setText("本地缓存里没有可用的曲目")

    def _on_cache_failed(self, message: str) -> None:
        # 扫描失败后停掉自动刷新，否则缓存目录一有写入就重试，变成刷屏
        self._scanning_cache = False
        self._cache_scan_failed = True
        self._update_empty_hint()
        self._on_task_error(message)

    def _scan_cache(self) -> tuple[str, list[Song]]:
        root = self.cache_dir
        if root is None or not Path(root).is_dir():
            root = cache_mod.default_cache_dir()
            self.cache_dir = root
        if root is None or not Path(root).is_dir():
            raise RuntimeError(
                "没找到客户端缓存目录，请在「设置 → 缓存目录」里指定"
            )

        entries = [
            e for e in cache_mod.scan(root) if e.complete and e.size > 200_000
        ]
        if not entries:
            return ("本地缓存", [])

        ids = [e.song_id for e in entries]
        self._cache_rates = {e.song_id: e.rate_label for e in entries}
        details: dict[int, Song] = {}
        for start in range(0, len(ids), 50):
            try:
                for song in self.backend.song_details(ids[start : start + 50]):
                    details[song.id] = song
            except Exception:  # noqa: BLE001
                continue

        songs = [
            details.get(sid) or Song(sid, f"未识别曲目 {sid}", [], "")
            for sid in ids
        ]
        high = sum(1 for e in entries if e.rate >= 320)
        title = f"本地缓存（{high} 首 320 档 / {len(entries) - high} 首低码率）"
        return (title, songs)

    def _on_task_error(self, message: str) -> None:
        log.warning("任务失败：%s", message)
        if "缓存目录" in message:
            # 光在状态栏说一句没用，给个能点的入口
            self.status_label.setText(
                '没找到客户端缓存目录，'
                '<a href="cache" style="color:#e8554a;text-decoration:none">点此指定</a>'
            )
            return
        self.status_label.setText(f"出错：{message}")

    def _set_busy(self, busy: bool, message: str | None) -> None:
        self.btn_search.setEnabled(not busy)
        self.input.setEnabled(not busy)
        if busy and message:
            self.status_label.setText(message)
        if not busy:
            self.task = None

    # ---------------- 结果表 ----------------

    def _fill_result_table(self) -> None:
        table = self.result_table
        table.blockSignals(True)
        table.setRowCount(len(self.songs))
        for row, song in enumerate(self.songs):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            check.setCheckState(Qt.Checked)
            table.setItem(row, 0, check)

            name = QTableWidgetItem(song.name)
            name.setToolTip(f"{song.name}\n{song.artist_text} · {song.album}\nid {song.id}")
            table.setItem(row, 1, name)

            artist = QTableWidgetItem(song.artist_text)
            artist.setForeground(QColor("#8fb8d8"))
            artist.setToolTip(song.artist_text)
            table.setItem(row, 2, artist)

            album = QTableWidgetItem(song.album)
            album.setForeground(QColor(MUTED))
            album.setToolTip(song.album)
            table.setItem(row, 3, album)

            rate = self._cache_rates.get(song.id)
            text = f"{song.duration_text} · {rate}" if rate else song.duration_text
            duration = QTableWidgetItem(text)
            duration.setForeground(QColor(MUTED))
            duration.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, 4, duration)

            if song.id == self._playing_id:
                for col in range(5):
                    cell = table.item(row, col)
                    if cell is not None:
                        cell.setBackground(QColor("#2c2c36"))
                name.setForeground(QColor("#ffffff"))
                bold = name.font()
                bold.setBold(True)
                name.setFont(bold)
        table.blockSignals(False)

    def _set_all(self, checked: bool) -> None:
        self.result_table.blockSignals(True)
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.result_table.rowCount()):
            item = self.result_table.item(row, 0)
            if item:
                item.setCheckState(state)
        self.result_table.blockSignals(False)
        self._update_download_button()

    def _selected_songs(self) -> list[Song]:
        picked: list[Song] = []
        for row, song in enumerate(self.songs):
            item = self.result_table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                picked.append(song)
        return picked

    def _update_download_button(self, *_args) -> None:
        if self.batch is not None:
            return
        count = len(self._selected_songs())
        # 紧凑模式一行要塞下两个选择器，文案缩到「下载 (n)」
        verb = "下载" if self._mini else "下载选中"
        self.btn_download.setText(f"{verb} ({count})" if count else verb)
        self.btn_download.setMinimumWidth(84 if self._mini else 126)
        self.btn_download.setIcon(icons.icon("download", "#ffffff", 18))
        self.btn_download.setIconSize(QSize(16, 16))
        self.btn_download.setEnabled(count > 0)

    def _download_single(self, row: int) -> None:
        """只勾选并下载指定行。"""
        self.result_table.blockSignals(True)
        for r in range(self.result_table.rowCount()):
            item = self.result_table.item(r, 0)
            if item:
                item.setCheckState(Qt.Checked if r == row else Qt.Unchecked)
        self.result_table.blockSignals(False)
        self._update_download_button()
        self.on_download()

    def _build_context_menu(self, song: Song) -> QMenu:
        menu = QMenu(self)
        picked = len(self._selected_songs())
        idle = self.batch is None

        for text, key, enabled in (
            ("播放", "play", True),
            ("仅播放此曲", "play_one", True),
        ):
            action = menu.addAction(text)
            action.setData(key)
            action.setEnabled(enabled)

        if self._playing_id is not None:
            menu.addAction("停止播放").setData("stop")

        menu.addSeparator()
        for text, key, enabled in (
            (f"下载选中（{picked} 首）", "download", picked > 0 and idle),
            ("仅下载此曲", "one", idle),
        ):
            action = menu.addAction(text)
            action.setData(key)
            action.setEnabled(enabled)

        menu.addSeparator()
        for text, key in (
            ("复制歌曲链接", "copy_link"),
            ("复制「歌名 - 歌手」", "copy_text"),
            ("复制歌曲 ID", "copy_id"),
        ):
            menu.addAction(text).setData(key)

        menu.addSeparator()
        menu.addAction("全选").setData("all")
        menu.addAction("取消全选").setData("none")
        menu.addAction("清空当前结果").setData("clear")
        menu.addSeparator()
        menu.addAction("在浏览器打开").setData("open")
        return menu

    def _show_context_menu(self, pos) -> None:
        index = self.result_table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        if not (0 <= row < len(self.songs)):
            return
        song = self.songs[row]

        menu = self._build_context_menu(song)
        chosen = menu.exec_(self.result_table.viewport().mapToGlobal(pos))
        if chosen is None:
            return

        key = chosen.data()
        if key == "play":
            self._play_from_list(self.songs, row)
        elif key == "play_one":
            self._play_from_list([song], 0)
        elif key == "stop":
            self._stop_playback()
        elif key == "download":
            self.on_download()
        elif key == "one":
            self._download_single(row)
        elif key == "copy_link":
            QApplication.clipboard().setText(f"https://music.163.com/song?id={song.id}")
            self.status_label.setText("已复制歌曲链接")
        elif key == "copy_text":
            QApplication.clipboard().setText(f"{song.name} - {song.artist_text}")
            self.status_label.setText("已复制歌名与歌手")
        elif key == "copy_id":
            QApplication.clipboard().setText(str(song.id))
            self.status_label.setText(f"已复制 ID {song.id}")
        elif key == "all":
            self._set_all(True)
        elif key == "none":
            self._set_all(False)
        elif key == "clear":
            self._clear_current()
        elif key == "open":
            QDesktopServices.openUrl(
                QUrl(f"https://music.163.com/song?id={song.id}")
            )

    # ---------------- 下载 ----------------

    def on_download(self) -> None:
        if self.batch is not None:
            # 下载中：主按钮原地变「取消」
            self.batch.cancel()
            self.btn_download.setEnabled(False)
            self.status_label.setText("正在取消…")
            return
        songs = self._selected_songs()
        if not songs:
            return
        self._start_batch(songs)

    def _start_batch(self, songs: list[Song]) -> None:
        source = self.source_box.currentData()
        log.info(
            "开始下载 %d 首：音源=%s 音质=%s 转码=%s 目录=%s",
            len(songs), source, self.level_box.currentData(), self.to_mp3, self.out_dir,
        )
        # 音源决定身份：web 用网页 UA，其余用客户端 UA
        self.backend = LocalBackend(
            cookie=auth.load_cookie(), client_mode=(source != "web")
        )

        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.out_dir / ".ncm-index.db")
        self.downloader = Downloader(
            backend=self.backend,
            store=self.store,
            out_dir=self.out_dir,
            template=build_template(self.name_pattern, self.folder_pattern),
            level=self.level_box.currentData(),
            workers=self.workers,
            source=source,
            to_mp3=self.to_mp3,
            cache_dir=self.cache_dir,
        )

        self._prepare_queue(songs)
        self.batch = BatchTask(self.downloader, songs)
        self.batch.on_start_song.connect(self._on_song_start)
        self.batch.on_progress_song.connect(self._on_song_progress)
        self.batch.on_result.connect(self._on_song_result)
        self.batch.finished_all.connect(self._on_batch_done)
        self.batch.fail.connect(self._on_task_error)
        self.batch.start()

        self.btn_download.setText("取消")
        self.btn_download.setIcon(icons.icon("x", "#ffffff", 18))
        self.btn_download.setIconSize(QSize(13, 13))
        self.btn_download.setEnabled(True)
        self.status_label.setText(f"开始下载 {len(songs)} 首…")

    def _show_queue_menu(self, pos) -> None:
        """队列右键：定位文件、重试、清空。"""
        index = self.queue_table.indexAt(pos)
        row = index.row() if index.isValid() else -1
        path = self._row_paths.get(row)

        menu = QMenu(self)
        act_play = menu.addAction("播放")
        act_play.setEnabled(bool(path and path.exists() and 0 <= row < len(self._queue_songs)))
        menu.addSeparator()
        act_reveal = menu.addAction("打开所在文件夹")
        act_reveal.setEnabled(bool(path and path.exists()))
        act_copy = menu.addAction("复制文件路径")
        act_copy.setEnabled(bool(path))
        menu.addSeparator()
        act_cancel_one = menu.addAction("取消这首")
        # 只对还没结束的行可用：已完成/已跳过的再点一次没有任何反馈，
        # 用户会以为按钮坏了
        status_item = (
            self.queue_table.item(row, 1) if 0 <= row < self.queue_table.rowCount() else None
        )
        pending = status_item is not None and status_item.text() in ("等待", "解析")
        act_cancel_one.setEnabled(self.batch is not None and pending)
        act_retry = menu.addAction(f"重试失败（{len(self._failed)}）")
        act_retry.setEnabled(bool(self._failed) and self.batch is None)
        act_clear = menu.addAction("清空队列")
        act_clear.setEnabled(self.batch is None)

        chosen = menu.exec_(self.queue_table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is act_play and path and 0 <= row < len(self._queue_songs):
            self.playlist = list(self._queue_songs)
            self.play_index = row
            self.player.play_local(self._queue_songs[row], path)
        elif chosen is act_reveal and path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        elif chosen is act_copy and path:
            QApplication.clipboard().setText(str(path))
            self.status_label.setText("已复制文件路径")
        elif chosen is act_cancel_one and 0 <= row < len(self._queue_songs):
            song = self._queue_songs[row]
            self.downloader.cancel_song(song.id)
            self.status_label.setText(f"已请求取消「{song.name}」，其余继续")
        elif chosen is act_retry:
            self._retry_failed()
        elif chosen is act_clear:
            self._clear_queue()

    def _clear_queue(self) -> None:
        if self.batch is not None:
            self.status_label.setText("正在下载，队列暂时不能清空")
            return
        self.queue_table.setRowCount(0)
        self.queue_rows.clear()
        self.bars.clear()
        self._failed = []
        self.queue_info.setText("下载队列")
        self.summary_label.setText("")
        self.status_label.setText("已清空下载队列")

    def _retry_failed(self) -> None:
        if not self._failed or self.batch is not None:
            return
        songs = list(self._failed)
        self._failed = []
        self.status_label.setText(f"重试 {len(songs)} 首…")
        self._start_batch(songs)

    def _prepare_queue(self, songs: list[Song]) -> None:
        table = self.queue_table
        table.setRowCount(0)
        self.queue_rows.clear()
        self.bars.clear()
        self._row_paths.clear()
        self._queue_songs = list(songs)
        self._batch_done = 0
        self._batch_total = len(songs)
        self._failed = []
        table.setRowCount(len(songs))

        for row, song in enumerate(songs):
            self.queue_rows[song.id] = row

            name = QTableWidgetItem(song.name)
            name.setToolTip(f"{song.name} - {song.artist_text}")
            table.setItem(row, 0, name)

            status = QTableWidgetItem("等待")
            status.setForeground(QColor(MUTED))
            table.setItem(row, 1, status)

            bar = QProgressBar()
            bar.setRange(0, 0)
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            holder = QWidget()
            # 不加这行会继承 QWidget 的实色底，在行里糊出一块深色矩形
            holder.setObjectName("barHolder")
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(10, 0, 10, 0)
            holder_layout.addWidget(bar)
            table.setCellWidget(row, 2, holder)
            self.bars[song.id] = bar

            size = QTableWidgetItem("")
            size.setForeground(QColor(MUTED))
            size.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, 3, size)

        self.queue_info.setText(f"下载队列 · {len(songs)} 首")
        self.summary_label.setText("")

    def _row_of(self, song_id: int) -> int | None:
        return self.queue_rows.get(song_id)

    def _on_song_start(self, song: Song) -> None:
        row = self._row_of(song.id)
        if row is None:
            return
        item = self.queue_table.item(row, 1)
        if item:
            item.setText("解析")
            item.setForeground(QColor(MUTED))

    def _on_song_progress(self, song_id: int, done: int, total: int) -> None:
        bar = self.bars.get(song_id)
        if bar is None:
            return
        if total:
            bar.setRange(0, total)
            bar.setValue(done)
        else:
            bar.setRange(0, 0)

    def _on_song_result(self, res: TaskResult) -> None:
        self._batch_done += 1
        if self._batch_total:
            self.status_label.setText(
                f"下载中 {self._batch_done}/{self._batch_total} · {res.song.name}"
            )
        row = self._row_of(res.song.id)
        if row is None:
            return
        status = self.queue_table.item(row, 1)
        if status:
            status.setText(STATUS_TEXT[res.status])
            status.setForeground(QColor(STATUS_COLOR.get(res.status, MUTED)))
            if res.message:
                status.setToolTip(res.message)

        bar = self.bars.get(res.song.id)
        if bar is not None:
            bar.setRange(0, 1000)
            if res.status == OK:
                bar.setValue(1000)
                bar.setStyleSheet(_bar_qss("#4caf7d"))
            elif res.status == TRIAL:
                # 只拿到片段，进度条故意不走满
                bar.setValue(300)
                bar.setStyleSheet(_bar_qss(STATUS_COLOR[TRIAL]))
            elif res.status in (NO_COPYRIGHT, FAILED):
                bar.setValue(1000)
                bar.setStyleSheet(_bar_qss(STATUS_COLOR[res.status]))
            else:
                bar.setValue(1000)
                bar.setStyleSheet(_bar_qss(MUTED))

        size = self.queue_table.item(row, 3)
        if res.path and res.path.exists():
            self._row_paths[row] = res.path
            if size:
                size.setText(_fmt_size(res.path.stat().st_size))

    def _on_batch_done(self, results: list[TaskResult]) -> None:
        cancelled = sum(1 for r in results if r.status == CANCELLED)
        self.batch = None
        if self.store:
            self.store.close()
            self.store = None
        self.btn_download.setEnabled(True)
        self._update_download_button()

        # 记下失败项，可通过汇总里的「失败 N」重试
        self._failed = [r.song for r in results if r.status == FAILED]

        stats = Counter(r.status for r in results)
        total_bytes = sum(
            r.path.stat().st_size
            for r in results
            if r.ok and r.path and r.path.exists()
        )
        done_n = stats.get(OK, 0)
        parts = [
            f'<a href="open" style="color:#4caf7d;text-decoration:none">完成 {done_n}</a>',
            f"跳过 {stats.get(SKIPPED, 0)}",
        ]
        if stats.get(NO_COPYRIGHT):
            parts.append(f"无版权 {stats[NO_COPYRIGHT]}")
        if stats.get(TRIAL):
            parts.append(f"试听 {stats[TRIAL]}")
        if stats.get(FAILED):
            # 紧凑模式一行放不下提示语，只留可点的「失败 N」
            tail = "" if self._mini else "，点此重试"
            parts.append(
                f'<a href="retry" style="color:#e8554a;text-decoration:none">'
                f"失败 {stats[FAILED]}{tail}</a>"
            )
        if cancelled:
            parts.append(f"取消 {cancelled}")

        size_text = (
            f" · {total_bytes / 1048576:.1f} MB"
            if total_bytes and not self._mini
            else ""
        )
        self.summary_label.setText(" · ".join(parts) + size_text)
        self._fade_in(self.summary_label, duration=220, start=0.3)
        self.status_label.setText(f"本批 {len(results)} 首处理完毕")
        self.queue_info.setText(f"下载队列 · 已完成 {len(results)} 首")

        log.info("批量完成：%s，共 %.1f MB", dict(stats), total_bytes / 1048576)
        # 窗口不在前台时闪一下任务栏，批量下歌切走了也知道结束
        if not self.isActiveWindow():
            QApplication.alert(self, 3000)
        failed = [r for r in results if r.status == FAILED]
        for res in failed:
            log.warning(
                "下载失败 %s - %s：%s",
                res.song.name, res.song.artist_text, res.message or "无详细信息",
            )

    # ---------------- 其他 ----------------

    def _choose_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "选择保存目录", str(self.out_dir)
        )
        if chosen:
            self.out_dir = Path(chosen)
            self.settings.setValue("out_dir", str(self.out_dir))
    
    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self.out_dir, self.cache_dir, self)
        accepted = dialog.exec_() == QDialog.Accepted
        # 登录状态和「保存」是两件事：扫码成功就已经写进配置了，
        # 用户点不点保存都该在状态栏立刻反映出来
        self._refresh_account_label()
        if not accepted:
            if dialog.changed:
                self.status_label.setText("登录状态已更新")
                self._cache_entries = None  # 权限变了，缓存的可用性要重判
            return
        if accepted:
            self.workers = dialog.workers.value()
            self.to_mp3 = dialog.to_mp3.isChecked()
            self.name_pattern = dialog.name_box.currentData()
            self.folder_pattern = dialog.folder_box.currentData()
            self.out_dir = dialog.out_dir
            self.settings.setValue("workers", self.workers)
            self.settings.setValue("to_mp3", self.to_mp3)
            self.settings.setValue("name_pattern", self.name_pattern)
            self.settings.setValue("folder_pattern", self.folder_pattern)
            self.settings.setValue("out_dir", str(self.out_dir))
            self.settings.setValue("startup_view", dialog.startup_view.currentData())
            if dialog.cache_dir != self.cache_dir:
                self.cache_dir = dialog.cache_dir
                # 换目录后旧的扫描结果作废，「容量/缓存」相关判断要重来
                self._cache_entries = None
                self._watch_cache_dir(self.cache_dir)
                if self.cache_dir is None:
                    self.status_label.setText("已清空缓存目录")
                elif cache_mod.looks_like_cache_dir(self.cache_dir):
                    self.status_label.setText(f"缓存目录已切到 {self.cache_dir}")
                else:
                    self.status_label.setText(
                        "缓存目录里没找到 .uc 文件，请确认是否选对"
                    )
            self.settings.setValue(
                "cache_dir", str(self.cache_dir) if self.cache_dir else ""
            )
            # 设置里可能刚清空过搜索历史，回去要立刻反映出来
            self.input.set_history(self._load_history())
            if dialog.changed:
                self.backend = LocalBackend(cookie=auth.load_cookie())
                self._refresh_account_label()
                account = auth.saved_account()
                self.status_label.setText(
                    f"已登录 {account.nickname}（{account.vip_text}）"
                    if auth.load_cookie()
                    else "已退出登录，当前为匿名身份"
                )

    def _refresh_account_label(self) -> None:
        if auth.load_cookie():
            account = auth.saved_account()
            name = account.nickname or "已登录"
            self.account_label.setText(f"{name} · {account.vip_text}")
            self.account_label.setToolTip(
                f"{name} · {account.vip_text}\n在「设置」里可扫码登录或退出"
            )
        else:
            self.account_label.setText("未登录")
            self.account_label.setToolTip("在「设置」里扫码登录")

    def _open_dir(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.out_dir)))

    def _on_level_changed(self) -> None:
        self.settings.setValue("level", self.level_box.currentData())

    def _on_source_changed(self) -> None:
        key = self.source_box.currentData()
        self.settings.setValue("source", key)
        self.status_label.setText(f"音源：{SOURCE_LABEL.get(key, key)}")
        # 每次切到「仅本地缓存」都重扫：客户端那边可能刚缓存了新歌，
        # 或者用户清过缓存，吃旧结果会显示已经不存在的东西
        if key == "cache":
            self.on_load_cache()
            return
        self._apply_view()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.batch is not None:
            self.batch.wait(3000)
        self.settings.setValue("out_dir", str(self.out_dir))
        # 位置和尺寸不再持久化：每次都从固定位置居中打开
        self._save_column_widths()
        player = getattr(self, "player", None)
        if player is not None:
            player.shutdown()
        super().closeEvent(event)
