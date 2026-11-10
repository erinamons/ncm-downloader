"""帮助窗口：分区 + 词条卡，纯文字，不放仿制控件。

放弃单 QLabel 塞整篇灰字的做法，改用滚动区里叠 QFrame 卡片：
- 每张卡是「标题 + 一句话」，左侧只有一条 3px 色条，不做成按钮的样子
- 播放状态用彩色徽章说明，配合状态机新语义
- 播放失败原因做成对照表，兼当排查手册
样式在 HelpDialog 里局部 setStyleSheet，不污染全局 QSS。
"""
from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui import __version__
from gui import icons
from gui.dialog import RoundDialog

APP_VERSION = __version__
# 帮助里的截图：真界面直接截，按宽度缩放后贴进来，不做仿制控件
HELP_ASSETS = Path(__file__).parent / "assets" / "help"

_CARD_CSS = """
QFrame#helpCard, QFrame#helpCardFlat {
    background: #2a2a31;
    border: 1px solid #34343c;
    border-radius: 8px;
}
QFrame#helpCard {
    border-color: #3a3a44;
}
QFrame#helpFigure {
    background: #17171a;
    border: 1px solid #34343c;
    border-radius: 8px;
}
QLabel#helpFigureCap {
    color: #7f7f88;
    font-size: 11px;
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
    """26×26 圆角底 + 居中图标，和界面上那个按钮的样子一致。"""
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
    """词条卡：标题 + 说明；按钮类条目左侧放界面上的真图标。

    ``icon_name`` 只在「界面上确实有这个图标的按钮」时才传；纯说明条目传空串，
    右侧不会凭空长出一个图标——帮助里的图标必须能在界面上照着找到。
    """
    card = QFrame()
    card.setObjectName("helpCard")
    lay = QHBoxLayout(card)
    lay.setContentsMargins(10, 8, 10, 8)
    lay.setSpacing(10)
    if icon_name:
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


def _figure(name: str, width: int, caption: str = "") -> QFrame:
    """真截图：按宽度等比缩放，装在一个带边框的框里。

    截图是程序自己截的（gui/assets/help），不是仿制控件；点不了，
    但能让人一眼对上界面长什么样。
    """
    holder = QFrame()
    holder.setObjectName("helpFigure")
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(6, 6, 6, 6)
    lay.setSpacing(4)
    pic = QLabel()
    pic.setAlignment(Qt.AlignCenter)
    path = HELP_ASSETS / name
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        pic.setText("（截图缺失）")
        pic.setStyleSheet("color:#7f7f88;")
    else:
        pic.setPixmap(pixmap.scaledToWidth(width, Qt.SmoothTransformation))
    lay.addWidget(pic)
    if caption:
        cap = QLabel(caption)
        cap.setObjectName("helpFigureCap")
        cap.setStyleSheet("color:#7f7f88; font-size:11px;")
        cap.setWordWrap(True)
        cap.setAlignment(Qt.AlignCenter)
        lay.addWidget(cap)
    return holder


def _bullets(items: list[tuple[str, str]]) -> QWidget:
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(2, 0, 2, 0)
    lay.setSpacing(7)
    for head, desc in items:
        line = QLabel(
            f'<span style="color:#e2e2e6; font-weight:600;">{head}</span>'
            f'<br><span style="color:#96969e;">{desc}</span>'
        )
        line.setWordWrap(True)
        lay.addWidget(line)
    lay.addStretch(1)
    return box


def _figure_row(figure: QFrame, text: QWidget) -> QFrame:
    """左图右字：小截图配要点，横向排一行。"""
    row = QFrame()
    row.setObjectName("helpCardFlat")
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(12)
    lay.addWidget(figure, 0, Qt.AlignTop)
    lay.addWidget(text, 1)
    return row


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

        # ---- 界面一览 ----
        _section("界面一览", flow)
        flow.addWidget(
            _figure(
                "main.png",
                470,
                "标准窗口 · 正在播放本地音乐。截图不可点，点界面上的对应位置就行",
            )
        )
        flow.addWidget(
            _bullets(
                [
                    (
                        "① 顶栏",
                        "标题、播放 / 暂停、上一首 / 下一首、停止、音量、单曲循环 / 列表循环、"
                        "最小化 / 最大化 / 关闭",
                    ),
                    (
                        "② 搜索行",
                        "歌名、歌手、专辑、纯数字 ID、歌单链接都能进；"
                        "本地 / 缓存视图里它是筛选器",
                    ),
                    ("③ 音源", "客户端接口 / 网页接口 / 仅本地缓存 / 本地"),
                    (
                        "④ 结果列表",
                        "勾选、曲名、艺人、专辑、时长、大小；双击播放，右键有完整菜单",
                    ),
                    ("⑤ 底部操作栏", "音源、重新扫描（仅本地视图）、设置、下载 / 导出"),
                    ("⑥ 状态栏", "进度与结果汇总、账号、缓存 / 下载目录入口、命令行按钮"),
                ]
            )
        )

        # ---- 工具行 ----
        _section("搜索行与按钮", flow)
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
                        "",
                        "音源切换",
                        "客户端接口（推荐，享客户端权益）/ 网页接口 / 仅本地缓存（不发请求）/ "
                        "本地（放自己的音频文件，只播放不下载）",
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
                        "音质、转 mp3、缓存目录、缓存存档、本地音乐目录、关闭主面板、"
                        "启动视图、搜索历史",
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
        _section("结果列表", flow)
        flow.addWidget(
            _grid(
                [
                    _card(
                        "",
                        "勾选与导出",
                        "左侧方框决定导出哪些歌，点一下切换。缓存视图的按钮叫「导出」——"
                        "解密或联网补齐到下载目录；搜索 / 歌单视图叫「下载」",
                    ),
                    _card(
                        "",
                        "全选",
                        "没有独立按钮：点表头最左边的空白格，在全选和取消之间切换",
                    ),
                    _card(
                        "",
                        "右键菜单",
                        "播放 / 仅播放此曲 / 下载选中 / 仅下载此曲 / 在文件夹中显示 / "
                        "复制链接、歌名、ID / 全选、取消全选、清空 / 在浏览器打开",
                    ),
                    _card(
                        "",
                        "列头排序",
                        "点「曲名 / 艺人 / 专辑 / 时长」：升序 → 降序 → 原序；"
                        "当前列加粗带箭头",
                    ),
                    _card(
                        "",
                        "音质与进度列",
                        "缓存视图显示实际档位（320k 等）和缓存完成度；"
                        "已导出的歌显示「已导出」，不再显示缓存百分比",
                    ),
                    _card(
                        "",
                        "缓存 / 本地筛选",
                        "「仅本地缓存」和「本地」视图下，搜索框只过滤本地列表，"
                        "不发任何请求",
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
                        "",
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
                        "",
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
                        "",
                        "队列状态",
                        "每首显示进度 / 状态 / 大小：等待、下载中、完成、失败、"
                        "已取消、试听、无版权",
                    ),
                    _card(
                        "",
                        "失败重试",
                        "点状态栏汇总里的「失败 N」重下所有失败项；队列右键也有",
                    ),
                    _card(
                        "",
                        "队列右键",
                        "播放、打开所在文件夹、复制文件路径；双击行直接播放",
                    ),
                    _card(
                        "",
                        "转 mp3",
                        "设置里开启；按源码率上浮一档转码，不提升音质只换容器",
                    ),
                    _card(
                        "",
                        "紧凑队列卡",
                        "窄窗口里队列是一张卡：标题和列表同一个圆角框，按窗口高度显示 "
                        "3~5 行，点顶栏收起 / 展开",
                    ),
                    _card(
                        "check",
                        "完成后自动收成一行",
                        "全部跑完变成「N 首已完成」+ 清空；失败或暂停时保持展开",
                    ),
                ]
            )
        )

        # ---- 本地音乐 ----
        _section("本地音乐与拖拽", flow)
        flow.addWidget(
            _figure_row(
                _figure("mini.png", 230, "紧凑窗口 · 本地音乐"),
                _bullets(
                    [
                        ("音源选「本地」", "列出下载目录 + 设置里指定的音乐目录，"
                         "按标签显示曲名 / 艺人 / 专辑 / 时长 / 大小"),
                        ("双击播放", "右键是播放 / 在文件夹中显示 / 复制路径；"
                         "本地没有下载动作，勾选框和进度列也不显示"),
                        ("拖进来就播", "音频文件或整个文件夹拖到窗口上松手即播；"
                         "文件夹会递归收集，不是音频的文件不接受"),
                        ("重新扫描", "目录里加了新文件时点一下；有索引，通常一两秒"),
                        ("没有下载队列", "队列、音质选择和下载按钮都会收起来，"
                         "搜索框变成实时筛选；切回客户端 / 缓存自动还原"),
                    ]
                ),
            )
        )

        # ---- 缓存 ----
        _section("缓存说明", flow)
        flow.addWidget(
            _grid(
                [
                    _card(
                        "",
                        "缓存是什么",
                        "客户端在线播放留下的音频，本工具能还原成标准 m4a / mp3",
                    ),
                    _card(
                        "refresh-cw",
                        "实时刷新",
                        "客户端听新歌 / 清缓存，列表 2~3 秒内自动跟上",
                    ),
                    _card(
                        "",
                        "缓存目录",
                        "默认自动探测；客户端里改过目录时到「设置 → 缓存目录」指定",
                    ),
                    _card(
                        "",
                        "缓存存档",
                        "客户端只留最近几首；设置里开启存档后，听过的都会留底",
                    ),
                ]
            )
        )

        # ---- 关闭与托盘 ----
        _section("关闭与托盘", flow)
        flow.addWidget(
            _figure_row(
                _figure("tray.png", 190, "托盘右键菜单"),
                _bullets(
                    [
                        ("关闭 ×", "按设置里的「关闭主面板」决定：最小化到系统托盘，或者直接退出"),
                        ("最小化", "只是最小化到任务栏，不进托盘；"
                         "命令行和开发者面板不会被一起最小化"),
                        ("托盘菜单", "显示主窗口、播放 / 暂停、上一首 / 下一首、命令行、退出；"
                         "顶部一行是当前曲目"),
                        ("托盘左键", "点一下把主窗口叫回来；"
                         "关闭到托盘后下载和播放都在后台继续"),
                    ]
                ),
            )
        )

        # ---- 设置 ----
        _section("设置窗口", flow)
        flow.addWidget(
            _grid(
                [
                    _card(
                        "",
                        "缓存目录",
                        "自动探测客户端缓存；识别不出来时手动指定，旁边「诊断」能看"
                        "目录里到底有多少 .uc、多少已下完",
                    ),
                    _card(
                        "",
                        "本地音乐",
                        "选一个放歌的文件夹，和下载目录一起列在「本地」音源里；只读扫描",
                    ),
                    _card(
                        "x",
                        "关闭主面板",
                        "最小化到系统托盘 / 退出程序——只影响点右上角 × 的行为",
                    ),
                    _card(
                        "",
                        "启动视图 / 紧凑队列",
                        "下次启动用标准窗口还是紧凑模式；窄窗口里队列用折叠面板、"
                        "单行停靠还是不显示",
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
