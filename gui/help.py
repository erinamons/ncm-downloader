"""帮助窗口：把所有功能（包括藏在表头、右键里的）摊开写清楚。

内容用富文本一段写完，不搞标签页——QSS 里没有 tab 样式，分页反而会
和设置窗口的观感割裂。整页可滚动，一屏看不完也没关系。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QLabel, QScrollArea

from gui import __version__
from gui.dialog import RoundDialog

APP_VERSION = __version__

_TITLE = "margin:18px 0 7px 0; font-size:13px; font-weight:600; color:#e8e8ea;"
_TEXT = "margin:0 0 7px 0; font-size:12px; color:#96969e;"
_KEY = "color:#b6b6c0; font-weight:600;"
_LINK = "color:#7fa9cc; text-decoration:none;"

HELP_HTML = f"""
<div style="font-size:12px; color:#96969e;">
<p style="margin:0 0 4px 0; font-size:14px; font-weight:600; color:#e8e8ea;">
网易云音乐下载器
</p>
<p style="{_TEXT}">
搜索、试听、下载、缓存读取都在一个窗口里。下面按使用顺序写，带「」的是界面上的文字。
</p>

<p style="{_TITLE}">搜索</p>
<p style="{_TEXT}"><span style="{_KEY}">关键词</span>　歌名、歌手、专辑名都能搜，回车或点「搜索」开始。</p>
<p style="{_TEXT}"><span style="{_KEY}">链接</span>　直接粘贴歌曲链接、歌单链接或一串纯数字的歌曲 ID，会自动识别并解析；歌单会展开成整张列表。</p>
<p style="{_TEXT}"><span style="{_KEY}">历史</span>　点一下搜索框会弹出最近 10 条记录，单击填回，不用重新打字；在「设置 → 搜索历史」里可以一键清空。</p>
<p style="{_TEXT}"><span style="{_KEY}">清空</span>　按 Esc 先清空搜索框；输入框已经空了再按一次，会取消表格里选中的行。</p>

<p style="{_TITLE}">结果列表</p>
<p style="{_TEXT}"><span style="{_KEY}">勾选</span>　左侧方框决定哪些歌会被下载，勾几首就下几首。</p>
<p style="{_TEXT}"><span style="{_KEY}">全选</span>　点表头最左边那一格（空白列头）可以在全选和取消之间切换；右键菜单里也有「全选」「取消全选」。这一格没有文字，所以不太好发现。</p>
<p style="{_TEXT}"><span style="{_KEY}">排序</span>　点「曲名 / 艺人 / 专辑 / 时长」列头：第一次升序，第二次降序，第三次恢复原始顺序。当前排序的列会加粗并带箭头。</p>
<p style="{_TEXT}"><span style="{_KEY}">播放</span>　双击任意一行直接播放；顶部播放条会显示封面、歌名和进度。</p>
<p style="{_TEXT}"><span style="{_KEY}">右键菜单</span>　播放、仅播放此曲、下载选中、仅下载此曲（只勾这一首并立即开始）、复制歌曲链接、「歌名 - 歌手」、歌曲 ID、全选、取消全选、清空当前结果、在浏览器打开。</p>
<p style="{_TEXT}"><span style="{_KEY}">筛选</span>　在「仅本地缓存」音源下，搜索框只过滤本地列表，不会发出任何网络请求。</p>

<p style="{_TITLE}">下载与音源</p>
<p style="{_TEXT}"><span style="{_KEY}">音质</span>　标准 / 较高 / 极高 / 无损 / Hi-Res。真实拿到的档位由账号权益决定，选了超出权益的档位会自动降级，队列里显示的是实际码率，不是请求值。</p>
<p style="{_TEXT}"><span style="{_KEY}">客户端接口</span>　以桌面客户端身份发起请求，能使用账号在客户端享有的权益；移动端开启免费听歌后，VIP 音乐也能下载。</p>
<p style="{_TEXT}"><span style="{_KEY}">网页接口</span>　以网页身份请求，区别在于音质和无法使用免费听歌，不是 VIP 无法下载完整版。</p>
<p style="{_TEXT}"><span style="{_KEY}">仅本地缓存</span>　完全不发请求，只列出客户端已经缓存的歌曲，没网络也能用。</p>
<p style="{_TEXT}"><span style="{_KEY}">转 mp3</span>　在设置里开启。转码按源码率上浮一档（如 96k AAC → 160k mp3），不会提升音质，只是换容器。</p>
<p style="{_TEXT}"><span style="{_KEY}">下载队列</span>　显示每首的进度、状态、大小。完成后在队列里右键可以播放、打开所在文件夹、复制文件路径；双击也能直接播放。</p>
<p style="{_TEXT}"><span style="{_KEY}">打开目录</span>　状态栏最右侧的文件夹图标，随时打开下载目录，不用等下载完成。</p>
<p style="{_TEXT}"><span style="{_KEY}">失败重试</span>　点状态栏汇总里的「失败 N」直接重下失败项；队列右键也有「重试失败」。</p>

<p style="{_TITLE}">播放</p>
<p style="{_TEXT}"><span style="{_KEY}">空格</span>　播放 / 暂停。光标在输入框里时不会触发，正常打字不受影响。</p>
<p style="{_TEXT}"><span style="{_KEY}">切歌</span>　Ctrl + ← / → 上一首、下一首。</p>
<p style="{_TEXT}"><span style="{_KEY}">进度条</span>　鼠标移上去出现圆点，单击或拖动跳转；播放条底部那条细线就是进度。</p>
<p style="{_TEXT}"><span style="{_KEY}">循环</span>　右下角按钮循环切换：列表循环 → 单曲循环 → 顺序播放。</p>
<p style="{_TEXT}"><span style="{_KEY}">音量</span>　点喇叭一键静音，右侧滑块调整；音量会被记住。</p>
<p style="{_TEXT}"><span style="{_KEY}">长歌名</span>　歌名或歌手太长时会自动横向滚动，不用拉宽窗口。</p>

<p style="{_TITLE}">缓存</p>
<p style="{_TEXT}"><span style="{_KEY}">缓存是什么</span>　客户端在线播放时留下的音频分片，本工具能直接还原成标准 m4a / mp3。</p>
<p style="{_TEXT}"><span style="{_KEY}">怎么用</span>　音源切到「仅本地缓存」，列表里就是这些歌，可以直接播放或另存。</p>
<p style="{_TEXT}"><span style="{_KEY}">实时刷新</span>　列表跟着客户端走：你在客户端听了新歌，或者清了缓存，这边 2～3 秒内会自动更新。</p>
<p style="{_TEXT}"><span style="{_KEY}">音质</span>　缓存音质等于当时播放时选的档位，不是下载档位。想要更高规格，在客户端把播放音质调高再听一遍。</p>
<p style="{_TEXT}"><span style="{_KEY}">目录</span>　默认自动探测客户端位置；如果你在客户端里改过缓存目录，到「设置 → 缓存目录」点「自动检测」或者手动指定。</p>

<p style="{_TITLE}">窗口</p>
<p style="{_TEXT}"><span style="{_KEY}">紧凑模式</span>　Ctrl + M 进入 / 还原。设置里的「启动视图」决定下次打开时用哪种。</p>
<p style="{_TEXT}"><span style="{_KEY}">启动位置</span>　每次打开都在主屏居中：标准窗口 1000×740，紧凑模式 400×620。不会记住上次的位置和大小，省得换了显示器之后窗口跑到屏幕外。</p>
<p style="{_TEXT}"><span style="{_KEY}">移动与缩放</span>　拖动顶部播放条的空白处移动窗口；鼠标贴近窗口边缘会变成双向箭头，拖动即可改变大小。</p>
<p style="{_TEXT}"><span style="{_KEY}">双击标题栏</span>　普通模式最大化，紧凑模式还原为完整窗口。</p>
<p style="{_TEXT}"><span style="{_KEY}">窗口按钮</span>　播放条最右侧是最小化、最大化、关闭。</p>

<p style="{_TITLE}">快捷键</p>
<table width="100%" cellspacing="0" cellpadding="3">
<tr><td width="34%" style="color:#b6b6c0;">Ctrl + F</td><td style="color:#96969e;">聚焦搜索框</td></tr>
<tr><td style="color:#b6b6c0;">F1</td><td style="color:#96969e;">打开本帮助</td></tr>
<tr><td style="color:#b6b6c0;">Ctrl + M</td><td style="color:#96969e;">紧凑模式 / 还原</td></tr>
<tr><td style="color:#b6b6c0;">Ctrl + ← / →</td><td style="color:#96969e;">上一首 / 下一首</td></tr>
<tr><td style="color:#b6b6c0;">空格</td><td style="color:#96969e;">播放 / 暂停</td></tr>
<tr><td style="color:#b6b6c0;">Esc</td><td style="color:#96969e;">清空搜索框，再按取消选中</td></tr>
<tr><td style="color:#b6b6c0;">双击行</td><td style="color:#96969e;">播放这首歌</td></tr>
<tr><td style="color:#b6b6c0;">双击标题栏</td><td style="color:#96969e;">最大化 / 还原</td></tr>
</table>

<p style="{_TITLE}">关于</p>
<p style="{_TEXT}">
桌面版 v{APP_VERSION}，基于 PyQt5。下载与播放都在本地完成，不上传任何数据。
</p>
<p style="{_TEXT}">
网站　<a href="https://mochizuki.top" style="{_LINK}">mochizuki.top</a><br>
邮箱　<a href="mailto:nagi@mochizuki.top" style="{_LINK}">nagi@mochizuki.top</a>
</p>
</div>
"""


class HelpDialog(RoundDialog):
    def __init__(self, parent=None):
        super().__init__(parent, "帮助", width=560)
        self.setFixedHeight(640)

        scroll = QScrollArea()
        scroll.setObjectName("helpScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)

        content = QLabel(HELP_HTML)
        content.setObjectName("helpText")
        content.setWordWrap(True)
        content.setTextFormat(Qt.RichText)
        content.setAlignment(Qt.AlignTop)
        # 网站和邮箱要能点开
        content.setTextInteractionFlags(Qt.TextBrowserInteraction)
        content.setOpenExternalLinks(True)
        scroll.setWidget(content)

        self.body_layout.addWidget(scroll)
