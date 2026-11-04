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

#### 3. PyInstaller 不会自动收 ffmpeg

`--collect-all imageio_ffmpeg` 只收 Python 模块，**100 MB 的 ffmpeg 二进制不会进包**。表现：打包版「转 mp3」不可用，设置里显示「转码引擎：未找到」。

必须显式：

```
--add-binary "<site-packages>/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v9.0.2.exe;imageio_ffmpeg/binaries"
```

验证方法：启动 exe 后看 `%TEMP%\_MEI*\imageio_ffmpeg\binaries\` 下有没有 100 MB 的文件。

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

## 二、接口相关

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

## 三、发布流程

```bash
# 1. 改版本号（只有一处）
#    gui/__init__.py: __version__
#    pyproject.toml: version

# 2. 回归
python -m pyflakes gui/ ncm/ tools/ tests/
python tests/gui_smoke.py

# 3. 更新截图（自动脱敏）
python tools/preview.py

# 4. 打包
python -m PyInstaller --noconfirm --onefile --windowed ...   # 见 README

# 5. 实测（三项必做）
#    启动 → 检查 %TEMP%\_MEI*\imageio_ffmpeg\binaries\ → 下载并转一首 mp3

# 6. 更新 CHANGELOG.md，提交
git add -A && git commit -m "发布 v1.0.0"
git tag v1.0.0
```

### 发布检查清单

- [ ] 版本号两处一致
- [ ] `pyflakes` 无输出
- [ ] `gui_smoke.py` 退出码 0
- [ ] 截图无真实账号、无真实登录二维码
- [ ] exe 双击能开窗、标题正确
- [ ] 内置 ffmpeg 存在（`_MEI` 目录）
- [ ] 下载 + 转 mp3 实测通过
- [ ] CHANGELOG 已更新

## 四、还没做的（候选需求）

按性价比排序，供后续取舍：

1. **下载完成系统通知** — 现在只闪任务栏；要气泡通知需引 `QSystemTrayIcon` 常驻托盘
2. **副屏拔掉后窗口位置** — 已改成每次居中打开，此问题自然消失；若日后恢复位置记忆需重新校验
3. **Cookie 加密存储** — 现在是 `config.json` 明文 + `0o600`（Windows 上无效），可上 DPAPI
4. **歌词 / 封面开关** — 目前默认开启写入，没有界面开关
5. **按音质筛选搜索结果** — 需要额外请求每首的可用档位，代价较大
6. **强代码保护** — 见打包踩坑，需先解决编译器问题

## 五、风险提示

- 本项目涉及第三方音乐平台的接口与版权内容，公开分发前请自行评估合规风险
- 默认分支建议保持私有，或仅发布编译产物、不公开源码
- `config.json` 含登录 cookie，**永远不要提交**
