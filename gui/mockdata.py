"""开发者面板用的模拟数据：假搜索结果 / 假下载队列 / 假缓存列表。

只走界面层：不写文件、不写 downloads 里的下载索引、不起真实下载任务。
所以拿它测出来的现象就是界面本身的反应，清掉之后立刻回到真实数据。

由命令行控制（mock / 模拟数据），入口见 gui/console.py。
"""
from __future__ import annotations

import random

from PyQt5.QtCore import QObject, QTimer

from ncm.downloader import FAILED, OK, SKIPPED, TaskResult
from ncm.models import Song

# id 统一用 990000 以上的号段：和真实歌曲、缓存、下载索引都不会撞上
BASE_ID = 990000

# 故意把边界情况凑齐：超长中文名、多艺人、无专辑、0 时长、日文、英文长名
DEMO_TRACKS: list[tuple[str, list[str], str, int]] = [
    ("Creepin' up on You", ["Darren Hayes"], "Spin", 293000),
    ("夜に駆ける", ["YOASOBI"], "THE BOOK", 261000),
    (
        "一首名字特别特别长的歌用来测试省略号和双行布局到底会不会把右边挤出屏幕",
        ["某位名字也很长的歌手", "第二位合作歌手"],
        "很长的专辑名称也要能放下",
        199000,
    ),
    ("Matryoshka", ["reche"], "Eight Scenery Of The Burning Piano", 275000),
    ("无专辑的歌", ["独立音乐人"], "", 183000),
    ("时长未知", ["测试艺人"], "边界情况", 0),
    ("晴天", ["周杰伦"], "叶惠美", 269000),
    ("Lemon", ["米津玄師"], "STRAY SHEEP", 256000),
    ("A Very Long English Track Title That Keeps Going And Going", ["Someone"], "Long Album", 301000),
    ("短歌", ["测试艺人"], "边界情况", 15000),
    ("仅试听的那种", ["版权受限艺人"], "厂牌合集", 240000),
    ("半截缓存的歌", ["缓存测试"], "客户端缓存", 212000),
]

# 缓存视图的进度：完成 / 进行中 / 半截 / 没有 idx
CACHE_STATES = [
    (1.0, True, "320k"),
    (1.0, True, "无损"),
    (0.62, False, "320k"),
    (0.18, False, "128k"),
    (0.0, False, "320k"),
    (1.0, True, "192k"),
    (0.95, False, "320k"),
    (0.34, False, "无损"),
]


def _make_songs(count: int, offset: int = 0) -> list[Song]:
    songs: list[Song] = []
    for index in range(count):
        name, artists, album, duration = DEMO_TRACKS[(index + offset) % len(DEMO_TRACKS)]
        # 循环取模板时补个序号，避免同一屏出现两个一模一样的名字
        if index >= len(DEMO_TRACKS):
            name = f"{name} #{index + 1}"
        songs.append(
            Song(
                BASE_ID + offset + index,
                name,
                list(artists),
                album,
                duration,
            )
        )
    return songs


class MockData(QObject):
    """一个窗口一份；所有方法都只改界面状态，随时可以 clear。"""

    def __init__(self, window):
        super().__init__(window)
        self._win = window
        self._timer = QTimer(self)
        self._timer.setInterval(140)
        self._timer.timeout.connect(self._tick)
        self._queue: list[Song] = []
        self._queue_index = 0
        self._step = 0
        self._results: list[TaskResult] = []
        # 哪些 id 是造出来的：下载/播放入口据此拦住，避免真的去请求
        self.ids: set[int] = set()
        self.fake_cache = False
        self.active = False

    def covers(self, songs: list[Song]) -> bool:
        """这些歌是不是模拟数据。用来挡住「真的下载 / 真的播放」。"""
        return bool(songs) and self.active and all(song.id in self.ids for song in songs)

    # ---------------- 假搜索结果 ----------------

    def load_songs(self, count: int = 12) -> str:
        win = self._win
        songs = _make_songs(count)
        self.ids.update(song.id for song in songs)
        win._pools["client"] = (list(songs), "模拟数据 · 搜索结果", False)
        win._has_more = False
        win.btn_more.hide()
        self._switch_source("client")
        win._apply_view()
        self.active = True
        self._mark(True)
        win.status_label.setText(f"模拟数据 · {len(songs)} 首搜索结果（不联网）")
        return f"已载入 {len(songs)} 首模拟搜索结果"

    # ---------------- 假下载队列 ----------------

    def start_queue(self, count: int = 6) -> str:
        win = self._win
        songs = _make_songs(count, offset=100)
        self.ids.update(song.id for song in songs)
        win._prepare_queue(songs)
        self._queue = songs
        self._queue_index = 0
        self._step = 0
        self._results = []
        self.active = True
        self._mark(True)
        self._timer.start()
        win.status_label.setText(f"模拟数据 · 下载队列 {len(songs)} 首")
        return f"已生成 {len(songs)} 首模拟下载队列，正在跑进度…"

    def _tick(self) -> None:
        win = self._win
        if self._queue_index >= len(self._queue):
            self._timer.stop()
            return
        song = self._queue[self._queue_index]
        if self._step == 0:
            win._on_song_start(song)
        self._step = min(100, self._step + random.randint(12, 26))
        total = 1000
        win._on_song_progress(song.id, int(total * self._step / 100), total)
        if self._step < 100:
            return
        # 第 4 首失败、第 5 首跳过：把失败重试和无版权/跳过两种状态也覆盖到
        status = OK
        if self._queue_index == 3:
            status = FAILED
        elif self._queue_index == 4:
            status = SKIPPED
        result = TaskResult(song=song, status=status, path=None, message="模拟数据")
        win._on_song_result(result)
        self._results.append(result)
        self._queue_index += 1
        self._step = 0
        if self._queue_index >= len(self._queue):
            self._timer.stop()
            # 走真实的批次收尾，汇总栏、失败重试入口都会出现
            win._on_batch_done(self._results)

    def stop_queue(self) -> str:
        if not self._timer.isActive():
            return "模拟队列没在跑"
        self._timer.stop()
        return "已停止模拟队列（停在当前状态）"

    # ---------------- 假缓存列表 ----------------

    def load_cache(self, count: int = 8) -> str:
        win = self._win
        count = min(count, len(CACHE_STATES))
        songs = _make_songs(count, offset=200)
        self.ids.update(song.id for song in songs)
        win._cache_progress = {}
        win._cache_rates = {}
        win._cache_sort_key = {}
        for index, song in enumerate(songs):
            ratio, complete, rate = CACHE_STATES[index % len(CACHE_STATES)]
            total = 8000000 + index * 1200000
            win._cache_progress[song.id] = (int(total * ratio), total, complete)
            win._cache_rates[song.id] = rate
            win._cache_sort_key[song.id] = float(index)
        win._cache_entries = {}
        win._pools["cache"] = (list(songs), "模拟数据 · 本地缓存", True)
        self.fake_cache = True
        self.active = True
        self._mark(True)
        # 切音源不能触发真实扫描，这里屏蔽信号直接改下拉框
        self._switch_source("cache", silently=True)
        win._apply_view()
        win.status_label.setText(f"模拟数据 · {len(songs)} 条本地缓存（不读盘）")
        return f"已载入 {len(songs)} 条模拟缓存（含半截/未开始）"

    # ---------------- 组合与清理 ----------------

    def load_all(self) -> str:
        return "\n".join(
            [self.load_songs(), self.start_queue(), self.load_cache()]
        )

    def clear(self) -> str:
        win = self._win
        self._timer.stop()
        self._queue = []
        self._queue_index = 0
        self._step = 0
        self._results = []
        self.ids.clear()
        self.fake_cache = False
        self.active = False
        self._mark(False)

        win._pools.pop("cache", None)
        win._pools.pop("client", None)
        win._cache_progress = {}
        win._cache_rates = {}
        win._cache_sort_key = {}
        win._cache_entries = None
        win._queue_songs = []
        win._batch_total = 0
        win._batch_done = 0
        win._done_ids.clear()
        win._failed = []
        win.queue_table.setRowCount(0)
        win.queue_rows.clear()
        win.bars.clear()
        win._batch_songs = []
        win._paused = False
        win._dl_progress = {}
        win._dl_totals = {}
        win.queue_info.setText("下载队列")
        win.summary_label.setText("")
        # 紧凑模式的停靠条/折叠标题、浮层都要一起清，否则还显示「6/6 · 100%」
        win._close_queue_sheet()
        win._update_mini_queue(force=True)
        self._switch_source("client")
        # 池子已清空，重画一次视图：标题回到「输入关键词开始搜索」，表格、空提示、
        # 下载按钮一起归位。只清表格的话标题还留着「模拟数据 · 搜索结果 · 共 6 首」
        win._apply_view()
        win.status_label.setText("已清除模拟数据，回到真实列表")
        return "模拟数据已清除"

    # ---------------- 内部 ----------------

    def _switch_source(self, key: str, silently: bool = False) -> None:
        box = self._win.source_box
        index = box.findData(key)
        if index < 0 or box.currentIndex() == index:
            return
        if silently:
            box.blockSignals(True)
            box.setCurrentIndex(index)
            box.blockSignals(False)
        else:
            box.setCurrentIndex(index)

    def _mark(self, on: bool) -> None:
        """标题栏加个标记，免得把模拟数据当成真实结果。"""
        topbar = getattr(self._win, "topbar", None)
        if topbar is not None and hasattr(topbar, "set_title"):
            topbar.set_title("网易云音乐下载器 · 模拟数据" if on else "网易云音乐下载器")

    def on_real_action(self) -> None:
        """真实搜索 / 真实扫描开始时自动失效，不用手动清。"""
        if not self.active:
            return
        self._timer.stop()
        self.fake_cache = False
        self.active = False
        self._mark(False)
