"""接口层：Backend 协议 + 自研 LocalBackend。

协议只暴露上层需要的最小集合，换成社区服务实现的 RemoteBackend 时
下载、打标签、落盘这些代码一行都不用动。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Protocol

import requests

from .crypto import weapi
from .models import AudioSource, Playlist, Song
from .trace import trace

BASE = "https://music.163.com"

# 服务端按 UA + appver 判断"端"，进而决定下发什么权限。
# 网页 UA 用不了「免费听歌」这类客户端权益，能拿到的音质也更低；
# 伪装成 PC 客户端后，同一账号能用上这些权益，档位也更高。
UA = "NeteaseMusicDesktop/2.10.13"
APP_VER = "8.10.90"
UA_WEB = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class BackendError(RuntimeError):
    pass


class Backend(Protocol):
    def search(self, keyword: str, limit: int = 30, offset: int = 0) -> list[Song]: ...

    def song_detail(self, song_id: int) -> Song | None: ...

    def song_details(self, song_ids: list[int]) -> list[Song]: ...

    def playlist(self, playlist_id: int) -> Playlist: ...

    def album(self, album_id: int) -> Playlist: ...

    def expand_link(self, url: str) -> str: ...

    def song_url(self, song_id: int, level: str = "exhigh") -> AudioSource: ...

    def lyric(self, song_id: int) -> tuple[str, str]: ...


class LocalBackend:
    """网页端接口实现。除详情/歌词外，其余走 weapi 加密。"""

    def __init__(
        self,
        cookie: str = "",
        timeout: float = 15.0,
        retries: int = 2,
        client_mode: bool = True,
    ):
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        cookie = (cookie or "").strip().strip('"').strip("'")
        if client_mode:
            headers = {
                "User-Agent": UA,
                "Cookie": f"os=pc; appver={APP_VER}; channel=netease; {cookie}".strip(),
            }
        else:
            headers = {
                "User-Agent": UA_WEB,
                "Cookie": f"os=pc; appver=2.9.7; channel=netease; {cookie}".strip(),
            }
        self.session.headers.update(
            {
                **headers,
                "Referer": f"{BASE}/",
                "Origin": BASE,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        # cookie jar 不是线程安全的，但用一把大锁把整个请求串起来，等于让
        # 3 个下载线程的取址/歌词/封面全部排队。改成每个线程一个 Session：
        # 互不共享 jar，也就不需要锁
        self._local = threading.local()
        self._local.session = self.session

    def _session(self) -> requests.Session:
        """当前线程专用的 Session（主线程复用构造时那份）。"""
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(self.session.headers)
            self._local.session = session
        return session

    # ---------------- 底层请求 ----------------

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = path if path.startswith("http") else BASE + path
        last: Exception | None = None
        started = time.perf_counter()
        for attempt in range(self.retries + 1):
            try:
                resp = self._session().request(
                    method, url, timeout=self.timeout, **kwargs
                )
                resp.raise_for_status()
                trace(
                    "接口", f"{method} {path}", "ok",
                    (time.perf_counter() - started) * 1000,
                    {"attempt": attempt + 1, "code": resp.status_code},
                )
                return resp.json()
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < self.retries:
                    time.sleep(0.6 * (attempt + 1))
        trace(
            "接口", f"{method} {path}", "fail",
            (time.perf_counter() - started) * 1000,
            {"error": type(last).__name__ if last else "unknown"},
        )
        raise BackendError(f"请求失败 {path}: {last}")

    def _weapi(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST", path, data=weapi(payload), params={"csrf_token": ""}
        )

    def _api(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._request("GET", path, params=params)

    # ---------------- 业务接口 ----------------

    def search(self, keyword: str, limit: int = 30, offset: int = 0) -> list[Song]:
        # cloudsearch/get/web 匿名调用会返回 50000005，改用 search/get
        data = self._weapi(
            "/weapi/search/get",
            {"s": keyword, "type": 1, "limit": limit, "offset": offset, "total": True},
        )
        raw = ((data.get("result") or {}).get("songs") or [])
        songs = [Song.from_api(item) for item in raw]
        if not songs:
            return []
        # 搜索结果是老字段结构，缺 no / 封面；补一次详情，保证后续命名与去重一致
        try:
            detail = {s.id: s for s in self.song_details([s.id for s in songs])}
        except BackendError:
            return songs
        return [detail.get(song.id, song) for song in songs]

    def song_details(self, song_ids: list[int]) -> list[Song]:
        if not song_ids:
            return []
        # c 参数是整段 JSON 拼 URL，批次太大会被服务端拒；统一按 100 一批
        result: list[Song] = []
        for start in range(0, len(song_ids), 100):
            chunk = song_ids[start : start + 100]
            payload = json.dumps([{"id": sid} for sid in chunk])
            data = self._api("/api/v3/song/detail", {"c": payload})
            result.extend(Song.from_api(item) for item in (data.get("songs") or []))
        return result

    def expand_link(self, url: str) -> str:
        """跟随 163cn.tv 短链的跳转，拿到带 id 的完整地址。"""
        resp = self._session().head(url, allow_redirects=True, timeout=self.timeout)
        return resp.url or url

    def song_detail(self, song_id: int) -> Song | None:
        songs = self.song_details([song_id])
        return songs[0] if songs else None

    def playlist(self, playlist_id: int) -> Playlist:
        meta = self._api(
            "/api/v6/playlist/detail", {"id": playlist_id, "n": 1000, "s": 0}
        )
        raw = meta.get("playlist") or {}
        if not raw:
            raise BackendError(
                f"歌单 {playlist_id} 打不开：可能已删除、设成私密，或需要登录才能查看"
            )

        songs = [Song.from_api(item) for item in (raw.get("tracks") or [])]
        # trackIds 才是完整曲目表：detail 的 tracks 最多给 1000 首，
        # 而且 trackCount 经常是旧值（实测有过 53 对 54 的情况），不能拿来当准
        ids: list[int] = []
        for item in raw.get("trackIds") or []:
            try:
                ids.append(int(item.get("id")))
            except (TypeError, ValueError):
                continue
        ids = list(dict.fromkeys(ids))  # 去重且保持歌单顺序

        got = {song.id for song in songs}
        missing = [ident for ident in ids if ident not in got]
        if missing:
            songs.extend(self._songs_by_ids(missing))
        if ids:
            # 按 trackIds 排序，保证和客户端里看到的顺序一致
            by_id = {song.id: song for song in songs}
            songs = [by_id[ident] for ident in ids if ident in by_id]

        creator = (raw.get("creator") or {}).get("nickname", "")
        return Playlist(
            id=playlist_id,
            name=raw.get("name") or f"歌单{playlist_id}",
            creator=creator,
            songs=songs,
            track_count=len(songs),
        )

    def album(self, album_id: int) -> Playlist:
        """专辑：/api/v1/album 才带 songs，v6 路径是 404。"""
        data = self._api(f"/api/v1/album/{album_id}", {})
        raw = data.get("album") or {}
        if not raw:
            raise BackendError(f"专辑 {album_id} 打不开：可能已下架或需要登录")
        songs = [Song.from_api(item) for item in (data.get("songs") or [])]
        artist = (raw.get("artist") or {}).get("name", "")
        return Playlist(
            id=album_id,
            name=raw.get("name") or f"专辑{album_id}",
            creator=artist,
            songs=songs,
            track_count=len(songs),
        )

    def _songs_by_ids(self, song_ids: list[int], chunk: int = 200) -> list[Song]:
        songs: list[Song] = []
        for start in range(0, len(song_ids), chunk):
            batch = song_ids[start : start + chunk]
            data = self._api("/api/song/detail", {"ids": json.dumps(batch)})
            songs.extend(Song.from_api(item) for item in (data.get("songs") or []))
        return songs

    def song_url(self, song_id: int, level: str = "exhigh") -> AudioSource:
        encode = "flac" if level in ("lossless", "hires") else "mp3"
        data = self._weapi(
            "/weapi/song/enhance/player/url/v1",
            {"ids": f"[{song_id}]", "level": level, "encodeType": encode},
        )
        if data.get("code") != 200:
            raise BackendError(f"取地址失败 code={data.get('code')}")
        items = data.get("data") or []
        if not items:
            raise BackendError("接口未返回音频数据")
        return AudioSource.from_api(items[0], level)

    def lyric(self, song_id: int) -> tuple[str, str]:
        data = self._api(
            "/api/song/lyric",
            {"id": song_id, "lv": 1, "kv": 1, "tv": -1, "rv": 1},
        )
        main = ((data.get("lrc") or {}).get("lyric") or "").strip()
        trans = ((data.get("tlyric") or {}).get("lyric") or "").strip()
        return main, trans
