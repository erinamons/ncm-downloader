"""客户端播放缓存（.uc）解码。

缓存文件是对音频容器整体做单字节异或，密钥固定 0xA3，解出来就是标准
m4a / mp3。文件名形如 <songId>-<档位>-<hash>.uc，同名 .idx 是明文 JSON，
记录已缓存的字节区间，可以用来判断是否完整。

缓存音质等于「播放时选择的档位」，不是下载音质；想要更高规格需要在
客户端把播放音质调高再播一遍。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

_UC_RE = re.compile(r"^(\d+)-(\d+)-([0-9a-f]+)$", re.IGNORECASE)
_XOR_TABLE = bytes(i ^ 0xA3 for i in range(256))
_CHUNK = 4 * 1024 * 1024

# 客户端档位 -> 大致规格，仅用于展示
RATE_LABEL = {96: "96k", 128: "128k", 192: "192k", 320: "320k", 999: "无损"}


@dataclass(slots=True)
class CacheEntry:
    path: Path
    song_id: int
    rate: int
    size: int
    complete: bool
    covered: int

    @property
    def rate_label(self) -> str:
        return RATE_LABEL.get(self.rate, f"{self.rate}k")


def candidate_dirs() -> list[Path]:
    """客户端可能存放播放缓存的目录，按命中优先级排列。

    默认位置是 %LOCALAPPDATA%\\Netease\\CloudMusic\\Cache\\Cache；某些版本
    少一层 Cache，一并试一下。用户改过缓存位置时靠设置里手动指定。
    """
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    base = Path(local) / "Netease" / "CloudMusic"
    return [base / "Cache", base / "Cache" / "Cache"]


def default_cache_dir() -> Path | None:
    """自动探测 Windows 客户端的在线播放缓存目录。

    优先返回真的有 .uc 文件的目录——装了客户端但从没播放过时，默认位置是
    空目录，指过去只会让「仅缓存」列表一片空白，还以为是程序坏了。
    """
    found = [path for path in candidate_dirs() if path.is_dir()]
    for path in found:
        if looks_like_cache_dir(path):
            return path
    return found[0] if found else None


def looks_like_cache_dir(path: Path | str) -> bool:
    """目录里是否真有缓存的 .uc/.idx，用来提示用户选错了地方。"""
    root = Path(path)
    if not root.is_dir():
        return False
    try:
        for entry in root.rglob("*"):
            if entry.suffix.lower() in (".uc", ".idx"):
                return True
    except OSError:
        return False
    return False


def _read_idx(idx: Path) -> tuple[int, int]:
    """返回 (声明大小, 已覆盖字节数)。"""
    try:
        data = json.loads(idx.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0, 0
    size = int(data.get("size") or 0)
    covered = 0
    for zone in data.get("zone") or []:
        parts = str(zone).split()
        if len(parts) != 2:
            continue
        try:
            start, end = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        covered += max(0, end - start + 1)
    return size, covered


def scan(cache_dir: Path | str, max_depth: int = 3) -> list[CacheEntry]:
    """扫出目录下的缓存条目。

    要递归：不同版本的客户端会把缓存分层放（按日期或哈希建子目录），
    只 glob 顶层会漏掉绝大多数文件，表现就是「缓存列表永远只有一首」。
    深度限制在 3 层，避免误选到磁盘根目录时遍历整块盘。
    """
    root = Path(cache_dir)
    entries: list[CacheEntry] = []
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        if len(current.parts) - root_depth >= max_depth:
            dirnames[:] = []
        for name in filenames:
            if not name.lower().endswith(".uc"):
                continue
            path = current / name
            match = _UC_RE.match(path.stem)
            if not match:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            declared, covered = _read_idx(path.with_suffix(".idx"))
            if declared:
                # idx 声明的是预期总长；文件实际长度和已覆盖区间都得对得上才算完整
                expected = max(declared, size)
                complete = covered >= expected * 0.98 and size >= expected * 0.98
            else:
                # 没有 idx 时按 `.uc!` 约定判断：带感叹号的表示写入未完成
                complete = not path.name.endswith("!")
            entries.append(
                CacheEntry(
                    path=path,
                    song_id=int(match.group(1)),
                    rate=int(match.group(2)),
                    size=size,
                    complete=complete,
                    covered=covered,
                )
            )
    entries.sort(key=lambda e: (e.song_id, -e.rate))
    return entries


def recheck(entry: CacheEntry) -> tuple[bool, int, int]:
    """用之前重新读一遍 idx，返回 (是否完整, 已覆盖字节, 文件长度)。

    缓存是边听边写的：扫描那一刻判成「完整」的文件，等到真的要播放时
    可能还是半截（idx 是后写的）。播放前核一次，比信任旧快照可靠。
    """
    try:
        size = entry.path.stat().st_size
    except OSError:
        return (False, 0, 0)
    declared, covered = _read_idx(entry.path.with_suffix(".idx"))
    if declared:
        expected = max(declared, size)
        complete = covered >= expected * 0.98 and size >= expected * 0.98
    else:
        complete = not entry.path.name.endswith("!")
    return (complete, covered, size)


def container_of(head: bytes) -> str:
    """根据已解密的文件头判断容器，返回扩展名。"""
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "m4a"
    if head[:3] == b"ID3":
        return "mp3"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"RIFF":
        return "wav"
    if head[:1] == b"\xff" and len(head) > 1 and (head[1] & 0xE0) == 0xE0:
        return "mp3"
    return "bin"


def peek(path: Path) -> str:
    with open(path, "rb") as fh:
        head = fh.read(16).translate(_XOR_TABLE)
    return container_of(head)


def decode(src: Path, dest: Path) -> str:
    """边解密边写，返回容器扩展名。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    ext = "bin"
    tmp = dest.with_name(dest.name + ".part")
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        first = True
        while True:
            chunk = fi.read(_CHUNK)
            if not chunk:
                break
            plain = chunk.translate(_XOR_TABLE)
            if first:
                ext = container_of(plain[:16])
                first = False
            fo.write(plain)
    os.replace(tmp, dest)
    return ext


def player_cache_dir() -> Path:
    """播放时从客户端缓存解密出来的临时文件放这儿。

    跟其它数据一样放在程序目录下，不碰系统临时目录：某些机器上 TEMP 会被
    其它软件改成公共目录（例如 Wondershare 的 CreatorTemp），那里没有写权限，
    解密完 os.replace 会直接报 WinError 5，表现为「播放失败」。
    """
    from ncm import paths

    return paths.play_cache_dir()


def clear_player_cache() -> tuple[int, int]:
    """清掉播放缓存，返回 (文件数, 释放字节数)。

    之所以需要它：未登录时听 VIP 歌曲，客户端只会缓存 30 秒试听片段，
    登录后这段缓存仍然是 30 秒。清掉后重新取，才能拿到完整版。
    """
    root = player_cache_dir()
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


def purge_legacy_player_cache() -> int:
    """清掉旧版本留在系统临时目录的播放缓存，返回删除的文件数。

    老版本用 tempfile.gettempdir()，某些机器上那是其它软件建的公共目录；
    1.0.1 起改用配置目录，这里只负责收拾残局，失败也不影响启动。
    """
    import tempfile

    root = Path(tempfile.gettempdir()) / "ncm-player"
    if not root.is_dir():
        return 0
    removed = 0
    for path in root.iterdir():
        if not path.is_file():
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    try:
        root.rmdir()
    except OSError:
        pass
    return removed


def report(cache_dir: Path | str) -> dict[str, object]:
    """给「缓存诊断」用：把目录里到底有什么、为什么没进列表说清楚。"""
    root = Path(cache_dir)
    stats: dict[str, object] = {
        "root": str(root),
        "exists": root.is_dir(),
        "files": 0,
        "parsed": 0,
        "complete": 0,
        "partial": 0,
        "tiny": 0,
        "unparsed": [],
        "recent": [],
    }
    if not root.is_dir():
        return stats

    files = [p for p in root.rglob("*.uc") if p.is_file()]
    stats["files"] = len(files)
    entries = scan(root)
    stats["parsed"] = len(entries)
    stats["complete"] = sum(1 for e in entries if e.complete)
    stats["partial"] = sum(1 for e in entries if not e.complete)
    stats["tiny"] = sum(1 for e in entries if e.size <= 100_000)
    parsed_names = {e.path.name for e in entries}
    stats["unparsed"] = [p.name for p in files if p.name not in parsed_names][:5]
    recent = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    stats["recent"] = [f"{p.name}（{p.stat().st_size / 1048576:.1f} MB）" for p in recent]

    # 按子目录分组：不同客户端版本会把缓存摊到不同层，
    # 「有文件却不显示」时先看文件到底躺在哪一层
    groups: dict[str, int] = {}
    for path in files:
        try:
            rel = str(path.parent.relative_to(root)) or "."
        except ValueError:
            rel = str(path.parent)
        groups[rel] = groups.get(rel, 0) + 1
    stats["dirs"] = sorted(groups.items(), key=lambda kv: -kv[1])
    return stats
