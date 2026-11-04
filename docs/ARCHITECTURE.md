# 架构说明

## 分层

```
run_gui.py ──► gui/  (PyQt5 界面)
                 │
                 ├─► ncm/downloader  批量下载、转码、打标签
                 ├─► ncm/backend     网易云接口封装
                 ├─► ncm/cache       客户端缓存扫描
                 └─► ncm/store       已下载索引（SQLite）
```

`ncm/` 不依赖 PyQt5，命令行 `python -m ncm` 与 GUI 共用同一套逻辑。

## 核心模块

| 模块 | 职责 | 关键点 |
|---|---|---|
| `ncm/backend.py` | 搜索、详情、歌单、取播放地址 | 两组 UA：客户端 `NeteaseMusicDesktop` + `appver`，网页 UA；服务端按此决定下发什么权限 |
| `ncm/eapi.py` | eapi 参数加密 | AES-ECB + 固定 key，部分接口走 `weapi` |
| `ncm/crypto.py` | 音频文件解密 | `.uc` 缓存是 NCM 容器，按字节流解密 |
| `ncm/cache.py` | 扫描客户端缓存 | `.uc` + `.idx` 配对，取标题/歌手/码率；只读，不改动客户端文件 |
| `ncm/downloader.py` | 单曲 / 批量下载 | 断点续传（`.part`）、跳过已存在、转码、写标签与歌词 |
| `ncm/store.py` | 下载索引 | SQLite，记录路径/码率/大小，用于去重与增量补全 |
| `ncm/tagger.py` | 写 ID3 / FLAC 标签 | mutagen，含封面与歌词 |
| `ncm/auth.py` | 登录态 | 二维码登录、cookie 持久化到 `~/.ncm-downloader/config.json` |
| `ncm/targets.py` | 输入解析 | 识别歌曲 ID、歌单链接、分享文本 |
| `ncm/log.py` | 日志 | 滚动文件，捕获未处理异常与线程异常 |

## 界面层

| 模块 | 说明 |
|---|---|
| `gui/main.py` | 主窗口：无边框、自绘圆角与阴影、结果表 + 下载队列 + 状态栏 |
| `gui/player.py` | `PlayerEngine`（QMediaPlayer 封装）+ `TopBar`（标题栏与播放条合一） |
| `gui/table.py` | `FluidTable`：所有列可拖，剩余宽度给曲名列 |
| `gui/workers.py` | `Task` / `BatchTask`：QThread 包装，结果用信号回主线程 |
| `gui/style.py` | 全局 QSS 与配色常量 |
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

搜索结果只做接口层过滤，界面上不再二次筛选（缓存视图例外，那是本地过滤）。

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

取本地文件优先级：**已下载（store 索引）→ 客户端缓存（`.uc` 解密到临时目录）→ 没有**。
`PlayerEngine` 在独立线程里准备文件，避免阻塞 UI。

## 关键设计取舍

| 问题 | 做法 | 原因 |
|---|---|---|
| 无边框窗口 | `WA_TranslucentBackground` + 自绘阴影 | 保留圆角与外发光；代价是 resize 需要额外处理（见维护文档） |
| 历史浮层 | 主窗口内子控件，非 `Qt::Popup` | Popup 会占住鼠标 grab，打开设置等对话框会卡住 |
| 列表列宽 | 全部 `Interactive`，手动分配余量 | Qt 的 `Stretch` 模式下列边界拖不动，且会反向挤占 |
| 配色 | QSS 统一在 `gui/style.py` | 避免散落各处的硬编码色值 |
| 线程模型 | 所有网络/磁盘操作走 `Task`，UI 只收信号 | 保证主线程不阻塞，进度更新不会重入 |

## 目录

```
ncm-downloader/
├── run_gui.py            图形界面入口
├── run.bat               开发期启动（自动找 Python）
├── build.bat             打包脚本
├── ncm/                  核心逻辑（无 GUI 依赖）
├── gui/                  界面与自绘控件
│   └── assets/           图标、应用图标
├── tools/                开发工具：截图生成、图标生成
├── tests/                冒烟测试
└── docs/                 文档与截图
```
