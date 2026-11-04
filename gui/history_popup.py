"""搜索历史下拉：作为主窗口内的子控件，而不是独立的系统弹窗。

用独立顶层窗口（Qt::Popup）会占住鼠标/键盘 grab，之后打开设置等对话框
时会直接卡住；改成主窗口里的浮层后没有这些问题，样式也和主界面统一。
"""
from __future__ import annotations

from PyQt5.QtCore import QEvent, QPoint, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

ROW_H = 30
MAX_ROWS = 8


class HistoryPopup(QFrame):
    picked = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("historyPopup")
        self._anchor: QWidget | None = None
        self.hide()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(5, 5, 5, 5)

        self.list = QListWidget()
        self.list.setObjectName("historyList")
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.list.setUniformItemSizes(True)
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.itemClicked.connect(self._on_clicked)
        outer.addWidget(self.list)

        # 浮层压在表格上，加一层阴影拉开层次
        from PyQt5.QtWidgets import QGraphicsDropShadowEffect

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(12)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 55))
        self.setGraphicsEffect(shadow)

    # ---------------- 展示 ----------------

    def populate(self, items: list[str]) -> None:
        self.list.clear()
        for text in items[:MAX_ROWS]:
            item = QListWidgetItem(text)
            item.setSizeHint(QSize(0, ROW_H))
            self.list.addItem(item)

    def show_for(self, anchor: QWidget, items: list[str]) -> None:
        if not items:
            return
        host = self.parentWidget()
        if host is None:
            return
        self.populate(items)
        width = max(anchor.width(), 240)
        rows = min(len(items), MAX_ROWS)
        self.setFixedSize(width, rows * ROW_H + 12)
        top_left = anchor.mapTo(host, QPoint(0, 0))
        offset = QPoint(top_left.x(), top_left.y() + anchor.height() + 6)
        # 底部空间不够时向上翻
        if offset.y() + self.height() > host.height():
            offset.setY(max(0, top_left.y() - self.height() - 4))
        self.move(offset)
        self.list.setCurrentRow(0)
        self._anchor = anchor
        self.raise_()
        self.show()
        # 表格、按钮大多设了 NoFocus，点它们时输入框不会失焦，
        # 单靠 focusOut 关不掉这个浮层，得自己盯着全局鼠标
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def hideEvent(self, event) -> None:  # noqa: N802
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.MouseButtonPress:
            target = QApplication.widgetAt(event.globalPos())
            inside = (
                target is not None
                and (target is self or self.isAncestorOf(target) or target is self._anchor)
            )
            if not inside:
                self.hide()
        return False

    # ---------------- 交互 ----------------

    def _on_clicked(self, item: QListWidgetItem) -> None:
        self.picked.emit(item.text())
        self.hide()

    def move_selection(self, delta: int) -> None:
        row = self.list.currentRow()
        row = max(0, min(row + delta, self.list.count() - 1))
        self.list.setCurrentRow(row)

    def current_text(self) -> str:
        item = self.list.currentItem()
        return item.text() if item is not None else ""
