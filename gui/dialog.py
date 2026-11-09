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
    QSizePolicy,
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


# 提示窗口的语气：徽标底色 / 描边 / 字色 / 字形
_TONES = {
    "ok": ("#20362a", "#356047", "#7fd0a2", "✓"),
    "info": ("#232f3d", "#3a5573", "#8fc0e8", "i"),
    "warn": ("#3a3020", "#66562b", "#e0b357", "!"),
    "error": ("#3a2523", "#6b3a33", "#f09595", "!"),
}


class MessageDialog(RoundDialog):
    """和主窗口同一套外观的提示 / 确认窗口，用来替代 QMessageBox。

    QMessageBox 会画系统标题栏、蓝色信息图标和浅色按钮，跟这里的深色圆角
    完全不是一套（缓存诊断那个窗口就是这样）。这里复用 RoundDialog 的外壳，
    再把内容拆成「摘要 + 若干分区卡片 + 按钮行」。

    用法：
        MessageDialog.info(self, "缓存诊断", "文本")
        if MessageDialog.ask(self, "清理下载残留", "文本"):
            ...
    需要自定义内容时直接实例化，用 add_heading / add_card / add_row 往里加，
    最后 add_button + finish。
    """

    def __init__(
        self,
        parent=None,
        title: str = "",
        text: str = "",
        tone: str = "info",
        width: int = 460,
    ):
        super().__init__(parent, title, width=width)
        self.setModal(True)
        self._buttons: list[QPushButton] = []
        self._default_button: QPushButton | None = None
        self._mono_labels: list[QLabel] = []
        self._finished = False

        header = QHBoxLayout()
        header.setSpacing(10)
        self._badge = QLabel()
        self._badge.setFixedSize(20, 20)
        self._badge.setAlignment(Qt.AlignCenter)
        self._badge.setAttribute(Qt.WA_StyledBackground, True)
        header.addWidget(self._badge, 0, Qt.AlignTop)

        self._summary = QLabel(text)
        self._summary.setWordWrap(True)
        # 摘要只占自己需要的高度：竖向策略放任它伸展时，多出来的空间会把
        # 这一行拉高，文字看上去悬在徽标下方
        self._summary.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        # 路径、文件名要让用户能选中复制，不然只能照着敲
        self._summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._summary.setStyleSheet("font-size:13px;")
        header.addWidget(self._summary, 1)
        self.body_layout.addLayout(header)

        self._host = QWidget()
        self._host.setObjectName("messageContent")
        self._host.setStyleSheet(
            "QWidget#messageContent { background: transparent; }"
        )
        self.content_layout = QVBoxLayout(self._host)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(8)
        self.body_layout.addWidget(self._host)
        # 多余的高度只给内容区，别让摘要行和按钮行被拉开
        self.body_layout.setStretch(1, 1)

        self._footer = QHBoxLayout()
        self._footer.setSpacing(8)
        self._footer.addStretch(1)
        self.body_layout.addLayout(self._footer)

        self.set_tone(tone)

    # ---------------- 外观 ----------------

    def set_tone(self, tone: str) -> None:
        bg, border, color, glyph = _TONES.get(tone, _TONES["info"])
        self._badge.setText(glyph)
        self._badge.setStyleSheet(
            f"background: {bg}; border: 1px solid {border}; border-radius: 10px;"
            f" color: {color}; font-size: 12px; font-weight: 600;"
        )

    # ---------------- 内容 ----------------

    def add_heading(self, text: str) -> None:
        label = QLabel(text)
        label.setStyleSheet("color: #85858e; font-size: 11px; padding-left: 2px;")
        self.content_layout.addWidget(label)

    def add_text(self, text: str, mono: bool = False, layout=None) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        style = "color: #c9c9d0; font-size: 12px;"
        if mono:
            style += ' font-family: "Consolas", "Cascadia Mono", monospace; font-size: 11px;'
        label.setStyleSheet(style)
        # 注意别写成 layout or self.content_layout：PyQt 里 QLayout 的布尔值是 False，
        # 传进来的卡片布局会被默默丢掉，文字全跑到卡片外面
        target = layout if layout is not None else self.content_layout
        target.addWidget(label)
        if mono:
            self._mono_labels.append(label)
        return label

    def add_card(self) -> QVBoxLayout:
        """一张圆角卡片，返回它内部的竖向布局。

        背景写在卡片自身的样式表上：祖先的通用 QWidget 规则会压过 ID 规则，
        这一点在帮助窗口上已经踩过。
        """
        card = QWidget()
        card.setObjectName("messageCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setStyleSheet(
            "QWidget#messageCard { background: #232327;"
            " border: 1px solid #2f2f36; border-radius: 8px; }"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        self.content_layout.addWidget(card)
        return layout

    def add_row(
        self,
        card_layout: QVBoxLayout,
        key: str,
        value: str,
        hint: str = "",
        color: str = "",
        mono: bool = False,
        value_width: int = 0,
    ) -> QLabel:
        row = QHBoxLayout()
        row.setSpacing(10)

        name = QLabel(key)
        name.setStyleSheet("color: #96969e; font-size: 12px;")
        name.setFixedWidth(74)
        row.addWidget(name, 0, Qt.AlignTop)

        style = f"color: {color or '#e8e8ea'}; font-size: 12px;"
        if mono:
            style += ' font-family: "Consolas", "Cascadia Mono", monospace; font-size: 11px;'
        val = QLabel(str(value))
        val.setStyleSheet(style)
        val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if value_width:
            # 给数值一个固定宽度，右侧的说明文字才能按列对齐，不会一行一个位置
            val.setFixedWidth(value_width)
        else:
            val.setWordWrap(True)
        row.addWidget(val, 0, Qt.AlignTop)

        if hint:
            tip = QLabel(hint)
            tip.setWordWrap(True)
            tip.setStyleSheet("color: #7a7a84; font-size: 11px;")
            row.addWidget(tip, 1)
        else:
            row.addStretch(1)
        card_layout.addLayout(row)
        return val

    # ---------------- 按钮 ----------------

    def add_button(self, text: str, primary: bool = False, action=None) -> QPushButton:
        btn = QPushButton(text)
        btn.setMinimumSize(80, 32)
        btn.setCursor(Qt.PointingHandCursor)
        if primary:
            btn.setObjectName("primary")
            # objectName 改在样式应用之后，必须重新 polish 才会命中 #primary
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        if action is not None:
            btn.clicked.connect(action)
        else:
            btn.clicked.connect(self.accept if primary else self.reject)
        self._footer.addWidget(btn)
        self._buttons.append(btn)
        if primary:
            self._default_button = btn
        return btn

    def finish(self, max_height: int = 600) -> None:
        """按内容定稿尺寸；内容太高时套一层滚动，别把按钮顶出屏幕。"""
        if self._finished:
            return
        self._finished = True
        # 开了 wordWrap 的 QLabel，sizeHint 会按很窄的宽度估行数（文件名那一行
        # 实际 15px 却被报成 45px），卡片因此虚高、底部留一大块空白。能在卡片
        # 里排成一行的，直接关掉换行并锁定高度。
        available = self.width() - SHADOW * 2 - 18 * 2 - 14 * 2 - 6
        for label in self._mono_labels:
            metrics = label.fontMetrics()
            if metrics.horizontalAdvance(label.text()) <= available:
                label.setWordWrap(False)
                label.setFixedHeight(metrics.height() + 2)
        # 摘要也是 wordWrap 的 QLabel：Qt 会按很窄的宽度估行数，报出来的高度
        # 比实际排版高一截，窗口底部就多出一块空白。按真实可用宽度锁死。
        avail = self.width() - SHADOW * 2 - 18 * 2
        shown = self._summary.heightForWidth(avail)
        if shown > 0:
            self._summary.setFixedHeight(shown)
        # 末尾补一个弹性空间：内容比窗口矮时留白落在卡片下面，而不是把每张卡拉开
        self.content_layout.addStretch(1)
        self.adjustSize()
        if self.height() - SHADOW * 2 <= max_height:
            return
        index = self.body_layout.indexOf(self._host)
        self.body_layout.removeWidget(self._host)
        scroll = QScrollArea(self._root)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        scroll.setWidget(self._host)
        self.body_layout.insertWidget(index, scroll, 1)
        self.setFixedHeight(max_height + SHADOW * 2)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 按钮被 reparent 进对话框时 Qt 会自动打开 autoDefault，回车会误触；
        # 这里统一关掉，只留下真正的主按钮吃回车
        for btn in self._buttons:
            btn.setAutoDefault(False)
        if self._default_button is not None:
            self._default_button.setAutoDefault(True)
            self._default_button.setDefault(True)

    # ---------------- 便捷入口 ----------------

    @classmethod
    def info(
        cls,
        parent,
        title: str,
        text: str,
        tone: str = "info",
        width: int = 460,
    ) -> None:
        dlg = cls(parent, title, text, tone, width)
        dlg.add_button("好", primary=True)
        dlg.finish()
        dlg.exec_()

    @classmethod
    def ask(
        cls,
        parent,
        title: str,
        text: str,
        yes: str = "确定",
        no: str = "取消",
        tone: str = "info",
        width: int = 460,
    ) -> bool:
        dlg = cls(parent, title, text, tone, width)
        dlg.add_button(no)
        dlg.add_button(yes, primary=True)
        dlg.finish()
        return dlg.exec_() == QDialog.Accepted
