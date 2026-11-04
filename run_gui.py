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
    log_file = setup_logging()
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
