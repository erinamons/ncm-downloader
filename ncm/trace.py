"""结构化运行轨迹。

开发者面板要的是「谁在什么时候做了哪一步、花了多久、成没成」，而不是
一行行自由文本。这里给一个统一出口：

    trace("下载", "取址", status="ok", ms=210, detail={"song": 585404})

监听者（开发者面板）注册进来收事件；没有监听者时不写文件日志，
普通用户的 app.log 不会被这些调试信息淹掉。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

Event = dict[str, Any]
Listener = Callable[[Event], None]

_listeners: list[Listener] = []
_guard = threading.Lock()
log = logging.getLogger("ncm.trace")


def add_listener(fn: Listener) -> None:
    with _guard:
        if fn not in _listeners:
            _listeners.append(fn)


def remove_listener(fn: Listener) -> None:
    with _guard:
        if fn in _listeners:
            _listeners.remove(fn)


def has_listener() -> bool:
    with _guard:
        return bool(_listeners)


def trace(
    scope: str,
    step: str,
    status: str = "ok",
    ms: float | None = None,
    detail: dict[str, Any] | None = None,
    group: str = "",
) -> None:
    """记一条流程事件。

    scope：搜索 / 缓存 / 下载 / 播放 / 接口
    status：ok / fail / skip / run
    group：同一批操作的分组名（下载批次里按歌分组），面板据此折叠
    """
    event: Event = {
        "t": time.time(),
        "scope": scope,
        "step": step,
        "status": status,
        "ms": ms,
        "detail": detail or {},
        "group": group,
    }
    with _guard:
        listeners = list(_listeners)
    for fn in listeners:
        try:
            fn(event)
        except Exception:  # noqa: BLE001
            continue
    if listeners:
        extra = ""
        if event["detail"]:
            pairs = " ".join(f"{k}={v}" for k, v in event["detail"].items())
            extra = f" {pairs}"
        took = f" {ms:.0f}ms" if ms is not None else ""
        log.info("[%s] %s %s%s%s", scope, step, status, took, extra)


class Span:
    """with Span("下载", "取址", song=id) as sp: ... sp.detail["x"] = 1"""

    def __init__(self, scope: str, step: str, group: str = "", **detail: Any):
        self.scope = scope
        self.step = step
        self.group = group
        self.detail = dict(detail)
        self.status = "ok"
        self._t0 = 0.0

    def __enter__(self) -> "Span":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        ms = (time.perf_counter() - self._t0) * 1000
        if exc_type is not None:
            self.status = "fail"
            self.detail["error"] = type(exc).__name__
        trace(self.scope, self.step, self.status, ms, self.detail, self.group)
        return False
