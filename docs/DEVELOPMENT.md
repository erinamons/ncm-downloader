# 开发指南

## 环境

- Windows 10 / 11
- Python 3.10+（开发验证于 3.12 / 3.13）
- 依赖见 `requirements.txt`

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyflakes pyinstaller        # 静态检查与打包
```

## 运行

| 方式 | 命令 |
|---|---|
| 图形界面 | `python run_gui.py` 或双击 `run.bat` |
| 命令行 | `python -m ncm --help` |
| 命令行示例 | `python -m ncm dl 501468000 --level hires` |
| 命令行（自动化） | `ncm-dl.bat dl 501468000 --json` |

`ncm-dl.bat` 与 `run.bat` 一样会自愈解释器，但不要求 PyQt5——命令行只需要
`requests / mutagen / typer / rich / pycryptodome`。

## 目录约定

- `ncm/` **不允许** 导入 PyQt5。需要弹窗、进度的地方用回调或返回数据，由 GUI 层展示。
- `gui/` 只做展示与交互，业务规则放 `ncm/`，方便命令行复用。
- 颜色、字体、圆角统一写在 `gui/style.py`。新增界面代码应尽量避免裸色值；确实需要时
  集中在一个函数或常量里。
- 流程埋点走 `ncm.trace.trace()`，不要直接 `print` 或往 UI 里塞日志字符串。

## 代码风格

- 中文注释，解释「为什么」而不是「做了什么」。
- 函数超过 40 行考虑拆分；`gui/main.py` 已偏大，新功能优先拆到独立模块。
- 类型标注尽量补全，`from __future__ import annotations` 放在文件头。
- 提交前跑一遍：

```bash
python -m pyflakes gui/ ncm/ tools/ tests/
python tests/gui_smoke.py
```

## 调试

### 开发者面板

设置 → 开发者 → 开发者面板（代码在 `gui/devtools.py`）。它有六页：状态 / 日志 / 问题 /
组件 / 指令 / 环境，左侧是结构化的流程时间线，右下角能导出诊断包。日常排查优先用它，
比翻 `app.log` 快。

面板里的「指令」支持 `url <id>`、`status`、`backend`、`cache clear` 等，导航行最右的
? 是它自己的帮助（`gui/devhelp.py`，条目数据就是 `HELP_ITEMS` 列表，改功能时顺手补一条）。

### 日志

运行日志在程序目录下的 `app.log`（便携模式；目录不可写时才退到
`%USERPROFILE%\.ncm-downloader`），滚动保留 3 份 × 1 MB。崩溃栈写 `crash.log`。

```python
import logging
log = logging.getLogger("ncm.gui")
log.info("...")
```

未捕获异常（含子线程）会自动写入，排查线上问题时先看这里。

### 常用断点位置

| 症状 | 先看 |
|---|---|
| 搜索没结果 | `backend.search()` 返回、`_on_songs_loaded()` |
| 下载失败 | `downloader.download()` 的 `TaskResult.message`，日志里 `下载失败 ...` 一行 |
| 音质不对 | `backend.song_url()` 返回的 `br` / `quality_label`，以及 `degraded` 标记 |
| 转码没执行 | `transcode.ffmpeg_path()` 是否返回路径 |
| 播放失败 | `PlayerEngine._on_error()` 的日志行（后端 / 格式 / error / 文件大小） |
| 缓存列表为空 | `cache.default_cache_dir()` 是否命中；`cache.scan()` 是否有 `.uc` 文件 |
| 界面卡顿 | 开发者面板「状态」页看队列与任务；时间线看是否有步骤耗时异常 |

### 界面截图

`python tools/preview.py` 会生成 `docs/*.png`。脚本里已把账号替换为「演示账号」、
登录二维码替换成指向项目网站的二维码——**新增截图时务必保持这个脱敏逻辑**，否则真实
昵称和登录二维码会进仓库。

## 测试

目前是冒烟测试，覆盖主流程：

```bash
python tests/gui_smoke.py    # 退出码 0 表示通过
```

它验证：窗口构建、搜索、勾选、下载 2 首、文件落盘、按钮状态恢复。

`tests/cache_browse.py` 用于手动验证缓存扫描，需要本机装网易云客户端并有缓存。

### 写新测试的注意点

- 不要往真实配置里写：用 `QSettings('ncm-test', ...)` 或临时目录，测试结束还原
- 下载测试用独立输出目录（如 `test-gui`），跑完清理
- 涉及窗口的测试记得 `app.processEvents()`，否则布局与信号还没处理完
- 给 `settings.ini` 做备份再测那些会 `accept()` 的对话框——一次误保存就能把
  `startup_view`、`folder_pattern` 写成别的值

## 打包

见根目录 `README.md` 的「打包」一节，以及 `docs/MAINTENANCE.md` 里的踩坑记录。

打包后必须实测三件事：

1. 窗口能打开（用 `Start-Process`，bash 直接跑中文名 exe 会静默失败）
2. `pack\网易云下载器\ffmpeg\binaries\` 下有 ffmpeg
3. 下载一首歌、转一次 mp3

## 分支与提交

- 提交信息用中文，一句话说清改了什么：`修复搜索历史浮层点空白处不关闭`
- 一次提交只做一件事，样式与逻辑改动分开
- 涉及界面变化的，附上 `docs/` 里的新截图
