# 维护手册

面向后续接手的人：这里记录踩过的坑、排查路径和发布流程。**踩坑部分都是花了真实时间换来的，改相关代码前先读。**

## 一、已知坑

### 打包踩坑

#### 1. Nuitka 在当前环境不可用

| 尝试 | 结果 |
|---|---|
| `--mingw64` | `FATAL: cannot use '--mingw64' on Python version 3.13 or higher` |
| `--clang` | `FATAL: Visual Studio required for using ClangCL on Windows` |
| `--msvc=latest` | 找不到 MSVC，编译失败 |
| `--zig` | Nuitka 会执行 `pip install ziglang`，经本地代理反复卡死 |

原因：Nuitka 4.x 在 Windows 上，Python 3.13+ 只支持 MSVC 编译。本机没有 Visual Studio，装 Build Tools 需要 2–3 GB。

**结论：用 PyInstaller。** 若日后要强代码保护，需先装 Python 3.12（支持 MinGW）或 VS Build Tools，再回来走 Nuitka。

#### 2. Nuitka 首次构建会卡在下载确认

无终端环境下运行 Nuitka，它问「是否下载编译器」时**永远等键盘输入**，日志停在 `Running C compilation via Scons.`，进程 CPU 为 0。

必须加 `--assume-yes-for-downloads`。判断方法：

```powershell
$p = Get-Process python | Sort-Object CPU -Descending | Select-Object -First 1
$p.CPU            # 记下
Start-Sleep 10
(Get-Process -Id $p.Id).CPU   # 10 秒后没变化 = 卡住，不是在编译
```

#### 3. ffmpeg 不会自动进包

`--collect-all imageio_ffmpeg` 只收 Python 模块，**100 MB 的 ffmpeg 二进制不会进包**。表现：打包版「转 mp3」不可用，设置里显示「转码引擎：未找到」。

文件夹版的正确做法是把 ffmpeg 摆到 exe 同级，让人能看见也能替换：

```
打包完成后：
mkdir "pack\网易云下载器\ffmpeg\binaries"
copy "<site-packages>\imageio_ffmpeg\binaries\ffmpeg-win-*.exe" 到上面这个目录
```

`ncm/transcode.py::_bundled_ffmpeg()` 会按 `exe目录\ffmpeg\binaries\ffmpeg*.exe` 找，
找不到再退回 PATH 和 imageio_ffmpeg。

验证方法（不用真跑打包后的程序）：

```python
import sys
from pathlib import Path
sys.frozen = True
sys.executable = str(Path("pack/网易云下载器/网易云下载器.exe").resolve())
from ncm.transcode import ffmpeg_path
print(ffmpeg_path())      # 应指向 pack\网易云下载器\ffmpeg\binaries\...
```

早期用 `--onefile --add-binary "...;imageio_ffmpeg/binaries"` 也能带上，但那样藏在解压目录里，
用户既看不见也换不了，已弃用。

#### 4. 中文文件名的 exe 不能用 bash 直接启动

`./pack/网易云下载器.exe` 会静默失败，进程不存在。用：

```powershell
Start-Process -FilePath "C:\...\网易云下载器.exe"
```

### 窗口系统踩坑

这一块最容易反复踩，按现象查。

#### 现象：横向拖动窗口时闪白条

**根因不是阴影，是横向滚动条。**

链路：窗口变窄 → 列宽有 45 ms 防抖还没跟上 → 列宽总和 > 视口 → `QTableWidget` 判定需要横向滚动条 → 冒出一条 → 它占掉 9 px 高度导致整块重排（闪），而 QSS 里滚动条是 `background: transparent`（透明，直接透出窗口，看起来就是白条）。

修法：

```python
table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # 禁止出现
# QSS: QScrollBar:horizontal { background: #242428; }
```

**排查顺序建议**：先枚举「这一帧多出来了什么控件」（滚动条、浮层、占位），再怀疑绘制逻辑。我在这上面绕了四轮改阴影，都是白费。

#### 现象：左右拉伸闪，上下不闪

横向独有两处每帧重排：

1. `TopBar.resizeEvent` 里 940 px 阈值让标题显隐 → 整条播放条布局重排
2. `FluidTable.resizeEvent` 每帧 `setColumnWidth` → 整个视口重排

两处都加了防抖（120 ms / 45 ms），拖动中保持当前布局，停手再调整。

**「方向性差异」是最快的线索**：上下不闪、左右闪，直接把范围从「阴影」缩到「横向专属布局」。

#### 现象：拖动时边缘出现透明缝

`resizeEvent` 里用 `self.update()` 只是把重绘排进队列，而系统 resize 与 Qt 重绘是异步的，中间必然有一帧「尺寸已变、内容未画」。

修法：`self.repaint()` 同步重绘。代价约 10–20 ms/次。

#### 现象：窗口某块区域完全透明（棋盘格）

透明窗口里**任何未绘制的区域都会透出桌面**。所以 `paintEvent` 不能提前 return：

```python
# 错误：跳过绘制 = 露出桌面
if pixmap.size() != self.size():
    return
```

拖动改尺寸期间，位图与窗口尺寸对不上，**不要贴位图也不要不画**，用四条实心半透明边兜底：

```python
margin = self.SHADOW
tint = QColor(0, 0, 0, 50)
painter.fillRect(QRectF(0, 0, w, margin), tint)          # 上
painter.fillRect(QRectF(0, h - margin, w, margin), tint)  # 下
painter.fillRect(QRectF(0, margin, margin, h - margin*2), tint)
painter.fillRect(QRectF(w - margin, margin, margin, h - margin*2), tint)
```

四边实心填充不依赖任何位图尺寸，每帧必定覆盖满。

#### 现象：右上角光标是拉伸，一拉却在移动窗口

`TopBar.mousePressEvent` 抢先 `startSystemMove()`，而光标由窗口侧边缘判定给出，两边打架。

修法：按下时先换算到窗口坐标，向 `_edges_at()` 问一次，命中边缘就 `event.ignore()` 让给缩放。

#### 现象：边缘拉伸要移到窗口外才响应

边缘判定不能以窗口矩形为基准——视觉边缘在 12 px 阴影带内侧。要以内缩后的边为基准，内外各留 6 px 命中区。

### 其他坑

| 现象 | 原因 | 修法 |
|---|---|---|
| 关窗时进程无提示消失 | `self.engine` 被 `Downloader` 覆盖，调用了不存在的 `shutdown()` | 播放器与下载器分开命名 |
| 点空白处历史浮层不关闭 | 表格设了 `NoFocus`，输入框不会失焦 | 浮层装应用级 `eventFilter`，按 `widgetAt()` 判断落点 |
| 搜索结果被二次过滤 | `_apply_view` 对所有视图都按输入框关键词过滤 | 只有缓存视图才本地筛选 |
| 打包后转码失败 | 见上文 ffmpeg 未入包 | `--add-binary` |
| 取消单首后重试仍被取消 | 取消集合未清 | 每批新建 `Downloader` 实例即可 |
| 文件名过长下载失败 | Windows 路径上限 260 | `render_template` 按相对路径 180 截断 |
| 登录后状态栏仍显示未登录 | `_open_settings` 只在 `Accepted` 时刷新账号 | 退出对话框就刷新，登录与保存解耦 |
| 点「本地缓存」一次跑两个扫描 | `on_load_cache` 里 `setCurrentIndex` 又触发 `_on_source_changed` | 切换时 `blockSignals`，并加 `_scanning_cache` 状态位 |
| 缓存行右键无反应 | 切换瞬间表格还留着旧音源的行，而 `self.songs` 已清空 | 先清视图再扫描，保证两者始终同步 |
| VIP 歌一直只有 30 秒 | 未登录时客户端只缓存试听片段，登录后仍吃这段缓存 | 按 `size×8/rate` 估时长，低于歌曲时长 60% 就跳过缓存走网络 |
| 播放缓存写入报 WinError 5 | `tempfile.gettempdir()` 被其它软件改到无写权限的公共目录（如 Wondershare 的 `C:\Users\Public\Documents\...\CreatorTemp`） | 播放缓存改放 `~\.ncm-downloader\play-cache`，并对 `os.replace` 兜底回退网络 |
| 缓存列表只有一首 | `scan()` 只 `glob("*.uc")` 扫顶层，而部分客户端版本把缓存放在子目录 | 改递归扫描，`max_depth=3` |
| 缓存视图不自动更新 | Windows 上 `QFileSystemWatcher` 大批增删后静默失效 | 监听自愈重挂 + 8 秒指纹轮询兜底 |
| 客户端删掉旧缓存后列表里没了 | 客户端只保留极少的播放缓存 | 「缓存存档」（设置里开启），扫描时解密留存一份，上限 2 GB |
| 媒体键注册失败 | `RegisterHotKey` 返回 1409，网易云客户端自己也占了媒体键 | 静默跳过，托盘菜单仍可控制；不要改注册方式硬抢，会抢掉别的播放器 |
| ctypes 调 RegisterHotKey 拿不到错误码 | 没声明 argtypes/restype，64 位下 HWND 被截断且 `get_last_error` 无效 | 用 `ctypes.WinDLL("user32", use_last_error=True)` 并显式声明 argtypes |
| 普通 QWidget 设了 QSS 背景却不显示 | 需要 `WA_StyledBackground` | 补丁类控件记得打开 |
| 盖住滚动条上段无效 | 滚动条容器后绘制，`raise_()` 改不了层序 | 直接改 `qt_scrollarea_vcontainer` 几何，把滚动条压到表头下方 |
| **双击 exe 秒退，报 `sys.stderr is None`** | `--windowed` 无控制台，`sys.stderr` 为 None；`faulthandler.enable()` 无参会直接抛异常 | 崩溃栈写到程序目录 `crash.log`（`enable(file=...)`）；windowed 程序里任何依赖 stdout/stderr 的代码都要防 None |
| **状态栏显示「正在播放」但没声音** | `QMediaPlayer.state` 是用户意图（`play()` 一调就是 Playing），不代表数据就绪 | 对外状态以 `mediaStatus` 为准：`LoadedMedia/BufferedMedia` 才报 PLAYING，否则 BUFFERING；QMediaPlayer 只能在主线程操作（`_start_requested` 信号封送） |
| **缓存 60% 的歌报「无缓存」/ 右键置灰 / 进度列从不更新** | 三处同类：`Downloader._cache`、`_cache_map` 只收 `complete` 条目，下游分支/查找永远拿不到半截条目；进度列读了 idx 里不存在的 `covered/total` 字段（实际是 `size/zone`） | **半截缓存必须全程保留**，由 `partial_fallback` 决定补齐还是跳过；idx 字段名以 `cache.py::_read_idx` 为准 |
| 联网播放必失败 `name 'src' is not defined` | 从 `downloader.py` 搬代码到 `player.py` 时带上了对方的 `src.size`，该作用域只有 `(url, ext)` | 总长从响应头 `Content-Length` 取；跨模块搬代码先核对变量作用域 |
| mini 模式测试怎么都进不去紧凑布局 | 直接 `resize(400)` 会被 full 模式的 `setMinimumSize(840)` 顶回去 | 测试要走真实 `toggle_mini()` 入口（Ctrl+M 同款） |
| 7 列布局在窄窗口下最右列被裁 | 弹性列保底 160px，`30+160+160+200+64+70+80` 超过视口，横向滚动条又被禁用 | `FluidTable.min_fluid` 可调，紧凑模式设 116；手动 `setColumnWidth` 后要 `refit()` |

## 二、文件都写到哪了

**便携模式（v1.0.3 起）**：一律写在程序目录，不写注册表、不碰用户目录。
路径统一由 `ncm/paths.py` 提供，**不要在别处拼 `Path.home()`**。

| 内容 | 位置 |
|---|---|
| 账号 cookie、缓存目录等 | `<程序目录>\config.json`（含登录凭据，不要外传） |
| 界面偏好 | `<程序目录>\settings.ini`（QSettings IniFormat） |
| 运行日志 | `<程序目录>\app.log`，滚动 3 份 × 1 MB |
| 播放缓存 | `<程序目录>\play-cache\` |
| 默认下载目录 | `<程序目录>\downloads\` |
| 下载索引 | `<下载目录>\.ncm-index.db` |

程序目录不可写时（例如放进 Program Files），`paths.data_dir()` 会自动退回家目录
`~\.ncm-downloader`，避免直接起不来。

**升级迁移**：`_open_settings()` 首次创建 ini 时，会把注册表 `HKCU\Software\ncm\downloader`
的键搬过来并清空；`_migrate_legacy_config()` 会把 `~\.ncm-downloader\config.json`
复制过来。老用户升级后登录态不丢。

**为什么这么改**：之前数据散在用户目录和注册表，用户看到的是一个 exe，却不知道
东西写去了哪，换机器也带不走。便携模式让「文件夹即全部状态」。

### 相关坑

- 播放缓存早期用 `tempfile.gettempdir()`，某些机器上 TEMP 被其它软件改成无写权限的
  公共目录（实测 Wondershare 的 `C:\Users\Public\Documents\Wondershare\CreatorTemp`），
  `os.replace` 报 `WinError 5` → 表现为「播放失败」。现已固定在程序目录。
- 打包目录里删上千个文件会被 safe-delete 拦，直接让 PyInstaller `--noconfirm` 覆盖即可。

### Qt 控件与样式踩坑

这一节的共同点：现象看着像「程序算错了」，实际是 Qt 的默认行为。

#### 播放：Qt5 在 Windows 默认选 DirectShow，它没有 AAC / FLAC 解码器

现象：缓存里的 m4a / flac 首播必失败，点「重试」走网络拿到 mp3 却能播。

根因不是系统缺解码器——`MFTEnumEx` 能列出 AAC / FLAC 各一个，
`MFCreateSourceReaderFromURL` 也能直接打开这些文件；是 Qt5 选了 DirectShow 后端，
而 DirectShow 只认 mp3 / wav。

修法：`QT_MULTIMEDIA_PREFERRED_PLUGINS=windows,dsengine`（优先 Media Foundation，
插件不可用时回退）。必须在 QMediaPlayer 创建前设置，入口在 `run_gui.py` 和
`gui/__init__.py`；用户可在开发者面板「组件」页改，写 `settings.ini` 的
`playback_backend`，重启生效。

排查顺序：先分后端（三种取值各跑一遍格式矩阵），再看系统解码器（`MFTEnumEx`），
最后才怀疑文件本身。曾经因为跳过后端这一步，白写了一版自动转码。

#### QSS：祖先样式表里的通用规则会压过子控件的 ID 选择器

现象：给帮助卡片写 `QWidget#helpCard { background: #2a2a30; }`，放在对话框的
`setStyleSheet()` 里，实测底色一直是窗口色（像素采样确认，改了三轮都没生效）。

原因两层：普通 `QWidget` 不打开 `WA_StyledBackground` 就根本不画 QSS 背景；而
`gui/style.py` 的 `QWidget { background: ... }` 是设在祖先上的，对这种「祖先通用规则
vs 自身 ID 规则」的层叠，实际结果是通用规则赢。

修法：两块都要——`card.setAttribute(Qt.WA_StyledBackground, True)`，并且用
**控件自身**的 `setStyleSheet()` 写这条规则。

#### QPalette.PlaceholderText 默认纯黑，QSS 改不了

现象：深色输入框里的占位文字完全看不见（搜索框的「搜索歌名，或粘贴歌曲 / 歌单链接」）。

QSS 没有 placeholder 相关属性，只能改 palette：

```python
palette = app.palette()
palette.setColor(QPalette.PlaceholderText, QColor("#7d7d86"))
app.setPalette(palette)
```

放在 `MainWindow.__init__` 里，从哪个入口启动都生效。

#### QDialog 里按回车会点中「默认按钮」

现象：在指令页按回车执行命令，同时把「导入回放」也点了一下，页面被切走。

原因：QPushButton 被 reparent 进 QDialog 时，Qt 会自动把它设成 autoDefault，
QDialog 的 Return 处理会去找这样的按钮。**布局刚建好时 setAutoDefault(False) 没用**，
reparent 发生在之后。

修法：在 `showEvent` 里再遍历一次 `findChildren(QPushButton)` 关掉。

#### QTabWidget 标签一多就被挤掉

6 个标签在约 600px 宽里放不下：Qt 会把第一个标签挤出可视区并出现 ◄► 滚动箭头，
用户根本看不到还有第一页。开发者面板已改成「一行互斥按钮 + QStackedWidget」，
以后加页也不会再挤。

#### 非模态子窗口不会跟着父窗口关闭

帮助窗口的 parent 是开发者面板，但面板 `close()` 时它仍留在屏幕上（Qt 只对
window 类型的子控件在 parent 销毁时处理）。需要在面板的 `_detach()` 里显式关闭。

#### QMessageBox 会破坏整套深色界面

现象：「缓存诊断」弹出来是白底 + 系统标题栏 + 蓝色信息图标 + 浅色按钮，和主窗口
完全不像同一个程序。

`QMessageBox` 是系统原生窗口，不走应用 QSS。所以提示 / 确认一律用
`gui.dialog.MessageDialog`：

```python
MessageDialog.info(self, "空间不够", "……", tone="warn")
if not MessageDialog.ask(self, "清理下载残留", "……", yes="清理", tone="warn"):
    return
```

需要分区展示就实例化，`add_heading / add_card / add_row / add_text` 往里填，最后
`add_button(...)` + `finish()` + `exec_()`。`tone` 取 `ok / info / warn / error`，
只影响左上角那个状态徽标。`finish()` 会在内容超过 600px 时自动套滚动区。

#### 开了 wordWrap 的 QLabel 会把窗口撑出一块空白

现象：内容只有两行字，窗口却有近百像素的多余高度，按钮浮在中间。

`QLabel::sizeHint()` 在 `wordWrap=True` 时按「很窄的宽度」估算行数。一行 15px 高的
文件名会被报成 45px，卡片和窗口跟着虚高。

修法：定稿尺寸前按真实可用宽度锁高度（见 `MessageDialog.finish()`）：

```python
shown = label.heightForWidth(available_width)
if shown > 0:
    label.setFixedHeight(shown)
```

只在能排成一行时才关掉换行并 `setFixedHeight`；确实要折行的长文本保持原样。

#### PyQt 里 QLayout 的布尔值是 False

现象：`add_text(text, layout=card_layout)` 里写成 `(layout or self.content_layout)`
时，传进来的卡片布局被默默丢掉，文字全跑到卡片外面，卡片变成一个空框。

```python
target = layout if layout is not None else self.content_layout   # 正确
target = layout or self.content_layout                            # 错误
```

PyQt 的 `QLayout` 没有 `__len__`，`bool()` 走的是 sip 的「底层对象是否有效」判断，
结果是 False。同类坑也适用于部分 Qt 对象，判断「有没有传」时一律用 `is None`。

## 三、接口相关

### 音源与身份

服务端按 **UA + appver** 判断「端」，进而决定下发什么权限：

| 音源 | UA | 特点 |
|---|---|---|
| `client` | `NeteaseMusicDesktop/2.10.13` + `appver=8.10.90` | 可用客户端权益（如看广告免费听），音质档位更高 |
| `web` | 浏览器 UA | 部分客户端权益不可用，音质偏低 |
| `cache` | 不请求接口 | 只读本地 `.uc` |

音质列表：`standard / higher / exhigh / lossless / hires`。**实际拿到的档位由账号权益决定**，请求无损但账号没有权益时，接口会返回较低码率，`AudioSource.degraded` 会标出来。

### 接口变动怎么查

1. 用 `python -m ncm url <song_id> --level hires` 看返回
2. 拿不到地址时先确认 cookie 是否过期（`python -m ncm whoami`）
3. 接口整体失效通常是 `eapi` 的 key 或参数结构变了，改 `ncm/eapi.py`

### 缓存格式

客户端缓存是 `.uc`（NCM 容器）+ `.idx` 配对。`ncm/crypto.py` 负责解密，`ncm/cache.py` 负责扫描配对。
**只读，不要修改客户端文件**，否则客户端自身会出问题。

## 四、发布流程

```bash
# 1. 改版本号（两处，必须一致）
#    ncm/__init__.py:  __version__      ← 真正生效的那处
#    pyproject.toml:   version
#    （gui/__init__.py 只是 re-export，不用改）

# 2. 回归
python -m pyflakes gui/ ncm/ tools/ tests/
python tests/gui_smoke.py

# 3. 更新截图（自动脱敏）
python tools/preview.py

# 4. 打包（文件夹版）
python -m PyInstaller --noconfirm --onedir --windowed ^
  --name "网易云下载器" --icon "gui/assets/app.ico" ^
  --add-data "gui/assets;gui/assets" ^
  --hidden-import PyQt5.QtMultimedia --hidden-import PyQt5.QtMultimediaWidgets ^
  --distpath pack --workpath pack_build run_gui.py

# 5. 回填 ffmpeg 与说明（build.bat 一条命令做完 4、5）
mkdir "pack\网易云下载器\ffmpeg\binaries"
copy "<site-packages>\imageio_ffmpeg\binaries\ffmpeg-win-*.exe" "pack\网易云下载器\ffmpeg\binaries\"
copy "docs\使用说明.txt" "pack\网易云下载器\"

# 6. 打 zip（自动排除日志、__pycache__、diagnostics）
python tools/make_zip.py            # → dist\网易云下载器-vX.X.X-win64.zip

# 7. 实测（三项必做）
#    双击 exe 能开窗 → pack\...\ffmpeg\binaries\ 有 ffmpeg → 下载一首并转一次 mp3

# 8. 更新 CHANGELOG.md，提交
git add -A && git commit -m "发布 v1.3.0"
```

### 发布检查清单

- [ ] 版本号两处一致（`ncm/__init__.py` + `pyproject.toml`）
- [ ] `pyflakes` 无输出
- [ ] `gui_smoke.py` 退出码 0
- [ ] 截图无真实账号、无真实登录二维码
- [ ] exe 双击能开窗、标题与版本正确
- [ ] `pack\网易云下载器\ffmpeg\binaries\` 有 ffmpeg
- [ ] `pack\网易云下载器\使用说明.txt` 是当前版本
- [ ] zip 里没有 `*.log`、`diagnostics`、`__pycache__`
- [ ] 下载 + 转 mp3 实测通过
- [ ] CHANGELOG 已更新为正式版本号（不是「未发布」）

## 五、还没做的（候选需求）

按性价比排序，供后续取舍：

1. **Cookie 加密存储** — 现在是 `config.json` 明文 + `0o600`（Windows 上无效），可上 DPAPI
2. **歌词 / 封面开关** — 目前默认开启写入，没有界面开关
3. **按音质筛选搜索结果** — 需要额外请求每首的可用档位，代价较大
4. **队列优先级** — 并发派发后再排序没有意义，要做就得改 Downloader 的调度
5. **强代码保护** — 见打包踩坑，需先解决编译器问题

已完成（v1.2.0）：下载完成托盘通知、托盘常驻、媒体键、整批暂停/继续、失败原因分类。

## 六、风险提示

- 本项目涉及第三方音乐平台的接口与版权内容，公开分发前请自行评估合规风险
- 默认分支建议保持私有，或仅发布编译产物、不公开源码
- `config.json` 含登录 cookie，**永远不要提交**
