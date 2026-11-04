"""网易云音乐下载工具核心包。"""
from __future__ import annotations

__version__ = "1.0.3"

from .backend import Backend, BackendError, LocalBackend
from .models import AudioSource, Playlist, Song
from .store import Store

__all__ = [
    "Backend",
    "BackendError",
    "LocalBackend",
    "AudioSource",
    "Playlist",
    "Song",
    "Store",
    "__version__",
]
