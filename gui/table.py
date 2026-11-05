"""结果表格：所有列都能拖，窗口变宽时余量给曲名。

Qt 原生的 Stretch 模式有两个毛病：被拉伸的列无法拖动（曲名和艺人之间
的边界变成死的），而且拖动别的列时会反过来挤占拉伸列，看起来像"往右
拖却往左长"。这里改成所有列 Interactive，弹性列按剩余空间收放，行为
接近资源管理器。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QHeaderView, QTableWidget, QWidget

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

        # Qt 的竖向滚动条默认占满整张表的高度，会跑到表头右侧，看起来像
        # 「超出了内容区」。这里在右上角盖一块与表头同色的补丁，把滚动条
        # 上半段遮住，视觉上它就只从内容区开始。
        self._corner = QWidget(self)
        self._corner.setObjectName("cornerPatch")
        # 普通 QWidget 默认不画 QSS 里的 background，必须显式打开，
        # 否则补丁位置算得再对，实际也是透明的，滚动条照样露出来
        self._corner.setAttribute(Qt.WA_StyledBackground, True)
        # 让滚轮能穿透到表格，否则鼠标停在这一小块上滚不动
        self._corner.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._corner.show()
        header.geometriesChanged.connect(self._place_corner)
        # 空表时滚动条范围是 0 但常驻显示，尺寸变化不一定伴随 resize，
        # 这里再挂两个信号保证补丁位置始终跟得上
        self.verticalScrollBar().rangeChanged.connect(lambda *_: self._place_corner())

        # 拖动窗口宽度时每帧都改列宽，会让整个视口跟着重排，横向拉伸看起来在闪。
        # 攒到一个很短的窗口再调整：既跟得上拖动，又不会每帧重排。
        self._fill_timer = QTimer(self)
        self._fill_timer.setSingleShot(True)
        self._fill_timer.setInterval(45)
        self._fill_timer.timeout.connect(self._fill_fluid)
        self._sync_scrollbar()

    def setRowCount(self, rows: int) -> None:
        super().setRowCount(rows)
        self._sync_scrollbar()

    def _sync_scrollbar(self) -> None:
        """空表不显示滚动条：范围是 0 时滑块会铺满整条槽，看着像根竖线。"""
        policy = Qt.ScrollBarAlwaysOff if self.rowCount() == 0 else Qt.ScrollBarAlwaysOn
        if self.verticalScrollBarPolicy() != policy:
            self.setVerticalScrollBarPolicy(policy)
            # 滚动条显隐会改变视口宽度，弹性列得跟着重算
            self._fill_fluid()
        self._place_corner()

    def _place_corner(self) -> None:
        """把滚动条压到表头下方，并用同色补丁补齐表头右侧那条竖缝。

        Qt 默认让竖向滚动条贯穿整张表（连表头右侧都是它），看起来像「越界」。
        盖一块补丁没用——滚动条容器是后绘制的，会压在补丁上面；直接改容器的
        几何才稳。
        """
        if not hasattr(self, "_corner"):
            return
        bar = self.verticalScrollBar()
        container = bar.parentWidget()
        header_h = self.horizontalHeader().height()

        # 先挪容器：这一步即使滚动条还没显示也要做，否则首帧还是贯穿的
        width = bar.width()
        if container is not None and container is not self and container.objectName().startswith(
            "qt_scrollarea_vcontainer"
        ):
            height = max(0, self.height() - header_h - 1)
            target = (self.width() - width - 1, header_h, width, height)
            if container.geometry().getRect() != target:
                container.setGeometry(*target)

        if not bar.isVisible():
            self._corner.hide()
            return

        # 表头只铺到 viewport 宽度，右侧这条缝要用同色补上，否则露底色
        self._corner.setGeometry(
            self.width() - width - 1, 0, width + 1, header_h + 1
        )
        self._corner.show()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        self._place_corner()

    def paintEvent(self, event) -> None:
        # 有些布局路径（换策略、动态调整视口边距）会悄悄把容器挪回去，
        # 这里每次绘制前对一眼几何，不对就按回去，成本是四次整数比较
        super().paintEvent(event)
        self._place_corner()

    def refit(self) -> None:
        """外部调完 setColumnWidth 之后叫一次，把弹性列重新拉满。"""
        self._fill_fluid()

    def setColumnHidden(self, column: int, hidden: bool) -> None:
        """列显隐变化后重算弹性列宽度。

        隐藏/显示列不会触发 resizeEvent，光靠那个钩子的话，切音源或进出紧凑
        模式之后曲名列还是旧宽度，右边会空出一截，滚动条也跟着偏。
        """
        super().setColumnHidden(column, hidden)
        self._fill_fluid()

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
        self._place_corner()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fill_timer.start()
        self._place_corner()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fill_fluid()
        QTimer.singleShot(0, self._place_corner)
