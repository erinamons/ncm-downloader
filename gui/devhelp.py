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
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.dialog import SHADOW, RoundDialog
from gui.style import QSS

# 每条：group 分组 / name 名称 / where 在哪儿 / desc 干什么 / keys 搜索用的同义词
# 顺序即展示顺序
HELP_ITEMS: list[dict] = [
    # ---- 概览 ----
    {
        "group": "概览",
        "name": "环境条",
        "where": "面板顶部",
        "desc": "一行看完运行环境：版本、Python / PyQt / Qt、播放后端、ffmpeg 是否就绪、"
        "缓存条目数，以及缓存目录和保存目录。远程排障先看这一行，一半问题能直接排除。",
        "keys": "版本 python pyqt qt 后端 ffmpeg 缓存目录 保存目录 环境",
    },
    {
        "group": "概览",
        "name": "导航按钮",
        "where": "右侧顶部一行",
        "desc": "状态 / 日志 / 问题 / 组件 / 命令行 / 环境 六个页面互斥切换。"
        "「问题」上有未处理的警告或错误时，按钮会变红并显示条数。",
        "keys": "页签 切换 导航 问题徽标 红色 计数",
    },
    {
        "group": "概览",
        "name": "帮助按钮",
        "where": "导航行最右的 ? 图标",
        "desc": "就是当前这个窗口。搜索框里输入功能名、按钮名或页面名都能筛，"
        "也认同义词（比如搜 backend 能找到播放后端）。",
        "keys": "帮助 说明 搜索 图标 circle-help",
    },
    # ---- 时间线 ----
    {
        "group": "时间线（左侧）",
        "name": "范围筛选",
        "where": "左侧顶部下拉框",
        "desc": "只显示某个范围的步骤：搜索 / 缓存 / 下载 / 播放 / 接口 / 任务。"
        "排查某一块时先选范围，剩下的杂项就不占屏了。",
        "keys": "范围 筛选 搜索 缓存 下载 播放 接口 任务 scope",
    },
    {
        "group": "时间线（左侧）",
        "name": "只看失败",
        "where": "范围下拉框右侧",
        "desc": "只保留状态为「失败」的步骤。批量下载出错时，勾上它就能立刻看到"
        "哪一步炸的、错误是什么。",
        "keys": "只看失败 过滤 fail 红色",
    },
    {
        "group": "时间线（左侧）",
        "name": "步数",
        "where": "「只看失败」右边",
        "desc": "当前累计收到的步骤总数（含被筛掉的），用来判断事件是不是还在进来。"
        "数字不涨说明确实没动作，不是面板卡了。",
        "keys": "步数 计数 统计",
    },
    {
        "group": "时间线（左侧）",
        "name": "导入回放",
        "where": "左侧顶部按钮",
        "desc": "读诊断包 zip 里的 steps.txt（或单独的 txt），把别人的现场按时间轴重放出来。"
        "回放期间实时事件暂停接收，按钮变成「退出回放」，点一下清空并恢复实时。",
        "keys": "回放 导入 steps.txt 诊断包 zip 离线 复现",
    },
    {
        "group": "时间线（左侧）",
        "name": "自动跟随",
        "where": "左侧顶部勾选框",
        "desc": "勾选时新事件自动滚到底；取消后新事件只追加、不动滚动条，"
        "往回翻历史不会被弹走。",
        "keys": "自动跟随 滚动 停在原地 翻历史",
    },
    {
        "group": "时间线（左侧）",
        "name": "清空",
        "where": "左侧顶部按钮",
        "desc": "清空时间线、日志窗口和问题列表。只清面板里的显示，"
        "磁盘上的 app.log 不受影响。",
        "keys": "清空 清除 clear 重置",
    },
    {
        "group": "时间线（左侧）",
        "name": "重复事件合并",
        "where": "时间线内部",
        "desc": "连续相同（范围、步骤、结果、说明都一样）且间隔 8 秒内的事件不新增行，"
        "在原行后面标 ×N。缓存自动刷新就是靠这个不刷屏的，数据一条没丢。",
        "keys": "合并 重复 × 次数 刷屏 xN",
    },
    {
        "group": "时间线（左侧）",
        "name": "分组折叠",
        "where": "时间线内部",
        "desc": "下载类事件按歌名折叠成一行父节点，展开能看到取址、下载、转码、写标签"
        "各步的耗时和结果。一眼看出卡在哪一步。",
        "keys": "分组 折叠 歌曲 步骤 展开",
    },
    # ---- 状态页 ----
    {
        "group": "状态页",
        "name": "播放器",
        "where": "状态页第一块",
        "desc": "曲名、进度条、播放状态与 mediaStatus 编号、播放时间。"
        "放不了时进度条会变红，缓冲时停在原地不动。",
        "keys": "播放器 进度条 曲名 mediastatus 状态 时间",
    },
    {
        "group": "状态页",
        "name": "播放器信息行",
        "where": "进度条下方",
        "desc": "来源（本地 / 网络）、文件名与大小、兼容播放开关、"
        "「无法直接播放：是/否」——针对当前这一首的实测判断，没有文件时显示 —。",
        "keys": "来源 本地 网络 文件大小 兼容播放 无法直接播放 是 否",
    },
    {
        "group": "状态页",
        "name": "任务",
        "where": "状态页第二块",
        "desc": "批量下载（进度、并发、是否暂停）、搜索是否在跑、缓存扫描上次的文件数与耗时。"
        "显示「空闲」时还会带上一批的结果，用来判断它到底跑没跑。",
        "keys": "任务 批量下载 搜索 缓存扫描 并发 暂停 上次",
    },
    {
        "group": "状态页",
        "name": "队列",
        "where": "状态页第三块",
        "desc": "每首一行：名称、进度条、状态词（等待 / 取址中 / 百分比 / 完成 / 失败）。"
        "失败整行变红。最多显示 40 首，超出的会提示去主窗口看完整进度。",
        "keys": "队列 进度 每首 等待 取址中 完成 失败 40",
    },
    {
        "group": "状态页",
        "name": "最近错误",
        "where": "状态页底部（有错才出现）",
        "desc": "5 分钟内的最后一条警告或错误。没有就整条不显示，"
        "避免很早以前的旧错误一直挂在那儿误导人。",
        "keys": "错误 警告 最近 底部 5 分钟",
    },
    # ---- 日志页 ----
    {
        "group": "日志页",
        "name": "级别过滤",
        "where": "日志页上方左侧",
        "desc": "信息以上 / 只看警告 / 只看错误。默认「信息以上」。",
        "keys": "级别 过滤 info warning error 日志等级",
    },
    {
        "group": "日志页",
        "name": "关键字过滤",
        "where": "日志页上方输入框",
        "desc": "同时过滤日志和时间线步骤（步骤按范围、名称、说明匹配）。"
        "cookie、token、csrf 这类内容显示时已经是 ***。",
        "keys": "关键字 搜索 过滤 找 关键字",
    },
    {
        "group": "日志页",
        "name": "暂停",
        "where": "日志页上方右侧",
        "desc": "冻结视图看现场。暂停期间的数据会攒着不丢，取消暂停后自动补上。",
        "keys": "暂停 冻结 停止滚动 看现场",
    },
    # ---- 问题页 ----
    {
        "group": "问题页",
        "name": "问题列表",
        "where": "问题页上半",
        "desc": "所有警告和错误（最多 800 条）。点某一条，下半部分显示它的完整内容，"
        "比在长日志里翻快得多。",
        "keys": "问题 错误 警告 列表 详情",
    },
    {
        "group": "问题页",
        "name": "问题徽标",
        "where": "导航行「问题」按钮",
        "desc": "有任何警告或错误时整个按钮变红并显示条数，不用点进去也能发现出过问题。",
        "keys": "徽标 红色 计数 提醒",
    },
    {
        "group": "问题页",
        "name": "常驻记录",
        "where": "程序启动即生效",
        "desc": "日志、流程事件、警告和异常从程序启动就开始采集，不需要先打开面板。"
        "打开面板时会把这段时间的历史补进日志页，用一条分隔线标出「以下是实时」；"
        "页面上限内的记录不会重复显示，反复开关面板也一样。",
        "usage": "日志页：──── 以上为启动以来的记录 · 以下实时 ────\n"
        "问题页：警告以上（含未捕获异常、Qt 自身的告警）",
        "notes": "缓冲上限：日志 800 条、事件 400 条、问题 200 条，超出后丢最早的。"
        "同一条消息 5 秒内重复只记一次，避免绘制异常把缓冲刷满。"
        "文件日志 app.log 一直在写，和面板开关无关。",
        "keys": "常驻 记录 历史 面板没打开 启动 采集 异常 回溯 补记录",
    },
    {
        "group": "问题页",
        "name": "复制现场",
        "where": "问题页右上按钮",
        "desc": "把「当前现场」复制到剪贴板：版本、运行时长、音源、音质、列表与勾选数、"
        "队列行数、批次是否在跑、紧凑模式、进过哪些模拟数据，再加最近 12 条流程事件"
        "和最近问题。贴出来就能复现大半问题。",
        "usage": "点一下 → 剪贴板里是一段纯文本",
        "notes": "cookie / token 一类字段已打码，可以直接贴到公开的地方。",
        "keys": "复制 现场 快照 上报 反馈 context copy",
    },
    {
        "group": "问题页",
        "name": "打开崩溃现场",
        "where": "问题页右上按钮",
        "desc": "打开 diagnostics\\last-crash-*.txt：出现未捕获异常或 Qt 致命错误时"
        "自动落盘的文件，含现场、最近 80 条日志、最近 40 条问题。"
        "「上次运行没有正常退出」的横幅就是靠运行时状态文件判断的。",
        "notes": "最多保留最近 3 份；正常退出不会生成。没有文件时按钮会打开 diagnostics 目录。",
        "keys": "崩溃 崩溃现场 闪退 last-crash diagnostics 上次异常退出",
    },
    # ---- 组件页 ----
    {
        "group": "组件页",
        "name": "ffmpeg 路径",
        "where": "组件页第一块",
        "desc": "显示当前真正在用的 ffmpeg 可执行文件。找不到时整行变红，"
        "下载转码、兼容播放都会失效。",
        "keys": "ffmpeg 路径 转码 找不到 红色",
    },
    {
        "group": "组件页",
        "name": "更换 ffmpeg",
        "where": "组件页「更换…」按钮",
        "desc": "选一个可执行文件。只接受文件名里带 ffmpeg 的，并先跑一次 -version 校验，"
        "通过才写进设置。杀毒误删、想换新版、绿色版没带 ffmpeg 都用它。",
        "keys": "更换 替换 ffmpeg exe 选择 路径 override",
    },
    {
        "group": "组件页",
        "name": "测试 ffmpeg",
        "where": "组件页「测试」按钮",
        "desc": "对当前 ffmpeg 跑一次 -version，结果显示在面板底部状态栏。"
        "在后台线程执行，卡住的 exe 不会把界面冻住。",
        "keys": "测试 ffmpeg version 校验 验证",
    },
    {
        "group": "组件页",
        "name": "恢复默认 ffmpeg",
        "where": "组件页「恢复默认」按钮",
        "desc": "清掉手动指定的路径，回到默认查找顺序：程序自带 → PATH → Python 包自带。",
        "keys": "恢复 默认 ffmpeg 重置",
    },
    {
        "group": "组件页",
        "name": "播放后端",
        "where": "组件页下拉框",
        "desc": "Media Foundation（推荐）/ 仅 MF / 仅 DirectShow。写进设置，"
        "重启程序才生效——这个环境变量是进程级的，没法热切。",
        "keys": "播放后端 backend media foundation directshow dsengine windows 重启",
    },
    {
        "group": "组件页",
        "name": "兼容播放",
        "where": "组件页勾选框 / 设置",
        "desc": "系统放不了的格式，先用内置 ffmpeg 无损转成 WAV 再播。默认关闭；"
        "首转 1~3 秒，临时占用磁盘。关闭时只提示，不会擅自转换。",
        "keys": "兼容播放 转 wav 转码 开关 compat",
    },
    {
        "group": "组件页",
        "name": "解码能力",
        "where": "组件页底部",
        "desc": "本进程里已经确认无法直接播放的格式列表。空着说明到现在为止都还顺利。",
        "keys": "解码 能力 格式 无法直接播放 m4a flac",
    },
    # ---- 环境页 ----
    {
        "group": "环境页",
        "name": "完整环境与配置",
        "where": "环境页",
        "desc": "比顶部环境条更全：系统版本、完整路径、缓存统计、磁盘剩余，"
        "以及全部配置项（cookie、token 这类已打码，二进制值只显示字节数）。",
        "keys": "环境 配置 settings 磁盘 剩余 路径 打码",
    },
    # ---- 快捷操作 ----
    {
        "group": "快捷操作与诊断",
        "name": "打开缓存 / 下载 / 配置目录",
        "where": "面板底部一行",
        "desc": "直接在资源管理器里打开对应目录。缓存目录没设置时会提示。",
        "keys": "打开 目录 缓存 下载 配置 folder 资源管理器",
    },
    {
        "group": "快捷操作与诊断",
        "name": "清空播放缓存",
        "where": "面板底部一行",
        "desc": "删掉 play-cache 里的解码与转码产物，播放时按需重新生成。"
        "不碰下载目录里的成品。",
        "keys": "清空 播放缓存 play-cache 删除 空间",
    },
    {
        "group": "快捷操作与诊断",
        "name": "重新扫描缓存",
        "where": "面板底部一行",
        "desc": "触发主窗口重扫客户端缓存，等同切换一次音源。卡在扫描中时用它。",
        "keys": "重新 扫描 缓存 rescan",
    },
    {
        "group": "快捷操作与诊断",
        "name": "打开日志文件",
        "where": "右下角按钮",
        "desc": "用系统默认程序打开 app.log。文件还没生成时会提示。",
        "keys": "打开 日志 app.log 文件",
    },
    {
        "group": "快捷操作与诊断",
        "name": "复制诊断信息",
        "where": "右下角按钮",
        "desc": "环境 + 最近 120 行日志 + 最近 40 条问题，直接复制到剪贴板，粘贴给别人就行。",
        "keys": "复制 诊断 信息 剪贴板 粘贴",
    },
    {
        "group": "快捷操作与诊断",
        "name": "导出诊断包",
        "where": "右下角按钮",
        "desc": "生成 zip：env.txt、context.txt（现场）、settings.txt、steps.txt、"
        "errors.txt、last-crash.txt（有崩溃时）、app.log（尾部 512 KB），"
        "导出后自动打开所在文件夹。敏感字段已脱敏。",
        "keys": "导出 诊断包 zip 上报 反馈 diag",
    },
    {
        "group": "快捷操作与诊断",
        "name": "关闭面板",
        "where": "右下角按钮 / 标题栏 ×",
        "desc": "关闭面板只是把面板自己的实时 Handler 摘掉，省点开销；"
        "常驻记录（环形缓冲 + 异常钩子 + app.log）一直在跑，下次打开会把这段时间"
        "补回日志页，不会漏事件。",
        "keys": "关闭 退出 x 面板",
    },
    # ---- 操作提示 ----
    {
        "group": "操作提示",
        "name": "移动窗口",
        "where": "标题栏",
        "desc": "按住标题栏任意空白处拖动。",
        "keys": "移动 拖动 标题栏 位置",
    },
    {
        "group": "操作提示",
        "name": "调整大小",
        "where": "右下角手柄",
        "desc": "拖动右下角的手柄改窗口大小，时间线和右侧内容会按 3:2 分。",
        "keys": "调整 大小 拉伸 缩放 手柄",
    },
    {
        "group": "操作提示",
        "name": "Esc",
        "where": "键盘",
        "desc": "按 Esc 关闭当前窗口（帮助或面板）。",
        "keys": "esc 键盘 关闭 快捷键",
    },
]

# 命令行手册：一条命令一张卡，写法照编程语言文档来——语法 / 示例 / 注意。
# 这里的文案和 devtools._commands() 一一对应，改命令时两边一起改。
COMMAND_DOCS = [
    {
        "name": "怎么用命令行",
        "desc": "两种入口共用同一份输出与历史：① 独立窗口（Ctrl+` 或状态栏右侧的 >_ 按钮、"
        "托盘「命令行…」）；② 开发者面板的「命令行」页。输入框敲命令，回车或点「执行」。",
        "usage": "Ctrl+`            开关独立窗口（Esc 隐藏，不销毁）\n"
        "? / F1            打开命令帮助（窗口右上角问号，只列命令与设置项）\n"
        "↑ / ↓             翻历史命令\n"
        "Tab               补全命令名\n"
        "history clear     清空历史（历史会存到下次启动）",
        "example": "> 音质 无损\n音质已切到 lossless",
        "notes": "窗口右上角有「置顶」开关；所有命令都作用在主窗口上，等价于点界面；"
        "清空队列这类操作必须显式确认（queue clear yes / 队列 清空 确认）。",
        "keys": "命令行 独立窗口 快捷键 ctrl 反引号 置顶 补全 tab 历史 中文",
    },
    {
        "name": "中文写法",
        "desc": "每条命令都有中文写法，参数值也认中文；大小写不敏感，全角空格也能识别。"
        "下面按「英文命令 → 中文写法」列全，照着抄就行。",
        "usage": "help     帮助 · 帮助 队列\n"
        "status   状态\n"
        "stats    统计 · 概览\n"
        "queue    队列 · 队列 重试 · 队列 清空 确认\n"
        "play     播放 3\n"
        "stop     停止\n"
        "volume   音量 · 音量 40\n"
        "level    音质 · 音质 无损\n"
        "pause    暂停\n"
        "resume   继续\n"
        "trace    轨迹 · 事件 · 轨迹 5\n"
        "mock     模拟数据 队列 · 模拟数据 全部 · 模拟数据 清除\n"
        "scan     扫描 · 重扫\n"
        "cache    缓存 清理\n"
        "compat   兼容 · 兼容播放 开 · 兼容播放 关\n"
        "backend  后端\n"
        "source   音源 · 音源 客户端 · 音源 网页 · 音源 缓存 · 音源 本地\n"
        "workers  并发 · 并发 3 · 线程 3\n"
        "get      读取 音质 · 读取 并发\n"
        "set      设置 音质 无损 · 设置 并发 3 · 设置 文件名 歌曲名\n"
        "log      日志 · 日志 调试 · 日志 信息\n"
        "reload   重载 · 样式\n"
        "diag     诊断 · 诊断包\n"
        "open     打开 下载 · 打开 缓存 · 打开 配置 · 打开 日志\n"
        "url      取址 585404 · 取址 585404 无损\n"
        "history  历史 · 历史 清空\n"
        "copy     复制\n"
        "clear    清空",
        "example": "> 队列 清空 确认\n队列已清空\n> 模拟数据 队列 3\n已生成 3 首模拟下载队列，正在跑进度…",
        "notes": "中英可以混写（设置 level 无损、音源 cache 都能跑）。设置键和参数值的完整中文"
        "对照见下面「中文 · 设置键」「中文 · 参数值」两张卡片。",
        "keys": "中文 别名 对照 全部命令 设置 音质 并发 队列 开关",
    },
    {
        "name": "中文 · 设置键",
        "desc": "get / set 能写的 9 个键都有中文名，键名和值都可以写中文。每个键的含义、"
        "默认值、可选值在「设置项」分组里。",
        "usage": "compat_play      兼容播放 / 兼容        设置 兼容播放 开\n"
        "partial_fallback 未缓存完 / 补齐        设置 未缓存完 关\n"
        "keep_cache       缓存存档 / 保留缓存     设置 缓存存档 开\n"
        "to_mp3           转mp3 / 输出格式       设置 输出格式 开\n"
        "workers          并发 / 线程            设置 并发 3\n"
        "level            音质                  设置 音质 无损\n"
        "name_pattern     文件名 / 命名          设置 文件名 歌曲名\n"
        "folder_pattern   分类 / 文件夹          设置 分类 按歌手\n"
        "mini_queue       紧凑队列 / 队列形态      设置 紧凑队列 停靠",
        "example": "> 读取 文件名\nname_pattern = 'artist_title'\n"
        "> 设置 分类 按歌手/专辑\nfolder_pattern = 'artist_album'",
        "notes": "只认这 9 个键，写别的会被拒绝；布尔值写 开 / 关，或 true / false 都行。",
        "keys": "设置键 中文 兼容播放 未缓存完 缓存存档 输出格式 并发 音质 文件名 分类 紧凑队列",
    },
    {
        "name": "中文 · 参数值",
        "desc": "各命令能写的中文取值。写错时命令会当场列出可选值，不会静默失败。",
        "usage": "音质      标准 / 较高 / 极高 / 无损 / 高清\n"
        "音源      客户端 / 网页 / 缓存 / 本地\n"
        "开关      开 / 开启 / 是 / 启用 · 关 / 关闭 / 否 / 停用\n"
        "打开      缓存 / 下载 / 配置 / 数据 / 日志\n"
        "队列      重试 · 清空 确认 · 是\n"
        "播放缓存  清理 / 清空\n"
        "模拟数据  歌曲 / 搜索 · 队列 / 下载 · 缓存 · 全部 · 停止 / 清除\n"
        "文件名    歌手-歌曲名 · 歌曲名 · 歌曲名-歌手\n"
        "分类      按歌手/专辑 · 按歌手 · 不分文件夹 / 不分类\n"
        "队列形态  面板 / 折叠面板 · 停靠 / 单行停靠 · 不显示 / 隐藏\n"
        "日志级别  调试 / 信息",
        "example": "> 音质 高清\n音质已切到 hires\n> 打开 数据\n已打开 data",
        "notes": "音质五档对应 standard / higher / exhigh / lossless / hires；"
        "命名和分类模板只影响之后下载的文件名与目录。",
        "keys": "参数值 中文 音质 音源 开关 打开 队列 缓存 模拟数据 模板 队列形态 日志",
    },
    {
        "name": "help",
        "desc": "列出全部命令；带参数时只显示那一条的语法和说明。",
        "usage": "help\nhelp <命令>",
        "example": "> help queue\nqueue\n  queue [retry|clear yes]：队列",
        "notes": "忘了命令名就敲 help，搜索框里也能搜到这一页。",
        "keys": "help 帮助 命令列表",
    },
    {
        "name": "status",
        "desc": "把「状态」页的内容原样打印到输出框，方便整段复制给别人。",
        "usage": "status",
        "example": "> status\n== 播放器 ==\n…（与状态页一致）",
        "notes": "只读；不会刷新状态页的节流计时。",
        "keys": "status 状态 打印",
    },
    {
        "name": "stats",
        "desc": "一行一个维度的概览：列表与勾选数、队列进度、缓存扫描条数、"
        "下载索引条数、播放缓存占用、当前音源与音质。",
        "usage": "stats",
        "example": "> stats\n列表      39 首（勾选 0）\n队列      4 首 / 进行中 4，已完成 0，失败 0",
        "notes": "播放缓存那行会真的遍历目录算体积，目录很大时慢一点。",
        "keys": "stats 统计 概览 缓存 索引 占用",
    },
    {
        "name": "queue",
        "desc": "查看下载队列明细（前 20 首，带状态和百分比）；"
        "retry 重试失败的曲目；clear 清空队列。",
        "usage": "queue\nqueue retry\nqueue clear yes",
        "example": "> queue\n队列 4 首（完成 0）\n   1. Song 1   等待 0%",
        "notes": "clear 必须写成 queue clear yes 才会执行；正在下载时 retry / clear 都会被拒绝。",
        "keys": "queue 队列 明细 重试 retry 清空 clear",
    },
    {
        "name": "play",
        "desc": "播放搜索结果里的第 N 行（行号从 1 开始，和表格左侧的顺序一致）。",
        "usage": "play <行号>",
        "example": "> play 3\n开始播放第 3 行：夜に駆ける",
        "notes": "优先播本地缓存/已下载文件；资源不在本地时按播放器的兜底逻辑走。",
        "keys": "play 播放 行号",
    },
    {
        "name": "stop",
        "desc": "停止播放并清掉「正在播放」状态，顶栏回到未播放。",
        "usage": "stop",
        "example": "> stop\n已停止播放",
        "notes": "等价于点顶栏的停止按钮。",
        "keys": "stop 停止 播放",
    },
    {
        "name": "volume",
        "desc": "设置音量；不带参数时只显示当前音量。",
        "usage": "volume\nvolume <0-100>",
        "example": "> volume 40\n音量 40",
        "notes": "超出范围会被夹到 0-100，并同步写进设置。",
        "keys": "volume 音量 声音",
    },
    {
        "name": "level",
        "desc": "切换下载音质；不带参数时显示当前音质。",
        "usage": "level\nlevel standard|higher|exhigh|hires|lossless",
        "example": "> level exhigh\n音质已切到 exhigh",
        "notes": "实际可用音质还受账号权限限制；切完等价于改下拉框。",
        "keys": "level 音质 码率 standard exhigh hires lossless",
    },
    {
        "name": "pause / resume",
        "desc": "暂停或继续当前的下载批次。",
        "usage": "pause\nresume",
        "example": "> pause\n已请求暂停",
        "notes": "没有批次时 pause 会提示「当前没有在下载」；"
        "resume 只在有剩余曲目时才真正开始。",
        "keys": "pause 暂停 resume 继续 批次",
    },
    {
        "name": "trace",
        "desc": "打印最近的流程事件（scope/step、状态、耗时、关键参数）。",
        "usage": "trace\ntrace <条数>",
        "example": "> trace 3\n  缓存/扫描 [ok] 4ms files=5 complete=3",
        "notes": "条数限制 1-200，默认 20；数据来自本次会话，重启后清空。",
        "keys": "trace 事件 流程 轨迹 耗时",
    },
    {
        "name": "mock / 模拟数据",
        "desc": "造一批假数据练手：假搜索结果、假下载队列、假缓存列表。"
        "只改界面——不联网、不读盘、不写文件、不写下载索引。",
        "usage": "mock songs [数量]    假搜索结果（默认 12）\n"
        "mock queue [数量]    假下载队列，自动跑进度（默认 6）\n"
        "mock cache [数量]    假缓存列表（默认 8，含半截/未开始）\n"
        "mock all             三样一起\n"
        "mock stop            停掉自动进度，停在当前状态\n"
        "mock clear           清除模拟数据，恢复真实列表",
        "example": "> 模拟数据 队列\n已生成 6 首模拟下载队列，正在跑进度…\n"
        "（第 4 首会假装失败、第 5 首假装跳过，用来验失败重试入口）",
        "notes": "期间标题栏显示「模拟数据」，点下载/播放会被拦下而不是真的去请求；"
        "真实搜索一开始，或者执行 mock clear，都会自动失效。",
        "keys": "mock 模拟数据 假数据 测试 队列 缓存 搜索结果 演练",
    },
    {
        "name": "scan",
        "desc": "触发一次客户端缓存重扫，等价于点「重新扫描缓存」。",
        "usage": "scan",
        "example": "> scan\n已触发缓存重扫",
        "notes": "扫描是异步的，结果会回到主窗口列表；本命令只负责发起。",
        "keys": "scan 扫描 缓存 刷新",
    },
    {
        "name": "cache",
        "desc": "清空 play-cache 里的解码 / 转码产物，返回清理个数和释放空间。",
        "usage": "cache clear",
        "example": "> cache clear\n已清理播放缓存 12 个（86 MB）",
        "notes": "只删播放缓存，不动下载目录里的成品；下次播放会按需重新生成。",
        "keys": "cache clear 清理 播放缓存 空间",
    },
    {
        "name": "compat",
        "desc": "开关「兼容播放 → 放不了时自动转换」；不带参数时显示当前状态。",
        "usage": "compat\ncompat on|off",
        "example": "> compat off\n兼容播放已关闭",
        "notes": "和组件页的勾选框是同一个设置，改完立刻写盘。",
        "keys": "compat 兼容播放 转换 on off",
    },
    {
        "name": "backend",
        "desc": "显示当前播放后端、已经确认无法直接播放的格式、ffmpeg 路径。",
        "usage": "backend",
        "example": "> backend\n后端 windows,dsengine\nffmpeg：D:\\tools\\ffmpeg.exe",
        "notes": "「无法直接播放的格式」只统计本进程遇到过的情况。",
        "keys": "backend 后端 解码 格式 ffmpeg",
    },
    {
        "name": "source",
        "desc": "切换音源；不带参数时显示当前音源。",
        "usage": "source\nsource client|web|cache|local",
        "example": "> source cache\n已切到 cache",
        "notes": "等价于主界面的音源下拉框；切到 cache 会立即触发本地缓存扫描，"
        "切到 local 会扫描下载目录和本地音乐目录（只读），本地视图没有下载队列。",
        "keys": "source 音源 client web cache",
    },
    {
        "name": "workers",
        "desc": "设置下载并发数；不带参数时显示当前值。",
        "usage": "workers\nworkers <1-8>",
        "example": "> workers 3\n并发改为 3（下一批生效）",
        "notes": "只影响下一批任务，正在跑的批次不会中途改并发。",
        "keys": "workers 并发 线程 下载",
    },
    {
        "name": "get / set",
        "desc": "读写白名单里的设置项。不带参数时 set 会列出可写键。",
        "usage": "get <键>\nset <键> <值>\n可写：compat_play partial_fallback keep_cache to_mp3 workers level "
        "name_pattern folder_pattern mini_queue",
        "example": "> get compat_play\ncompat_play = False\n> set mini_queue dock\nmini_queue = 'dock'",
        "notes": "true/false 会转成布尔、纯数字转成整数；level 与 mini_queue 会校验取值，"
        "写错直接拒绝，不会把界面改成不认识的状态。",
        "keys": "get set 设置 白名单 读取 修改",
    },
    {
        "name": "log",
        "desc": "临时调整日志级别；不带参数时显示当前级别。",
        "usage": "log\nlog debug|info",
        "example": "> log debug\n日志级别改为 debug",
        "notes": "debug 会产生大量细节，排完记得 log info 改回来（重启也会恢复）。",
        "keys": "log 日志 级别 debug info",
    },
    {
        "name": "reload",
        "desc": "重新载入样式表：改完 QSS 不用重启就能看到效果。",
        "usage": "reload",
        "example": "> reload\n样式表已重新载入",
        "notes": "只重套样式，不重启界面逻辑；个别控件仍可能需要重开窗口才完全生效。",
        "keys": "reload 样式 重载 qss 主题",
    },
    {
        "name": "diag",
        "desc": "导出诊断包（env / settings / steps / errors / app.log 尾部），"
        "导出后自动打开所在目录。",
        "usage": "diag",
        "example": "> diag\n已导出：C:\\Users\\…\\diagnostics\\diag-20261110-0850.zip",
        "notes": "cookie、token 这类字段已打码；等同右下角的「导出诊断包」。",
        "keys": "diag 诊断包 导出 zip",
    },
    {
        "name": "open",
        "desc": "打开缓存 / 下载 / 配置目录，或直接打开日志文件。",
        "usage": "open cache|download|data|log",
        "example": "> open log\n已打开 log",
        "notes": "缓存目录没设置时会提示；其它三项总是可用的。",
        "keys": "open 打开 目录 日志 资源管理器",
    },
    {
        "name": "url",
        "desc": "查某首歌的取址结果：容器、实际码率、文件大小、URL 前 160 字。",
        "usage": "url <歌曲id>\nurl <歌曲id> <音质>",
        "example": "> url 585404 hires\n查询中：585404（hires）",
        "notes": "在后台线程执行，不卡界面；结果稍后出现在输出框里。"
        "用来判断「为什么这首下不了」。",
        "keys": "url 取址 地址 码率 大小 探针",
    },
    {
        "name": "history",
        "desc": "列出最近的命令历史（最多 20 条）；带 clear 清空历史。",
        "usage": "history\nhistory clear",
        "example": "> 历史\n最近 2 条（↑↓ 取用，history clear 清空）\n   1. 队列\n   2. 设置 音质 无损",
        "notes": "历史存在设置里，关掉窗口再开也能用 ↑↓ 翻；只留最近 50 条。",
        "keys": "history 历史 记录 清空",
    },
    {
        "name": "copy / clear",
        "desc": "copy 把整段输出复制到剪贴板；clear 清空输出框。",
        "usage": "copy\nclear",
        "example": "> copy\n输出已复制到剪贴板",
        "notes": "贴反馈时先 copy，比截图好认。",
        "keys": "copy 复制 剪贴板 clear 清空",
    },
]

# 每条命令的中文写法：拼到对应卡片的语法块末尾，一张卡里就能看到中英对照。
# 键是 COMMAND_DOCS 里的 name（合并命令按合并后的写法）。
ZH_EXAMPLES = {
    "help": "帮助 · 帮助 队列",
    "status": "状态",
    "stats": "统计 · 概览",
    "queue": "队列 · 队列 重试 · 队列 清空 确认",
    "play": "播放 3",
    "stop": "停止",
    "volume": "音量 · 音量 40",
    "level": "音质 · 音质 无损",
    "pause / resume": "暂停 · 继续",
    "trace": "轨迹 · 事件 · 轨迹 5",
    "mock / 模拟数据": "模拟数据 队列 · 模拟数据 全部 · 模拟数据 清除",
    "scan": "扫描 · 重扫",
    "cache": "缓存 清理",
    "compat": "兼容 · 兼容播放 开 · 兼容播放 关",
    "backend": "后端",
    "source": "音源 · 音源 客户端 · 音源 网页 · 音源 缓存 · 音源 本地",
    "workers": "并发 · 并发 3 · 线程 3",
    "get / set": "读取 音质 · 设置 音质 无损 · 设置 并发 3",
    "log": "日志 · 日志 调试 · 日志 信息",
    "reload": "重载 · 样式",
    "diag": "诊断 · 诊断包",
    "open": "打开 下载 · 打开 缓存 · 打开 配置 · 打开 日志",
    "url": "取址 585404 · 取址 585404 无损",
    "history": "历史 · 历史 清空",
    "copy / clear": "复制 · 清空",
}

HELP_ITEMS.extend(
    {
        "group": "命令行手册",
        "where": "命令行",
        "name": doc["name"],
        "desc": doc["desc"],
        "usage": (
            f"{doc.get('usage', '')}\n\n中文写法：{ZH_EXAMPLES[doc['name']]}".strip()
            if ZH_EXAMPLES.get(doc["name"])
            else doc.get("usage", "")
        ),
        "example": doc.get("example", ""),
        "notes": doc.get("notes", ""),
        "keys": doc.get("keys", ""),
    }
    for doc in COMMAND_DOCS
)

# 设置项手册：set/get 能写的每个键是什么、可选值、默认值、对应界面哪里。
# 键名和 devtools.SETTING_KEYS 保持一致。
SETTING_DOCS = [
    {
        "name": "怎么改设置",
        "desc": "命令行页用 get 读、set 写；下面每一项都对应「设置」窗口里的一个控件。"
        "键名和值都能用中文，例如「设置 音质 无损」。",
        "usage": "get <键>\nset <键> <值>",
        "example": "> get workers\nworkers = 4\n> 设置 并发 3\nworkers = 3",
        "notes": "改动立即写进 settings.ini；level / mini_queue 会当场反映到界面，"
        "其余多数从下一批任务开始生效。每个键下面的「取值」都可以用中文写。",
        "keys": "设置 修改 读取 get set 中文",
    },
    {
        "name": "compat_play",
        "desc": "兼容播放：系统解码器不全、m4a / flac 放不出来时，先用内置 ffmpeg "
        "转成 WAV 再播。界面在「设置 → 兼容播放」。",
        "usage": "取值：true | false\n默认：false",
        "example": "> set compat_play true",
        "notes": "转码不重新压缩，音质不变；首次播放慢 1~3 秒，并在 play-cache 里"
        "临时占空间（约每分钟 10 MB）。默认关闭，关闭时播放失败只提示，不会偷偷转。",
        "keys": "compat_play 兼容播放 转码 wav 无法播放",
    },
    {
        "name": "partial_fallback",
        "desc": "缓存没下完的歌，导出 / 下载时要不要联网补齐。界面在「设置 → 未缓存完」。",
        "usage": "取值：true | false\n默认：true",
        "example": "> set partial_fallback false",
        "notes": "true：自动取完整版；false：直接跳过，不会生成半截文件。"
        "客户端是边听边缓存的，没听完的歌只有半截。",
        "keys": "partial_fallback 未缓存完 补齐 半截 跳过",
    },
    {
        "name": "keep_cache",
        "desc": "缓存存档：把扫描到的客户端缓存解密留一份到 cache-store，"
        "客户端自己删掉之后仍能在「本地缓存」里看到并播放。界面在「设置 → 缓存存档」。",
        "usage": "取值：true | false\n默认：false",
        "example": "> set keep_cache true",
        "notes": "占用磁盘，上限 2 GB，超出按最久未访问淘汰；关掉不会删除已存档内容，"
        "要清空请用设置里的「清理」按钮。",
        "keys": "keep_cache 缓存存档 保留 本地缓存",
    },
    {
        "name": "to_mp3",
        "desc": "下载完把 m4a / flac 转成 mp3。界面在「设置 → 输出格式」。",
        "usage": "取值：true | false\n默认：false",
        "example": "> set to_mp3 true",
        "notes": "需要系统里有 ffmpeg，没有时设置里会直接标出来；"
        "按源码率上浮一档转（如 96k AAC → 160k mp3），是有损转码，不是无损。",
        "keys": "to_mp3 转码 mp3 输出格式 ffmpeg",
    },
    {
        "name": "workers",
        "desc": "同时下载几首。界面在「设置 → 并发数」。",
        "usage": "取值：1-8\n默认：4",
        "example": "> workers 3",
        "notes": "只影响下一批任务，正在跑的批次不会中途改；网络不稳时降到 2 更稳。",
        "keys": "workers 并发 线程 同时下载",
    },
    {
        "name": "level",
        "desc": "下载音质。界面在底部操作栏的音质下拉框，和它是同一个值。",
        "usage": "取值：standard | higher | exhigh | hires | lossless\n默认：exhigh",
        "example": "> level hires",
        "notes": "实际能拿到哪一档取决于账号权限，超出权限会回退到可用档位；"
        "下载时按这个值取地址。",
        "keys": "level 音质 码率 standard higher exhigh hires lossless",
    },
    {
        "name": "name_pattern",
        "desc": "下载文件的命名模板。界面在「设置 → 文件名」。",
        "usage": "取值：artist_title（歌手-歌曲名，默认）\n"
        "　　　title（歌曲名）\n"
        "　　　title_artist（歌曲名-歌手）",
        "example": "> set name_pattern title",
        "notes": "只影响之后新下载的文件，已经下好的不会重命名。",
        "keys": "name_pattern 文件名 命名 模板 歌手 歌曲名",
    },
    {
        "name": "folder_pattern",
        "desc": "下载目录里的分类子文件夹。界面在「设置 → 分类」。",
        "usage": "取值：artist_album（歌手/专辑，默认）\n"
        "　　　artist（歌手）\n"
        "　　　none（不分文件夹）",
        "example": "> set folder_pattern none",
        "notes": "只影响新下载的文件；换分类不会移动已经存在的文件。",
        "keys": "folder_pattern 分类 文件夹 目录 歌手 专辑",
    },
    {
        "name": "mini_queue",
        "desc": "紧凑模式（400px 窄窗口）里下载队列长什么样。界面在「设置 → 紧凑队列」。",
        "usage": "取值：panel（底部卡片，默认；按窗口高度显示 3～5 行）\n"
        "　　　dock（单行停靠，点开看全部）\n"
        "　　　off（不显示）",
        "example": "> set mini_queue dock",
        "notes": "只影响紧凑模式；标准窗口的队列永远是完整表格。改完立刻生效，不用重启。"
        "卡片顶栏点一下折叠/展开，全部完成后会收成一行「N 首已完成」并给出清空入口。",
        "keys": "mini_queue 紧凑 队列 面板 停靠 隐藏",
    },
]

HELP_ITEMS.extend(
    {
        "group": "设置项",
        "where": "设置",
        "name": doc["name"],
        "desc": doc["desc"],
        "usage": doc.get("usage", ""),
        "example": doc.get("example", ""),
        "notes": doc.get("notes", ""),
        "keys": doc.get("keys", ""),
    }
    for doc in SETTING_DOCS
)

_CSS = """
QLabel#helpName { color: #e8e8ea; font-size: 12px; }
QLabel#helpWhere { color: #7a7a82; font-size: 11px; }
QLabel#helpDesc { color: #b9b9c2; font-size: 12px; }
QLabel#helpHead { color: #96969e; font-size: 11px; }
QLabel#helpCode {
    background: #1b1b1f; border: 1px solid #35353d; border-radius: 4px;
    padding: 5px 7px; color: #d2d2da;
    font-family: Consolas, "Cascadia Mono", monospace; font-size: 11px;
}
QLabel#helpExample {
    color: #9aa3ad; font-family: Consolas, "Cascadia Mono", monospace;
    font-size: 11px; padding-left: 2px;
}
QLabel#helpNotes { color: #7f7f88; font-size: 11px; }
QLineEdit#helpSearch {
    background: #242428; color: #d8d8dc; border: 1px solid #3a3a42;
    border-radius: 5px; padding: 5px 8px;
}
"""


class DevHelpDialog(RoundDialog):
    def __init__(self, parent=None, title: str = "开发者面板 · 帮助", groups=None):
        """``groups`` 给定时只展示这几组（命令行窗口的「?」只关心命令和设置项）。"""
        super().__init__(parent, title)
        self._cards: list[dict] = []
        self._headers: dict[str, QLabel] = {}
        self._only_groups = tuple(groups) if groups else ()
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
        total = len(
            [i for i in HELP_ITEMS if not self._only_groups or i["group"] in self._only_groups]
        )
        self.count = QLabel(f"{total} 条")
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

        tip = QLabel("按功能名、按钮名或页面名搜索；本窗口不影响面板运行。")
        tip.setStyleSheet("color:#7a7a82; font-size:11px;")
        body.addWidget(tip)

    def _build_cards(self, layout: QVBoxLayout) -> None:
        current = None
        items = [
            item for item in HELP_ITEMS
            if not self._only_groups or item["group"] in self._only_groups
        ]
        for item in items:
            if item["group"] != current:
                current = item["group"]
                head = QLabel(current)
                head.setObjectName("helpHead")
                # 组间距用 label 自己的外边距，不用 addSpacing：过滤后整组隐藏时，
                # addSpacing 留下的空档不会跟着收起来，列表顶上会空一大块
                head.setContentsMargins(0, 12 if self._headers else 0, 0, 2)
                layout.addWidget(head)
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
            card_layout.addLayout(head_row)

            desc = QLabel(item["desc"])
            desc.setObjectName("helpDesc")
            desc.setWordWrap(True)
            card_layout.addWidget(desc)

            # 命令行手册才有：语法块 / 示例 / 注意，排布照编程语言文档来。
            # 样式写在控件自己身上：祖先样式表里的通用 QWidget 规则会压过
            # ID 选择器，只写在 _CSS 里底色不生效（卡片当初也是这么踩的）
            if item.get("usage"):
                code = QLabel(item["usage"])
                code.setWordWrap(True)
                code.setTextInteractionFlags(Qt.TextSelectableByMouse)
                code.setStyleSheet(
                    "QLabel { background:#1b1b1f; border:1px solid #35353d;"
                    " border-radius:4px; padding:5px 7px; color:#d2d2da;"
                    " font-family:Consolas, 'Cascadia Mono', monospace; font-size:11px; }"
                )
                card_layout.addWidget(code)
            if item.get("example"):
                example = QLabel(item["example"])
                example.setWordWrap(True)
                example.setTextInteractionFlags(Qt.TextSelectableByMouse)
                example.setStyleSheet(
                    "QLabel { color:#9aa3ad; padding-left:2px;"
                    " font-family:Consolas, 'Cascadia Mono', monospace; font-size:11px; }"
                )
                card_layout.addWidget(example)
            if item.get("notes"):
                notes = QLabel("注意：" + item["notes"])
                notes.setWordWrap(True)
                notes.setStyleSheet("QLabel { color:#7f7f88; font-size:11px; }")
                card_layout.addWidget(notes)

            layout.addWidget(card)
            blob = " ".join(
                (
                    item["group"],
                    item["name"],
                    item["where"],
                    item["desc"],
                    item.get("usage", "") or "",
                    item.get("example", "") or "",
                    item.get("notes", "") or "",
                    item.get("keys", "") or "",
                )
            ).lower()
            self._cards.append({"card": card, "group": item["group"], "blob": blob})

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
