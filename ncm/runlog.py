"""常驻运行记录：程序一起来就开始记，不依赖开发者面板是否打开。

以前日志只在面板打开时才进 UI 缓冲区，于是「刚才闪退/报错，回头开面板什么都
没有」——问题现场直接丢了。这里把记录做成进程级的：

- 环形缓冲（日志 800 / 事件 400 / 警告以上 200），启动即挂到 root logger 上；
- 未捕获异常（主线程 + 子线程）和 Qt 自己的告警一起收进来；
- 记录「上次是否正常退出」，启动时能报出「上次异常退出（时间）」；
- 出现致命情况时把现场（上下文 + 最近日志 + 最近事件）落到
  ``diagnostics/last-crash-*.txt``，进程崩了也有据可查。

面板打开时用 :meth:`RunLog.snapshot` 把这段时间的历史灌进去，之后接实时流。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path
from typing import Any, Callable

from ncm import trace as trace_mod
from ncm.paths import data_dir

# 缓冲上限：够看现场，又不至于让界面重建时卡顿
MAX_LOGS = 800
MAX_EVENTS = 400
MAX_ERRORS = 200
MAX_CRASH_FILES = 3
# 同一条消息在这个窗口内重复出现只记一次（绘制里抛异常时每帧都在刷）
DEDUP_SEC = 5.0
# 同样的崩溃现场，这么久只落一次盘
DUMP_SEC = 30.0

_LEVEL_NAME = {
    logging.DEBUG: "调试",
    logging.INFO: "信息",
    logging.WARNING: "警告",
    logging.ERROR: "错误",
    logging.CRITICAL: "致命",
}

_SECRET_TEXT = (
    re.compile(r"MUSIC_U=[^;\s\"']+", re.I),
    re.compile(r"__csrf=[^;\s\"']+", re.I),
    re.compile(r"(?i)(cookie|token|authorization)\s*[:=]\s*\S+"),
)


def redact(text: str) -> str:
    """把 cookie / token 一类的字段抹掉，日志和诊断包都可能被贴到公开地方。"""
    for pattern in _SECRET_TEXT:
        text = pattern.sub("***", text)
    return text


def _fmt_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


class _RingHandler(logging.Handler):
    """把日志塞进环形缓冲。绝不能在 emit 里抛异常，否则日志本身会打断业务。"""

    def __init__(self, owner: "RunLog"):
        super().__init__()
        self._owner = owner
        self.setLevel(logging.DEBUG)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + logging.Formatter().formatException(record.exc_info)
            self._owner.add_log(record.levelno, record.name, message, record.created)
        except Exception:  # noqa: BLE001
            pass


class RunLog:
    """进程级记录器。全局只用一个实例（模块末尾的 ``runlog``）。"""

    def __init__(self) -> None:
        self._logs: deque[tuple[int, str, str, float]] = deque(maxlen=MAX_LOGS)
        self._events: deque[dict] = deque(maxlen=MAX_EVENTS)
        self._errors: deque[tuple[float, str]] = deque(maxlen=MAX_ERRORS)
        self._lock = threading.RLock()
        self._installed = False
        self._handler: logging.Handler | None = None
        self._qt_keepalive: Callable | None = None
        self._prev_excepthook: Callable | None = None
        self._prev_thread_hook: Callable | None = None
        self._dedup: dict[str, float] = {}
        self._dump_at: dict[str, float] = {}
        self.started = time.time()
        self.version = ""
        self.window: Any = None
        self.previous: dict[str, Any] | None = None

    # ---------------- 采集 ----------------

    def add_log(self, level: int, name: str, message: str, created: float | None = None) -> None:
        created = time.time() if created is None else created
        key = f"{name}|{message[:160]}"
        with self._lock:
            last = self._dedup.get(key)
            if last is not None and created - last < DEDUP_SEC:
                return
            if len(self._dedup) > 400:
                cutoff = created - DEDUP_SEC * 4
                self._dedup = {k: v for k, v in self._dedup.items() if v >= cutoff}
            self._dedup[key] = created
            self._logs.append((level, name, message, created))
            if level >= logging.WARNING:
                line = "%s %s %s: %s" % (
                    time.strftime("%H:%M:%S", time.localtime(created)),
                    _LEVEL_NAME.get(level, "信息"),
                    name,
                    message,
                )
                self._errors.append((created, redact(line)))

    def add_event(self, event: dict) -> None:
        with self._lock:
            self._events.append(event)

    # ---------------- 读取 ----------------

    def snapshot(self, since: float = 0.0) -> dict[str, list]:
        """取出 ``since`` 之后的记录。面板打开时用它补历史。

        时间戳严格大于，面板据此只补自己没见过的部分（反复开关不会重复）。
        """
        with self._lock:
            logs = [item for item in self._logs if item[3] > since]
            events = [dict(item) for item in self._events if item.get("t", 0) > since]
            errors = [(ts, text) for ts, text in self._errors if ts > since]
        return {"logs": logs, "events": events, "errors": errors}

    def counts(self) -> tuple[int, int, int]:
        with self._lock:
            return len(self._logs), len(self._events), len(self._errors)

    def error_count(self) -> int:
        with self._lock:
            return len(self._errors)

    def recent_events(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return [dict(e) for e in list(self._events)[-limit:]]

    def recent_logs(self, limit: int = 60) -> list[str]:
        with self._lock:
            rows = list(self._logs)[-limit:]
        out = []
        for level, name, message, created in rows:
            out.append(
                "%s %-2s %s: %s"
                % (
                    time.strftime("%H:%M:%S", time.localtime(created)),
                    _LEVEL_NAME.get(level, "信息"),
                    name.replace("ncm.", ""),
                    message,
                )
            )
        return out

    def context(self) -> str:
        """当前现场：版本、音源、勾选、队列、最近事件。贴出来就能复现大半问题。"""
        win = self.window
        lines = ["版本 %s" % (self.version or "?"), "运行 %.0f 秒" % (time.time() - self.started)]
        pieces: list[str] = []
        try:
            if win is not None:
                source = ""
                try:
                    source = win.source_box.currentText()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
                level = ""
                try:
                    level = str(win.level_box.currentData())  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
                try:
                    total = len(win.songs)
                    picked = len(win._selected_songs())
                except Exception:  # noqa: BLE001
                    total = picked = -1
                try:
                    rows = win.queue_table.rowCount()
                except Exception:  # noqa: BLE001
                    rows = -1
                pieces.append("音源 %s" % (source or "?"))
                pieces.append("音质 %s" % (level or "?"))
                pieces.append("列表 %d（勾选 %d）" % (total, picked))
                pieces.append("队列 %d 行" % rows)
                pieces.append("批次 %s" % ("进行中" if getattr(win, "batch", None) else "空闲"))
                pieces.append("紧凑 %s" % ("开" if getattr(win, "_mini", False) else "关"))
                pieces.append(
                    "模拟数据 %s" % ("开" if getattr(getattr(win, "_mock", None), "active", False) else "关")
                )
        except Exception as exc:  # noqa: BLE001
            pieces.append("状态读取失败 %s" % type(exc).__name__)
        if pieces:
            lines.append(" / ".join(pieces))
        lines.append("缓冲 日志 %d / 事件 %d / 问题 %d" % self.counts())
        if self.previous:
            state = "正常退出" if self.previous.get("clean") else "异常退出"
            lines.append(
                "上次运行 %s（启动于 %s，版本 %s）"
                % (
                    state,
                    _fmt_time(float(self.previous.get("started_at") or 0)),
                    self.previous.get("version") or "?",
                )
            )
        events = self.recent_events(12)
        if events:
            lines.append("最近事件：")
            for event in events:
                took = " %dms" % event["ms"] if event.get("ms") is not None else ""
                detail = ""
                if event.get("detail"):
                    detail = " " + " ".join("%s=%s" % (k, v) for k, v in event["detail"].items())
                lines.append(
                    "  %s %s/%s %s%s%s"
                    % (
                        time.strftime("%H:%M:%S", time.localtime(event.get("t", 0))),
                        event.get("scope"),
                        event.get("step"),
                        event.get("status"),
                        took,
                        detail,
                    )
                )
        return redact("\n".join(lines))

    # ---------------- 崩溃现场 ----------------

    def _run_state_path(self) -> Path:
        return data_dir() / "run-state.json"

    def mark_startup(self, version: str = "") -> None:
        """记下本次启动；顺手把上次的收尾状态读出来。"""
        if version:
            self.version = version
        path = self._run_state_path()
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.previous = data
        except Exception:  # noqa: BLE001
            self.previous = None
        self._write_run_state(clean=False)

    def mark_clean_exit(self) -> None:
        self._write_run_state(clean=True)

    def _write_run_state(self, clean: bool) -> None:
        path = self._run_state_path()
        payload = {
            "started_at": self.started,
            "updated_at": time.time(),
            "clean": clean,
            "pid": os.getpid(),
            "version": self.version,
        }
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass

    def dump(self, reason: str) -> Path | None:
        """把现场写到磁盘。同样的原因 30 秒内只写一次，避免被循环异常刷爆。"""
        key = reason[:120]
        now = time.time()
        with self._lock:
            last = self._dump_at.get(key, 0.0)
            if now - last < DUMP_SEC:
                return None
            self._dump_at[key] = now
        folder = data_dir() / "diagnostics"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / ("last-crash-%s.txt" % time.strftime("%Y%m%d-%H%M%S"))
            body = [
                "时间 %s" % _fmt_time(now),
                "原因 %s" % reason,
                "",
                "== 现场 ==",
                self.context(),
                "",
                "== 最近日志 ==",
                *self.recent_logs(80),
                "",
                "== 问题列表 ==",
            ]
            with self._lock:
                body.extend(text for _ts, text in list(self._errors)[-40:])
            path.write_text(redact("\n".join(body)), encoding="utf-8")
            self._prune_crash_files(folder)
            return path
        except OSError:
            return None

    @staticmethod
    def _prune_crash_files(folder: Path) -> None:
        try:
            files = sorted(folder.glob("last-crash-*.txt"))
        except OSError:
            return
        for old in files[:-MAX_CRASH_FILES]:
            try:
                old.unlink()
            except OSError:
                pass

    # ---------------- 安装 ----------------

    def install(self, version: str = "") -> "RunLog":
        """挂到 root logger / trace / 异常钩子 / Qt 消息处理器上。重复调用无副作用。"""
        with self._lock:
            if self._installed:
                # 第一次安装时还不知道版本（setup_logging 早于界面初始化），
                # 后面补上时要顺手把 run-state 里的版本改掉，否则诊断里是空的
                if version and not self.version:
                    self.version = version
                    self._write_run_state(clean=False)
                return self
            self._installed = True
        root = logging.getLogger()
        self._handler = _RingHandler(self)
        root.addHandler(self._handler)
        if root.level > logging.INFO or root.level == logging.NOTSET:
            root.setLevel(logging.INFO)
        trace_mod.add_listener(self.add_event)

        self._prev_excepthook = sys.excepthook
        sys.excepthook = self._excepthook
        self._prev_thread_hook = getattr(threading, "excepthook", None)
        threading.excepthook = self._thread_excepthook
        self._install_qt_handler()
        self.mark_startup(version)
        return self

    def _excepthook(self, exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            logging.getLogger("ncm.runlog").critical("未捕获异常\n%s", text)
            self.dump("未捕获异常 %s: %s" % (getattr(exc_type, "__name__", "?"), exc))
        except Exception:  # noqa: BLE001
            pass
        prev = self._prev_excepthook
        if prev is not None and prev is not self._excepthook:
            try:
                prev(exc_type, exc, tb)
            except Exception:  # noqa: BLE001
                pass

    def _thread_excepthook(self, args) -> None:
        exc_type = args.exc_type
        exc = args.exc_value
        tb = args.exc_traceback
        name = getattr(getattr(args, "thread", None), "name", "?")
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            logging.getLogger("ncm.runlog").critical("线程 %s 异常\n%s", name, text)
            self.dump("线程 %s 异常 %s: %s" % (name, getattr(exc_type, "__name__", "?"), exc))
        except Exception:  # noqa: BLE001
            pass
        prev = self._prev_thread_hook
        if prev is not None and prev is not self._thread_excepthook:
            try:
                prev(args)
            except Exception:  # noqa: BLE001
                pass

    def _install_qt_handler(self) -> None:
        """Qt 自己的告警（样式解析失败、播放器报错、qFatal 等）也要进日志。"""
        try:
            from PyQt5.QtCore import QtMsgType, qInstallMessageHandler
        except Exception:  # noqa: BLE001
            return
        mapping = {
            QtMsgType.QtDebugMsg: logging.DEBUG,
            QtMsgType.QtInfoMsg: logging.INFO,
            QtMsgType.QtWarningMsg: logging.WARNING,
            QtMsgType.QtCriticalMsg: logging.ERROR,
            QtMsgType.QtFatalMsg: logging.CRITICAL,
        }
        logger = logging.getLogger("qt")

        def handler(mode, context, message):  # noqa: ANN001
            try:
                level = mapping.get(mode, logging.INFO)
                where = ""
                try:
                    if context is not None and context.file:
                        where = " (%s:%d)" % (context.file, context.line)
                except Exception:  # noqa: BLE001
                    where = ""
                logger.log(level, "%s%s", message, where)
                if level >= logging.CRITICAL:
                    # qFatal 之后进程立刻 abort，先把现场刷到磁盘
                    self.dump("Qt 致命错误: %s" % message)
                    logging.shutdown()
            except Exception:  # noqa: BLE001
                pass

        qInstallMessageHandler(handler)
        # 回调是 C 层持有的，留个引用防止被 GC
        self._qt_keepalive = handler


runlog = RunLog()
