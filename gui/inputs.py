"""输入框：点击时就能看到完整搜索历史，而不是打了字才出现。

历史列表是窗口内的浮层（不是系统弹窗），所以打字始终落在输入框里，
上下键和回车由输入框转交给列表处理。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QLineEdit

from gui.history_popup import HistoryPopup


class HistoryLineEdit(QLineEdit):
    picked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[str] = []
        self._popup: HistoryPopup | None = None
        self.textChanged.connect(self._on_text_changed)

    # ---------------- 历史数据 ----------------

    def set_history(self, items: list[str]) -> None:
        self._items = list(items)

    def _ensure_popup(self) -> HistoryPopup:
        if self._popup is None:
            host = self.window()
            host = getattr(host, "_root", host)
            self._popup = HistoryPopup(host)
            self._popup.picked.connect(self._on_picked)
        return self._popup

    def show_history(self) -> None:
        if self.text().strip() or not self._items:
            return
        self._ensure_popup().show_for(self, self._items)

    def hide_history(self) -> None:
        if self._popup is not None:
            self._popup.hide()

    # ---------------- 事件 ----------------

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        if not self.text():
            self.show_history()

    def focusOutEvent(self, event) -> None:
        # 列表内的项不可获得焦点，点它不会触发这里
        self.hide_history()
        super().focusOutEvent(event)

    def _on_text_changed(self, text: str) -> None:
        if text:
            self.hide_history()

    def _on_picked(self, text: str) -> None:
        self.setText(text)
        self.returnPressed.emit()

    def keyPressEvent(self, event) -> None:
        popup = self._popup
        visible = popup is not None and popup.isVisible()
        if visible:
            if event.key() == Qt.Key_Down:
                popup.move_selection(1)
                return
            if event.key() == Qt.Key_Up:
                popup.move_selection(-1)
                return
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                text = popup.current_text()
                if text:
                    self._on_picked(text)
                    return
            if event.key() == Qt.Key_Escape:
                popup.hide()
                return
        elif event.key() == Qt.Key_Down and not self.text():
            self.show_history()
            return
        super().keyPressEvent(event)
