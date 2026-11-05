"""缓存存档：把客户端缓存解密一份留在程序目录。

为什么要这个：网易云客户端只保留很有限的播放缓存，换下一首时上一首的
.uc 就被删掉了。下载器的「本地缓存」列表如果只依赖那些文件，就会变成
「永远只剩当前一首」，之前听过的既看不见也播不了。

所以扫描到新缓存时顺手解密存一份到 `cache-store` 目录，并记下曲目信息。
列表 = 存档 + 当前仍在缓存里的，客户端删了也不影响已经存下来的。

存档有容量上限，超了按最久未访问的删。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ncm import cache as cache_mod
from ncm import paths

# 存档上限，超过就按最后访问时间淘汰
LIMIT_MB = 2048


def store_dir() -> Path:
    return paths.data_dir() / "cache-store"


def index_path() -> Path:
    return store_dir() / "index.json"


def load() -> dict[int, dict]:
    """读存档索引：{song_id: {file, rate, name, artists, album, duration_ms, at}}。"""
    path = index_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    result: dict[int, dict] = {}
    for key, value in raw.items():
        try:
            result[int(key)] = value
        except (TypeError, ValueError):
            continue
    return result


def save(index: dict[int, dict]) -> None:
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.part")
    tmp.write_text(
        json.dumps({str(k): v for k, v in index.items()}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def path_of(item: dict) -> Path | None:
    name = item.get("file")
    if not name:
        return None
    path = store_dir() / name
    return path if path.exists() else None


def store(entry, song, index: dict[int, dict]) -> bool:
    """把一条缓存解密存下来，返回是否新增。

    entry 是 cache.CacheEntry，song 是带元数据的 Song（可缺）。
    """
    if entry.song_id in index and path_of(index[entry.song_id]):
        return False
    store_dir().mkdir(parents=True, exist_ok=True)
    scratch = store_dir() / f"{entry.song_id}.bin"
    try:
        ext = cache_mod.decode(entry.path, scratch)
    except Exception:  # noqa: BLE001
        scratch.unlink(missing_ok=True)
        return False
    final = store_dir() / f"{entry.song_id}.{ext}"
    try:
        os.replace(scratch, final)
    except OSError:
        scratch.unlink(missing_ok=True)
        return False
    index[entry.song_id] = {
        "file": final.name,
        "rate": entry.rate,
        "name": getattr(song, "name", "") or f"未识别曲目 {entry.song_id}",
        "artists": list(getattr(song, "artists", []) or []),
        "album": getattr(song, "album", "") or "",
        "duration_ms": getattr(song, "duration_ms", 0) or 0,
        "at": int(time.time()),
    }
    return True


def prune(limit_mb: int = LIMIT_MB) -> int:
    """超过上限时按最久未访问删除，返回删掉的文件数。"""
    index = load()
    if not index:
        return 0
    total = 0
    sizes: dict[int, int] = {}
    for song_id, item in index.items():
        path = path_of(item)
        size = path.stat().st_size if path else 0
        sizes[song_id] = size
        total += size
    limit = limit_mb * 1024 * 1024
    if total <= limit:
        return 0

    removed = 0
    for song_id, _ in sorted(index.items(), key=lambda kv: kv[1].get("at", 0)):
        if total <= limit:
            break
        item = index.pop(song_id)
        path = path_of(item)
        if path is not None:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        total -= sizes.get(song_id, 0)
    if removed:
        save(index)
    return removed


def total_bytes() -> int:
    root = store_dir()
    if not root.is_dir():
        return 0
    total = 0
    for path in root.iterdir():
        if path.is_file() and path.suffix.lower() != ".json":
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def clear() -> tuple[int, int]:
    """清空存档，返回 (文件数, 释放字节)。"""
    root = store_dir()
    if not root.is_dir():
        return (0, 0)
    count = 0
    total = 0
    for path in root.iterdir():
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        count += 1
        total += size
    return (count, total)
