"""图形界面启动入口：python run_gui.py"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QIcon  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from gui.main import MainWindow  # noqa: E402
from ncm.log import setup_logging  # noqa: E402


def main() -> None:
    # C++/Qt 层段错误不会走 Python 异常机制，faulthandler 能把崩溃时的
    # Python 调用栈写到文件里，是定位 segfault 的唯一线索。
    # 注意：--windowed 打包后没有控制台，sys.stderr 是 None，enable() 不给
    # 文件参数会直接 RuntimeError（表现为双击 exe 秒退），必须显式开文件
    import faulthandler
    import logging

    log_file = setup_logging()
    crash_fh = Path(log_file).with_name("crash.log").open(
        "a", encoding="utf-8", errors="replace"
    )
    faulthandler.enable(file=crash_fh)
    logging.getLogger("ncm.gui").info("启动，日志位于 %s", log_file)

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
