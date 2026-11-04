"""ID3 / MP4 / Vorbis 标签写入。

三种容器的字段名完全不同，这里按扩展名分派，对外只暴露一个函数。
"""
from __future__ import annotations

import time
from pathlib import Path

from .models import Song


def _mime_of(cover: bytes) -> str:
    if cover[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/jpeg"


def _year(song: Song) -> str:
    if not song.publish_time:
        return ""
    return time.strftime("%Y", time.localtime(song.publish_time / 1000))


def _write_mp3(path: Path, song: Song, lyrics: str, cover: bytes) -> None:
    from mutagen.id3 import (
        APIC,
        ID3,
        ID3NoHeaderError,
        TALB,
        TDRC,
        TIT2,
        TPE1,
        TRCK,
        USLT,
    )

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    tags.delall("TIT2")
    tags.add(TIT2(encoding=3, text=song.name))
    tags.delall("TPE1")
    tags.add(TPE1(encoding=3, text=song.artist_text))
    if song.album:
        tags.delall("TALB")
        tags.add(TALB(encoding=3, text=song.album))
    if song.track_no:
        tags.delall("TRCK")
        tags.add(TRCK(encoding=3, text=str(song.track_no)))
    year = _year(song)
    if year:
        tags.delall("TDRC")
        tags.add(TDRC(encoding=3, text=year))
    if lyrics:
        tags.delall("USLT")
        tags.add(USLT(encoding=3, lang="chi", desc="", text=lyrics))
    if cover:
        tags.delall("APIC")
        tags.add(
            APIC(encoding=3, mime=_mime_of(cover), type=3, desc="Cover", data=cover)
        )
    tags.save(path, v2_version=3)


def _write_m4a(path: Path, song: Song, lyrics: str, cover: bytes) -> None:
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(path)
    audio["\xa9nam"] = [song.name]
    audio["\xa9ART"] = [song.artist_text]
    if song.album:
        audio["\xa9alb"] = [song.album]
    if song.track_no:
        audio["trkn"] = [(song.track_no, 0)]
    year = _year(song)
    if year:
        audio["\xa9day"] = [year]
    if lyrics:
        audio["\xa9lyr"] = [lyrics]
    if cover:
        fmt = (
            MP4Cover.FORMAT_PNG
            if _mime_of(cover) == "image/png"
            else MP4Cover.FORMAT_JPEG
        )
        audio["covr"] = [MP4Cover(cover, imageformat=fmt)]
    audio.save()


def _write_flac(path: Path, song: Song, lyrics: str, cover: bytes) -> None:
    from mutagen.flac import FLAC, Picture

    audio = FLAC(path)
    audio["title"] = song.name
    audio["artist"] = song.artist_text
    if song.album:
        audio["album"] = song.album
    if song.track_no:
        audio["tracknumber"] = str(song.track_no)
    year = _year(song)
    if year:
        audio["date"] = year
    if lyrics:
        audio["lyrics"] = lyrics
    if cover:
        if audio.pictures:
            audio.clear_pictures()
        pic = Picture()
        pic.type = 3
        pic.mime = _mime_of(cover)
        pic.desc = "Cover"
        pic.data = cover
        audio.add_picture(pic)
    audio.save()


_WRITERS = {
    "mp3": _write_mp3,
    "m4a": _write_m4a,
    "flac": _write_flac,
}


def write_tags(
    path: Path, song: Song, lyrics: str = "", cover: bytes = b""
) -> None:
    ext = path.suffix.lower().lstrip(".")
    writer = _WRITERS.get(ext)
    if writer is None:
        raise ValueError(f"不支持的音频容器: .{ext}")
    writer(path, song, lyrics, cover)


def merge_lyrics(lyrics: str, trans: str) -> str:
    """把翻译歌词按时间轴合并进正文，便于播放器直接显示。"""
    if not trans:
        return lyrics
    main_lines = lyrics.splitlines()
    trans_map = {}
    for line in trans.splitlines():
        if line.startswith("[") and "]" in line:
            key = line[: line.index("]") + 1]
            trans_map[key] = line[line.index("]") + 1 :].strip()
    out = []
    for line in main_lines:
        out.append(line)
        if line.startswith("[") and "]" in line:
            key = line[: line.index("]") + 1]
            extra = trans_map.get(key)
            if extra and line[line.index("]") + 1 :].strip():
                out.append(extra)
    return "\n".join(out)
