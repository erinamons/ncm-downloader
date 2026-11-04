"""验证界面里的「本地缓存」入口能正确列出曲目。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtWidgets import QApplication

from gui.main import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.on_load_cache()

    start = time.time()
    while win.task is not None:
        app.processEvents()
        time.sleep(0.05)
        if time.time() - start > 90:
            print("超时")
            return 1

    rows = win.result_table.rowCount()
    print("提示:", win.result_info.text())
    print("表格行数:", rows)
    for i in range(min(5, rows)):
        name = win.result_table.item(i, 1)
        artist = win.result_table.item(i, 2)
        print(f"  {name.text() if name else '-'} | {artist.text() if artist else '-'}")
    win.close()
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
