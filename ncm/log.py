"""运行日志。

分发给别人之后，用户在另一台机器上出错，唯一能拿到的现场就是这个文件：
写在 %USERPROFILE%\\.ncm-downloader\\app.log，滚动保留最近几份。
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ncm import auth

_MAX_BYTES = 1_000_000
_BACKUPS = 3
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def log_path() -> Path:
    return auth.config_dir() / "app.log"


def setup_logging(level: int = logging.INFO) -> Path:
    """挂上文件日志，并开启常驻记录。重复调用无副作用。"""
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # 常驻记录（环形缓冲 + 异常钩子 + Qt 告警 + 崩溃现场落盘）统一在这里开，
    # 不依赖开发者面板是否打开；面板晚点打开时用它的 snapshot 补历史。
    from ncm.runlog import runlog

    runlog.install()

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
    return path
