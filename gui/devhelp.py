"""开发者面板的使用说明：条目式清单 + 搜索。

面板里控件多、几个按钮名字又接近，纯说明文档不好查。这里按「分组 + 条目」
列出来，每条写清在哪儿、干什么，顶部一个搜索框按功能名/按钮名/页面名直接筛。
条目带页面的还能一键跳过去。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.dialog import SHADOW, RoundDialog
from gui.style import QSS

# page: 点「跳到该页」时切到哪个页面；None 表示不涉及具体页面
# 顺序即展示顺序
HELP_ITEMS: list[dict] = [
    # ---- 概览 ----
    {
        "group": "概览",
        "name": "环境条",
        "where": "面板顶部",
        "desc": "一行看完运行环境：版本、Python / PyQt / Qt、播放后端、ffmpeg 是否就绪、"
        "缓存条目数，以及缓存目录和保存目录。远程排障先看这一行，一半问题能直接排除。",
        "page": None,
        "keys": "版本 python pyqt qt 后端 ffmpeg 缓存目录 保存目录 环境",
    },
    {
        "group": "概览",
        "name": "导航按钮",
        "where": "右侧顶部一行",
        "desc": "状态 / 日志 / 问题 / 组件 / 指令 / 环境 六个页面互斥切换。"
        "「问题」上有未处理的警告或错误时，按钮会变红并显示条数。",
        "page": None,
        "keys": "页签 切换 导航 问题徽标 红色 计数",
    },
    {
        "group": "概览",
        "name": "帮助按钮",
        "where": "导航行最右的 ? 图标",
        "desc": "就是当前这个窗口。搜索框里输入功能名、按钮名或页面名都能筛，"
        "带「跳到该页」的条目点一下会直接切到对应页面。",
        "page": None,
        "keys": "帮助 说明 搜索 图标 circle-help",
    },
    # ---- 时间线 ----
    {
        "group": "时间线（左侧）",
        "name": "范围筛选",
        "where": "左侧顶部下拉框",
        "desc": "只显示某个范围的步骤：搜索 / 缓存 / 下载 / 播放 / 接口 / 任务。"
        "排查某一块时先选范围，剩下的杂项就不占屏了。",
        "page": 1,
        "keys": "范围 筛选 搜索 缓存 下载 播放 接口 任务 scope",
    },
    {
        "group": "时间线（左侧）",
        "name": "只看失败",
        "where": "范围下拉框右侧",
        "desc": "只保留状态为「失败」的步骤。批量下载出错时，勾上它就能立刻看到"
        "哪一步炸的、错误是什么。",
        "page": 1,
        "keys": "只看失败 过滤 fail 红色",
    },
    {
        "group": "时间线（左侧）",
        "name": "步数",
        "where": "「只看失败」右边",
        "desc": "当前累计收到的步骤总数（含被筛掉的），用来判断事件是不是还在进来。"
        "数字不涨说明确实没动作，不是面板卡了。",
        "page": 1,
        "keys": "步数 计数 统计",
    },
    {
        "group": "时间线（左侧）",
        "name": "导入回放",
        "where": "左侧顶部按钮",
        "desc": "读诊断包 zip 里的 steps.txt（或单独的 txt），把别人的现场按时间轴重放出来。"
        "回放期间实时事件暂停接收，按钮变成「退出回放」，点一下清空并恢复实时。",
        "page": 1,
        "keys": "回放 导入 steps.txt 诊断包 zip 离线 复现",
    },
    {
        "group": "时间线（左侧）",
        "name": "自动跟随",
        "where": "左侧顶部勾选框",
        "desc": "勾选时新事件自动滚到底；取消后新事件只追加、不动滚动条，"
        "往回翻历史不会被弹走。",
        "page": 1,
        "keys": "自动跟随 滚动 停在原地 翻历史",
    },
    {
        "group": "时间线（左侧）",
        "name": "清空",
        "where": "左侧顶部按钮",
        "desc": "清空时间线、日志窗口和问题列表。只清面板里的显示，"
        "磁盘上的 app.log 不受影响。",
        "page": 1,
        "keys": "清空 清除 clear 重置",
    },
    {
        "group": "时间线（左侧）",
        "name": "重复事件合并",
        "where": "时间线内部",
        "desc": "连续相同（范围、步骤、结果、说明都一样）且间隔 8 秒内的事件不新增行，"
        "在原行后面标 ×N。缓存自动刷新就是靠这个不刷屏的，数据一条没丢。",
        "page": 1,
        "keys": "合并 重复 × 次数 刷屏 xN",
    },
    {
        "group": "时间线（左侧）",
        "name": "分组折叠",
        "where": "时间线内部",
        "desc": "下载类事件按歌名折叠成一行父节点，展开能看到取址、下载、转码、写标签"
        "各步的耗时和结果。一眼看出卡在哪一步。",
        "page": 1,
        "keys": "分组 折叠 歌曲 步骤 展开",
    },
    # ---- 状态页 ----
    {
        "group": "状态页",
        "name": "播放器",
        "where": "状态页第一块",
        "desc": "曲名、进度条、播放状态与 mediaStatus 编号、播放时间。"
        "放不了时进度条会变红，缓冲时停在原地不动。",
        "page": 0,
        "keys": "播放器 进度条 曲名 mediastatus 状态 时间",
    },
    {
        "group": "状态页",
        "name": "播放器信息行",
        "where": "进度条下方",
        "desc": "来源（本地 / 网络）、文件名与大小、兼容播放开关、"
        "「无法直接播放：是/否」——针对当前这一首的实测判断，没有文件时显示 —。",
        "page": 0,
        "keys": "来源 本地 网络 文件大小 兼容播放 无法直接播放 是 否",
    },
    {
        "group": "状态页",
        "name": "任务",
        "where": "状态页第二块",
        "desc": "批量下载（进度、并发、是否暂停）、搜索是否在跑、缓存扫描上次的文件数与耗时。"
        "显示「空闲」时还会带上一批的结果，用来判断它到底跑没跑。",
        "page": 0,
        "keys": "任务 批量下载 搜索 缓存扫描 并发 暂停 上次",
    },
    {
        "group": "状态页",
        "name": "队列",
        "where": "状态页第三块",
        "desc": "每首一行：名称、进度条、状态词（等待 / 取址中 / 百分比 / 完成 / 失败）。"
        "失败整行变红。最多显示 40 首，超出的会提示去主窗口看完整进度。",
        "page": 0,
        "keys": "队列 进度 每首 等待 取址中 完成 失败 40",
    },
    {
        "group": "状态页",
        "name": "最近错误",
        "where": "状态页底部（有错才出现）",
        "desc": "5 分钟内的最后一条警告或错误。没有就整条不显示，"
        "避免很早以前的旧错误一直挂在那儿误导人。",
        "page": 0,
        "keys": "错误 警告 最近 底部 5 分钟",
    },
    # ---- 日志页 ----
    {
        "group": "日志页",
        "name": "级别过滤",
        "where": "日志页上方左侧",
        "desc": "信息以上 / 只看警告 / 只看错误。默认「信息以上」。",
        "page": 1,
        "keys": "级别 过滤 info warning error 日志等级",
    },
    {
        "group": "日志页",
        "name": "关键字过滤",
        "where": "日志页上方输入框",
        "desc": "同时过滤日志和时间线步骤（步骤按范围、名称、说明匹配）。"
        "cookie、token、csrf 这类内容显示时已经是 ***。",
        "page": 1,
        "keys": "关键字 搜索 过滤 找 关键字",
    },
    {
        "group": "日志页",
        "name": "暂停",
        "where": "日志页上方右侧",
        "desc": "冻结视图看现场。暂停期间的数据会攒着不丢，取消暂停后自动补上。",
        "page": 1,
        "keys": "暂停 冻结 停止滚动 看现场",
    },
    # ---- 问题页 ----
    {
        "group": "问题页",
        "name": "问题列表",
        "where": "问题页上半",
        "desc": "所有警告和错误（最多 800 条）。点某一条，下半部分显示它的完整内容，"
        "比在长日志里翻快得多。",
        "page": 2,
        "keys": "问题 错误 警告 列表 详情",
    },
    {
        "group": "问题页",
        "name": "问题徽标",
        "where": "导航行「问题」按钮",
        "desc": "有任何警告或错误时整个按钮变红并显示条数，不用点进去也能发现出过问题。",
        "page": 2,
        "keys": "徽标 红色 计数 提醒",
    },
    # ---- 组件页 ----
    {
        "group": "组件页",
        "name": "ffmpeg 路径",
        "where": "组件页第一块",
        "desc": "显示当前真正在用的 ffmpeg 可执行文件。找不到时整行变红，"
        "下载转码、兼容播放都会失效。",
        "page": 3,
        "keys": "ffmpeg 路径 转码 找不到 红色",
    },
    {
        "group": "组件页",
        "name": "更换 ffmpeg",
        "where": "组件页「更换…」按钮",
        "desc": "选一个可执行文件。只接受文件名里带 ffmpeg 的，并先跑一次 -version 校验，"
        "通过才写进设置。杀毒误删、想换新版、绿色版没带 ffmpeg 都用它。",
        "page": 3,
        "keys": "更换 替换 ffmpeg exe 选择 路径 override",
    },
    {
        "group": "组件页",
        "name": "测试 ffmpeg",
        "where": "组件页「测试」按钮",
        "desc": "对当前 ffmpeg 跑一次 -version，结果显示在面板底部状态栏。"
        "在后台线程执行，卡住的 exe 不会把界面冻住。",
        "page": 3,
        "keys": "测试 ffmpeg version 校验 验证",
    },
    {
        "group": "组件页",
        "name": "恢复默认 ffmpeg",
        "where": "组件页「恢复默认」按钮",
        "desc": "清掉手动指定的路径，回到默认查找顺序：程序自带 → PATH → Python 包自带。",
        "page": 3,
        "keys": "恢复 默认 ffmpeg 重置",
    },
    {
        "group": "组件页",
        "name": "播放后端",
        "where": "组件页下拉框",
        "desc": "Media Foundation（推荐）/ 仅 MF / 仅 DirectShow。写进设置，"
        "重启程序才生效——这个环境变量是进程级的，没法热切。",
        "page": 3,
        "keys": "播放后端 backend media foundation directshow dsengine windows 重启",
    },
    {
        "group": "组件页",
        "name": "兼容播放",
        "where": "组件页勾选框 / 设置",
        "desc": "系统放不了的格式，先用内置 ffmpeg 无损转成 WAV 再播。默认关闭；"
        "首转 1~3 秒，临时占用磁盘。关闭时只提示，不会擅自转换。",
        "page": 3,
        "keys": "兼容播放 转 wav 转码 开关 compat",
    },
    {
        "group": "组件页",
        "name": "解码能力",
        "where": "组件页底部",
        "desc": "本进程里已经确认无法直接播放的格式列表。空着说明到现在为止都还顺利。",
        "page": 3,
        "keys": "解码 能力 格式 无法直接播放 m4a flac",
    },
    # ---- 指令页 ----
    {
        "group": "指令页",
        "name": "执行命令",
        "where": "指令页底部输入框",
        "desc": "输入后按回车或点「执行」。输出在上方独立的框里，不会和日志混在一起。"
        "回车只执行当前命令，不会误触其他按钮。",
        "page": 4,
        "keys": "指令 命令 回车 执行 console",
    },
    {
        "group": "指令页",
        "name": "help",
        "where": "指令",
        "desc": "列出全部命令和一行说明。忘了命令名就敲它。",
        "page": 4,
        "keys": "help 帮助 命令列表",
    },
    {
        "group": "指令页",
        "name": "status",
        "where": "指令",
        "desc": "把状态页的内容原样打印到输出框，方便复制给别人。",
        "page": 4,
        "keys": "status 状态 打印 复制",
    },
    {
        "group": "指令页",
        "name": "scan / cache clear",
        "where": "指令",
        "desc": "scan 触发一次客户端缓存重扫；cache clear 清空 play-cache 里的解码与转码产物。",
        "page": 4,
        "keys": "scan 扫描 cache clear 清理 播放缓存",
    },
    {
        "group": "指令页",
        "name": "compat on|off",
        "where": "指令",
        "desc": "开关兼容播放，和组件页的勾选框是同一个设置。不带参数时只显示当前状态。",
        "page": 4,
        "keys": "compat 兼容播放 on off 开关",
    },
    {
        "group": "指令页",
        "name": "backend",
        "where": "指令",
        "desc": "显示当前播放后端、无法直接播放的格式、ffmpeg 路径。",
        "page": 4,
        "keys": "backend 后端 解码 格式 ffmpeg",
    },
    {
        "group": "指令页",
        "name": "source client|web|cache",
        "where": "指令",
        "desc": "切换音源，等价于点主界面的音源下拉框。不带参数时显示当前音源。",
        "page": 4,
        "keys": "source 音源 client web cache 切换",
    },
    {
        "group": "指令页",
        "name": "log debug|info",
        "where": "指令",
        "desc": "临时调整日志级别。debug 会多出很多细节，排完记得改回 info。",
        "page": 4,
        "keys": "log 日志级别 debug info",
    },
    {
        "group": "指令页",
        "name": "url <歌曲id> [音质]",
        "where": "指令",
        "desc": "查某首歌的取址结果：容器、实际码率、大小、URL 前 160 字。"
        "在后台线程执行，不卡界面。用来判断「为什么这首下不了」。",
        "page": 4,
        "keys": "url 取址 地址 码率 大小 歌曲 id 探针",
    },
    {
        "group": "指令页",
        "name": "diag / open",
        "where": "指令",
        "desc": "diag 导出诊断包（等同右下角按钮）；open cache|download|data|log "
        "打开对应目录或日志文件。",
        "page": 4,
        "keys": "diag 诊断包 open 打开 目录 日志",
    },
    # ---- 环境页 ----
    {
        "group": "环境页",
        "name": "完整环境与配置",
        "where": "环境页",
        "desc": "比顶部环境条更全：系统版本、完整路径、缓存统计、磁盘剩余，"
        "以及全部配置项（cookie、token 这类已打码，二进制值只显示字节数）。",
        "page": 5,
        "keys": "环境 配置 settings 磁盘 剩余 路径 打码",
    },
    # ---- 快捷操作 ----
    {
        "group": "快捷操作与诊断",
        "name": "打开缓存 / 下载 / 配置目录",
        "where": "面板底部一行",
        "desc": "直接在资源管理器里打开对应目录。缓存目录没设置时会提示。",
        "page": None,
        "keys": "打开 目录 缓存 下载 配置 folder 资源管理器",
    },
    {
        "group": "快捷操作与诊断",
        "name": "清空播放缓存",
        "where": "面板底部一行",
        "desc": "删掉 play-cache 里的解码与转码产物，播放时按需重新生成。"
        "不碰下载目录里的成品。",
        "page": None,
        "keys": "清空 播放缓存 play-cache 删除 空间",
    },
    {
        "group": "快捷操作与诊断",
        "name": "重新扫描缓存",
        "where": "面板底部一行",
        "desc": "触发主窗口重扫客户端缓存，等同切换一次音源。卡在扫描中时用它。",
        "page": None,
        "keys": "重新 扫描 缓存 rescan",
    },
    {
        "group": "快捷操作与诊断",
        "name": "打开日志文件",
        "where": "右下角按钮",
        "desc": "用系统默认程序打开 app.log。文件还没生成时会提示。",
        "page": None,
        "keys": "打开 日志 app.log 文件",
    },
    {
        "group": "快捷操作与诊断",
        "name": "复制诊断信息",
        "where": "右下角按钮",
        "desc": "环境 + 最近 120 行日志 + 最近 40 条问题，直接复制到剪贴板，粘贴给别人就行。",
        "page": None,
        "keys": "复制 诊断 信息 剪贴板 粘贴",
    },
    {
        "group": "快捷操作与诊断",
        "name": "导出诊断包",
        "where": "右下角按钮",
        "desc": "生成 zip：env.txt、settings.txt、steps.txt、errors.txt、app.log（尾部 512 KB），"
        "导出后自动打开所在文件夹。敏感字段已脱敏。",
        "page": None,
        "keys": "导出 诊断包 zip 上报 反馈 diag",
    },
    {
        "group": "快捷操作与诊断",
        "name": "关闭面板",
        "where": "右下角按钮 / 标题栏 ×",
        "desc": "关闭面板时日志 Handler 和事件监听会一起摘掉，app.log 照常写。"
        "下次打开会重新挂上，不会漏事件。",
        "page": None,
        "keys": "关闭 退出 x 面板",
    },
    # ---- 操作提示 ----
    {
        "group": "操作提示",
        "name": "移动窗口",
        "where": "标题栏",
        "desc": "按住标题栏任意空白处拖动。",
        "page": None,
        "keys": "移动 拖动 标题栏 位置",
    },
    {
        "group": "操作提示",
        "name": "调整大小",
        "where": "右下角手柄",
        "desc": "拖动右下角的手柄改窗口大小，时间线和右侧内容会按 3:2 分。",
        "page": None,
        "keys": "调整 大小 拉伸 缩放 手柄",
    },
    {
        "group": "操作提示",
        "name": "Esc",
        "where": "键盘",
        "desc": "按 Esc 关闭当前窗口（帮助或面板）。",
        "page": None,
        "keys": "esc 键盘 关闭 快捷键",
    },
]

_CSS = """
QLabel#helpName { color: #e8e8ea; font-size: 12px; }
QLabel#helpWhere { color: #7a7a82; font-size: 11px; }
QLabel#helpDesc { color: #b9b9c2; font-size: 12px; }
QLabel#helpHead { color: #96969e; font-size: 11px; }
QLineEdit#helpSearch {
    background: #242428; color: #d8d8dc; border: 1px solid #3a3a42;
    border-radius: 5px; padding: 5px 8px;
}
"""


class DevHelpDialog(RoundDialog):
    def __init__(self, parent=None, on_goto=None):
        super().__init__(parent, "开发者面板 · 帮助")
        self._on_goto = on_goto
        self._cards: list[dict] = []
        self._headers: dict[str, QLabel] = {}
        self.setStyleSheet(QSS + _CSS)
        self.resize(780 + SHADOW * 2, 620 + SHADOW * 2)

        body = self.body_layout
        body.setContentsMargins(14, 6, 14, 12)
        body.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("helpSearch")
        self.search.setPlaceholderText("搜索功能名、按钮名、页面，例如：回放 / ffmpeg / 队列")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search, 1)
        self.count = QLabel(f"{len(HELP_ITEMS)} 条")
        self.count.setStyleSheet("color:#96969e;")
        bar.addWidget(self.count)
        body.addLayout(bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: 0; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(8)
        self._build_cards(holder_layout)
        holder_layout.addStretch(1)
        scroll.setWidget(holder)
        body.addWidget(scroll, 1)

        tip = QLabel("点击条目右侧的「跳到该页」可以直接切到对应页面；本窗口不影响面板运行。")
        tip.setStyleSheet("color:#7a7a82; font-size:11px;")
        body.addWidget(tip)

    def _build_cards(self, layout: QVBoxLayout) -> None:
        current = None
        for item in HELP_ITEMS:
            if item["group"] != current:
                current = item["group"]
                if self._headers:
                    # 分组之间留白，卡片挨着上一组的最后一条会看不清层级
                    layout.addSpacing(6)
                head = QLabel(current)
                head.setObjectName("helpHead")
                layout.addWidget(head)
                layout.addSpacing(2)
                self._headers[current] = head

            card = QWidget()
            card.setObjectName("helpCard")
            # 普通 QWidget 画 QSS 背景要开这个属性；而且祖先样式表里的通用
            # QWidget 规则优先级压过了 ID 规则（实测底色一直是窗口色），
            # 所以这里直接用控件自身样式表，最稳
            card.setAttribute(Qt.WA_StyledBackground, True)
            card.setStyleSheet(
                "QWidget#helpCard { background: #2a2a30;"
                " border: 1px solid #3f3f48; border-radius: 6px; }"
            )
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 7, 10, 8)
            card_layout.setSpacing(3)

            head_row = QHBoxLayout()
            head_row.setSpacing(8)
            name = QLabel(item["name"])
            name.setObjectName("helpName")
            head_row.addWidget(name)
            head_row.addStretch(1)
            where = QLabel(item["where"])
            where.setObjectName("helpWhere")
            head_row.addWidget(where)
            if item.get("page") is not None:
                btn = QPushButton("跳到该页")
                btn.setCursor(Qt.PointingHandCursor)
                btn.setFixedHeight(24)
                btn.clicked.connect(
                    lambda _checked=False, page=item["page"]: self._goto(page)
                )
                head_row.addWidget(btn)
            card_layout.addLayout(head_row)

            desc = QLabel(item["desc"])
            desc.setObjectName("helpDesc")
            desc.setWordWrap(True)
            card_layout.addWidget(desc)

            layout.addWidget(card)
            blob = " ".join(
                (item["group"], item["name"], item["where"], item["desc"], item.get("keys", ""))
            ).lower()
            self._cards.append({"card": card, "group": item["group"], "blob": blob})

    def _goto(self, page: int) -> None:
        if self._on_goto is not None:
            self._on_goto(page)
        self.close()

    def _apply_filter(self, _text: str = "") -> None:
        key = self.search.text().strip().lower()
        shown = 0
        per_group: dict[str, int] = {}
        for item in self._cards:
            match = not key or key in item["blob"]
            item["card"].setVisible(match)
            if match:
                shown += 1
                per_group[item["group"]] = per_group.get(item["group"], 0) + 1
        for group, head in self._headers.items():
            head.setVisible(per_group.get(group, 0) > 0)
        self.count.setText(
            f"{shown} / {len(self._cards)} 条" if key else f"{len(self._cards)} 条"
        )
