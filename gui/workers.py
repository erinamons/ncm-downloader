"""后台线程：所有网络与磁盘操作都不在主线程执行。"""
from __future__ import annotations

from typing import Any, Callable

from PyQt5.QtCore import QThread, pyqtSignal

from ncm.downloader import Downloader, TaskResult
from ncm.models import Song


class Task(QThread):
    """跑一个可调用对象，结果通过信号回主线程。

    wants_progress=True 时，可调用对象会多收到一个 emit 函数，用来往状态栏
    推「3/50」这类进度——缓存存档要从几十个文件里解密时，光转圈用户会以为卡死。
    """

    ok = pyqtSignal(object)
    fail = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, fn: Callable[..., Any], parent=None, wants_progress: bool = False):
        super().__init__(parent)
        self._fn = fn
        self._wants_progress = wants_progress

    def run(self) -> None:
        try:
            if self._wants_progress:
                self.ok.emit(self._fn(lambda msg: self.progress.emit(msg)))
            else:
                self.ok.emit(self._fn())
        except Exception as exc:  # noqa: BLE001
            self.fail.emit(str(exc))


class BatchTask(QThread):
    """批量下载。内部的并发由 Downloader 的线程池控制。"""

    on_start_song = pyqtSignal(object)
    on_progress_song = pyqtSignal(int, int, int)
    on_result = pyqtSignal(object)
    finished_all = pyqtSignal(list)
    fail = pyqtSignal(str)

    def __init__(self, engine: Downloader, songs: list[Song], parent=None):
        super().__init__(parent)
        self._engine = engine
        self._songs = songs

    def cancel(self) -> None:
        self._engine.cancel()

    def run(self) -> None:
        try:
            results: list[TaskResult] = self._engine.run(
                self._songs,
                on_result=self.on_result.emit,
                on_start=self.on_start_song.emit,
                on_progress=lambda s, d, t: self.on_progress_song.emit(s.id, d, t),
            )
        except Exception as exc:  # noqa: BLE001
            self.fail.emit(str(exc))
            results = []
        self.finished_all.emit(results)
