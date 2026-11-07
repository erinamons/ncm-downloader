"""帮助窗口：卡片式版式，界面上每个按钮都有一张词条卡。

放弃单 QLabel 塞整篇灰字的做法，改用滚动区里叠 QFrame 卡片：
- 按钮词条卡左侧放和界面一致的图标（icons 库渲染），一眼能对上实物
- 播放状态用彩色徽章模拟真实显示，配合状态机新语义
- 播放失败原因做成对照表，兼当排查手册
样式在 HelpDialog 里局部 setStyleSheet，不污染全局 QSS。
"""
from __future__ import annotations

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui import __version__
from gui import icons
from gui.dialog import RoundDialog

APP_VERSION = __version__

_CARD_CSS = """
QFrame#helpCard, QFrame#helpCardFlat {
    background: #2a2a31;
    border: 1px solid #34343c;
    border-radius: 8px;
}
QFrame#helpCard {
    border-color: #3a3a44;
}
QLabel#helpSectionTitle {
    color: #e8554a;
    font-size: 12px;
    font-weight: 600;
}
QLabel#helpCardTitle {
    color: #e2e2e6;
    font-size: 12px;
    font-weight: 600;
}
QLabel#helpCardDesc {
    color: #96969e;
    font-size: 11px;
}
QLabel#helpIconBox {
    background: #34343e;
    border-radius: 6px;
}
QLabel#helpBadge {
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
}
QTableWidget#helpMiniTable {
    background: #24242b;
    border: 1px solid #34343c;
    border-radius: 6px;
    font-size: 11px;
}
QTableWidget#helpMiniTable::item {
    padding: 2px;
}
"""


def _icon_box(icon_name: str, color: str = "#b6b6c0") -> QLabel:
    """26×26 圆角底 + 居中图标，和界面按钮观感一致。"""
    box = QLabel()
    box.setObjectName("helpIconBox")
    box.setFixedSize(26, 26)
    box.setAlignment(Qt.AlignCenter)
    pm = icons.pixmap(icon_name, color, 14)
    if not pm.isNull():
        box.setPixmap(pm)
    return box


def _card(
    icon_name: str,
    title: str,
    desc: str,
    accent: str | None = None,
) -> QFrame:
    """按钮词条卡：图标块 + 标题 + 说明。accent 给图标块上色以模拟开启态。"""
    card = QFrame()
    card.setObjectName("helpCard")
    lay = QHBoxLayout(card)
    lay.setContentsMargins(10, 8, 10, 8)
    lay.setSpacing(10)
    lay.addWidget(_icon_box(icon_name, accent or "#b6b6c0"))
    text = QVBoxLayout()
    text.setSpacing(1)
    t = QLabel(title)
    t.setObjectName("helpCardTitle")
    t.setWordWrap(True)
    d = QLabel(desc)
    d.setObjectName("helpCardDesc")
    d.setWordWrap(True)
    text.addWidget(t)
    text.addWidget(d)
    lay.addLayout(text, 1)
    return card


def _section(title: str, parent_layout: QVBoxLayout) -> None:
    label = QLabel(title)
    label.setObjectName("helpSectionTitle")
    parent_layout.addWidget(label)


def _grid(cards: list[QFrame], columns: int = 2) -> QFrame:
    """卡片两列网格；奇数张时最后一张独占一行。"""
    holder = QFrame()
    holder.setObjectName("helpCardFlat")
    grid = QGridLayout(holder)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(8)
    for i, card in enumerate(cards):
        grid.addWidget(card, i // columns, i % columns)
    grid.setColumnStretch(columns, 1)
    return holder


def _badge(text: str, fg: str, bg: str) -> QLabel:
    b = QLabel(text)
    b.setObjectName("helpBadge")
    b.setAlignment(Qt.AlignCenter)
    b.setStyleSheet(f"color:{fg}; background:{bg};")
    return b


def _state_row(badge: QLabel, desc: str) -> QFrame:
    row = QFrame()
    row.setObjectName("helpCardFlat")
    lay = QHBoxLayout(row)
    lay.setContentsMargins(10, 7, 10, 7)
    lay.setSpacing(10)
    lay.addWidget(badge)
    d = QLabel(desc)
    d.setObjectName("helpCardDesc")
    d.setWordWrap(True)
    lay.addWidget(d, 1)
    return row


def _plain_card(title: str, desc: str) -> QFrame:
    """无图标卡：用于非按钮类条目（快捷键、行为说明）。"""
    card = QFrame()
    card.setObjectName("helpCard")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(10, 8, 10, 8)
    lay.setSpacing(1)
    t = QLabel(title)
    t.setObjectName("helpCardTitle")
    t.setWordWrap(True)
    d = QLabel(desc)
    d.setObjectName("helpCardDesc")
    d.setWordWrap(True)
    lay.addWidget(t)
    lay.addWidget(d)
    return card


def _fail_table(rows: list[tuple[str, str]]) -> QFrame:
    """播放失败原因对照：提示 → 原因和办法。"""
    holder = QFrame()
    holder.setObjectName("helpCard")
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(2, 2, 2, 2)
    lay.setSpacing(0)
    head = QLabel(
        '<span style="color:#c8c8d0; font-weight:600;">状态栏提示</span>'
        '　　<span style="color:#c8c8d0; font-weight:600;">原因和办法</span>'
    )
    head.setContentsMargins(8, 6, 8, 6)
    lay.addWidget(head)
    for i, (tip, fix) in enumerate(rows):
        line = QLabel(
            f'<span style="color:#96969e;">{tip}</span><br>'
            f'<span style="color:#b6b6c0;">{fix}</span>'
        )
        line.setWordWrap(True)
        line.setContentsMargins(8, 0, 8, 7)
        if i:
            line.setStyleSheet("border-top: 1px solid #34343c; padding-top: 6px;")
        lay.addWidget(line)
    return holder


# ---------------- 真实控件模拟 ----------------
# 帮助里出现的"样子"必须和软件里一模一样：勾选框用真实的表格 indicator
# 样式，导出按钮用真实的主按钮样式，右键菜单弹真的 QMenu。
# 不给功能硬配图标，避免用户照着找却找不到。

def _mini_result_table(states: list[bool], header: bool = False) -> QTableWidget:
    """迷你结果表：一列勾选 + 一列曲名，勾选框走全局 QSS 的真实样式。"""
    table = QTableWidget(len(states), 2)
    table.setObjectName("helpMiniTable")
    table.setShowGrid(False)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    table.setColumnWidth(0, 26)
    if header:
        table.setHorizontalHeaderLabels(["", "曲名"])
        table.setFixedHeight(26 + 30 * len(states) + 6)
    else:
        table.horizontalHeader().setVisible(False)
        table.setFixedHeight(30 * len(states) + 4)
    table.setFixedWidth(190)
    for row, checked in enumerate(states):
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        table.setItem(row, 0, item)
        name = QTableWidgetItem(f"示例曲目 {row + 1}")
        name.setForeground(QColor("#d8d8dc"))
        table.setItem(row, 1, name)
    return table


def _picker_demo() -> QWidget:
    """勾选演示：上未选下已选，可点击切换，样式与列表完全一致。"""
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)
    lay.addWidget(_mini_result_table([False, True]))
    btn = QPushButton("导出选中 (1)")
    btn.setObjectName("primary")
    btn.setFixedHeight(30)
    btn.setIcon(icons.icon("download", "#ffffff", 18))
    btn.setIconSize(QSize(15, 15))
    btn.setToolTip("这就是缓存视图里的导出按钮，数量跟着勾选变")
    lay.addWidget(btn)
    lay.addStretch(1)
    return holder


def _header_select_demo() -> QWidget:
    """表头全选演示：点表头最左边的空白格切换全选，真实复刻。"""
    table = _mini_result_table([True, True], header=True)

    def toggle(_col: int) -> None:
        checked = sum(
            1 for r in range(table.rowCount())
            if table.item(r, 0) and table.item(r, 0).checkState() == Qt.Checked
        )
        target = Qt.Unchecked if checked == table.rowCount() else Qt.Checked
        table.blockSignals(True)
        for r in range(table.rowCount()):
            item = table.item(r, 0)
            if item:
                item.setCheckState(target)
        table.blockSignals(False)

    table.horizontalHeader().sectionClicked.connect(toggle)
    table.horizontalHeader().setSectionsClickable(True)
    holder = QWidget()
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    lay.addWidget(table)
    hint = QLabel("← 点最左边的空白表头试试")
    hint.setObjectName("helpCardDesc")
    lay.addWidget(hint)
    return holder


def _context_menu_demo(parent) -> QWidget:
    """右键菜单演示：弹出真实 QMenu，样式与软件内一致（含置灰项）。"""
    holder = QWidget()
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    btn = QPushButton("点我展开完整右键菜单")
    btn.setFixedHeight(28)
    btn.setCursor(Qt.PointingHandCursor)

    def show_menu() -> None:
        menu = QMenu(btn)
        menu.addAction("播放")
        menu.addAction("仅播放此曲")
        menu.addSeparator()
        act = menu.addAction("导出选中（2 首）")
        act.setEnabled(True)
        menu.addAction("仅导出此曲")
        menu.addSeparator()
        menu.addAction("在文件夹中显示")
        menu.addSeparator()
        menu.addAction("复制歌曲链接")
        menu.addAction("复制「歌名 - 歌手」")
        menu.addAction("复制歌曲 ID")
        menu.addSeparator()
        menu.addAction("全选")
        menu.addAction("取消全选")
        menu.addAction("清空当前结果")
        menu.addSeparator()
        menu.addAction("在浏览器打开")
        menu.exec_(btn.mapToGlobal(btn.rect().bottomLeft()))

    btn.clicked.connect(show_menu)
    lay.addWidget(btn)
    return holder


def _demo_row(widget: QWidget, title: str, desc: str) -> QFrame:
    """演示卡：左侧真实控件，右侧标题与说明。"""
    card = QFrame()
    card.setObjectName("helpCard")
    lay = QHBoxLayout(card)
    lay.setContentsMargins(10, 8, 10, 8)
    lay.setSpacing(12)
    lay.addWidget(widget)
    text = QVBoxLayout()
    text.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("helpCardTitle")
    d = QLabel(desc)
    d.setObjectName("helpCardDesc")
    d.setWordWrap(True)
    text.addWidget(t)
    text.addWidget(d)
    text.addStretch(1)
    lay.addLayout(text, 1)
    return card


class HelpDialog(RoundDialog):
    def __init__(self, parent=None):
        super().__init__(parent, "帮助", width=640)
        self.setMinimumHeight(560)
        self.setStyleSheet(_CARD_CSS)

        scroll = QScrollArea()
        scroll.setObjectName("helpScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)

        body = QWidget()
        flow = QVBoxLayout(body)
        flow.setContentsMargins(14, 10, 14, 14)
        flow.setSpacing(10)

        # ---- 快速上手 ----
        _section("快速上手", flow)
        steps = QHBoxLayout()
        steps.setSpacing(8)
        for i, (t, d) in enumerate(
            (
                ("1 · 找歌", "搜索框输入关键词，或直接粘贴歌曲 / 歌单链接"),
                ("2 · 试听", "双击列表任意一行直接播放"),
                ("3 · 导出", "勾选想要的歌，点右下角「导出 / 下载」"),
            ),
            1,
        ):
            step = QFrame()
            step.setObjectName("helpCard")
            sl = QVBoxLayout(step)
            sl.setContentsMargins(10, 8, 10, 8)
            sl.setSpacing(1)
            st = QLabel(t)
            st.setObjectName("helpCardTitle")
            sd = QLabel(d)
            sd.setObjectName("helpCardDesc")
            sd.setWordWrap(True)
            sl.addWidget(st)
            sl.addWidget(sd)
            steps.addWidget(step, 1)
        flow.addLayout(steps)

        # ---- 工具行 ----
        _section("工具行按钮", flow)
        flow.addWidget(
            _grid(
                [
                    _card(
                        "search",
                        "搜索框",
                        "歌名 / 歌手 / 专辑 / 纯数字 ID / 歌单链接都从这里进，回车开始；"
                        "点一下弹出最近 10 条历史；Esc 清空",
                    ),
                    _card(
                        "music",
                        "音源切换",
                        "客户端接口（推荐，享客户端权益）/ 网页接口 / 仅本地缓存（不发请求）",
                    ),
                    _card(
                        "rotate-ccw",
                        "刷新",
                        "重新载入当前列表；缓存视图下会重扫客户端缓存目录",
                    ),
                    _card(
                        "refresh-cw",
                        "自动刷新（红底 = 开启）",
                        "每 0.5 秒检测缓存变化，客户端听到新歌会自动出现在列表顶部",
                        accent="#e8554a",
                    ),
                    _card(
                        "arrow-down-wide-narrow",
                        "排序",
                        "切换「最新缓存在前」和「按曲名」；红底表示按时间排序",
                        accent="#e8554a",
                    ),
                    _card(
                        "trash-2",
                        "清空",
                        "清空当前列表，不影响其他音源已载入的内容",
                    ),
                    _card(
                        "settings",
                        "设置",
                        "音质、转 mp3、缓存目录、缓存存档、启动视图、搜索历史",
                    ),
                    _card(
                        "folder-open",
                        "打开目录",
                        "状态栏右侧文件夹图标，随时打开下载目录",
                    ),
                ]
            )
        )

        # ---- 结果列表 ----
        _section("结果列表：真实样子（可点击试试）", flow)
        flow.addWidget(
            _demo_row(
                _picker_demo(),
                "勾选与导出",
                "左侧方框决定导出哪些歌，点一下切换。缓存视图的按钮叫「导出」——"
                "解密或联网补齐到下载目录；搜索 / 歌单视图叫「下载」",
            )
        )
        flow.addWidget(
            _demo_row(
                _header_select_demo(),
                "全选",
                "没有独立按钮：点表头最左边的空白格，在全选和取消之间切换",
            )
        )
        flow.addWidget(
            _demo_row(
                _context_menu_demo(self),
                "右键菜单",
                "完整菜单就长这样：播放、导出、在文件夹中显示、复制、全选、清空、"
                "在浏览器打开；对着任意一行点右键即可",
            )
        )
        flow.addWidget(
            _grid(
                [
                    _card(
                        "chevron-down",
                        "列头排序",
                        "点「曲名 / 艺人 / 专辑 / 时长」：升序 → 降序 → 原序；"
                        "当前列加粗带箭头",
                    ),
                    _card(
                        "music",
                        "音质与进度列",
                        "缓存视图显示实际档位（320k 等）和缓存完成度；"
                        "已导出的歌显示「已导出」，不再显示缓存百分比",
                    ),
                    _card(
                        "search",
                        "缓存内筛选",
                        "「仅本地缓存」下搜索框只过滤本地列表，不发任何请求",
                    ),
                ]
            )
        )

        # ---- 播放条 ----
        _section("播放条", flow)
        flow.addWidget(
            _grid(
                [
                    _card(
                        "play",
                        "播放 / 暂停",
                        "空格同效；光标在输入框里打字时不触发",
                    ),
                    _card(
                        "skip-back",
                        "上一首 / 下一首",
                        "Ctrl + ← / →，按当前列表顺序切",
                    ),
                    _card(
                        "square",
                        "停止",
                        "停掉当前播放并清空播放条",
                    ),
                    _card(
                        "music",
                        "进度条",
                        "播放条底部细线，悬停出现圆点，单击或拖动跳转",
                    ),
                    _card(
                        "repeat",
                        "循环模式",
                        "右下角循环切换：列表循环 → 单曲循环 → 顺序播放",
                    ),
                    _card(
                        "volume-2",
                        "音量",
                        "点喇叭一键静音，滑块调节；音量会被记住",
                    ),
                    _card(
                        "maximize-2",
                        "窗口按钮",
                        "最小化 / 最大化 / 关闭；双击标题栏也可最大化 / 还原",
                    ),
                    _card(
                        "music",
                        "长歌名",
                        "歌名太长会自动横向滚动；拖播放条空白处可移动窗口",
                    ),
                ]
            )
        )

        # ---- 播放状态 ----
        _section("播放状态怎么看", flow)
        flow.addWidget(
            _state_row(
                _badge("正在获取 45%", "#b6b6c0", "#34343e"),
                "双击还没缓存的歌：先联网下载音频（未缓存完成的歌要补齐整首），"
                "状态栏显示进度，请稍等",
            )
        )
        flow.addWidget(
            _state_row(
                _badge("缓冲中", "#7fa9cc", "#2c3a4a"),
                "音频到手、正在加载——马上出声，不是卡住；缓冲中点播放键是暂停",
            )
        )
        flow.addWidget(
            _state_row(
                _badge("正在播放", "#7fbf9a", "#2c3a2e"),
                "真正出声了才会显示这个",
            )
        )
        flow.addWidget(
            _state_row(
                _badge("播放失败", "#f09595", "#462e2e"),
                "原因见下方对照表；状态栏里有「重试」入口",
            )
        )

        # ---- 播放失败原因 ----
        _section("播放失败原因对照", flow)
        flow.addWidget(
            _fail_table(
                [
                    ("接口未返回播放地址", "该曲无版权或已下架；换一首，或换音源接口再试"),
                    (
                        "仅提供试听片段",
                        "会员权益不足；登录账号，或换「客户端接口」+ 移动端免费听歌",
                    ),
                    (
                        "本地文件不完整，已改用网络播放",
                        "缓存只有半截，已自动联网补齐，无需处理",
                    ),
                    ("播放失败：下载中断 / 请求失败", "网络波动，点状态栏「重试」即可"),
                    (
                        "播放失败：解不开 / 无法播放",
                        "缓存文件损坏；点「重试」会丢弃本地文件直接联网取",
                    ),
                ]
            )
        )

        # ---- 下载队列 ----
        _section("下载队列与状态栏", flow)
        flow.addWidget(
            _grid(
                [
                    _card(
                        "download",
                        "队列状态",
                        "每首显示进度 / 状态 / 大小：等待、下载中、完成、失败、"
                        "已取消、试听、无版权",
                    ),
                    _card(
                        "rotate-ccw",
                        "失败重试",
                        "点状态栏汇总里的「失败 N」重下所有失败项；队列右键也有",
                    ),
                    _card(
                        "folder-open",
                        "队列右键",
                        "播放、打开所在文件夹、复制文件路径；双击行直接播放",
                    ),
                    _card(
                        "settings",
                        "转 mp3",
                        "设置里开启；按源码率上浮一档转码，不提升音质只换容器",
                    ),
                ]
            )
        )

        # ---- 缓存 ----
        _section("缓存说明", flow)
        flow.addWidget(
            _grid(
                [
                    _card(
                        "music",
                        "缓存是什么",
                        "客户端在线播放留下的音频，本工具能还原成标准 m4a / mp3",
                    ),
                    _card(
                        "refresh-cw",
                        "实时刷新",
                        "客户端听新歌 / 清缓存，列表 2~3 秒内自动跟上",
                    ),
                    _card(
                        "settings",
                        "缓存目录",
                        "默认自动探测；客户端里改过目录时到「设置 → 缓存目录」指定",
                    ),
                    _card(
                        "archive",
                        "缓存存档",
                        "客户端只留最近几首；设置里开启存档后，听过的都会留底",
                    ),
                ]
            )
        )

        # ---- 快捷键 ----
        _section("快捷键", flow)
        keys = QFrame()
        keys.setObjectName("helpCard")
        kl = QVBoxLayout(keys)
        kl.setContentsMargins(10, 8, 10, 8)
        kl.setSpacing(4)
        for k, d in (
            ("Ctrl + F", "聚焦搜索框"),
            ("F1", "打开本帮助"),
            ("Ctrl + M", "紧凑模式 / 还原"),
            ("Ctrl + ← / →", "上一首 / 下一首"),
            ("空格", "播放 / 暂停"),
            ("Esc", "清空搜索框，再按取消选中"),
            ("双击行", "播放这首歌"),
            ("双击标题栏", "最大化 / 还原"),
        ):
            line = QLabel(
                f'<span style="color:#b6b6c0; font-weight:600;">{k}</span>'
                f'　<span style="color:#96969e;">{d}</span>'
            )
            kl.addWidget(line)
        flow.addWidget(keys)

        # ---- 关于 ----
        about = QLabel(
            f'桌面版 v{APP_VERSION} · 下载与播放都在本地完成，不上传任何数据<br>'
            f'网站 <a href="https://mochizuki.top" style="color:#7fa9cc;">mochizuki.top</a>'
            f'　邮箱 <a href="mailto:nagi@mochizuki.top" style="color:#7fa9cc;">nagi@mochizuki.top</a>'
        )
        about.setObjectName("helpCardDesc")
        about.setOpenExternalLinks(True)
        flow.addWidget(about)
        flow.addStretch(1)

        scroll.setWidget(body)
        self.body_layout.addWidget(scroll)
