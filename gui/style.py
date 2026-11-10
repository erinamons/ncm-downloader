"""深色主题样式表。"""
from pathlib import Path

BORDER = "#34343a"
PANEL = "#242428"
BG = "#1c1c1f"
TEXT = "#e8e8ea"
MUTED = "#96969e"
ACCENT = "#e8554a"
ACCENT_HOVER = "#f0655a"

# Qt 的 QSS 不支持用 border 画三角，只能用图片；用绝对路径避免受工作目录影响
_ASSET_DIR = Path(__file__).parent / "assets"
_ARROW = (_ASSET_DIR / "arrow_down.png").as_posix()
_CHECK = (_ASSET_DIR / "check.png").as_posix()


def scrollbar_qss(top_margin: int = 5, hide_handle: bool = False) -> str:
    """竖向滚动条样式。``top_margin`` 是滑槽距控件顶端的像素，用来避开表头。

    单独抽成函数是因为：给某个滚动条设了 widget 级样式表之后，应用级 QSS 里
    针对它的 ``::handle`` 规则就不再生效（滑块会变成没圆角的实心方块）。
    所以这块样式必须整段复用，不能只在外面覆盖一个 margin。

    ``hide_handle`` 给空表用：滚动条仍占着位置（这样视口不会盖住表格圆角），
    但滑块透明，看起来就是「没有滚动条」。
    """
    handle_bg = "transparent" if hide_handle else "#55555f"
    return f"""
QScrollBar:vertical {{
    /* 悬浮式：只留滑块，槽透明。槽带底色会糊住表格右上/右下角的 1px 边框 */
    background: transparent;
    width: 11px;
    margin: {top_margin}px 2px 5px 2px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {handle_bg};
    border-radius: 4px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {"transparent" if hide_handle else "#6e6e7c"};
}}
QScrollBar::handle:vertical:pressed {{
    background: {"transparent" if hide_handle else "#85859a"};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    height: 0;
    background: none;
}}
"""


QSS = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
    font-size: 12px;
}}
QMainWindow {{
    background: transparent;
}}
/* QLabel 默认继承 QWidget 的实色底，会盖住父级 1px 分隔线（顶栏/状态栏各一处断口），
   统一透明；需要底色的（如封面占位）各自覆盖。 */
QLabel {{
    background: transparent;
}}
QDialog {{
    background: transparent;
}}
QWidget#shell {{
    background: transparent;
}}

QLineEdit {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{
    border: 1px solid {ACCENT};
}}

QPushButton {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 14px;
    color: {TEXT};
}}
QPushButton:hover {{
    border: 1px solid #4a4a52;
    background: #2c2c31;
}}
QPushButton:pressed {{
    background: #202024;
}}
QPushButton:disabled {{
    color: #5a5a62;
    border-color: #2b2b30;
}}
QPushButton#primary {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: #ffffff;
    font-weight: 500;
}}
QPushButton#primary:hover {{
    background: {ACCENT_HOVER};
}}
QPushButton#primary:disabled {{
    background: #4a2f2d;
    border-color: #4a2f2d;
    color: #8a6f6d;
}}
QPushButton#iconOnly {{
    padding: 0;
}}

QComboBox {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
    min-width: 78px;
}}

QSpinBox {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
    min-height: 16px;
}}
QSpinBox:focus {{
    border: 1px solid {ACCENT};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 0;
    height: 0;
    border: none;
}}

QCheckBox {{
    spacing: 7px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid #4a4a52;
    border-radius: 3px;
    background: {PANEL};
}}
QCheckBox::indicator:hover {{
    border: 1px solid #6a6a74;
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    image: url({_CHECK});
}}
QComboBox:hover {{
    border: 1px solid #4a4a52;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 22px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: url({_ARROW});
    width: 10px;
    height: 6px;
}}
QComboBox QAbstractItemView {{
    background: {PANEL};
    border: 1px solid #3a3a42;
    border-radius: 9px;
    padding: 5px;
    outline: none;
    color: {TEXT};
    selection-background-color: #34343d;
}}
QComboBox QAbstractItemView::item {{
    padding-left: 10px;
    border-radius: 6px;
}}
QComboBox QAbstractItemView::item:hover {{
    background: #2e2e36;
}}
QComboBox QAbstractItemView::item:selected {{
    background: #34343d;
    color: #ffffff;
}}

QTableWidget {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    gridline-color: transparent;
    selection-background-color: #3e3e4a;
    selection-color: #ffffff;
}}
QTableWidget::item {{
    padding: 3px 6px;
    border: none;
}}
QTableWidget::item:hover {{
    background: #2e2e36;
}}
QTableWidget::item:selected {{
    background: #3e3e4a;
    color: #ffffff;
}}
QTableWidget::indicator,
QTableView::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid #4a4a52;
    border-radius: 3px;
    background: {PANEL};
}}
QTableWidget::indicator:checked,
QTableView::indicator:checked {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    image: url({_CHECK});
}}
QHeaderView::section {{
    background: {BG};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px;
}}
QHeaderView::section:hover {{
    background: #232327;
}}
QTableCornerButton::section {{
    background: {BG};
    border: none;
}}
/* 盖住表格右上角、滚动条上段的那块补丁，配色跟表头保持一致 */
QWidget#cornerPatch {{
    background: {BG};
    border: none;
    border-bottom: 1px solid {BORDER};
    border-top-right-radius: 7px;
}}

QProgressBar {{
    background: #2e2e34;
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 3px;
}}
/* 进度条的容器默认会继承 QWidget 的实色底，在表格行里形成一块突兀的深色矩形 */
QWidget#barHolder {{
    background: transparent;
}}

{scrollbar_qss()}
QScrollBar:horizontal {{
    /* 横向滚动条在表格里是禁用的；这里给个实底，万一将来哪个控件用到，
       不会再出现「透明条透出桌面」的白条 */
    background: {PANEL};
    height: 9px;
}}
QScrollBar::handle:horizontal {{
    background: #3d3d44;
    border-radius: 4px;
    min-width: 26px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

QLabel#titleText {{
    color: #b6b6c0;
    font-size: 12px;
}}
QWidget#titleBar {{
    background: transparent;
}}
QPushButton#winBtn {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 0;
}}
QPushButton#winBtn:hover {{
    background: #2f2f36;
    border: none;
}}
QPushButton#winBtn:pressed {{
    background: #26262c;
}}
QPushButton#closeBtn {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 0;
}}
QPushButton#closeBtn:hover {{
    background: #c8433a;
    border: none;
}}
QPushButton#closeBtn:pressed {{
    background: #a93830;
}}
QLabel#muted {{
    color: {MUTED};
}}
QLabel#accent {{
    color: {ACCENT};
}}
QLabel#ok {{
    color: #4caf7d;
}}
QLabel#warn {{
    color: #d9a13b;
}}
QLabel#dirLink {{
    color: #7fa9cc;
    padding: 4px 6px;
}}
QLabel#dirLink:hover {{
    color: #a8cce8;
}}

QWidget#dialogBody {{
    background: transparent;
}}

/* 帮助窗口：滚动区不能带白底或描边，否则和圆角窗口脱层 */
QScrollArea#helpScroll {{
    background: transparent;
    border: none;
}}
QScrollArea#helpScroll > QWidget > QWidget {{
    background: transparent;
}}

QWidget#statusBar {{
    background: transparent;
    border-top: 1px solid {BORDER};
}}QWidget#statusBar QLabel {{
    padding: 0 7px;
    color: {MUTED};
}}
QPushButton#helpBtn {{
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 0;
}}
QPushButton#helpBtn:hover {{
    background: #2f2f36;
    border: none;
}}
QPushButton#autoRefreshBtn, QPushButton#sortBtn {{
    background: transparent;
    border: none;
    border-radius: 10px;
    padding: 0;
}}
QPushButton#autoRefreshBtn:hover, QPushButton#sortBtn:hover {{
    background: #2f2f36;
}}
QPushButton#autoRefreshBtn:checked, QPushButton#sortBtn:checked {{
    background: {ACCENT};
}}
QPushButton#autoRefreshBtn:checked:hover, QPushButton#sortBtn:checked:hover {{
    background: {ACCENT_HOVER};
}}
QPushButton#moreButton {{
    background: transparent;
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 0 10px;
    color: #b6b6c0;
    font-size: 11px;
}}
QPushButton#moreButton:hover {{
    background: #2c2c31;
    border: 1px solid #4a4a52;
}}
QPushButton#moreButton:disabled {{
    color: #5a5a62;
    border-color: #2b2b30;
}}
QLabel#emptyHint {{
    background: transparent;
    color: #67676f;
    font-size: 12px;
    padding: 0 32px;
}}

QSplitter::handle {{
    background: transparent;
    height: 6px;
}}

QToolTip {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 8px;
}}

QWidget#topBar {{
    background: transparent;
}}
QWidget#topBarRow {{
    background: {BG};
    border: none;
    border-bottom: 1px solid #2c2c33;
    /* 贴合 windowRoot 的 12px 圆角（外框占 1px） */
    border-top-left-radius: 11px;
    border-top-right-radius: 11px;
}}
QPushButton#barButton {{
    background: transparent;
    border: none;
    border-radius: 5px;
    padding: 0;
}}
QPushButton#barButton:hover {{
    background: #2f2f36;
    border: none;
}}
QPushButton#playButton {{
    background: {ACCENT};
    border: none;
    border-radius: 13px;
    padding: 0;
}}
QPushButton#playButton:hover {{
    background: {ACCENT_HOVER};
    border: none;
}}
QLabel#cover {{
    background: #34343a;
    border-radius: 4px;
}}
QLabel#timeText {{
    color: #8b8b95;
}}
QSlider#volumeSlider {{
    background: transparent;
}}
QSlider#volumeSlider::groove:horizontal {{
    height: 3px;
    background: #3a3a42;
    border-radius: 1.5px;
}}
QSlider#volumeSlider::sub-page:horizontal {{
    background: #9a9aa4;
    border-radius: 1.5px;
}}
QSlider#volumeSlider::handle:horizontal {{
    width: 8px;
    margin: -3px 0;
    border-radius: 4px;
    background: #ffffff;
}}

QFrame#historyPopup {{
    background: #242428;
    border: 1px solid #3a3a42;
    border-radius: 9px;
}}
QListWidget#historyList {{
    background: transparent;
    border: none;
    outline: none;
}}
QListWidget#historyList::item {{
    padding-left: 10px;
    border-radius: 6px;
    color: {TEXT};
}}
QListWidget#historyList::item:hover {{
    background: #2e2e36;
}}
QListWidget#historyList::item:selected {{
    background: #34343d;
    color: #ffffff;
}}

/* 紧凑模式的下载队列：单行停靠条 + 点开的明细浮层 */
QWidget#miniQueueDock {{
    background: #202024;
    border: none;
    border-top: 1px solid #2c2c33;
}}
QFrame#queueSheet {{
    background: #232327;
    border: 1px solid #3a3a42;
    border-radius: 8px;
}}
/* 浮层里的行/文字必须透明：全局 QWidget 规则会给每个子控件刷上更深的底色，
   子控件不会被父级圆角裁切，左上角就会露出一块直角深色方块 */
QFrame#queueSheet QWidget,
QFrame#queueSheet QLabel {{
    background: transparent;
}}

QMenu {{
    background: #26262a;
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px;
}}
QMenu::item {{
    padding: 6px 26px 6px 14px;
    border-radius: 4px;
    color: {TEXT};
}}
QMenu::item:selected {{
    background: {ACCENT};
    color: #ffffff;
}}
QMenu::item:disabled {{
    color: #5a5a62;
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 4px 10px;
}}
"""
