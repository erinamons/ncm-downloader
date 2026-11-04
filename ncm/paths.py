"""程序数据目录：便携模式。

所有用户数据都放在程序自己所在的文件夹里——配置、日志、播放缓存、下载索引、
默认下载目录，一个都不往用户目录或注册表里塞。拷走整个文件夹就等于带走全部
状态，删掉文件夹就等于卸载干净。

放在只读位置（例如 Program Files）时无法写入，这里会自动退回家目录，
避免程序直接起不来。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def app_dir() -> Path:
    """程序所在目录：打包后是 exe 的目录，源码运行时是项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _writable(root: Path) -> bool:
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def data_dir() -> Path:
    root = app_dir()
    if _writable(root):
        return root
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    fallback = home / ".ncm-downloader"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def settings_path() -> Path:
    return data_dir() / "settings.ini"


def config_path() -> Path:
    return data_dir() / "config.json"


def log_path() -> Path:
    return data_dir() / "app.log"


def play_cache_dir() -> Path:
    return data_dir() / "play-cache"


def downloads_dir() -> Path:
    return data_dir() / "downloads"
