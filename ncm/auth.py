"""账号登录与登录态持久化。

只做扫码登录：不接触密码，成功后在本地保存服务端下发的 Cookie。
Cookie 等价于账号凭证，配置文件不要外传。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

BASE = "https://music.163.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 轮询状态码
EXPIRED = 800
WAITING = 801
SCANNED = 802
CONFIRMED = 803


@dataclass(slots=True)
class Account:
    nickname: str = ""
    user_id: int = 0
    vip_type: int = 0

    @property
    def vip_text(self) -> str:
        if self.vip_type <= 0:
            return "非会员"
        return "黑胶 VIP" if self.vip_type >= 10 else "会员"


def config_dir() -> Path:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    return home / ".ncm-downloader"


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_config(data: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:  # noqa: BLE001
        pass


def load_cookie() -> str:
    return str(load_config().get("cookie") or "")


def saved_account() -> Account:
    cfg = load_config()
    return Account(
        nickname=str(cfg.get("nickname") or ""),
        user_id=int(cfg.get("user_id") or 0),
        vip_type=int(cfg.get("vip_type") or 0),
    )


def save_login(cookie: str, account: Account) -> None:
    cfg = load_config()
    cfg.update(
        {
            "cookie": cookie,
            "nickname": account.nickname,
            "user_id": account.user_id,
            "vip_type": account.vip_type,
            "saved_at": int(time.time()),
        }
    )
    save_config(cfg)


def clear_login() -> None:
    cfg = load_config()
    for key in ("cookie", "nickname", "user_id", "vip_type", "saved_at"):
        cfg.pop(key, None)
    save_config(cfg)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": UA,
            "Referer": f"{BASE}/",
            "Origin": BASE,
            "Cookie": "os=pc; appver=2.9.7; channel=netease;",
        }
    )
    return s


def cookie_string(session: requests.Session) -> str:
    """把 session 里的 Cookie 拼成请求头格式，只保留有效键值。"""
    parts = []
    for key, value in session.cookies.items():
        if not value:
            continue
        parts.append(f"{key}={value}")
    return "; ".join(parts)


class QrLogin:
    """扫码登录会话。"""

    def __init__(self) -> None:
        self.session = _session()
        self.key = ""

    def start(self) -> str:
        resp = self.session.post(
            f"{BASE}/api/login/qrcode/unikey", data={"type": 1}, timeout=15
        )
        data = resp.json()
        if data.get("code") != 200 or not data.get("unikey"):
            raise RuntimeError(f"获取二维码失败: {data}")
        self.key = data["unikey"]
        return self.key

    @property
    def url(self) -> str:
        return f"{BASE}/login?codekey={self.key}"

    def poll(self) -> int:
        resp = self.session.post(
            f"{BASE}/api/login/qrcode/client/login",
            data={"key": self.key, "type": 1},
            timeout=15,
        )
        return int(resp.json().get("code") or 0)

    def wait(self, on_tick=None, timeout: float = 180.0) -> bool:
        """阻塞等待扫码确认。返回是否成功。"""
        deadline = time.time() + timeout
        last = -1
        while time.time() < deadline:
            code = self.poll()
            if code != last and on_tick:
                on_tick(code)
                last = code
            if code == CONFIRMED:
                return True
            if code == EXPIRED:
                return False
            time.sleep(2)
        return False


def fetch_account(cookie: str) -> Account:
    """用 Cookie 查账号信息，顺带验证 Cookie 是否还有效。"""
    s = _session()
    s.headers["Cookie"] = f"os=pc; appver=2.9.7; channel=netease; {cookie}"
    try:
        resp = s.post(f"{BASE}/api/nuser/account/get", timeout=15)
        data = resp.json()
    except Exception:  # noqa: BLE001
        return Account()
    profile = data.get("profile") or {}
    account = data.get("account") or {}
    return Account(
        nickname=profile.get("nickname") or "",
        user_id=int(profile.get("userId") or account.get("id") or 0),
        vip_type=int(account.get("vipType") or profile.get("vipType") or 0),
    )
