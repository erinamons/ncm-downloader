# 架构说明

## 分层

```
run_gui.py ──► gui/   (PyQt5 界面)
   │              │
   │              ├─► ncm/downloader  批量下载、转码、打标签
   │              ├─► ncm/backend     网易云接口封装
   │              ├─► ncm/cache       客户端缓存扫描
   │              ├─► ncm/store       已下载索引（SQLite）
   │              └─► ncm/trace       流程埋点（开发者面板的数据源）
   │
ncm-dl.bat ──► ncm/cli.py  (同一条链路，无 GUI 依赖)
```

`ncm/` 不依赖 PyQt5，图形界面和命令行共用同一套逻辑、同一份配置、同一个下载索引。

## 核心模块

| 模块 | 职责 | 关键点 |
|---|---|---|
| `ncm/backend.py` | 搜索、详情、歌单、取播放地址 | 两组 UA：客户端 `NeteaseMusicDesktop` + `appver`，网页 UA；服务端按此决定下发什么权限。Session 按线程隔离 |
| `ncm/eapi.py` | eapi 参数加密 | AES-ECB + 固定 key，部分接口走 `weapi` |
| `ncm/crypto.py` | 音频文件解密 | `.uc` 缓存是 NCM 容器，按字节流解密 |
| `ncm/cache.py` | 扫描客户端缓存 | `.uc` + `.idx` 配对，取标题/歌手/码率；只读，不改动客户端文件 |
| `ncm/downloader.py` | 单曲 / 批量下载 | 断点续传（`.part`）、跳过已存在、转码、写标签与歌词 |
| `ncm/store.py` | 下载索引 | SQLite（WAL + 15s timeout），记录路径/码率/大小，用于去重与增量补全 |
| `ncm/tagger.py` | 写 ID3 / FLAC 标签 | mutagen，含封面与歌词 |
| `ncm/transcode.py` | ffmpeg 封装 | 路径优先级：手动指定 → 程序自带 → PATH → imageio-ffmpeg；支持转 mp3 / 转 WAV |
| `ncm/archive.py` | 缓存存档 | 把听过的缓存留下来，更高码率会替换 |
| `ncm/auth.py` | 登录态 | 二维码登录、cookie 持久化到 `config.json` |
| `ncm/targets.py` | 输入解析 | 识别歌曲 ID、歌单/专辑链接、分享文本 |
| `ncm/paths.py` | 便携路径 | 全部数据落在程序目录；不可写时退回 `%USERPROFILE%\.ncm-downloader` |
| `ncm/models.py` | 数据模型 | `Song` / `AudioSource`，音质档位与降级判定 |
| `ncm/trace.py` | 流程埋点 | `trace(scope, step, status, ms, detail, group)`，无监听者时零开销 |
| `ncm/cli.py` | 命令行 | typer + rich；`--json` 时 stdout 只有一行 JSON，过程提示走 stderr |
| `ncm/log.py` | 日志 | 滚动文件，捕获未处理异常与线程异常 |

## 界面层

| 模块 | 说明 |
|---|---|
| `gui/main.py` | 主窗口：无边框、自绘圆角与阴影、结果表 + 下载队列 + 状态栏 |
| `gui/player.py` | `PlayerEngine`（QMediaPlayer 封装，含兼容播放兜底）+ `TopBar` |
| `gui/table.py` | `FluidTable`：所有列可拖，剩余宽度给曲名列 |
| `gui/workers.py` | `Task` / `BatchTask`：QThread 包装，结果用信号回主线程 |
| `gui/dialog.py` | `RoundDialog`：对话框基类，复用圆角/阴影/标题栏 |
| `gui/devtools.py` | 开发者面板：流程时间线、日志、问题、组件、指令台、环境、诊断包 |
| `gui/devhelp.py` | 开发者面板的帮助（条目清单 + 搜索） |
| `gui/help.py` | 面向用户的帮助（按钮图解，F1 打开） |
| `gui/tray.py` | 托盘图标、菜单、媒体键 |
| `gui/style.py` | 全局 QSS 与配色常量 |
| `gui/icons.py` | Lucide 线性 SVG → QIcon，按颜色与尺寸缓存 |
| `gui/shadow.py` | 生成高斯模糊阴影位图，带 LRU 缓存 |
| `gui/history_popup.py` | 搜索历史浮层（窗口内子控件，不是系统弹窗） |
| `gui/marquee.py` | 长曲名滚动 |

## 数据流

### 搜索

```
输入框 ──► targets.parse_target()
              ├─ 是链接 → backend.playlist() / song_detail()
              └─ 是关键词 → backend.search(limit=50, offset=n)
                          └─► 主线程填充 result_table
```

搜索结果只做接口层过滤；缓存视图里的搜索框是本地过滤，不发请求。

### 下载

```
勾选行 ──► _selected_songs() ──► BatchTask(Downloader, songs)
                                     │ 4 线程并发
                                     ├─ store.done()? → SKIPPED
                                     ├─ cache 命中? → 直接从 .uc 解密
                                     ├─ backend.song_url() → 拿直链
                                     ├─ _fetch_stream() → 写 .part，可断点续传
                                     ├─ os.replace(.part → 目标)
                                     └─ tagger 写标签 / transcode 转 mp3
                                     ▼
                              信号 → 队列行状态 + 进度条
```

### 播放

取本地文件优先级：**已下载（store 索引）→ 播放缓存（`play-cache`）→ 客户端缓存
（`.uc` 解密）→ 网络**。`PlayerEngine` 在独立线程里准备文件，避免阻塞 UI。

播放失败时的兜底顺序：

1. 系统能放 → 直接播（Media Foundation 后端，m4a/flac/mp3/wav 全支持）
2. 系统放不了 + 兼容播放开 → ffmpeg 无损转 WAV 再播，产物留在 `play-cache`
3. 都不行 → 状态栏给一句人话 + 可点动作，技术细节只进 `app.log`

### 开发者面板

```
ncm.trace.trace() ──┐
logging（root）   ──┴─► 队列 ──► QTimer(140ms) ──► 时间线 / 日志 / 问题
                                                  │
主窗口状态（播放器/任务/队列）──► QTimer(1s) ─────┘
```

工作线程只入队，UI 侧批量取，避免高频日志把主线程拖卡。面板关闭时摘掉 Handler 与
监听器，文件日志不受影响。

## 关键设计取舍

| 问题 | 做法 | 原因 |
|---|---|---|
| 无边框窗口 | `WA_TranslucentBackground` + 自绘阴影 | 保留圆角与外发光；代价是 resize 需要额外处理（见维护文档） |
| 播放后端 | `QT_MULTIMEDIA_PREFERRED_PLUGINS=windows,dsengine` | Qt5 默认 DirectShow 没有 AAC/FLAC 解码器，必须优先 Media Foundation |
| 兼容播放 | 默认关闭，用户手动开 | 转码有等待和磁盘占用，不能擅自做 |
| 历史浮层 | 主窗口内子控件，非 `Qt::Popup` | Popup 会占住鼠标 grab，打开设置等对话框会卡住 |
| 列表列宽 | 全部 `Interactive`，手动分配余量 | Qt 的 `Stretch` 模式下列边界拖不动，且会反向挤占 |
| 面板页签 | 一行互斥按钮 + `QStackedWidget` | 6 个标签在默认宽度下会被挤掉并出现滚动箭头 |
| CLI 输出 | `--json` 时 stdout 单行 JSON，过程走 stderr | 既要人看得懂表格，也要程序能直接解析 |
| 线程模型 | 所有网络/磁盘操作走 `Task`，UI 只收信号 | 保证主线程不阻塞，进度更新不会重入 |

## 目录

```
ncm-downloader/
├── run_gui.py            图形界面入口
├── run.bat               开发期启动（自动找 Python）
├── ncm-dl.bat            命令行入口（不启动界面）
├── build.bat             打包脚本
├── ncm/                  核心逻辑（无 GUI 依赖）
├── gui/                  界面与自绘控件
│   └── assets/           图标、应用图标
├── tools/                开发工具：打包 zip、图标生成、截图
├── tests/                冒烟测试
└── docs/                 文档与截图
```
