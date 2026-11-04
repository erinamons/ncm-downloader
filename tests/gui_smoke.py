"""界面链路冒烟测试：搜索 → 勾选 → 下载 → 汇总。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from gui.main import MainWindow


def pump(app, cond, timeout=90, label=""):
    start = time.time()
    while cond():
        app.processEvents()
        time.sleep(0.05)
        if time.time() - start > timeout:
            raise TimeoutError(f"{label} 超时")


def main() -> int:
    app = QApplication(sys.argv)
    return run(app)


def run(app) -> int:
    win = MainWindow()
    # 测试会改写 out_dir，必须在同一个 QSettings 实例上改回去：
    # 另开实例写入会被主窗口那份在析构时 flush 覆盖回来
    saved_out_dir = win.settings.value("out_dir")
    win.out_dir = Path("test-gui")
    win.show()

    try:
        return run_cases(app, win)
    finally:
        win.close()
        if saved_out_dir is None:
            win.settings.remove("out_dir")
        else:
            win.settings.setValue("out_dir", saved_out_dir)
        win.settings.sync()


def run_cases(app, win) -> int:

    # 启动时若上次停在「仅缓存」会自动扫描，先等它跑完
    pump(app, lambda: win.task is not None, 90, "启动加载")

    # 固定走接口搜索，避免受上次保存的音源影响
    from gui.main import GUI_SOURCES
    win.source_box.setCurrentIndex(GUI_SOURCES.index("client"))
    win._base_songs = None
    win._clear_current()

    win.input.setText("Matryoshka")
    win.on_search()
    pump(app, lambda: win.task is not None, 40, "搜索")
    print(f"[1] 搜索完成，命中 {len(win.songs)} 首")
    if not win.songs:
        print("搜索无结果，测试中止")
        return 1

    win._set_all(False)
    for row in range(min(2, win.result_table.rowCount())):
        win.result_table.item(row, 0).setCheckState(Qt.Checked)
    print(f"[2] 勾选 {len(win._selected_songs())} 首，按钮文案：{win.btn_download.text()}")

    win.on_download()
    pump(app, lambda: win.batch is not None, 180, "下载")
    print(f"[3] 下载结束，汇总：{win.summary_label.text()}")
    print(f"[4] 状态栏：{win.status_label.text()}")

    files = [p for p in Path("test-gui").rglob("*") if p.is_file() and p.suffix != ".db"]
    for f in files:
        print(f"    文件 {f.relative_to('test-gui')} {f.stat().st_size/1048576:.1f} MB")
    print(f"[5] 落盘 {len(files)} 个文件，按钮恢复：{win.btn_download.isEnabled()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
