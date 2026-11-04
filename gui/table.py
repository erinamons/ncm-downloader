"""结果表格：所有列都能拖，窗口变宽时余量给曲名。

Qt 原生的 Stretch 模式有两个毛病：被拉伸的列无法拖动（曲名和艺人之间
的边界变成死的），而且拖动别的列时会反过来挤占拉伸列，看起来像"往右
拖却往左长"。这里改成所有列 Interactive，弹性列按剩余空间收放，行为
接近资源管理器。
"""
from __future__ import annotations

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QHeaderView, QTableWidget

MIN_FLUID = 160


class FluidTable(QTableWidget):
    def __init__(self, rows: int, cols: int, fluid_col: int = 1, parent=None):
        super().__init__(rows, cols, parent)
        self.fluid_col = fluid_col
        self._filling = False

        header = self.horizontalHeader()
        for col in range(cols):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setSectionsMovable(False)

        # 拖动窗口宽度时每帧都改列宽，会让整个视口跟着重排，横向拉伸看起来在闪。
        # 攒到一个很短的窗口再调整：既跟得上拖动，又不会每帧重排。
        self._fill_timer = QTimer(self)
        self._fill_timer.setSingleShot(True)
        self._fill_timer.setInterval(45)
        self._fill_timer.timeout.connect(self._fill_fluid)

    def _fill_fluid(self) -> None:
        """把视口剩余宽度给弹性列，其余列保持用户设定的宽度。"""
        if self.fluid_col < 0 or self._filling:
            return
        total = self.viewport().width()
        if total <= 0:
            return
        others = sum(
            self.columnWidth(c)
            for c in range(self.columnCount())
            if c != self.fluid_col and not self.isColumnHidden(c)
        )
        target = max(MIN_FLUID, total - others)
        if self.columnWidth(self.fluid_col) == target:
            return
        self._filling = True
        self.setColumnWidth(self.fluid_col, target)
        self._filling = False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fill_timer.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fill_fluid()
