"""圆角对话框基类：与主窗口保持同一套外观。

主窗口是无边框自绘的，对话框如果还带系统标题栏会明显不一样。这里把
无边框、圆角、阴影、标题栏、拖动全部复用，子类只需要往 self.body 里
塞内容。
"""
from __future__ import annotations

from PyQt5.QtCore import QPoint, QSize, Qt
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui import icons
from gui.shadow import clip_outside_window, render_shadow
from gui.style import BG, QSS

SHADOW = 12
RADIUS = 12
TITLE_H = 38


class DialogTitleBar(QWidget):
    """只有标题和关闭按钮，可拖动、双击无动作。"""

    def __init__(self, window: QWidget, title: str, parent=None):
        super().__init__(parent)
        self._win = window
        self._drag_from: QPoint | None = None
        self.setObjectName("titleBar")
        self.setFixedHeight(TITLE_H)

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 0, 8, 0)
        row.setSpacing(8)

        label = QLabel(title)
        label.setObjectName("titleText")
        row.addWidget(label)
        row.addStretch(1)

        close = QPushButton()
        close.setObjectName("closeBtn")
        close.setIcon(icons.stateful("x", "#c0c0c8", "#ffffff", 14))
        close.setIconSize(QSize(14, 14))
        close.setFixedSize(38, 26)
        close.setToolTip("关闭")
        close.setFocusPolicy(Qt.NoFocus)
        close.clicked.connect(self._win.reject)
        row.addWidget(close)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            handle = self._win.windowHandle()
            if handle is not None and hasattr(handle, "startSystemMove"):
                if handle.startSystemMove():
                    event.accept()
                    return
            self._drag_from = event.globalPos() - self._win.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None and event.buttons() & Qt.LeftButton:
            self._win.move(event.globalPos() - self._drag_from)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_from = None
        super().mouseReleaseEvent(event)


class RoundDialog(QDialog):
    SHADOW = SHADOW
    RADIUS = RADIUS

    def __init__(self, parent=None, title: str = "", width: int = 0):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(QSS)

        shell = QVBoxLayout(self)
        shell.setContentsMargins(SHADOW, SHADOW, SHADOW, SHADOW)
        shell.setSpacing(0)

        self._root = QWidget()
        self._root.setObjectName("windowRoot")
        self._root.setStyleSheet(
            f"QWidget#windowRoot {{ background: {BG};"
            f" border-radius: {RADIUS}px; border: 1px solid #2c2c33; }}"
        )
        shell.addWidget(self._root)

        stack = QVBoxLayout(self._root)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)
        stack.addWidget(DialogTitleBar(self, title, self._root))

        self.body = QWidget()
        # 透明底：让 windowRoot 的圆角与描边从底部透出来，否则实色矩形会把下两角盖成直角
        self.body.setObjectName("dialogBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(18, 6, 18, 16)
        self.body_layout.setSpacing(12)
        stack.addWidget(self.body, 1)

        if width:
            self.setFixedWidth(width + SHADOW * 2)

        self._centered = False
        self._shadow = None
        self._shadow_size = (0, 0)

    def make_scrollable(self, max_height: int = 640) -> None:
        """内容可能超出屏幕的对话框（比如设置）套一层滚动。

        小屏幕只有 768 高，设置项一多按钮就被顶出可视区，用户既看不到也点不着。
        """
        if getattr(self, "_scrolled", False):
            return
        self._scrolled = True
        wrap = QScrollArea(self._root)
        wrap.setWidgetResizable(True)
        wrap.setFrameShape(QFrame.NoFrame)
        wrap.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        wrap.viewport().setAutoFillBackground(False)
        wrap.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        # 按钮行必须留在滚动区外面：跟着内容一起滚的话，内容一长
        # 「保存 / 取消」就被顶到看不见的地方，用户只能瞎点
        footer = QWidget(self._root)
        footer.setObjectName("dialogFooter")
        # 背景必须透明：QWidget 一旦设了 stylesheet 就会用调色板底色铺满，
        # 铺成实色方块后会把 windowRoot 的下半部分圆角盖成直角
        footer.setAttribute(Qt.WA_StyledBackground, True)
        footer.setStyleSheet(
            "QWidget#dialogFooter { background: transparent;"
            " border-top: 1px solid #2c2c33; }"
        )
        footer_layout = QVBoxLayout(footer)
        # 上边留白把分隔线让开，底部多留一点，按钮别贴着圆角
        footer_layout.setContentsMargins(18, 12, 18, 16)
        footer_layout.setSpacing(0)
        for box in self.body.findChildren(QDialogButtonBox):
            self.body_layout.removeWidget(box)
            # 按钮统一高度、拉开间距，默认间距在深色下显得挤
            box.layout().setSpacing(10) if box.layout() else None
            for button in box.buttons():
                button.setMinimumSize(78, 32)
            footer_layout.addWidget(box)

        stack = self._root.layout()
        stack.removeWidget(self.body)
        wrap.setWidget(self.body)
        stack.addWidget(wrap, 1)
        stack.addWidget(footer)

        # 滚动区的 sizeHint 很小，不撑一下对话框会缩成 470 那种矮条。
        # 这里按内容高度撑开，超过上限才真的滚动。
        content = self.body.sizeHint().height() + footer.sizeHint().height() + 60
        self.setFixedHeight(min(content, max_height) + SHADOW * 2)

    # 阴影与主窗口同一套素材
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        # 尺寸变了要重画：窗口能拉伸（开发者面板）时，旧尺寸的阴影会糊在边上
        size = (self.width(), self.height())
        if self._shadow is None or self._shadow_size != size:
            self._shadow = render_shadow(size[0], size[1], SHADOW, RADIUS)
            self._shadow_size = size
        if self._shadow.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        clip_outside_window(painter, self.rect(), SHADOW, RADIUS)
        painter.drawPixmap(0, 0, self._shadow)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._centered:
            self._center_on_parent()
            self._centered = True

    def _center_on_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        geo = parent.frameGeometry()
        self.move(
            geo.center().x() - self.width() // 2,
            geo.center().y() - self.height() // 2,
        )
