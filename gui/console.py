"""命令行：独立窗口 + 开发者面板共用的一套命令执行层。

三层：
- ``CommandConsole``：纯逻辑，命令表 / 中文别名 / 执行 / 输出缓冲 / 历史
- ``ConsolePanel``：输入框 + 输出区 + Tab 补全 + ↑↓ 历史，面板和独立窗口都用它
- ``ConsoleWindow``：独立窗口（无边框圆角、可置顶、Esc 隐藏）

两个入口共享同一个 ``CommandConsole``（挂在主窗口上），所以输出和历史是同一份。
"""
from __future__ import annotations

import json
import logging
import threading
from collections import deque
from pathlib import Path

from PyQt5.QtCore import QEvent, QObject, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QFont
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QCompleter,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui import icons
from gui.dialog import RoundDialog, SHADOW
from gui.style import QSS
from ncm import trace as trace_mod
from ncm.models import QUALITY_ORDER
from ncm.paths import data_dir

# 命令行里允许写的设置键；不确定的一律不给写
SETTING_KEYS = (
    "compat_play",
    "partial_fallback",
    "keep_cache",
    "to_mp3",
    "workers",
    "level",
    "name_pattern",
    "folder_pattern",
    "mini_queue",
)

# 中文别名：命令名、设置键、参数值都能用中文写，省得记英文
COMMAND_ALIASES = {
    "?": "help",
    "帮助": "help",
    "状态": "status",
    "统计": "stats",
    "概览": "stats",
    "队列": "queue",
    "播放": "play",
    "停止": "stop",
    "音量": "volume",
    "音质": "level",
    "暂停": "pause",
    "继续": "resume",
    "轨迹": "trace",
    "事件": "trace",
    "模拟数据": "mock",
    "模拟": "mock",
    "扫描": "scan",
    "重扫": "scan",
    "缓存": "cache",
    "兼容": "compat",
    "兼容播放": "compat",
    "后端": "backend",
    "音源": "source",
    "并发": "workers",
    "线程": "workers",
    "读取": "get",
    "设置": "set",
    "写入": "set",
    "日志": "log",
    "重载": "reload",
    "样式": "reload",
    "诊断": "diag",
    "诊断包": "diag",
    "打开": "open",
    "取址": "url",
    "链接": "url",
    "复制": "copy",
    "清空": "clear",
    "历史": "history",
}

SETTING_ALIASES = {
    "兼容播放": "compat_play",
    "兼容": "compat_play",
    "未缓存完": "partial_fallback",
    "补齐": "partial_fallback",
    "缓存存档": "keep_cache",
    "保留缓存": "keep_cache",
    "转mp3": "to_mp3",
    "输出格式": "to_mp3",
    "并发": "workers",
    "线程": "workers",
    "音质": "level",
    "文件名": "name_pattern",
    "命名": "name_pattern",
    "分类": "folder_pattern",
    "文件夹": "folder_pattern",
    "紧凑队列": "mini_queue",
    "队列形态": "mini_queue",
}

TRUE_WORDS = {"true", "on", "1", "开", "开启", "是", "启用"}
FALSE_WORDS = {"false", "off", "0", "关", "关闭", "否", "停用"}

VALUE_ALIASES = {
    "level": {
        "标准": "standard",
        "较高": "higher",
        "极高": "exhigh",
        "无损": "lossless",
        "hi-res": "hires",
        "高清": "hires",
    },
    "source": {"客户端": "client", "网页": "web", "缓存": "cache", "本地": "local"},
    "compat": {"开": "on", "开启": "on", "关": "off", "关闭": "off"},
    "log": {"调试": "debug", "信息": "info"},
    "open": {
        "缓存": "cache",
        "下载": "download",
        "配置": "data",
        "数据": "data",
        "日志": "log",
    },
    "queue": {"重试": "retry", "清空": "clear", "确认": "yes", "是": "yes"},
    "cache": {"清理": "clear", "清空": "clear"},
    "history": {"清空": "clear", "清除": "clear"},
    "mock": {
        "歌曲": "songs",
        "搜索": "songs",
        "队列": "queue",
        "下载": "queue",
        "缓存": "cache",
        "全部": "all",
        "停止": "stop",
        "清除": "clear",
        "清理": "clear",
    },
    "name_pattern": {
        "歌手-歌曲名": "artist_title",
        "歌曲名": "title",
        "歌曲名-歌手": "title_artist",
    },
    "folder_pattern": {
        "按歌手/专辑": "artist_album",
        "歌手/专辑": "artist_album",
        "按歌手": "artist",
        "不分文件夹": "none",
        "不分类": "none",
        "无": "none",
    },
    "mini_queue": {
        "面板": "panel",
        "折叠面板": "panel",
        "停靠": "dock",
        "单行停靠": "dock",
        "不显示": "off",
        "隐藏": "off",
    },
}


def canonical_command(word: str) -> str:
    text = word.strip()
    return COMMAND_ALIASES.get(text, COMMAND_ALIASES.get(text.lower(), text.lower()))


def canonical_key(word: str) -> str:
    text = word.strip()
    return SETTING_ALIASES.get(text, text)


def alias_value(scope: str, raw: str) -> str:
    text = raw.strip()
    table = VALUE_ALIASES.get(scope, {})
    return table.get(text, table.get(text.lower(), text))


def aliases_of(name: str) -> list[str]:
    return [a for a, target in COMMAND_ALIASES.items() if target == name and a.isalpha()]


# 补全弹层（QCompleter 的 popup）是独立顶层窗口，不跟随应用样式表，
# 只能单独给它一份深色配色；弹层没有透明背景，圆角会露出方块底，所以用直角
_POPUP_CSS = """
QListView {
    background: #232327;
    color: #d8d8dc;
    border: 1px solid #3a3a42;
    outline: 0;
    padding: 2px;
}
QListView::item {
    padding: 4px 8px;
    border-radius: 4px;
}
QListView::item:hover { background: #2c2c33; }
QListView::item:selected { background: #33333a; color: #ffffff; }
QScrollBar:vertical {
    background: #232327; width: 9px; margin: 2px;
}
QScrollBar::handle:vertical {
    background: #4a4a54; border-radius: 3px; min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0; background: none;
}
"""


class CommandConsole(QObject):
    """命令执行层。全局一份，窗口和面板共享输出与历史。"""

    line = pyqtSignal(str)  # 新的一行输出
    cleared = pyqtSignal()

    MAX_LINES = 2000
    HISTORY_KEY = "console_history"
    HISTORY_LIMIT = 50

    def __init__(self, window):
        super().__init__(window)
        self._win = window
        self.lines: deque[str] = deque(maxlen=self.MAX_LINES)
        self.steps: deque[dict] = deque(maxlen=400)
        self.history: list[str] = self._load_history()
        # 启动就挂 trace 监听：面板没开的时候也有轨迹可查（trace 命令要用）
        trace_mod.add_listener(self._on_trace)

    # ---------------- 输出 ----------------

    def emit_block(self, text: str) -> None:
        for chunk in str(text).splitlines() or [""]:
            self.lines.append(chunk)
            self.line.emit(chunk)

    def text(self) -> str:
        return "\n".join(self.lines)

    def clear(self) -> None:
        self.lines.clear()
        self.cleared.emit()

    # ---------------- 历史 ----------------

    def _load_history(self) -> list[str]:
        raw = self._win.settings.value(self.HISTORY_KEY, "", type=str) or ""
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        return [str(x) for x in data][-self.HISTORY_LIMIT :]

    def _save_history(self) -> None:
        self._win.settings.setValue(
            self.HISTORY_KEY, json.dumps(self.history[-self.HISTORY_LIMIT :], ensure_ascii=False)
        )

    def remember(self, text: str) -> None:
        text = text.strip()
        if not text or (self.history and self.history[-1] == text):
            return
        self.history.append(text)
        del self.history[: max(0, len(self.history) - self.HISTORY_LIMIT)]
        self._save_history()

    # ---------------- 执行 ----------------

    def run(self, text: str) -> str:
        """执行一条命令，把输出写进缓冲并返回纯文本（方便测试）。"""
        text = text.strip()
        if not text:
            return ""
        self.emit_block(f"> {text}")
        self.remember(text)
        parts = text.replace("\u3000", " ").split()
        name = canonical_command(parts[0])
        args = parts[1:]
        entry = self._commands().get(name)
        if entry is None:
            out = "未知命令，输入 help 查看（也支持中文，如：统计 / 队列）"
        else:
            try:
                out = entry[0](args) or ""
            except Exception as exc:  # noqa: BLE001
                out = f"执行失败：{type(exc).__name__}: {exc}"
        if out:
            self.emit_block(out)
        return out

    # ---------------- 命令表 ----------------

    def _commands(self) -> dict:
        return {
            "help": (self._cmd_help, "help [命令]：列表 / 看某条用法"),
            "status": (lambda a: self._status_text(), "打印当前状态"),
            "stats": (lambda a: self._cmd_stats(), "结果/队列/缓存/索引 概览"),
            "queue": (self._cmd_queue, "queue [retry|clear yes]：队列"),
            "play": (self._cmd_play, "play <行号>：播搜索结果第 N 行"),
            "stop": (lambda a: self._cmd_stop(), "停止播放"),
            "volume": (self._cmd_volume, "volume <0-100>"),
            "level": (self._cmd_level, "level <standard|higher|exhigh|hires|lossless>"),
            "pause": (lambda a: self._cmd_pause(), "暂停当前下载批次"),
            "resume": (lambda a: self._cmd_resume(), "继续当前批次"),
            "trace": (self._cmd_trace, "trace [条数]：最近的流程事件"),
            "mock": (self._cmd_mock, "mock songs|queue|cache|all|stop|clear：模拟数据"),
            "scan": (lambda a: self._cmd_scan(), "重新扫描缓存"),
            "cache": (self._cmd_cache, "cache clear：清空播放缓存"),
            "compat": (self._cmd_compat, "compat on|off"),
            "backend": (lambda a: self._cmd_backend(), "显示后端与格式能力"),
            "source": (self._cmd_source, "source client|web|cache|local"),
            "workers": (self._cmd_workers, "workers <1-8>：下载并发"),
            "get": (self._cmd_get, "get <设置键>"),
            "set": (self._cmd_set, "set <设置键> <值>"),
            "log": (self._cmd_log, "log debug|info"),
            "reload": (lambda a: self._cmd_reload(), "重新载入样式表"),
            "diag": (lambda a: self._cmd_diag(), "导出诊断包"),
            "open": (self._cmd_open, "open cache|download|data|log"),
            "url": (self._cmd_url, "url <歌曲id>：打印取址结果"),
            "history": (self._cmd_history, "history [clear]：命令历史"),
            "copy": (lambda a: self._cmd_copy(), "复制输出到剪贴板"),
            "clear": (lambda a: self._cmd_clear(), "清空输出"),
        }

    def command_names(self) -> list[str]:
        return list(self._commands()) + [a for a in COMMAND_ALIASES if a.isalpha()]

    # ---------------- 基础命令 ----------------

    def _cmd_help(self, args) -> str:
        if args:
            name = canonical_command(args[0])
            entry = self._commands().get(name)
            if entry is None:
                return f"没有命令 {args[0]}"
            alias = "、".join(aliases_of(name))
            tail = f"\n  中文别名：{alias}" if alias else ""
            return f"{name}\n  {entry[1]}{tail}"
        width = max(len(name) for name in self._commands()) + 2
        lines = ["可用命令（help <命令> 看单条用法；命令和参数都可以用中文）"]
        for name, (_fn, desc) in self._commands().items():
            lines.append(f"  {name:<{width}}{desc}")
        lines.append("")
        lines.append("中文写法：命令名和参数都能写中文，如 帮助 / 队列 重试 / 设置 音质 无损")
        lines.append("完整对照表：帮助窗口 →「命令行手册」→ 中文写法 / 设置键 / 参数值")
        return "\n".join(lines)

    def _cmd_clear(self) -> str:
        self.clear()
        return ""

    def _cmd_copy(self) -> str:
        QApplication.clipboard().setText(self.text())
        return "输出已复制到剪贴板"

    def _cmd_history(self, args) -> str:
        if args and alias_value("history", args[0]) == "clear":
            self.history.clear()
            self._save_history()
            return "命令历史已清空"
        if not self.history:
            return "还没有历史"
        lines = [f"最近 {len(self.history)} 条（↑↓ 取用，history clear 清空）"]
        for index, item in enumerate(self.history[-20:], start=1):
            lines.append(f"  {index:>2}. {item}")
        return "\n".join(lines)

    # ---------------- 查看类 ----------------

    def _status_text(self) -> str:
        dev = getattr(self._win, "_dev_window", None)
        if dev is not None and getattr(dev, "_status_plain", ""):
            return dev._status_text()
        # 面板没开时给一份精简状态，至少能看出现在卡在哪一步
        win = self._win
        state = "下载中" if getattr(win, "batch", None) is not None else "空闲"
        song = getattr(win, "_playing_id", None)
        return "\n".join(
            [
                f"状态      {state}　音源 {win._current_source()}　音质 {win.level_box.currentData()}",
                f"列表      {len(getattr(win, 'songs', []) or [])} 首",
                f"队列      {len(getattr(win, '_queue_songs', []) or [])} 首"
                f"（完成 {getattr(win, '_batch_done', 0)}/{getattr(win, '_batch_total', 0)}）",
                f"播放      {'id=' + str(song) if song else '未播放'}",
                "（完整状态请打开开发者面板的状态页）",
            ]
        )

    def _cmd_stats(self) -> str:
        win = self._win
        songs = len(getattr(win, "songs", []) or [])
        checked = 0
        table = getattr(win, "result_table", None)
        if table is not None:
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item is not None and item.checkState() == Qt.Checked:
                    checked += 1
        queue = len(getattr(win, "_queue_songs", []) or [])
        done = getattr(win, "_batch_done", 0)
        total = getattr(win, "_batch_total", 0)
        failed = len(getattr(win, "_failed", []) or [])
        entries = getattr(win, "_cache_entries", None)
        cache_line = "未扫描" if entries is None else f"{len(entries)} 条"
        store = getattr(win, "_audio_store", None)
        try:
            index_line = f"{store.count()} 条" if store is not None else "未打开"
        except Exception:  # noqa: BLE001
            index_line = "读取失败"
        return "\n".join(
            [
                f"列表      {songs} 首（勾选 {checked}）",
                f"队列      {queue} 首 / 进行中 {total}，已完成 {done}，失败 {failed}",
                f"缓存扫描  {cache_line}",
                f"下载索引  {index_line}",
                f"播放缓存  {self._dir_size_text('play')}",
                f"音源/音质 {win._current_source()} / {win.level_box.currentData()}",
            ]
        )

    def _dir_size_text(self, which: str) -> str:
        try:
            from ncm import cache as cache_mod

            path = (
                cache_mod.player_cache_dir()
                if which == "play"
                else getattr(self._win, "cache_dir", None)
            )
            if path is None:
                return "未设置"
            total = 0
            count = 0
            for item in Path(path).rglob("*"):
                if item.is_file():
                    total += item.stat().st_size
                    count += 1
            if not count:
                return f"空（{path}）"
            return f"{count} 个文件 / {total / 1048576:.1f} MB"
        except Exception as exc:  # noqa: BLE001
            return f"统计失败：{type(exc).__name__}"

    def _cmd_trace(self, args) -> str:
        try:
            limit = max(1, min(200, int(args[0]))) if args else 20
        except ValueError:
            return "条数必须是数字"
        events = list(self.steps)[-limit:]
        if not events:
            return "还没有流程事件"
        lines = []
        for event in events:
            detail = event.get("detail") or {}
            extra = " ".join(f"{k}={v}" for k, v in list(detail.items())[:3])
            ms = f" {event['ms']:.0f}ms" if event.get("ms") else ""
            lines.append(
                f"  {event.get('scope', '')}/{event.get('step', '')}"
                f" [{event.get('status', '')}]{ms} {extra}".rstrip()
            )
        return "\n".join(lines)

    def _on_trace(self, event: dict) -> None:
        self.steps.append(event)

    # ---------------- 队列 / 播放 ----------------

    def _cmd_queue(self, args) -> str:
        win = self._win
        action = alias_value("queue", args[0]) if args else ""
        if action == "retry":
            if getattr(win, "batch", None) is not None:
                return "正在下载，稍后再重试"
            if not getattr(win, "_failed", None):
                return "没有失败的曲目"
            count = len(win._failed)
            win._retry_failed()
            return f"已重试 {count} 首"
        if action == "clear":
            confirm = alias_value("queue", args[1]) if len(args) > 1 else ""
            if confirm not in ("yes",):
                return "确认清空输入：queue clear yes（队列 清空 确认）"
            if getattr(win, "batch", None) is not None:
                return "正在下载，队列暂时不能清空"
            win._clear_queue()
            return "队列已清空"
        songs = list(getattr(win, "_queue_songs", []) or [])
        if not songs:
            return "队列是空的"
        lines = [f"队列 {len(songs)} 首（完成 {getattr(win, '_batch_done', 0)}）"]
        for index, song in enumerate(songs[:20], start=1):
            row = win.queue_rows.get(song.id)
            state = "等待"
            pct = 0
            if row is not None:
                item = win.queue_table.item(row, 1)
                if item is not None:
                    state = item.text() or "等待"
                bar = win.bars.get(song.id)
                if bar is not None and bar.maximum() > 0:
                    pct = int(bar.value() * 100 / bar.maximum())
            lines.append(f"  {index:>2}. {song.name[:24]:<26}{state} {pct}%")
        if len(songs) > 20:
            lines.append(f"  …另有 {len(songs) - 20} 首")
        return "\n".join(lines)

    def _cmd_play(self, args) -> str:
        if not args:
            return "用法：play <行号>（第 1 行是 1）"
        try:
            row = int(args[0]) - 1
        except ValueError:
            return "行号必须是数字"
        songs = getattr(self._win, "songs", []) or []
        if not 0 <= row < len(songs):
            return f"行号超出范围（1-{len(songs)}）"
        self._win._play_row(row)
        return f"开始播放第 {row + 1} 行：{songs[row].name}"

    def _cmd_stop(self) -> str:
        self._win._stop_playback()
        return "已停止播放"

    def _cmd_volume(self, args) -> str:
        win = self._win
        if not args:
            return f"当前音量 {win.player.volume}；用法：volume <0-100>"
        try:
            value = max(0, min(100, int(args[0])))
        except ValueError:
            return "音量必须是数字"
        win.topbar.set_volume(value)
        win._on_volume_changed(value)
        return f"音量 {value}"

    def _cmd_level(self, args) -> str:
        win = self._win
        labels = "|".join(QUALITY_ORDER)
        if not args:
            return f"当前 {win.level_box.currentData()}；用法：level <{labels}>（也可写 标准/较高/极高/无损）"
        word = alias_value("level", args[0])
        index = win.level_box.findData(word)
        if index < 0:
            return f"可选：{labels}（也可写 标准/较高/极高/无损）"
        win.level_box.setCurrentIndex(index)
        return f"音质已切到 {word}"

    def _cmd_pause(self) -> str:
        if getattr(self._win, "batch", None) is None:
            return "当前没有在下载"
        self._win._pause_batch()
        return "已请求暂停"

    def _cmd_resume(self) -> str:
        if getattr(self._win, "batch", None) is not None:
            return "已经在下载"
        self._win._resume_batch()
        return "已继续"

    # ---------------- 缓存 / 数据 ----------------

    def _cmd_scan(self) -> str:
        self._win.on_load_cache()
        return "已触发缓存重扫"

    def _cmd_cache(self, args) -> str:
        action = alias_value("cache", args[0]) if args else ""
        if action and action != "clear":
            return "用法：cache clear（也可写「缓存 清理」）"
        from ncm import cache as cache_mod

        count, freed = cache_mod.clear_player_cache()
        return f"已清理播放缓存 {count} 个（{freed / 1048576:.0f} MB）"

    def _cmd_backend(self) -> str:
        import os as _os

        backend = _os.environ.get("QT_MULTIMEDIA_PREFERRED_PLUGINS", "默认")
        bad = sorted(getattr(self._win.player, "_bad_formats", []) or [])
        return (
            f"后端 {backend}\n无法直接播放的格式：{', '.join(bad) if bad else '无'}\n"
            f"ffmpeg：{self._ffmpeg_line()}"
        )

    def _ffmpeg_line(self) -> str:
        try:
            from ncm import transcode

            return transcode.ffmpeg_path() or "未找到"
        except Exception:  # noqa: BLE001
            return "未知"

    def _cmd_source(self, args) -> str:
        if not args:
            return f"当前音源 {self._win._current_source()}"
        word = alias_value("source", args[0])
        if word not in ("client", "web", "cache", "local"):
            return "用法：source client|web|cache|local（客户端|网页|缓存|本地）"
        idx = self._win.source_box.findData(word)
        if idx < 0:
            return "界面里没有这个音源"
        self._win.source_box.setCurrentIndex(idx)
        return f"已切到 {word}"

    def _cmd_open(self, args) -> str:
        if not args:
            return "用法：open cache|download|data|log（缓存|下载|配置|日志）"
        key = alias_value("open", args[0])
        win = self._win
        if key == "cache":
            target = getattr(win, "cache_dir", None)
        elif key == "download":
            target = getattr(win, "out_dir", None)
        elif key == "data":
            target = data_dir()
        elif key == "log":
            from ncm.log import log_path

            path = Path(log_path())
            if not path.exists():
                return "日志文件还没生成"
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            return "已打开 log"
        else:
            return "用法：open cache|download|data|log（缓存|下载|配置|日志）"
        if target is None:
            return "这个目录还没设置"
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        return f"已打开 {key}"

    def _cmd_url(self, args) -> str:
        if not args:
            return "用法：url <歌曲id>"
        try:
            sid = int(args[0])
        except ValueError:
            return "歌曲 id 必须是数字"
        level = (
            alias_value("level", args[1])
            if len(args) > 1
            else (self._win.level_box.currentData() or "exhigh")
        )
        threading.Thread(target=self._url_worker, args=(sid, level), daemon=True).start()
        return f"查询中：{sid}（{level}）"

    def _url_worker(self, sid: int, level: str) -> None:
        try:
            src = self._win.backend.song_url(sid, level)
            text = f"{sid}: ext={src.ext} br={src.br} size={src.size}\n  {src.url[:160]}"
        except Exception as exc:  # noqa: BLE001
            text = f"{sid}: 失败 {type(exc).__name__}: {exc}"
        # 线程里不能直接碰控件，交回主线程
        QTimer.singleShot(0, lambda: self.emit_block(text))

    # ---------------- 设置 / 开发 ----------------

    def _cmd_get(self, args) -> str:
        if not args:
            return f"用法：get <键>（每个键的含义见 帮助 → 设置项）\n可读：{'、'.join(SETTING_KEYS)}"
        key = canonical_key(args[0])
        value = self._win.settings.value(key, None)
        if value is None:
            return f"{key} 没有设置过"
        if isinstance(value, str) and value.lower() in ("true", "false"):
            value = value.lower() == "true"
        return f"{key} = {value!r}"

    def _cmd_set(self, args) -> str:
        if len(args) < 2:
            return (
                "用法：set <键> <值>（每个键的含义 / 可选值见 帮助 → 设置项）\n"
                f"可写：{'、'.join(SETTING_KEYS)}"
            )
        key = canonical_key(args[0])
        raw = " ".join(args[1:]).strip()
        if key not in SETTING_KEYS:
            return f"{key} 不在可写白名单里：{'、'.join(SETTING_KEYS)}"
        mapped = alias_value(key, raw)
        low = mapped.lower()
        if key == "level" and mapped not in QUALITY_ORDER:
            return f"level 只能是 {'、'.join(QUALITY_ORDER)}（也可写 标准/较高/极高/无损）"
        if key == "mini_queue" and mapped not in ("panel", "dock", "off"):
            return "mini_queue 只能是 panel / dock / off（面板 / 停靠 / 不显示）"
        if low in TRUE_WORDS:
            value: object = True
        elif low in FALSE_WORDS:
            value = False
        elif mapped.isdigit():
            value = int(mapped)
        else:
            value = mapped
        win = self._win
        win.settings.setValue(key, value)
        win.settings.sync()
        if key == "workers":
            win.workers = max(1, min(8, int(value)))  # type: ignore[arg-type]
        elif key == "compat_play":
            win.player.compat_play = bool(value)
        elif key == "mini_queue":
            if getattr(win, "_mini", False):
                win._apply_mini_queue()
        elif key == "level":
            index = win.level_box.findData(value)
            if index >= 0:
                win.level_box.setCurrentIndex(index)
        hook = getattr(win, "refresh_dev_components", None)
        if hook is not None:
            hook()
        return f"{key} = {value!r}"

    def _cmd_compat(self, args) -> str:
        if not args:
            return f"当前：{'开' if self._win.player.compat_play else '关'}；用法：compat on|off（开|关）"
        word = alias_value("compat", args[0]).lower()
        if word in TRUE_WORDS:
            enabled = True
        elif word in FALSE_WORDS:
            enabled = False
        else:
            return f"当前：{'开' if self._win.player.compat_play else '关'}；用法：compat on|off（开|关）"
        self._win.settings.setValue("compat_play", enabled)
        self._win.player.compat_play = enabled
        hook = getattr(self._win, "refresh_dev_components", None)
        if hook is not None:
            hook()
        return "兼容播放已" + ("开启" if enabled else "关闭")

    def _cmd_workers(self, args) -> str:
        win = self._win
        if not args:
            return f"当前并发 {win.workers}；用法：workers <1-8>"
        try:
            value = max(1, min(8, int(args[0])))
        except ValueError:
            return "并发必须是 1-8 的数字"
        win.workers = value
        win.settings.setValue("workers", value)
        return f"并发改为 {value}（下一批生效）"

    def _cmd_log(self, args) -> str:
        word = alias_value("log", args[0]) if args else ""
        if word not in ("debug", "info"):
            return f"当前 {logging.getLevelName(logging.getLogger().level)}；用法：log debug|info（调试|信息）"
        logging.getLogger().setLevel(logging.DEBUG if word == "debug" else logging.INFO)
        return f"日志级别改为 {word}"

    def _cmd_reload(self) -> str:
        import importlib

        from gui import style as style_mod

        importlib.reload(style_mod)
        win = self._win
        if win is not None:
            win.setStyleSheet(style_mod.QSS)
        dev = getattr(win, "_dev_window", None)
        if dev is not None:
            dev.reload_style()
        return "样式表已重新载入"

    def _cmd_diag(self) -> str:
        win = self._win
        dev = getattr(win, "_dev_window", None)
        if dev is None:
            return "诊断包要在开发者面板里导出（F1 旁边的「开发者面板」入口打开后执行 diag）"
        dev._export_diagnostics()
        return dev.status.text()

    def _cmd_mock(self, args) -> str:
        mock = getattr(self._win, "_mock", None)
        if mock is None:
            return "当前窗口没有模拟数据层"
        action = alias_value("mock", args[0]) if args else ""
        count = None
        if len(args) > 1 and args[1].isdigit():
            count = int(args[1])
        if action in ("", "songs", "queue", "cache", "all"):
            if action in ("", "songs"):
                return mock.load_songs(count or 12) if count else mock.load_songs()
            if action == "queue":
                return mock.start_queue(count or 6) if count else mock.start_queue()
            if action == "cache":
                return mock.load_cache(count or 8) if count else mock.load_cache()
            return mock.load_all()
        if action == "stop":
            return mock.stop_queue()
        if action == "clear":
            return mock.clear()
        return (
            "用法：mock songs|queue|cache|all|stop|clear [数量]\n"
            "　　　模拟数据 歌曲|队列|缓存|全部|停止|清除"
        )


class ConsolePanel(QWidget):
    """输入框 + 输出区。开发者面板的命令行页和独立窗口都用它。"""

    def __init__(self, console: CommandConsole, parent=None, hint: bool = True):
        super().__init__(parent)
        self.console = console
        self._hist_index = len(console.history)
        # Tab 补全的轮换状态：(当前文本, 已选到第几个候选)
        self._tab_state: tuple[str, int] = ("", 0)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        if hint:
            label = QLabel(
                "输入 help 查看全部命令（Tab 补全，↑↓ 翻历史）；"
                "命令和参数都能用中文，危险操作要显式确认。"
            )
            label.setStyleSheet("color:#96969e;")
            label.setWordWrap(True)
            lay.addWidget(label)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(console.MAX_LINES)
        self.view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.view.setStyleSheet(
            "QPlainTextEdit { background:#17171a; color:#d8d8dc;"
            " border:1px solid #34343a; border-radius:6px; padding:4px; }"
        )
        font = QFont("Consolas")
        font.setPointSize(9)
        self.view.setFont(font)
        lay.addWidget(self.view, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText("命令，如：stats / 队列 / 模拟数据 队列")
        self.input.returnPressed.connect(self._submit)
        # QLineEdit 自己不处理 Tab（会去切焦点），补全弹层也只认方向键，
        # 所以 Tab 补全得自己接：见 _tab_complete
        self.input.installEventFilter(self)
        completer = QCompleter(console.command_names(), self.input)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        # 补全弹层是独立的顶层窗口，不继承应用级 QSS 的配色：不单独设就是
        # 系统默认的「白底黑字」，在深色界面里像一块贴错地方的东西
        popup = completer.popup()
        popup.setStyleSheet(_POPUP_CSS)
        self.input.setCompleter(completer)
        row.addWidget(self.input, 1)
        run = QPushButton("执行")
        run.clicked.connect(self._submit)
        row.addWidget(run)
        clear = QPushButton("清空")
        clear.clicked.connect(console.clear)
        row.addWidget(clear)
        lay.addLayout(row)

        self._seed()
        console.line.connect(self._append)
        console.cleared.connect(self.view.clear)

    # ---------------- 输出 ----------------

    def _seed(self) -> None:
        """把窗口打开前的输出灌进来，接着往下写。"""
        for line in self.console.lines:
            self.view.appendPlainText(line)
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def _append(self, text: str) -> None:
        self.view.appendPlainText(text)
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ---------------- 输入 ----------------

    def _submit(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self.console.run(text)
        self._hist_index = len(self.console.history)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Up:
            self._recall(-1)
            event.accept()
            return
        if event.key() == Qt.Key_Down:
            self._recall(1)
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.input and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Tab:
            self._tab_complete()
            return True
        return super().eventFilter(obj, event)

    def _tab_complete(self) -> None:
        """Tab 补全命令名；候选多于一个时连续按 Tab 依次轮换。"""
        text = self.input.text()
        head, sep, tail = text.partition(" ")
        names = [n for n in self.console.command_names() if n.lower().startswith(head.lower())]
        if not names or not head:
            return
        if self._tab_state[0] == text and self._tab_state[1] < len(names):
            index = (self._tab_state[1] + 1) % len(names)
        else:
            index = 0
        pick = names[index]
        new_text = pick + (sep + tail if sep else "")
        self.input.setText(new_text)
        self._tab_state = (new_text, index)

    def _recall(self, step: int) -> None:
        history = self.console.history
        if not history:
            return
        self._hist_index = max(0, min(len(history), self._hist_index + step))
        if self._hist_index >= len(history):
            self.input.clear()
            return
        self.input.setText(history[self._hist_index])
        self.input.selectAll()


class ConsoleWindow(RoundDialog):
    """独立命令行窗口：Esc 隐藏（保留输出），可置顶。"""

    def __init__(self, console: CommandConsole, parent=None):
        super().__init__(parent, "命令行")
        self.console = console
        self.setStyleSheet(QSS)
        self.resize(760 + SHADOW * 2, 460 + SHADOW * 2)

        body = self.body_layout
        body.setContentsMargins(10, 4, 10, 10)
        body.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.tip_label = QLabel("命令直接作用于主窗口，和开发者面板里的命令行是同一份输出。")
        self.tip_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        top.addWidget(self.tip_label, 1)
        # 独立窗口原来没有帮助入口，要查命令得先开开发者面板；这里直接打开
        # 只含「命令行手册 + 设置项」这两组的帮助窗口
        self.help_btn = QPushButton()
        self.help_btn.setObjectName("helpBtn")
        self.help_btn.setIcon(icons.icon("circle-help", "#96969e", 15))
        self.help_btn.setIconSize(QSize(15, 15))
        self.help_btn.setFixedSize(22, 20)
        self.help_btn.setToolTip("命令帮助（命令语法、设置项含义）")
        self.help_btn.setFocusPolicy(Qt.NoFocus)
        self.help_btn.setCursor(Qt.PointingHandCursor)
        self.help_btn.clicked.connect(self.open_help)
        top.addWidget(self.help_btn)
        self.pin = QCheckBox("置顶")
        self.pin.toggled.connect(self._toggle_pin)
        top.addWidget(self.pin)
        body.addLayout(top)

        self.panel = ConsolePanel(console, self, hint=False)
        body.addWidget(self.panel, 1)

    def _toggle_pin(self, on: bool) -> None:
        flags = self.windowFlags()
        if on:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def open_help(self) -> None:
        """打开命令帮助。窗口复用，Esc 关窗口时一并收起。"""
        from gui.devhelp import DevHelpDialog

        win = getattr(self, "_help_window", None)
        if win is None:
            win = DevHelpDialog(
                self, title="命令行 · 帮助", groups=("命令行手册", "设置项")
            )
            self._help_window = win
        win.show()
        win.raise_()
        win.activateWindow()

    def focus_input(self) -> None:
        self.panel.input.setFocus()

    def hideEvent(self, event) -> None:  # noqa: N802
        # 和开发者面板一样：主窗口藏起来时帮助窗口不该留在屏幕上
        help_win = getattr(self, "_help_window", None)
        if help_win is not None and help_win.isVisible():
            help_win.close()
        super().hideEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.hide()
            event.accept()
            return
        if event.key() == Qt.Key_F1:
            # 和主窗口一致：F1 打开帮助
            self.open_help()
            event.accept()
            return
        super().keyPressEvent(event)
