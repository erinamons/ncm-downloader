"""下载调度：取地址、断点续传、原子落盘、打标签。"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import requests

from . import cache as cache_mod
from . import transcode
from .backend import Backend, BackendError
from .models import AudioSource, Song
from .store import DEFAULT_TEMPLATE, Store, render_template
from .tagger import merge_lyrics, write_tags

OK = "ok"
SKIPPED = "skipped"
NO_COPYRIGHT = "nocopyright"
TRIAL = "trial"
CACHE_MISS = "cachemiss"
CANCELLED = "cancelled"
FAILED = "failed"

STATUS_TEXT = {
    OK: "完成",
    SKIPPED: "已存在",
    NO_COPYRIGHT: "无版权",
    TRIAL: "仅试听",
    CACHE_MISS: "无缓存",
    CANCELLED: "已取消",
    FAILED: "失败",
}

# 音源：auto 先查本地缓存再走接口，其余为强制指定
SOURCES = ("auto", "client", "web", "cache")
SOURCE_LABEL = {
    "auto": "自动（缓存优先）",
    "client": "客户端接口",
    "web": "网页接口",
    "cache": "仅本地缓存",
}
# 界面里空间紧张，用短标签
SOURCE_SHORT = {
    "auto": "自动",
    "client": "客户端",
    "web": "网页",
    "cache": "仅缓存",
}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_CHUNK = 64 * 1024


@dataclass(slots=True)
class TaskResult:
    song: Song
    status: str
    path: Path | None = None
    br: int = 0
    quality: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == OK


ProgressFn = Callable[[int, int], None]
StartFn = Callable[[Song], None]


def fetch_lyrics(backend: Backend, song: Song) -> str:
    """取歌词并按时间轴合并翻译；失败返回空串。"""
    try:
        main, trans = backend.lyric(song.id)
        return merge_lyrics(main, trans)
    except Exception:  # noqa: BLE001
        return ""


def fetch_cover(song: Song, size: int = 800) -> bytes:
    if not song.cover_url:
        return b""
    try:
        resp = requests.get(
            f"{song.cover_url}?param={size}y{size}",
            headers={"User-Agent": _UA, "Referer": "https://music.163.com/"},
            timeout=15,
        )
        return resp.content if resp.ok else b""
    except Exception:  # noqa: BLE001
        return b""


class Downloader:
    def __init__(
        self,
        backend: Backend,
        store: Store,
        out_dir: Path | str = "downloads",
        template: str = DEFAULT_TEMPLATE,
        level: str = "exhigh",
        workers: int = 3,
        with_cover: bool = True,
        with_lyric: bool = True,
        force: bool = False,
        source: str = "auto",
        cache_dir: Path | str | None = None,
        to_mp3: bool = False,
        mp3_bitrate: str = "auto",
        partial_fallback: bool = True,
    ):
        self.backend = backend
        self.store = store
        self.out_dir = Path(out_dir)
        self.template = template
        self.level = level
        self.workers = max(1, workers)
        self.with_cover = with_cover
        self.with_lyric = with_lyric
        self.force = force
        self.source = source if source in SOURCES else "auto"
        # 缓存只写了一半时怎么办：True=联网取完整版，False=跳过（不产生半截文件）
        self.partial_fallback = partial_fallback
        self.to_mp3 = to_mp3
        self.mp3_bitrate = mp3_bitrate
        self._printed = threading.Lock()
        self._cache: dict[int, cache_mod.CacheEntry] = {}
        self._cancel = threading.Event()
        self._song_cancels: set[int] = set()
        self._song_lock = threading.Lock()

        if self.source in ("auto", "cache"):
            root = Path(cache_dir) if cache_dir else cache_mod.default_cache_dir()
            if root and Path(root).is_dir():
                self._cache = {
                    e.song_id: e
                    for e in cache_mod.scan(root)
                    if e.complete and e.size > 200_000
                }

    def cancel(self) -> None:
        """请求中断：进行中的下载尽快收尾，未开始的直接跳过。"""
        self._cancel.set()

    def reset_cancel(self) -> None:
        self._cancel.clear()

    def cancel_song(self, song_id: int) -> None:
        """只放弃某一首：正在下的尽快停，还没开始的直接跳过。

        和 cancel() 分开记，是因为批量里下一首还得继续。
        """
        with self._song_lock:
            self._song_cancels.add(song_id)

    def _song_cancelled(self, song_id: int) -> bool:
        with self._song_lock:
            return song_id in self._song_cancels

    # ---------------- 单曲 ----------------

    def _resolve(self, song: Song) -> TaskResult | AudioSource:
        try:
            src = self.backend.song_url(song.id, self.level)
        except BackendError as exc:
            return TaskResult(song, FAILED, message=str(exc))

        if not src.url:
            return TaskResult(song, NO_COPYRIGHT, message="接口未返回播放地址")
        if src.trial:
            detail = src.free_trial or {}
            span = ""
            if detail.get("start") is not None and detail.get("end") is not None:
                span = f" {detail.get('start')}-{detail.get('end')}s"
            return TaskResult(
                song, TRIAL, br=src.br, quality=src.quality_label,
                message=f"仅提供试听片段{span}",
            )
        return src

    def _target_path(self, song: Song, src: AudioSource) -> Path:
        rel = render_template(self.template, song, src.quality_label)
        ext = (src.ext or "mp3").lower()
        name = str(rel)
        # 不能用 with_suffix，曲名里带 "." 会把后半截当成后缀吃掉
        if not name.lower().endswith("." + ext):
            name = f"{name}.{ext}"
        return self.out_dir / name

    def _from_cache(self, song: Song, entry: cache_mod.CacheEntry) -> TaskResult:
        ext = cache_mod.peek(entry.path)
        if ext == "bin":
            ext = "m4a"
        rel = render_template(self.template, song, entry.rate_label)
        target = self.out_dir / f"{rel}.{ext}"

        if target.exists() and not self.force:
            self.store.record(song.id, 0, ext, target, target.stat().st_size)
            return TaskResult(
                song, SKIPPED, path=target, quality=entry.rate_label,
                message="文件已存在",
            )

        try:
            real = cache_mod.decode(entry.path, target)
        except Exception as exc:  # noqa: BLE001
            return TaskResult(song, FAILED, message=f"缓存解码失败: {exc}")

        if real != ext:
            renamed = target.with_name(target.name[: -len(ext)] + real)
            target.replace(renamed)
            target = renamed

        target, extra = self._maybe_transcode(target)
        note = extra + self._tag(target, song)
        size = target.stat().st_size
        # 缓存拿不到精确码率，记 0 表示来源为本地，不参与接口音质比较
        self.store.record(song.id, 0, target.suffix.lstrip("."), target, size)
        return TaskResult(
            song, OK, path=target, br=0, quality=entry.rate_label,
            message=f"缓存 {entry.rate_label}{note}",
        )

    def download(self, song: Song, on_progress: ProgressFn | None = None) -> TaskResult:
        # 单曲取消标记只在这一次任务里生效：结束后必须清掉，否则同一会话
        # 里再下载这首（换音质重试、重新入队）会永远 CANCELLED
        try:
            return self._download_one(song, on_progress)
        finally:
            with self._song_lock:
                self._song_cancels.discard(song.id)

    def _download_one(
        self, song: Song, on_progress: ProgressFn | None = None
    ) -> TaskResult:
        if self._cancel.is_set() or self._song_cancelled(song.id):
            return TaskResult(song, CANCELLED, message="已取消")
        if not self.force and self.store.done(song.id):
            row = self.store.get(song.id)
            return TaskResult(
                song, SKIPPED, path=Path(row["path"]) if row else None,
                br=int(row["br"]) if row else 0,
                quality="", message="记录已存在",
            )

        partial = False
        if self.source in ("auto", "cache"):
            entry = self._cache.get(song.id)
            if entry is not None:
                if entry.complete:
                    return self._from_cache(song, entry)
                # 缓存只写了一半（客户端边听边写，没听完就是半截）。
                # 直接导出会得到一首残缺的歌，所以默认联网取完整版；
                # 明确关掉联网补齐时宁可跳过，也不产出半截文件。
                if self.source == "cache" and not self.partial_fallback:
                    return TaskResult(
                        song,
                        CACHE_MISS,
                        message="缓存未下完，已跳过（未开启联网补齐）",
                    )
                partial = True
            elif self.source == "cache":
                return TaskResult(song, CACHE_MISS, message="本地缓存里没有这首")

        resolved = self._resolve(song)
        if isinstance(resolved, TaskResult):
            return resolved
        src = resolved

        target = self._target_path(song, src)
        if target.exists() and not self.force:
            self.store.record(song.id, src.br, src.ext, target, target.stat().st_size)
            return TaskResult(
                song, SKIPPED, path=target, br=src.br, quality=src.quality_label,
                message="文件已存在",
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")

        try:
            self._fetch_stream(src.url, part, src.size, on_progress, song.id)
        except InterruptedError:
            # 主动取消就把半成品删掉：留着也没人续传，只会占空间
            part.unlink(missing_ok=True)
            return TaskResult(
                song, CANCELLED, br=src.br, quality=src.quality_label,
                message="已取消",
            )
        except Exception as exc:  # noqa: BLE001
            return TaskResult(
                song, FAILED, br=src.br, quality=src.quality_label,
                message=f"下载中断: {exc}",
            )

        if src.size and part.stat().st_size < src.size:
            return TaskResult(
                song, FAILED, br=src.br, quality=src.quality_label,
                message=f"文件不完整 {part.stat().st_size}/{src.size}",
            )

        os.replace(part, target)

        note = "（缓存未完整，已联网补齐）" if partial else ""
        if src.degraded:
            note += f"（请求 {self.level}，实得 {src.br // 1000}k）"
        target, extra = self._maybe_transcode(target)
        note += extra
        if self.with_lyric or self.with_cover:
            note += self._tag(target, song)

        self.store.record(
            song.id, src.br, target.suffix.lstrip("."), target, target.stat().st_size
        )
        return TaskResult(
            song, OK, path=target, br=src.br, quality=src.quality_label,
            message=note.strip(),
        )

    def _fetch_stream(
        self,
        url: str,
        part: Path,
        total_hint: int,
        on_progress: ProgressFn | None,
        song_id: int = 0,
    ) -> None:
        if part.exists() and part.stat().st_size >= 3:
            # 正常 .part 是裸音频；出现 ID3 头说明里面混了已打标签的文件，
            # 按字节偏移续传会让音频错位，直接丢弃
            with open(part, "rb") as fh:
                if fh.read(3) == b"ID3":
                    part.unlink(missing_ok=True)

        have = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": _UA, "Referer": "https://music.163.com/"}
        if have:
            headers["Range"] = f"bytes={have}-"

        with requests.get(url, headers=headers, stream=True, timeout=(10, 60)) as resp:
            if resp.status_code == 416 and have:
                # .part 已经等于全量（上次最后一字节写完、还没来得及
                # os.replace 就断了）。Range 起点越界永远 416，删掉从头下
                part.unlink(missing_ok=True)
                return self._fetch_stream(url, part, total_hint, on_progress, song_id)
            if have and resp.status_code == 200:
                have = 0  # 服务端不支持续传，从头来
            elif resp.status_code not in (200, 206):
                raise RuntimeError(f"HTTP {resp.status_code}")

            total = total_hint
            crange = resp.headers.get("Content-Range")
            if crange and "/" in crange:
                tail = crange.rsplit("/", 1)[1]
                # 有些代理回 "bytes 0-99/*"，total 是星号
                total = int(tail) if tail.isdigit() else total_hint
            elif resp.headers.get("Content-Length"):
                total = have + int(resp.headers["Content-Length"])

            mode = "ab" if have else "wb"
            done = have
            with open(part, mode) as fh:
                for chunk in resp.iter_content(_CHUNK):
                    if not chunk:
                        continue
                    if self._cancel.is_set() or self._song_cancelled(song_id):
                        raise InterruptedError("cancelled")
                    fh.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)

    def _maybe_transcode(self, target: Path) -> tuple[Path, str]:
        """按需把非 mp3 容器转成 mp3，返回 (新路径, 附加说明)。"""
        if not self.to_mp3 or target.suffix.lower() == ".mp3":
            return target, ""
        src_br = transcode.probe_bitrate(target)
        try:
            new_path = transcode.to_mp3(target, bitrate=self.mp3_bitrate)
        except Exception as exc:  # noqa: BLE001
            return target, f"；转 mp3 失败: {exc}"
        out_br = transcode.probe_bitrate(new_path) // 1000
        if src_br:
            return new_path, f"；已转 mp3 {out_br}k（源 {src_br // 1000}k）"
        return new_path, f"；已转 mp3 {out_br}k"

    def _tag(self, path: Path, song: Song) -> str:
        notes = []
        lyrics = ""
        if self.with_lyric:
            lyrics = fetch_lyrics(self.backend, song)
            if not lyrics:
                notes.append("歌词缺失")
        cover = fetch_cover(song) if self.with_cover else b""
        if self.with_cover and song.cover_url and not cover:
            notes.append("封面缺失")
        try:
            write_tags(path, song, lyrics, cover)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"标签写入失败: {exc}")
        return "".join(f"；{n}" for n in notes)

    # ---------------- 批量 ----------------

    def run(
        self,
        songs: Iterable[Song],
        on_result: Callable[[TaskResult], None] | None = None,
        on_start: StartFn | None = None,
        on_progress: Callable[[Song, int, int], None] | None = None,
    ) -> list[TaskResult]:
        items = list(songs)
        results: list[TaskResult] = []
        self.reset_cancel()

        def work(song: Song) -> TaskResult:
            if self._cancel.is_set():
                return TaskResult(song, CANCELLED, message="已取消")
            if on_start:
                on_start(song)
            cb = (lambda d, t: on_progress(song, d, t)) if on_progress else None
            return self.download(song, cb)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(work, s): s for s in items}
            for fut in as_completed(futures):
                song = futures[fut]
                try:
                    res = fut.result()
                except Exception as exc:  # noqa: BLE001
                    res = TaskResult(song, FAILED, message=f"未捕获错误: {exc}")
                results.append(res)
                if on_result:
                    with self._printed:
                        on_result(res)
        return results
