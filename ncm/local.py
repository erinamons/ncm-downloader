"""本地音乐扫描。

来源两处：下载目录（总是包含）和设置里选的「本地音乐目录」。
扫描结果按 (mtime, size) 缓存到 data_dir()/local-index.json：
目录没动过时再进来只做一次 stat，不重新读标签。

只读文件，不改音乐目录里的任何东西；本地曲目用负数 id，
和在线（正数）、客户端缓存、模拟数据（99 万段）完全隔开。
"""
from __future__ import annotations

import json
import os
import zlib
from dataclasses import dataclass
from pathlib import Path

# 能扫到的扩展名。系统放不了的（ape / wma）也列出来，播的时候走「无法直接播放」提示
AUDIO_EXT = {
    ".mp3",
    ".flac",
    ".m4a",
    ".wav",
    ".ogg",
    ".opus",
    ".ape",
    ".aac",
    ".wma",
}
INDEX_NAME = "local-index.json"
# 上限只是防止误选到磁盘根目录时把程序拖死；正常音乐目录远到不了
MAX_FILES = 20000


@dataclass
class LocalTrack:
    path: Path
    name: str
    artists: list[str]
    album: str
    duration: int  # ms
    size: int
    fmt: str
    mtime: float

    @property
    def id(self) -> int:
        # crc32 稳定：同一路径每次扫都是同一个 id，播放列表切歌不会串
        return -(1 + zlib.crc32(str(self.path).lower().encode("utf-8")))

    @property
    def artist_text(self) -> str:
        return " / ".join(self.artists) if self.artists else "未知艺人"


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXT


def index_path(data_dir: Path) -> Path:
    return Path(data_dir) / INDEX_NAME


def _read_tags(path: Path) -> tuple[str, list[str], str, int]:
    name = path.stem
    artists: list[str] = []
    album = ""
    duration = 0
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(str(path), easy=True)
        if audio is not None:
            tags = audio.tags or {}
            title = (tags.get("title") or [""])[0].strip()
            if title:
                name = title
            artists = [a.strip() for a in (tags.get("artist") or []) if a.strip()]
            album = ((tags.get("album") or [""])[0] or "").strip()
            length = getattr(getattr(audio, "info", None), "length", 0) or 0
            duration = int(length * 1000)
    except Exception:  # noqa: BLE001
        # 标签坏了不影响播放，退回文件名
        pass
    # 没有艺人标签时，从「艺人 - 曲名」这种文件名里拆一下，比整串当曲名好认
    if not artists and " - " in name:
        left, right = name.split(" - ", 1)
        if left.strip() and right.strip():
            artists, name = [left.strip()], right.strip()
    return name, artists, album, duration


def _track(path: Path, cached: dict | None = None) -> LocalTrack | None:
    try:
        st = path.stat()
    except OSError:
        return None
    if (
        cached
        and abs(float(cached.get("mtime") or 0) - st.st_mtime) < 0.001
        and cached.get("size") == st.st_size
    ):
        name = cached.get("name") or path.stem
        artists = list(cached.get("artists") or [])
        album = cached.get("album") or ""
        duration = int(cached.get("duration") or 0)
        fmt = cached.get("fmt") or path.suffix.lstrip(".").upper()
    else:
        name, artists, album, duration = _read_tags(path)
        fmt = path.suffix.lstrip(".").upper()
    return LocalTrack(
        path=path,
        name=name,
        artists=artists,
        album=album,
        duration=duration,
        size=st.st_size,
        fmt=fmt,
        mtime=st.st_mtime,
    )


def iter_audio(root: Path):
    """按目录顺序吐音频文件；跳过隐藏项和播放缓存目录。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not d.startswith(".") and d.lower() not in ("play-cache", "$recycle.bin")
        )
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            path = Path(dirpath) / filename
            if is_audio(path):
                yield path


def scan(paths: list[Path], cache_file: Path) -> list[LocalTrack]:
    """扫所有目录并刷新索引；返回按曲名排序的结果。"""
    known: dict = {}
    try:
        data = json.loads(Path(cache_file).read_text("utf-8"))
        known = data.get("files", {}) or {}
    except (OSError, ValueError):
        known = {}

    seen: dict[str, LocalTrack] = {}
    fresh: dict[str, dict] = {}
    for root in paths:
        if root is None or not Path(root).exists():
            continue
        for path in iter_audio(Path(root)):
            key = str(path).lower()
            if key in seen:
                continue
            track = _track(path, known.get(key))
            if track is None:
                continue
            seen[key] = track
            fresh[key] = {
                "mtime": track.mtime,
                "size": track.size,
                "name": track.name,
                "artists": track.artists,
                "album": track.album,
                "duration": track.duration,
                "fmt": track.fmt,
            }
            if len(seen) >= MAX_FILES:
                break

    try:
        target = Path(cache_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps({"files": fresh}, ensure_ascii=False), "utf-8")
        tmp.replace(target)
    except OSError:
        pass
    return sorted(seen.values(), key=lambda t: (t.name.lower(), str(t.path).lower()))


def read_files(paths: list[Path]) -> list[LocalTrack]:
    """拖进来的文件：直接读标签，不写索引（它们可能不在扫描目录里）。"""
    tracks: list[LocalTrack] = []
    for path in paths:
        track = _track(Path(path))
        if track is not None:
            tracks.append(track)
    return tracks
