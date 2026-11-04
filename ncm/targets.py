"""从链接或裸 ID 识别下载目标。"""
from __future__ import annotations

import re

ID_RE = re.compile(r"[?&#]id=(\d+)")
KINDS = ("playlist", "album", "song", "program")


def parse_target(text: str) -> tuple[str, int]:
    text = (text or "").strip()
    if not text:
        raise ValueError("目标为空")
    if text.isdigit():
        return "song", int(text)
    match = ID_RE.search(text)
    if not match:
        raise ValueError(f"无法从「{text}」中解析出 id，请使用完整链接或直接给数字 id")
    ident = int(match.group(1))
    lowered = text.lower()
    kind = "song"
    for candidate in KINDS:
        if candidate in lowered:
            kind = candidate
            break
    if kind == "program":
        raise ValueError("暂不支持电台节目，请使用单曲或歌单")
    return kind, ident
