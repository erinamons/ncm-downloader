"""数据模型与音质定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

QUALITY_ORDER = ["standard", "higher", "exhigh", "lossless", "hires"]

QUALITY_LABEL = {
    "standard": "标准",
    "higher": "较高",
    "exhigh": "极高",
    "lossless": "无损",
    "hires": "Hi-Res",
}

# 各档位「合格」的最低实际码率，用于识别服务端静默降级
QUALITY_FLOOR = {
    "standard": 96_000,
    "higher": 160_000,
    "exhigh": 256_000,
    "lossless": 900_000,
    "hires": 1_000_000,
}


def rank(level: str) -> int:
    return QUALITY_ORDER.index(level) if level in QUALITY_ORDER else -1


def normalize_level(level: str) -> str:
    level = (level or "").strip().lower()
    return level if level in QUALITY_ORDER else "exhigh"


@dataclass(slots=True)
class Song:
    id: int
    name: str
    artists: list[str]
    album: str
    duration_ms: int = 0
    track_no: int = 0
    cover_url: str = ""
    publish_time: int = 0

    @property
    def artist_text(self) -> str:
        # 接口偶尔给出 name: null，join 会直接抛 TypeError 把界面带崩
        names = [str(a) for a in self.artists if a]
        return " / ".join(names) if names else "未知艺人"

    @property
    def duration_text(self) -> str:
        total = self.duration_ms // 1000
        return f"{total // 60}:{total % 60:02d}"

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "Song":
        # /api/song/detail 用 artists/album/duration，/api/v3/song/detail 用 ar/al/dt
        artists_raw = raw.get("ar") or raw.get("artists") or []
        album_raw = raw.get("al") or raw.get("album") or {}
        cover = album_raw.get("picUrl") or raw.get("picUrl") or ""
        if cover.startswith("http://"):
            cover = "https://" + cover[len("http://"):]
        return cls(
            id=int(raw.get("id") or 0),
            name=raw.get("name") or "未知曲目",
            artists=[
                str(a.get("name")).strip()
                for a in artists_raw
                if isinstance(a, dict) and a.get("name")
            ],
            album=album_raw.get("name") or "",
            duration_ms=int(raw.get("dt") or raw.get("duration") or 0),
            track_no=int(raw.get("no") or 0),
            cover_url=cover,
            publish_time=int(raw.get("publishTime") or 0),
        )


@dataclass(slots=True)
class AudioSource:
    """一次取地址的结果。level 是请求档位，br/type 才代表真实到手的东西。"""

    song_id: int
    url: str
    br: int
    ext: str
    size: int = 0
    req_level: str = "exhigh"
    trial: bool = False
    free_trial: dict[str, Any] | None = None
    expi: int = 1200

    @property
    def ok(self) -> bool:
        return bool(self.url) and not self.trial

    @property
    def degraded(self) -> bool:
        """实际码率没达到请求档位，说明被服务端降级了。"""
        floor = QUALITY_FLOOR.get(self.req_level, 0)
        return self.br > 0 and self.br < floor

    @property
    def quality_label(self) -> str:
        for level in reversed(QUALITY_ORDER):
            if self.br >= QUALITY_FLOOR[level] * 0.9:
                return QUALITY_LABEL[level]
        return "低码率"

    @classmethod
    def from_api(cls, raw: dict[str, Any], req_level: str) -> "AudioSource":
        trial_info = raw.get("freeTrialInfo")
        return cls(
            song_id=int(raw.get("id") or 0),
            url=raw.get("url") or "",
            br=int(raw.get("br") or 0),
            ext=(raw.get("type") or "").lower(),
            size=int(raw.get("size") or 0),
            req_level=req_level,
            trial=trial_info is not None,
            free_trial=trial_info,
            expi=int(raw.get("expi") or 1200),
        )


@dataclass(slots=True)
class Playlist:
    id: int
    name: str
    creator: str
    songs: list[Song] = field(default_factory=list)
    track_count: int = 0
