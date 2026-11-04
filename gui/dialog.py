"""圆角对话框基类：与主窗口保持同一套外观。

主窗口是无边框自绘的，对话框如果还带系统标题栏会明显不一样。这里把
无边框、圆角、阴影、标题栏、拖动全部复用，子类只需要往 self.body 里
塞内容。
"""
from __future__ import annotations

from PyQt5.QtCore import QPoint, QSize, Qt
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

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

    # 阴影与主窗口同一套素材
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._shadow is None:
            self._shadow = render_shadow(
                self.width(), self.height(), SHADOW, RADIUS
            )
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
