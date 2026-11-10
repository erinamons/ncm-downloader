"""图形界面启动入口：python run_gui.py"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Qt5 在 Windows 上默认选 DirectShow 后端，而 DirectShow 没有 AAC / FLAC 解码器：
# 客户端缓存解出来是 m4a、无损是 flac，默认后端下必然「播放失败」，只有 mp3/wav
# 能放。Media Foundation（插件键 "windows"）直接用系统自带解码器，实测
# m4a / flac / mp3 / wav 全通。必须在 QMediaPlayer 创建前设置。
def _preferred_backend() -> str:
    try:
        from PyQt5.QtCore import QSettings

        from ncm.paths import settings_path

        store = QSettings(str(settings_path()), QSettings.IniFormat)
        value = (store.value("playback_backend", "", type=str) or "").strip()
        return value or "windows,dsengine"
    except Exception:  # noqa: BLE001
        return "windows,dsengine"


os.environ.setdefault("QT_MULTIMEDIA_PREFERRED_PLUGINS", _preferred_backend())

# 常驻记录要早过 gui.main 的导入：依赖模块在 import 阶段打的日志也要能回看。
# 这里只开内存缓冲和异常钩子，文件日志在 main() 里由 setup_logging 补上。
from ncm.runlog import runlog as _runlog  # noqa: E402

_runlog.install()

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QIcon  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from gui.main import MainWindow  # noqa: E402
from ncm import __version__ as APP_VERSION  # noqa: E402
from ncm.log import setup_logging  # noqa: E402
from ncm.runlog import runlog  # noqa: E402


def main() -> None:
    # C++/Qt 层段错误不会走 Python 异常机制，faulthandler 能把崩溃时的
    # Python 调用栈写到文件里，是定位 segfault 的唯一线索。
    # 注意：--windowed 打包后没有控制台，sys.stderr 是 None，enable() 不给
    # 文件参数会直接 RuntimeError（表现为双击 exe 秒退），必须显式开文件
    import faulthandler
    import logging

    log_file = setup_logging()
    # 常驻记录要尽早挂上：窗口构造期间的错误也才有据可查
    runlog.install(version=APP_VERSION)
    crash_fh = Path(log_file).with_name("crash.log").open(
        "a", encoding="utf-8", errors="replace"
    )
    faulthandler.enable(file=crash_fh)
    logging.getLogger("ncm.gui").info("启动 v%s，日志位于 %s", APP_VERSION, log_file)

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("网易云音乐下载器")
    # 任务栏与 Alt+Tab 用的图标；窗口自己的图标在 MainWindow 里设
    icon = Path(__file__).resolve().parent / "gui" / "assets" / "app.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
