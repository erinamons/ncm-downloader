"""网易云 weapi 加密。

请求体经两次 AES-128-CBC:
    明文 -> AES(固定 nonce) -> AES(随机 secret) -> params
随机 secret 反转后经 RSA(1024, e=65537) 加密 -> encSecKey
"""
from __future__ import annotations

import base64
import json
import random
import string
from typing import Any

from Crypto.Cipher import AES

_MODULUS = (
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b72515"
    "2b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbd"
    "a92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cf"
    "e4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7"
)
_PUB_KEY = "010001"
_NONCE = "0CoJUm6Qyw8W8jud"
_IV = "0102030405060708"
_ALPHABET = string.ascii_letters + string.digits


def _pkcs7(raw: bytes, block: int = 16) -> bytes:
    pad = block - len(raw) % block
    return raw + bytes([pad]) * pad


def _aes_cbc(text: str, key: str) -> str:
    cipher = AES.new(key.encode(), AES.MODE_CBC, _IV.encode())
    return base64.b64encode(cipher.encrypt(_pkcs7(text.encode()))).decode()


def _rsa(text: str) -> str:
    num = int(text[::-1].encode().hex(), 16)
    return format(pow(num, int(_PUB_KEY, 16), int(_MODULUS, 16)), "x").zfill(256)


def random_secret(length: int = 16) -> str:
    return "".join(random.choices(_ALPHABET, k=length))


def weapi(payload: dict[str, Any], secret: str | None = None) -> dict[str, str]:
    """把业务参数加密成 weapi 的 params / encSecKey 两个字段。"""
    secret = secret or random_secret()
    plain = json.dumps(payload, separators=(",", ":"))
    params = _aes_cbc(_aes_cbc(plain, _NONCE), secret)
    return {"params": params, "encSecKey": _rsa(secret)}
