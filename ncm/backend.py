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
        # Session 的 cookie jar 不是线程安全的，取地址阶段又会多线程调用
        self._lock = threading.Lock()
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

    # ---------------- 底层请求 ----------------

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = path if path.startswith("http") else BASE + path
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with self._lock:
                    resp = self.session.request(
                        method, url, timeout=self.timeout, **kwargs
                    )
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < self.retries:
                    time.sleep(0.6 * (attempt + 1))
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
        payload = json.dumps([{"id": sid} for sid in song_ids])
        data = self._api("/api/v3/song/detail", {"c": payload})
        return [Song.from_api(item) for item in (data.get("songs") or [])]

    def song_detail(self, song_id: int) -> Song | None:
        songs = self.song_details([song_id])
        return songs[0] if songs else None

    def playlist(self, playlist_id: int) -> Playlist:
        meta = self._api(
            "/api/v6/playlist/detail", {"id": playlist_id, "n": 1000, "s": 0}
        )
        raw = meta.get("playlist") or {}
        if not raw:
            raise BackendError(f"歌单 {playlist_id} 不存在或不可见")
        songs = [Song.from_api(item) for item in (raw.get("tracks") or [])]
        total = int(raw.get("trackCount") or len(songs))
        if len(songs) < total:
            songs = self._playlist_tracks(playlist_id, total)
        creator = (raw.get("creator") or {}).get("nickname", "")
        return Playlist(
            id=playlist_id,
            name=raw.get("name") or f"歌单{playlist_id}",
            creator=creator,
            songs=songs,
            track_count=total,
        )

    def _playlist_tracks(self, playlist_id: int, total: int) -> list[Song]:
        songs: list[Song] = []
        offset = 0
        while offset < total:
            data = self._api(
                "/api/v6/playlist/track/all",
                {"id": playlist_id, "limit": 500, "offset": offset},
            )
            batch = [Song.from_api(item) for item in (data.get("songs") or [])]
            if not batch:
                break
            songs.extend(batch)
            offset += len(batch)
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
