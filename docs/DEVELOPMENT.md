# 开发指南

## 环境

- Windows 10 / 11
- Python 3.10+（开发验证于 3.13）
- 依赖见 `requirements.txt`

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 运行

| 方式 | 命令 |
|---|---|
| 图形界面 | `python run_gui.py` 或双击 `run.bat` |
| 命令行 | `python -m ncm --help` |
| 命令行示例 | `python -m ncm dl 501468000 --level hires` |

## 目录约定

- `ncm/` **不允许** 导入 PyQt5。需要弹窗、进度的地方用回调或返回数据，由 GUI 层展示。
- `gui/` 只做展示与交互，业务规则放 `ncm/`，方便命令行复用。
- 颜色、字体、圆角统一写在 `gui/style.py`，界面代码里不再出现裸色值（`#242428` 这类只能在 style.py 出现）。

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

### 日志

运行日志在 `%USERPROFILE%\.ncm-downloader\app.log`，滚动保留 3 份 × 1 MB。

```python
import logging
log = logging.getLogger("ncm.gui")
log.info("...")
```

未捕获异常（含子线程）会自动写入该文件，排查线上问题时先看这里。

### 常用断点位置

| 症状 | 先看 |
|---|---|
| 搜索没结果 | `backend.search()` 返回、`_on_songs_loaded()` |
| 下载失败 | `downloader.download()` 的 `TaskResult.message`，日志里 `下载失败 ...` 一行 |
| 音质不对 | `backend.song_url()` 返回的 `br` / `quality_label`，以及 `degraded` 标记 |
| 转码没执行 | `transcode.ffmpeg_path()` 是否返回路径；设置里「转码引擎」那一行的颜色 |
| 缓存列表为空 | `cache.default_cache_dir()` 是否命中；`cache.scan()` 是否有 `.uc` 文件 |

### 界面截图

`python tools/preview.py` 会生成 `docs/*.png`。脚本里已把账号替换为「演示账号」、登录二维码替换成指向项目网站的二维码——**新增截图时务必保持这个脱敏逻辑**，否则真实昵称和登录二维码会进仓库。

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

## 打包

见根目录 `README.md` 的「打包」一节，以及 `docs/MAINTENANCE.md` 里的踩坑记录。

打包后必须实测三件事：

1. 窗口能打开（用 `Start-Process`，bash 直接跑中文名 exe 会静默失败）
2. `%TEMP%\_MEI*\imageio_ffmpeg\binaries\` 下有 ffmpeg
3. 下载一首歌、转一次 mp3

## 分支与提交

- 提交信息用中文，一句话说清改了什么：`修复搜索历史浮层点空白处不关闭`
- 一次提交只做一件事，样式与逻辑改动分开
- 涉及界面变化的，附上 `docs/` 里的新截图
