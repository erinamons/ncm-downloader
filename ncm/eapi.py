"""eapi 加密（客户端接口使用）。

与 weapi 的双层 AES + RSA 不同，eapi 是 AES-128-ECB 单层：
    明文 = f"nobody{path}use{json}md5forencrypt"
    digest = md5(明文)
    待加密 = f"{path}-36cd479b6b5-{json}-36cd479b6b5-{digest}"
    params = AES-ECB(待加密).hex().upper()
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from Crypto.Cipher import AES

EAPI_KEY = b"e82ckenh8dichen8"
EAPI_MAGIC = "36cd479b6b5"


def eapi(path: str, payload: dict[str, Any]) -> str:
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    message = f"nobody{path}use{text}md5forencrypt"
    digest = hashlib.md5(message.encode()).hexdigest()
    data = f"{path}-{EAPI_MAGIC}-{text}-{EAPI_MAGIC}-{digest}"
    raw = data.encode()
    pad = 16 - len(raw) % 16
    cipher = AES.new(EAPI_KEY, AES.MODE_ECB)
    return cipher.encrypt(raw + bytes([pad]) * pad).hex().upper()


def eapi_body(path: str, payload: dict[str, Any]) -> dict[str, str]:
    return {"params": eapi(path, payload)}
