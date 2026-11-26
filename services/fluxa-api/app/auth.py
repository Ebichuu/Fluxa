from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

from app.storage import FluxaStore

SESSION_COOKIE = "fluxa_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


class AuthManager:
    def __init__(self, store: FluxaStore):
        self.store = store

    def is_initialized(self) -> bool:
        return self.store.read_admin() is not None

    def setup(self, username: str, password: str) -> dict[str, Any]:
        username = str(username or "").strip()
        password = str(password or "")
        if not 3 <= len(username) <= 32:
            raise ValueError("管理员账号需为 3~32 位字符")
        if not 6 <= len(password) <= 128:
            raise ValueError("管理员密码需为 6~128 位字符")
        created = self.store.init_admin(
            username=username,
            password_hash=generate_password_hash(password),
            session_secret=secrets.token_urlsafe(32),
        )
        if created is None:
            raise ValueError("管理员账号已初始化，请直接登录")
        return created

    def verify_login(self, username: str, password: str) -> dict[str, Any] | None:
        admin = self.store.read_admin()
        if not admin:
            return None
        if str(username or "").strip().casefold() != admin["username_normalized"]:
            return None
        if not check_password_hash(admin["password_hash"], str(password or "")):
            return None
        return admin

    def create_token(self, admin: dict[str, Any]) -> str:
        payload = {
            "u": admin["username"],
            "exp": int(time.time()) + SESSION_TTL_SECONDS,
        }
        body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        sig = hmac.new(
            str(admin["session_secret"]).encode("utf-8"),
            body.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{body}.{_b64(sig)}"

    def verify_token(self, token: str) -> dict[str, Any] | None:
        if not token or "." not in token:
            return None
        admin = self.store.read_admin()
        if not admin:
            return None
        try:
            body, sig_b64 = token.split(".", 1)
            expected = hmac.new(
                str(admin["session_secret"]).encode("utf-8"),
                body.encode("ascii"),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(expected, _unb64(sig_b64)):
                return None
            payload = json.loads(_unb64(body).decode("utf-8"))
            if int(payload.get("exp") or 0) < int(time.time()):
                return None
            return {"username": str(payload.get("u") or admin["username"])}
        except Exception:
            return None
