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

                    CREATE TABLE IF NOT EXISTS sa_identify_mappings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        arc_key TEXT NOT NULL,
                        arc_title TEXT NOT NULL,
                        source_season INTEGER NOT NULL DEFAULT 1,
                        media_type TEXT NOT NULL DEFAULT 'tv',
                        tmdb_id TEXT NOT NULL,
                        canonical_title TEXT NOT NULL,
                        target_season INTEGER NOT NULL,
                        season_name TEXT NOT NULL DEFAULT '',
                        episode_offset INTEGER NOT NULL DEFAULT 0,
                        transfer_task_id TEXT NOT NULL DEFAULT '',
                        auto_apply INTEGER NOT NULL DEFAULT 1,
                        confirmed_by_sample TEXT NOT NULL DEFAULT '',
                        evidence_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(arc_key, source_season)
                    );

                    CREATE TABLE IF NOT EXISTS sa_recovery_jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        sa_record_id TEXT NOT NULL,
                        file_name TEXT NOT NULL,
                        src_path TEXT NOT NULL DEFAULT '',
                        failed_date TEXT NOT NULL DEFAULT '',
                        failure_reason TEXT NOT NULL DEFAULT '',
                        arc_key TEXT NOT NULL DEFAULT '',
                        mapping_id INTEGER,
                        media_type TEXT NOT NULL DEFAULT 'tv',
                        tmdb_id TEXT NOT NULL DEFAULT '',
                        canonical_title TEXT NOT NULL DEFAULT '',
                        source_season INTEGER,
                        source_episode INTEGER,
                        target_season INTEGER,
                        target_episode INTEGER,
                        strategy TEXT NOT NULL DEFAULT '',
                        transfer_task_id TEXT NOT NULL DEFAULT '',
                        renamed_from TEXT NOT NULL DEFAULT '',
                        renamed_to TEXT NOT NULL DEFAULT '',
                        rollback_json TEXT NOT NULL DEFAULT '{}',
                        status TEXT NOT NULL DEFAULT 'draft',
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        max_attempts INTEGER NOT NULL DEFAULT 2,
                        last_error TEXT NOT NULL DEFAULT '',
                        verification_json TEXT NOT NULL DEFAULT '{}',
                        submitted_at TEXT NOT NULL DEFAULT '',
                        resolved_at TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_sa_recovery_record
                        ON sa_recovery_jobs(sa_record_id, id DESC);
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

    # -- SA Identify Mappings & Recovery Jobs -----------------------------

    def list_sa_mappings(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM sa_identify_mappings ORDER BY updated_at DESC, id DESC").fetchall()
                return [self._row_to_mapping(r) for r in rows]
            finally:
                conn.close()

    def find_sa_mapping(self, arc_key: str, source_season: int) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM sa_identify_mappings WHERE arc_key = ? AND source_season = ?",
                    (arc_key, int(source_season)),
                ).fetchone()
                return self._row_to_mapping(row) if row else None
            finally:
                conn.close()

    def save_sa_mapping(
        self,
        *,
        arc_key: str,
        arc_title: str,
        source_season: int,
        media_type: str,
        tmdb_id: str,
        canonical_title: str,
        target_season: int,
        season_name: str = "",
        episode_offset: int = 0,
        transfer_task_id: str = "",
        auto_apply: bool = True,
        confirmed_by_sample: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO sa_identify_mappings (
                        arc_key, arc_title, source_season, media_type, tmdb_id,
                        canonical_title, target_season, season_name, episode_offset,
                        transfer_task_id, auto_apply, confirmed_by_sample, evidence_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(arc_key, source_season) DO UPDATE SET
                        arc_title = excluded.arc_title,
                        media_type = excluded.media_type,
                        tmdb_id = excluded.tmdb_id,
                        canonical_title = excluded.canonical_title,
                        target_season = excluded.target_season,
                        season_name = excluded.season_name,
                        episode_offset = excluded.episode_offset,
                        transfer_task_id = excluded.transfer_task_id,
                        auto_apply = excluded.auto_apply,
                        confirmed_by_sample = excluded.confirmed_by_sample,
                        evidence_json = excluded.evidence_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        arc_key,
                        arc_title,
                        int(source_season),
                        media_type,
                        str(tmdb_id),
                        canonical_title,
                        int(target_season),
                        season_name,
                        int(episode_offset),
                        transfer_task_id,
                        1 if auto_apply else 0,
                        confirmed_by_sample,
                        json.dumps(evidence or {}, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return self.find_sa_mapping(arc_key, source_season) or {}

    def delete_sa_mapping(self, mapping_id: int) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM sa_identify_mappings WHERE id = ?", (int(mapping_id),))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    @staticmethod
    def _row_to_mapping(row: sqlite3.Row) -> dict[str, Any]:
        try:
            evidence = json.loads(row["evidence_json"] or "{}")
        except Exception:
            evidence = {}
        return {
            "id": int(row["id"]),
            "arcKey": str(row["arc_key"]),
            "arcTitle": str(row["arc_title"]),
            "sourceSeason": int(row["source_season"]),
            "mediaType": str(row["media_type"]),
            "tmdbId": str(row["tmdb_id"]),
            "canonicalTitle": str(row["canonical_title"]),
            "targetSeason": int(row["target_season"]),
            "seasonName": str(row["season_name"]),
            "episodeOffset": int(row["episode_offset"]),
            "transferTaskId": str(row["transfer_task_id"]),
            "autoApply": bool(row["auto_apply"]),
            "confirmedBySample": str(row["confirmed_by_sample"]),
            "evidence": evidence,
            "createdAt": str(row["created_at"]),
            "updatedAt": str(row["updated_at"]),
        }

    def create_or_update_sa_recovery_job(self, data: dict[str, Any]) -> dict[str, Any]:
        now = _now_iso()
        job_id = data.get("id")
        with self._lock:
            conn = self._connect()
            try:
                if job_id:
                    conn.execute(
                        """
                        UPDATE sa_recovery_jobs SET
                            mapping_id = ?, media_type = ?, tmdb_id = ?, canonical_title = ?,
                            target_season = ?, target_episode = ?, strategy = ?, transfer_task_id = ?,
                            renamed_from = ?, renamed_to = ?, rollback_json = ?,
                            status = ?, attempt_count = ?, max_attempts = ?, last_error = ?,
                            verification_json = ?, submitted_at = ?, resolved_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            data.get("mappingId"),
                            data.get("mediaType", "tv"),
                            str(data.get("tmdbId") or ""),
                            str(data.get("canonicalTitle") or ""),
                            data.get("targetSeason"),
                            data.get("targetEpisode"),
                            str(data.get("strategy") or ""),
                            str(data.get("transferTaskId") or ""),
                            str(data.get("renamedFrom") or ""),
                            str(data.get("renamedTo") or ""),
                            json.dumps(data.get("rollback") or {}, ensure_ascii=False),
                            str(data.get("status") or "pending"),
                            int(data.get("attemptCount") or 0),
                            int(data.get("maxAttempts") or 2),
                            str(data.get("lastError") or ""),
                            json.dumps(data.get("verification") or {}, ensure_ascii=False),
                            str(data.get("submittedAt") or ""),
                            str(data.get("resolvedAt") or ""),
                            now,
                            int(job_id),
                        ),
                    )
                    conn.commit()
                    resolved_id = int(job_id)
                else:
                    cur = conn.execute(
                        """
                        INSERT INTO sa_recovery_jobs (
                            sa_record_id, file_name, src_path, failed_date, failure_reason, arc_key,
                            mapping_id, media_type, tmdb_id, canonical_title,
                            source_season, source_episode, target_season, target_episode,
                            strategy, transfer_task_id, renamed_from, renamed_to, rollback_json,
                            status, attempt_count, max_attempts, last_error, verification_json,
                            submitted_at, resolved_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(data.get("saRecordId") or ""),
                            str(data.get("fileName") or ""),
                            str(data.get("srcPath") or ""),
                            str(data.get("failedDate") or ""),
                            str(data.get("failureReason") or ""),
                            str(data.get("arcKey") or ""),
                            data.get("mappingId"),
                            data.get("mediaType", "tv"),
                            str(data.get("tmdbId") or ""),
                            str(data.get("canonicalTitle") or ""),
                            data.get("sourceSeason"),
                            data.get("sourceEpisode"),
                            data.get("targetSeason"),
                            data.get("targetEpisode"),
                            str(data.get("strategy") or ""),
                            str(data.get("transferTaskId") or ""),
                            str(data.get("renamedFrom") or ""),
                            str(data.get("renamedTo") or ""),
                            json.dumps(data.get("rollback") or {}, ensure_ascii=False),
                            str(data.get("status") or "pending"),
                            int(data.get("attemptCount") or 0),
                            int(data.get("maxAttempts") or 2),
                            str(data.get("lastError") or ""),
                            json.dumps(data.get("verification") or {}, ensure_ascii=False),
                            str(data.get("submittedAt") or ""),
                            str(data.get("resolvedAt") or ""),
                            now,
                            now,
                        ),
                    )
                    conn.commit()
                    resolved_id = int(cur.lastrowid)
                row = conn.execute("SELECT * FROM sa_recovery_jobs WHERE id = ?", (resolved_id,)).fetchone()
                return self._row_to_recovery_job(row)
            finally:
                conn.close()

    def get_sa_recovery_job(self, job_id: int) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM sa_recovery_jobs WHERE id = ?", (int(job_id),)).fetchone()
                return self._row_to_recovery_job(row) if row else None
            finally:
                conn.close()

    def get_latest_sa_recovery_for_record(self, sa_record_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM sa_recovery_jobs WHERE sa_record_id = ? ORDER BY id DESC LIMIT 1",
                    (str(sa_record_id),),
                ).fetchone()
                return self._row_to_recovery_job(row) if row else None
            finally:
                conn.close()

    def list_sa_recovery_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(200, int(limit)))
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM sa_recovery_jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
                return [self._row_to_recovery_job(r) for r in rows]
            finally:
                conn.close()

    @staticmethod
    def _row_to_recovery_job(row: sqlite3.Row) -> dict[str, Any]:
        try:
            rollback = json.loads(row["rollback_json"] or "{}")
        except Exception:
            rollback = {}
        try:
            verification = json.loads(row["verification_json"] or "{}")
        except Exception:
            verification = {}
        return {
            "id": int(row["id"]),
            "saRecordId": str(row["sa_record_id"]),
            "fileName": str(row["file_name"]),
            "srcPath": str(row["src_path"]),
            "failedDate": str(row["failed_date"]),
            "failureReason": str(row["failure_reason"]),
            "arcKey": str(row["arc_key"]),
            "mappingId": int(row["mapping_id"]) if row["mapping_id"] is not None else None,
            "mediaType": str(row["media_type"]),
            "tmdbId": str(row["tmdb_id"]),
            "canonicalTitle": str(row["canonical_title"]),
            "sourceSeason": int(row["source_season"]) if row["source_season"] is not None else None,
            "sourceEpisode": int(row["source_episode"]) if row["source_episode"] is not None else None,
            "targetSeason": int(row["target_season"]) if row["target_season"] is not None else None,
            "targetEpisode": int(row["target_episode"]) if row["target_episode"] is not None else None,
            "strategy": str(row["strategy"]),
            "transferTaskId": str(row["transfer_task_id"]),
            "renamedFrom": str(row["renamed_from"]),
            "renamedTo": str(row["renamed_to"]),
            "rollback": rollback,
            "status": str(row["status"]),
            "attemptCount": int(row["attempt_count"]),
            "maxAttempts": int(row["max_attempts"]),
            "lastError": str(row["last_error"]),
            "verification": verification,
            "submittedAt": str(row["submitted_at"]),
            "resolvedAt": str(row["resolved_at"]),
            "createdAt": str(row["created_at"]),
            "updatedAt": str(row["updated_at"]),
        }

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
