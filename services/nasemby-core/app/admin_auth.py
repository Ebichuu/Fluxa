from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from werkzeug.security import check_password_hash, generate_password_hash

from app.access_auth import (
    AccessSession,
    FailureState,
    LoginResult,
    FAILURE_WINDOW_MS,
    LOCKOUT_MS,
    MAX_FAILURES,
    MAX_SAFE_INTEGER,
    SESSION_TTL_MS,
    SIGNING_CONTEXT,
    _base64url,
    _decode_base64url,
)


@dataclass(frozen=True)
class AdminCredential:
    username: str
    username_normalized: str
    password_hash: str
    session_secret: str
    created_at: int
    updated_at: int


def normalize_admin_username(username: str) -> str:
    return str(username or "").strip().casefold()


def validate_admin_username(username: str) -> str:
    value = str(username or "").strip()
    if not 3 <= len(value) <= 32 or any(ord(character) < 32 for character in value):
        raise ValueError("管理员账号必须为 3–32 个可见字符")
    return value


def validate_admin_password(password: str) -> str:
    value = str(password or "")
    if not 8 <= len(value) <= 128:
        raise ValueError("管理员密码必须为 8–128 个字符")
    return value


class AdminCredentialStore:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)

    def _connect(self):
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_credentials (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                username TEXT NOT NULL,
                username_normalized TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                session_secret TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        return connection

    @staticmethod
    def _map_row(row):
        if row is None:
            return None
        return AdminCredential(
            username=str(row["username"]),
            username_normalized=str(row["username_normalized"]),
            password_hash=str(row["password_hash"]),
            session_secret=str(row["session_secret"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )

    def read(self):
        connection = self._connect()
        try:
            return self._map_row(connection.execute(
                "SELECT username, username_normalized, password_hash, session_secret, created_at, updated_at "
                "FROM admin_credentials WHERE id = 1"
            ).fetchone())
        finally:
            connection.close()

    def initialize(self, username: str, password_hash: str, now_ms: int):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM admin_credentials WHERE id = 1").fetchone():
                connection.rollback()
                return None
            session_secret = secrets.token_urlsafe(32)
            connection.execute(
                "INSERT INTO admin_credentials "
                "(id, username, username_normalized, password_hash, session_secret, created_at, updated_at) "
                "VALUES (1, ?, ?, ?, ?, ?, ?)",
                (
                    username,
                    normalize_admin_username(username),
                    password_hash,
                    session_secret,
                    now_ms,
                    now_ms,
                ),
            )
            connection.commit()
            return AdminCredential(
                username,
                normalize_admin_username(username),
                password_hash,
                session_secret,
                now_ms,
                now_ms,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update_password(self, username: str, password_hash: str, now_ms: int):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT username FROM admin_credentials WHERE id = 1 AND username_normalized = ?",
                (normalize_admin_username(username),),
            ).fetchone()
            if current is None:
                connection.rollback()
                return None
            session_secret = secrets.token_urlsafe(32)
            connection.execute(
                "UPDATE admin_credentials SET password_hash = ?, session_secret = ?, updated_at = ? WHERE id = 1",
                (password_hash, session_secret, now_ms),
            )
            connection.commit()
            return self.read()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


class AdminAuth:
    supports_setup = True

    def __init__(self, store: AdminCredentialStore, now_ms: Callable[[], int] | None = None):
        self.store = store
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._failures: dict[str, FailureState] = {}
        self._dummy_password_hash = generate_password_hash("fluxa-invalid-password", method="scrypt")

    def is_enabled(self):
        return True

    def setup_required(self):
        return self.store.read() is None

    def cookie_secure(self, request_is_secure: bool):
        return bool(request_is_secure)

    @staticmethod
    def _signing_key(session_secret: str):
        return hmac.new(session_secret.encode("utf-8"), SIGNING_CONTEXT, hashlib.sha256).digest()

    def issue_token(self, admin: AdminCredential, expires_at: int, nonce: str | None = None):
        token_nonce = nonce or secrets.token_urlsafe(16)
        payload = f"v2.{expires_at}.{token_nonce}"
        signature = hmac.new(
            self._signing_key(admin.session_secret),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return f"{payload}.{_base64url(signature)}"

    def _success(self, admin: AdminCredential):
        expires_at = self._now_ms() + SESSION_TTL_MS
        return LoginResult("success", self.issue_token(admin, expires_at), expires_at, admin.username)

    def initialize(self, username: str, password: str):
        username = validate_admin_username(username)
        password = validate_admin_password(password)
        password_hash = generate_password_hash(password, method="scrypt")
        admin = self.store.initialize(username, password_hash, self._now_ms())
        return LoginResult("already_initialized") if admin is None else self._success(admin)

    def attempt_credentials(self, username: str, password: str, address: str):
        now = self._now_ms()
        current = self._failures.get(address)
        if current and current.locked_until > now:
            return LoginResult("locked")

        admin = self.store.read()
        password_hash = admin.password_hash if admin is not None else self._dummy_password_hash
        username_matches = bool(admin) and hmac.compare_digest(
            normalize_admin_username(username).encode("utf-8"),
            admin.username_normalized.encode("utf-8"),
        )
        password_matches = check_password_hash(password_hash, str(password or ""))
        if admin is not None and username_matches and password_matches:
            self._failures.pop(address, None)
            return self._success(admin)

        if current is None or now - current.window_started_at >= FAILURE_WINDOW_MS:
            current = FailureState(0, now, 0)
        current.failures += 1
        if current.failures >= MAX_FAILURES:
            current.locked_until = now + LOCKOUT_MS
        self._failures[address] = current
        return LoginResult("locked" if current.locked_until > now else "invalid")

    def reset_password(self, username: str, password: str):
        username = validate_admin_username(username)
        password = validate_admin_password(password)
        return self.store.update_password(
            username,
            generate_password_hash(password, method="scrypt"),
            self._now_ms(),
        )

    def read_session(self, token: str | None):
        if token is None:
            return None, False
        parts = token.split(".")
        if len(parts) != 4 or parts[0] != "v2":
            return None, True
        try:
            expires_at = int(parts[1])
        except (TypeError, ValueError):
            return None, True
        if expires_at > MAX_SAFE_INTEGER or expires_at <= self._now_ms():
            return None, True
        admin = self.store.read()
        if admin is None:
            return None, True
        payload = ".".join(parts[:3])
        expected = hmac.new(
            self._signing_key(admin.session_secret),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        try:
            supplied = _decode_base64url(parts[3])
        except (ValueError, TypeError):
            supplied = b""
        if not hmac.compare_digest(expected, supplied):
            return None, True
        return AccessSession(expires_at, admin.username), False
