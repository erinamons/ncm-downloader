"""运行日志。

分发给别人之后，用户在另一台机器上出错，唯一能拿到的现场就是这个文件：
写在 %USERPROFILE%\\.ncm-downloader\\app.log，滚动保留最近几份。
"""
from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ncm import auth

_MAX_BYTES = 1_000_000
_BACKUPS = 3
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def log_path() -> Path:
    return auth.config_dir() / "app.log"


def setup_logging(level: int = logging.INFO) -> Path:
    """挂上文件日志，并把未捕获异常也记进去。重复调用无副作用。"""
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, RotatingFileHandler):
            return path

    root.setLevel(level)
    handler = RotatingFileHandler(
        path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_FORMAT, "%Y-%m-%d %H:%M:%S"))
    root.addHandler(handler)

    def _hook(exc_type, exc, tb):
        logging.getLogger("ncm").critical(
            "未捕获异常", exc_info=(exc_type, exc, tb)
        )
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook

    def _thread_hook(args):
        logging.getLogger("ncm").critical(
            "线程 %s 异常",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_hook
    return path
