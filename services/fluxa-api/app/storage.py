from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import default_settings, merge_settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class FluxaStore:
    """Minimal, self-contained SQLite persistence (6 clean tables)."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS admin_credentials (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        username TEXT NOT NULL,
                        username_normalized TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        session_secret TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS app_settings (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        payload_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS media_preferences (
                        media_key TEXT PRIMARY KEY,
                        intent TEXT NOT NULL DEFAULT 'follow',
                        preferred_target TEXT NOT NULL DEFAULT '',
                        notes TEXT NOT NULL DEFAULT '',
                        muted_issue_keys TEXT NOT NULL DEFAULT '[]',
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS decision_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        media_key TEXT NOT NULL,
                        title TEXT NOT NULL,
                        decision_type TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        source_label TEXT NOT NULL DEFAULT '',
                        rule_name TEXT NOT NULL DEFAULT '',
                        reason_text TEXT NOT NULL,
                        details_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_decision_logs_media
                        ON decision_logs(media_key, id DESC);

                    CREATE TABLE IF NOT EXISTS rss_sources (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        site_id TEXT NOT NULL DEFAULT '',
                        url TEXT NOT NULL,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS action_audit (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        action_type TEXT NOT NULL,
                        target_service TEXT NOT NULL,
                        target_id TEXT NOT NULL DEFAULT '',
                        media_key TEXT NOT NULL DEFAULT '',
                        summary TEXT NOT NULL,
                        status TEXT NOT NULL,
                        error_text TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    );
                    """
                )
                conn.commit()
            finally:
                conn.close()

    # -- Settings ---------------------------------------------------------

    def get_settings(self) -> dict[str, Any]:
        base = default_settings()
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT payload_json FROM app_settings WHERE id = 1").fetchone()
                if not row:
                    return base
                saved = json.loads(row["payload_json"])
                return merge_settings(base, saved)
            except Exception:
                return base
            finally:
                conn.close()

    def save_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self.get_settings()
            updated = merge_settings(current, patch)
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO app_settings (id, payload_json, updated_at)
                    VALUES (1, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        payload_json = excluded.payload_json,
                        updated_at = excluded.updated_at
                    """,
                    (json.dumps(updated, ensure_ascii=False), _now_iso()),
                )
                conn.commit()
            finally:
                conn.close()
            return updated

    # -- Media preferences (follow vs collect, muted issues) --------------

    def list_preferences(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM media_preferences").fetchall()
                result: dict[str, dict[str, Any]] = {}
                for r in rows:
                    try:
                        muted = json.loads(r["muted_issue_keys"] or "[]")
                    except Exception:
                        muted = []
                    result[str(r["media_key"])] = {
                        "mediaKey": str(r["media_key"]),
                        "intent": str(r["intent"] or "follow"),
                        "preferredTarget": str(r["preferred_target"] or ""),
                        "notes": str(r["notes"] or ""),
                        "mutedIssueKeys": muted if isinstance(muted, list) else [],
                        "updatedAt": str(r["updated_at"]),
                    }
                return result
            finally:
                conn.close()

    def get_preference(self, media_key: str) -> dict[str, Any]:
        prefs = self.list_preferences()
        return prefs.get(
            media_key,
            {
                "mediaKey": media_key,
                "intent": "follow",
                "preferredTarget": "",
                "notes": "",
                "mutedIssueKeys": [],
                "updatedAt": "",
            },
        )

    def set_preference(
        self,
        media_key: str,
        *,
        intent: str | None = None,
        preferred_target: str | None = None,
        notes: str | None = None,
        mute_issue_key: str | None = None,
        unmute_issue_key: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            current = self.get_preference(media_key)
            next_intent = intent if intent in {"follow", "collect"} else current["intent"]
            next_target = preferred_target if preferred_target is not None else current["preferredTarget"]
            next_notes = notes if notes is not None else current["notes"]
            muted = list(current["mutedIssueKeys"])
            if mute_issue_key and mute_issue_key not in muted:
                muted.append(mute_issue_key)
            if unmute_issue_key and unmute_issue_key in muted:
                muted = [m for m in muted if m != unmute_issue_key]
            now = _now_iso()
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO media_preferences (media_key, intent, preferred_target, notes, muted_issue_keys, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(media_key) DO UPDATE SET
                        intent = excluded.intent,
                        preferred_target = excluded.preferred_target,
                        notes = excluded.notes,
                        muted_issue_keys = excluded.muted_issue_keys,
                        updated_at = excluded.updated_at
                    """,
                    (media_key, next_intent, next_target, next_notes, json.dumps(muted, ensure_ascii=False), now),
                )
                conn.commit()
            finally:
                conn.close()
            return self.get_preference(media_key)

    # -- Decision logs ("why subscribed / why chosen / which rule") -------

    def add_decision_log(
        self,
        *,
        media_key: str,
        title: str,
        decision_type: str,
        outcome: str,
        source_label: str,
        rule_name: str,
        reason_text: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO decision_logs
                    (media_key, title, decision_type, outcome, source_label, rule_name, reason_text, details_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        media_key,
                        title,
                        decision_type,
                        outcome,
                        source_label,
                        rule_name,
                        reason_text,
                        json.dumps(details or {}, ensure_ascii=False),
                        now,
                    ),
                )
                conn.commit()
                row_id = cur.lastrowid
            finally:
                conn.close()
        return {
            "id": row_id,
            "mediaKey": media_key,
            "title": title,
            "decisionType": decision_type,
            "outcome": outcome,
            "sourceLabel": source_label,
            "ruleName": rule_name,
            "reasonText": reason_text,
            "details": details or {},
            "createdAt": now,
        }

    def list_decision_logs(self, *, media_key: str = "", limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(200, int(limit)))
        with self._lock:
            conn = self._connect()
            try:
                if media_key:
                    rows = conn.execute(
                        "SELECT * FROM decision_logs WHERE media_key = ? ORDER BY id DESC LIMIT ?",
                        (media_key, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM decision_logs ORDER BY id DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                items = []
                for r in rows:
                    try:
                        details = json.loads(r["details_json"] or "{}")
                    except Exception:
                        details = {}
                    items.append({
                        "id": int(r["id"]),
                        "mediaKey": str(r["media_key"]),
                        "title": str(r["title"]),
                        "decisionType": str(r["decision_type"]),
                        "outcome": str(r["outcome"]),
                        "sourceLabel": str(r["source_label"]),
                        "ruleName": str(r["rule_name"]),
                        "reasonText": str(r["reason_text"]),
                        "details": details,
                        "createdAt": str(r["created_at"]),
                    })
                return items
            finally:
                conn.close()

    # -- RSS Sources ------------------------------------------------------

    def list_rss_sources(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM rss_sources ORDER BY created_at DESC").fetchall()
                return [
                    {
                        "id": str(r["id"]),
                        "name": str(r["name"]),
                        "siteId": str(r["site_id"]),
                        "url": str(r["url"]),
                        "enabled": bool(r["enabled"]),
                        "createdAt": str(r["created_at"]),
                    }
                    for r in rows
                ]
            finally:
                conn.close()

    def save_rss_source(self, source_id: str, name: str, site_id: str, url: str, enabled: bool = True) -> dict[str, Any]:
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO rss_sources (id, name, site_id, url, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        site_id = excluded.site_id,
                        url = excluded.url,
                        enabled = excluded.enabled,
                        updated_at = excluded.updated_at
                    """,
                    (source_id, name.strip(), site_id.strip(), url.strip(), 1 if enabled else 0, now, now),
                )
                conn.commit()
            finally:
                conn.close()
        return {"id": source_id, "name": name.strip(), "siteId": site_id.strip(), "url": url.strip(), "enabled": enabled}

    def delete_rss_source(self, source_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM rss_sources WHERE id = ?", (source_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # -- Action audit -----------------------------------------------------

    def add_audit(
        self,
        *,
        action_type: str,
        target_service: str,
        target_id: str = "",
        media_key: str = "",
        summary: str,
        status: str,
        error_text: str = "",
    ) -> dict[str, Any]:
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO action_audit
                    (action_type, target_service, target_id, media_key, summary, status, error_text, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (action_type, target_service, target_id, media_key, summary, status, error_text, now),
                )
                conn.commit()
                row_id = cur.lastrowid
            finally:
                conn.close()
        return {
            "id": row_id,
            "actionType": action_type,
            "targetService": target_service,
            "targetId": target_id,
            "mediaKey": media_key,
            "summary": summary,
            "status": status,
            "errorText": error_text,
            "createdAt": now,
        }

    def list_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(200, int(limit)))
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM action_audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
                return [
                    {
                        "id": int(r["id"]),
                        "actionType": str(r["action_type"]),
                        "targetService": str(r["target_service"]),
                        "targetId": str(r["target_id"]),
                        "mediaKey": str(r["media_key"]),
                        "summary": str(r["summary"]),
                        "status": str(r["status"]),
                        "errorText": str(r["error_text"]),
                        "createdAt": str(r["created_at"]),
                    }
                    for r in rows
                ]
            finally:
                conn.close()

    # -- Admin credentials helper -----------------------------------------

    def read_admin(self) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM admin_credentials WHERE id = 1").fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def init_admin(self, username: str, password_hash: str, session_secret: str) -> dict[str, Any] | None:
        now_ms = int(time.time() * 1000)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                if conn.execute("SELECT 1 FROM admin_credentials WHERE id = 1").fetchone():
                    conn.rollback()
                    return None
                conn.execute(
                    """
                    INSERT INTO admin_credentials
                    (id, username, username_normalized, password_hash, session_secret, created_at, updated_at)
                    VALUES (1, ?, ?, ?, ?, ?, ?)
                    """,
                    (username, username.strip().casefold(), password_hash, session_secret, now_ms, now_ms),
                )
                conn.commit()
                return self.read_admin()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
