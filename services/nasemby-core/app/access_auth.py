from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urlsplit


COOKIE_NAME = "mcc_session"
SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000
FAILURE_WINDOW_MS = 15 * 60 * 1000
LOCKOUT_MS = 15 * 60 * 1000
MAX_FAILURES = 5
MAX_SAFE_INTEGER = 9_007_199_254_740_991
SIGNING_CONTEXT = b"media-control-center/session/v1"


@dataclass(frozen=True)
class AccessConfig:
    enabled: bool
    access_key: str
    allowed_origins: tuple[str, ...]
    cookie_secure: bool


@dataclass(frozen=True)
class AccessSession:
    expires_at: int
    username: str | None = None


@dataclass(frozen=True)
class LoginResult:
    status: str
    token: str = ""
    expires_at: int = 0
    username: str | None = None


@dataclass
class FailureState:
    failures: int
    window_started_at: int
    locked_until: int


def _parse_boolean(name: str, value: str | None, fallback: bool):
    if value is None or value == "":
        return fallback
    if value.lower() in {"1", "true", "yes", "on"}:
        return True
    if value.lower() in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true 或 false")


def _parse_allowed_origins(value: str | None):
    if not value or not value.strip():
        return ()
    origins = []
    for raw in value.split(","):
        entry = raw.strip()
        try:
            parsed = urlsplit(entry)
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError
        except ValueError as exc:
            raise ValueError("MCC_ALLOWED_ORIGINS 必须使用完整的 http 或 https origin") from exc
        normalized = f"{parsed.scheme}://{parsed.netloc}"
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or "*" in entry
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or normalized != entry
        ):
            raise ValueError("MCC_ALLOWED_ORIGINS 必须使用不含路径、凭据或通配符的精确 origin")
        origins.append(entry)
    return tuple(origins)


def is_production_environment(environment: Mapping[str, str]):
    return any(
        str(environment.get(name, "")).lower() == "production"
        for name in ("NODE_ENV", "MCC_ENV", "APP_ENV")
    )


def resolve_access_config(environment: Mapping[str, str] | None = None):
    values = environment or {}
    production = is_production_environment(values)
    access_key = str(values.get("MCC_ACCESS_KEY", ""))
    if production and len(access_key) < 16:
        raise ValueError("生产环境 MCC_ACCESS_KEY 必须至少包含 16 个字符")
    return AccessConfig(
        enabled=bool(access_key),
        access_key=access_key,
        allowed_origins=_parse_allowed_origins(values.get("MCC_ALLOWED_ORIGINS")),
        cookie_secure=_parse_boolean("MCC_COOKIE_SECURE", values.get("MCC_COOKIE_SECURE"), production),
    )


def _base64url(value: bytes):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(value: str):
    padding = "=" * (-len(value) % 4)
    decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    if _base64url(decoded) != value:
        raise ValueError("non-canonical base64url")
    return decoded


class AccessAuth:
    supports_setup = False

    def __init__(self, config: AccessConfig, now_ms: Callable[[], int] | None = None):
        self.config = config
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._failures: dict[str, FailureState] = {}
        self._signing_key = hmac.new(
            (config.access_key or "auth-disabled").encode("utf-8"),
            SIGNING_CONTEXT,
            hashlib.sha256,
        ).digest()

    def _constant_time_key_equal(self, supplied: str):
        left = hashlib.sha256(supplied.encode("utf-8")).digest()
        right = hashlib.sha256(self.config.access_key.encode("utf-8")).digest()
        return hmac.compare_digest(left, right)

    def is_enabled(self):
        return self.config.enabled

    def setup_required(self):
        return False

    def cookie_secure(self, request_is_secure: bool):
        return self.config.cookie_secure

    def attempt_credentials(self, username: str, password: str, address: str):
        return self.attempt_login(password, address)

    def issue_token(self, expires_at: int, nonce: str | None = None):
        token_nonce = nonce or secrets.token_urlsafe(16)
        payload = f"v1.{expires_at}.{token_nonce}"
        signature = hmac.new(self._signing_key, payload.encode("utf-8"), hashlib.sha256).digest()
        return f"{payload}.{_base64url(signature)}"

    def attempt_login(self, access_key: str, address: str):
        if not self.config.enabled:
            return LoginResult("invalid")
        now = self._now_ms()
        current = self._failures.get(address)
        if current and current.locked_until > now:
            return LoginResult("locked")

        if self._constant_time_key_equal(access_key):
            self._failures.pop(address, None)
            expires_at = now + SESSION_TTL_MS
            return LoginResult("success", self.issue_token(expires_at), expires_at)

        if current is None or now - current.window_started_at >= FAILURE_WINDOW_MS:
            current = FailureState(0, now, 0)
        current.failures += 1
        if current.failures >= MAX_FAILURES:
            current.locked_until = now + LOCKOUT_MS
        self._failures[address] = current
        return LoginResult("locked" if current.locked_until > now else "invalid")

    def read_session(self, token: str | None):
        if not self.config.enabled:
            return None, False
        if token is None:
            return None, False
        parts = token.split(".")
        if len(parts) != 4 or parts[0] != "v1":
            return None, True
        try:
            expires_at = int(parts[1])
        except (TypeError, ValueError):
            return None, True
        if expires_at > MAX_SAFE_INTEGER or expires_at <= self._now_ms():
            return None, True
        payload = ".".join(parts[:3])
        expected = hmac.new(self._signing_key, payload.encode("utf-8"), hashlib.sha256).digest()
        try:
            supplied = _decode_base64url(parts[3])
        except (ValueError, TypeError):
            supplied = b""
        if not hmac.compare_digest(expected, supplied):
            return None, True
        return AccessSession(expires_at), False
