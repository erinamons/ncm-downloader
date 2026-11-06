"""落盘相关：命名模板、文件名净化、已下载记录。"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path

from .models import Song

_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_DOTS = re.compile(r"[. ]+$")

DEFAULT_TEMPLATE = "{artist}/{album}/{track:02d} - {title}"

# 界面上的命名与分类选项，组合成实际模板
NAME_PATTERNS = {
    "artist_title": "{artist} - {title}",
    "title": "{title}",
    "title_artist": "{title} - {artist}",
}
NAME_LABELS = {
    "artist_title": "歌手-歌曲名",
    "title": "歌曲名",
    "title_artist": "歌曲名-歌手",
}
FOLDER_PATTERNS = {
    "artist_album": "{artist}/{album}/",
    "artist": "{artist}/",
    "none": "",
}
FOLDER_LABELS = {
    "artist_album": "按歌手/专辑",
    "artist": "按歌手",
    "none": "不分文件夹",
}


def build_template(name_key: str, folder_key: str) -> str:
    prefix = FOLDER_PATTERNS.get(folder_key, "")
    name = NAME_PATTERNS.get(name_key, "{artist} - {title}")
    return f"{prefix}{name}"

TEMPLATE_HELP = """可用变量：
  {artist}   艺人（多位用 / 连接）
  {title}    曲名
  {album}    专辑
  {track}    音轨号（两位补零）
  {year}     发行年份
  {id}       歌曲 ID
  {quality}  实际音质标签（标准/较高/极高/无损/Hi-Res）
"""


# Windows 保留设备名：歌曲/专辑名撞上时 open() 直接 WinError，且极难排查
_RESERVED = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.IGNORECASE
)


def sanitize(name: str, fallback: str = "未命名") -> str:
    name = _ILLEGAL.sub("_", name or "").strip()
    name = _DOTS.sub("", name)
    if _RESERVED.match(name):
        name += "_"
    return name[:120] or fallback


def render_template(template: str, song: Song, quality: str = "") -> Path:
    year = ""
    if song.publish_time:
        year = time.strftime("%Y", time.localtime(song.publish_time / 1000))
    values = {
        "artist": sanitize(song.artist_text, "未知艺人"),
        "title": sanitize(song.name, "未知曲目"),
        "album": sanitize(song.album, "未知专辑"),
        "track": song.track_no,
        "year": year,
        "id": song.id,
        "quality": sanitize(quality, ""),
    }
    tpl = template
    if not song.track_no:
        # 没有音轨号时，整段 "{track...} - " 一起去掉，避免出现 "00 - 曲名"
        tpl = re.sub(r"\{track[^}]*\}\s*[-–—]?\s*", "", tpl)
        values["track"] = 0
    try:
        rel = tpl.format(**values)
    except (KeyError, IndexError, ValueError):
        rel = DEFAULT_TEMPLATE.format(**values)
    # 必须逐段净化，否则模板里的 "/" 会被当成非法字符替换掉
    segments = [
        sanitize(seg) for seg in rel.replace("\\", "/").split("/") if seg.strip()
    ]
    if not segments:
        return Path(sanitize(song.name))

    # Windows 单条路径上限 260。每段 sanitize 后已有 120 的上限，但三段叠起来
    # 仍然会超，超了 open() 直接抛 Win32 错误，用户根本看不出是路径太长。
    # 这里按相对路径 180 留出盘符、下载根目录与扩展名的余量。
    joined = "/".join(segments)
    if len(joined) > 180:
        last = segments[-1]
        keep = max(40, len(last) - (len(joined) - 180))
        segments[-1] = last[: keep - 1].rstrip() + "…"
    return Path(*segments)


class Store:
    """记录已下载的曲目，用于跳过去重与增量补全。"""

    def __init__(self, db_path: Path | str):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=15)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS downloads (
                song_id  INTEGER PRIMARY KEY,
                br       INTEGER NOT NULL DEFAULT 0,
                ext      TEXT    NOT NULL DEFAULT '',
                path     TEXT    NOT NULL DEFAULT '',
                size     INTEGER NOT NULL DEFAULT 0,
                ts       INTEGER NOT NULL DEFAULT 0
            )"""
        )
        self._conn.commit()

    def get(self, song_id: int) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT song_id, br, ext, path, size, ts FROM downloads WHERE song_id = ?",
                (song_id,),
            )
            return cur.fetchone()

    def done(self, song_id: int, need_br: int = 0) -> bool:
        row = self.get(song_id)
        if not row:
            return False
        if not Path(row["path"]).exists():
            return False
        return row["br"] >= need_br

    def record(
        self, song_id: int, br: int, ext: str, path: Path, size: int
    ) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO downloads (song_id, br, ext, path, size, ts)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(song_id) DO UPDATE SET
                     br = excluded.br, ext = excluded.ext, path = excluded.path,
                     size = excluded.size, ts = excluded.ts""",
                (song_id, br, ext, str(path), size, int(time.time())),
            )
            self._conn.commit()

    def count(self) -> int:
        with self._lock:
            return int(
                self._conn.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
            )

    def close(self) -> None:
        self._conn.close()
