from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone

from app.resource_identity_runtime import artifact_key
from app.sqlite_runtime import SQLiteRuntime


SENSITIVE_QUERY_PATTERN = re.compile(r"([?&][^=&#\s]+)=([^&#\s]+)", re.I)
CREDENTIAL_ASSIGNMENT_PATTERN = re.compile(
    r"\b(password|passwd|token|api[_-]?key|api[_-]?hash|cookie|secret|authorization|passkey|sign)=([^\s&]+)",
    re.I,
)
BEARER_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+\-/]+=*", re.I)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _text(value, limit=500) -> str:
    value = str(value or "").replace("\r", " ").replace("\n", " ")
    value = SENSITIVE_QUERY_PATTERN.sub(r"\1=***", value)
    value = CREDENTIAL_ASSIGNMENT_PATTERN.sub(r"\1=***", value)
    value = BEARER_PATTERN.sub("Bearer ***", value)
    return value[:limit]


def _safe_code(value, fallback="UNKNOWN") -> str:
    code = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return code[:120] or fallback


def _json(value) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _row(row):
    return dict(row) if row else None


class ResourceIdentityConflict(RuntimeError):
    pass


class ResourceTaskRepository:
    """Local evidence ledger for stable media chains and stage observations."""

    def __init__(self, database_path, clock=None):
        self.runtime = SQLiteRuntime(database_path)
        self.database_path = self.runtime.database_path
        self.clock = clock or _utc_now
        self.runtime.initialize()
        self.initialize()

    def initialize(self):
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS resource_chains ("
                "chain_id TEXT PRIMARY KEY, media_key TEXT NOT NULL, target_key TEXT NOT NULL, "
                "subscription_id TEXT NOT NULL DEFAULT '', media_type TEXT NOT NULL DEFAULT '', "
                "tmdb_id TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL DEFAULT '', "
                "state TEXT NOT NULL DEFAULT 'waiting', health_state TEXT NOT NULL DEFAULT 'evidence_insufficient', "
                "observed_at TEXT NOT NULL, fresh_until TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', "
                "reason_code TEXT NOT NULL DEFAULT '', reason_text TEXT NOT NULL DEFAULT '', "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_chains_target "
                "ON resource_chains(target_key, updated_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_chains_health "
                "ON resource_chains(health_state, updated_at DESC)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS resource_artifacts ("
                "artifact_key TEXT PRIMARY KEY, chain_id TEXT NOT NULL, artifact_type TEXT NOT NULL, "
                "source TEXT NOT NULL DEFAULT '', external_id TEXT NOT NULL DEFAULT '', "
                "first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', "
                "FOREIGN KEY(chain_id) REFERENCES resource_chains(chain_id) ON DELETE CASCADE)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_artifacts_chain "
                "ON resource_artifacts(chain_id, last_seen_at DESC)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS resource_events ("
                "event_id TEXT PRIMARY KEY, chain_id TEXT NOT NULL, artifact_key TEXT NOT NULL DEFAULT '', "
                "stage TEXT NOT NULL, status TEXT NOT NULL, health_state TEXT NOT NULL, evidence TEXT NOT NULL, "
                "observed_at TEXT NOT NULL, fresh_until TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', "
                "reason_code TEXT NOT NULL DEFAULT '', reason_text TEXT NOT NULL DEFAULT '', "
                "idempotency_key TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL DEFAULT '{}', "
                "created_at TEXT NOT NULL, "
                "FOREIGN KEY(chain_id) REFERENCES resource_chains(chain_id) ON DELETE CASCADE)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_events_chain "
                "ON resource_events(chain_id, observed_at DESC, created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_events_health "
                "ON resource_events(health_state, observed_at DESC)"
            )

    def _event_key(self, chain_id, artifact_key_value, stage):
        return hashlib.sha256(
            _json({
                "chainId": chain_id,
                "artifactKey": artifact_key_value,
                "stage": stage.get("stage"),
                "status": stage.get("status"),
                "healthState": stage.get("healthState"),
                "evidence": stage.get("evidence"),
                "source": _text(stage.get("source"), 160),
                "reasonCode": _safe_code(stage.get("reasonCode"), ""),
                "reasonText": _text(stage.get("reasonText")),
            }).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _artifact_rows(item):
        source_ids = item.get("sourceIds") or {}
        rows = []
        for value in source_ids.get("qbHashes") or []:
            external_id = _text(value, 180)
            if external_id:
                rows.append((artifact_key(qb_hash=external_id), "qb_hash", "qBittorrent", external_id))
        for value in source_ids.get("symediaIds") or []:
            external_id = _text(value, 180)
            if external_id:
                rows.append((artifact_key(remote_file_id=external_id), "remote_file", "Symedia", external_id))
        return rows

    def _upsert_chain(self, connection, item, now_text):
        chain_id = _text(item.get("chainId"), 120)
        if not chain_id:
            raise ValueError("资源链缺少 chainId")
        observed_at = _text(item.get("observedAt"), 80) or now_text
        fresh_until = _text(item.get("freshUntil"), 80) or _iso(self.clock() + timedelta(minutes=5))
        values = (
            chain_id,
            _text(item.get("mediaKey"), 180),
            _text(item.get("targetKey"), 180),
            _text(item.get("subscriptionId"), 180),
            _text(item.get("mediaType"), 40),
            _text(item.get("tmdbId"), 80),
            _text(item.get("title"), 240),
            _text(item.get("origin"), 60),
            _text(item.get("state"), 40) or "waiting",
            _safe_code(item.get("healthState"), "evidence_insufficient"),
            observed_at,
            fresh_until,
            _text(item.get("source"), 160),
            _safe_code(item.get("reasonCode"), ""),
            _text(item.get("reasonText")),
            now_text,
            now_text,
        )
        connection.execute(
            "INSERT INTO resource_chains ("
            "chain_id, media_key, target_key, subscription_id, media_type, tmdb_id, title, origin, state, "
            "health_state, observed_at, fresh_until, source, reason_code, reason_text, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(chain_id) DO UPDATE SET media_key=excluded.media_key, target_key=excluded.target_key, "
            "subscription_id=excluded.subscription_id, media_type=excluded.media_type, tmdb_id=excluded.tmdb_id, "
            "title=excluded.title, origin=excluded.origin, state=excluded.state, health_state=excluded.health_state, "
            "observed_at=excluded.observed_at, fresh_until=excluded.fresh_until, source=excluded.source, "
            "reason_code=excluded.reason_code, reason_text=excluded.reason_text, updated_at=excluded.updated_at, "
            "version=resource_chains.version + 1",
            values,
        )
        return chain_id

    def _upsert_artifact(self, connection, chain_id, artifact_row, now_text):
        artifact_key_value, artifact_type, source, external_id = artifact_row
        existing = connection.execute(
            "SELECT chain_id FROM resource_artifacts WHERE artifact_key=?", (artifact_key_value,)
        ).fetchone()
        if existing and existing["chain_id"] != chain_id:
            raise ResourceIdentityConflict(f"artifact {artifact_key_value} 已关联其他资源链")
        connection.execute(
            "INSERT INTO resource_artifacts ("
            "artifact_key, chain_id, artifact_type, source, external_id, first_seen_at, last_seen_at, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, '{}') "
            "ON CONFLICT(artifact_key) DO UPDATE SET last_seen_at=excluded.last_seen_at, "
            "source=excluded.source, external_id=excluded.external_id",
            (artifact_key_value, chain_id, artifact_type, source, external_id, now_text, now_text),
        )

    def _append_event(self, connection, chain_id, artifact_key_value, stage, now_text):
        stage_name = _safe_code(stage.get("stage"), "unknown")
        event_key = self._event_key(chain_id, artifact_key_value, stage)
        observed_at = _text(stage.get("observedAt"), 80) or now_text
        fresh_until = _text(stage.get("freshUntil"), 80) or _iso(self.clock() + timedelta(minutes=5))
        values = (
            str(uuid.uuid4()),
            chain_id,
            artifact_key_value,
            stage_name,
            _safe_code(stage.get("status"), "unknown"),
            _safe_code(stage.get("healthState"), "evidence_insufficient"),
            _safe_code(stage.get("evidence"), "missing"),
            observed_at,
            fresh_until,
            _text(stage.get("source"), 160),
            _safe_code(stage.get("reasonCode"), ""),
            _text(stage.get("reasonText")),
            event_key,
            _json({"evidence": _safe_code(stage.get("evidence"), "missing")}),
            now_text,
        )
        cursor = connection.execute(
            "INSERT OR IGNORE INTO resource_events ("
            "event_id, chain_id, artifact_key, stage, status, health_state, evidence, observed_at, fresh_until, "
            "source, reason_code, reason_text, idempotency_key, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        return int(cursor.rowcount or 0)

    def _record_artifact_conflict(self, connection, chain_id, now_text):
        connection.execute(
            "UPDATE resource_chains SET state='blocked', health_state='action_required', "
            "reason_code='ARTIFACT_CHAIN_CONFLICT', reason_text='产物已关联其他资源链，未自动改绑', "
            "updated_at=?, version=version + 1 WHERE chain_id=?",
            (now_text, chain_id),
        )
        return self._append_event(
            connection,
            chain_id,
            "",
            {
                "stage": "identity",
                "status": "blocked",
                "healthState": "action_required",
                "evidence": "verified",
                "observedAt": now_text,
                "freshUntil": _iso(self.clock() + timedelta(minutes=5)),
                "source": "resource-ledger",
                "reasonCode": "ARTIFACT_CHAIN_CONFLICT",
                "reasonText": "产物已关联其他资源链，未自动改绑",
            },
            now_text,
        )

    def _record_artifacts(self, connection, chain_id, item, now_text):
        artifact_count = event_count = conflict_count = 0
        artifact_keys = []
        for artifact_row in self._artifact_rows(item):
            try:
                self._upsert_artifact(connection, chain_id, artifact_row, now_text)
                artifact_count += 1
                artifact_keys.append(artifact_row[0])
            except ResourceIdentityConflict:
                conflict_count += 1
                event_count += self._record_artifact_conflict(connection, chain_id, now_text)
        return artifact_count, event_count, conflict_count, artifact_keys

    def _record_stages(self, connection, chain_id, stages, artifact_keys, now_text):
        artifact_key_value = artifact_keys[0] if len(artifact_keys) == 1 else ""
        return sum(
            self._append_event(connection, chain_id, artifact_key_value, stage, now_text)
            for stage in stages
            if isinstance(stage, dict)
        )

    def record_snapshot(self, payload):
        now_text = _iso(self.clock())
        chain_count = artifact_count = event_count = conflict_count = 0
        with self.runtime.transaction(immediate=True) as connection:
            for item in payload.get("items") or []:
                if not isinstance(item, dict):
                    continue
                chain_id = self._upsert_chain(connection, item, now_text)
                artifacts, conflicts_events, conflicts, artifact_keys = self._record_artifacts(
                    connection, chain_id, item, now_text
                )
                chain_count += 1
                artifact_count += artifacts
                conflict_count += conflicts
                event_count += conflicts_events
                event_count += self._record_stages(
                    connection, chain_id, item.get("stages") or [], artifact_keys, now_text
                )
        return {
            "persisted": True,
            "chains": chain_count,
            "artifacts": artifact_count,
            "events": event_count,
            "artifactConflicts": conflict_count,
            "observedAt": now_text,
        }

    def get_chain(self, chain_id):
        with closing(self.runtime.connect()) as connection:
            row = connection.execute("SELECT * FROM resource_chains WHERE chain_id=?", (str(chain_id),)).fetchone()
        return _row(row)

    def list_events(self, chain_id, limit=100):
        try:
            limit = max(1, min(int(limit or 100), 1000))
        except (TypeError, ValueError):
            limit = 100
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM resource_events WHERE chain_id=? ORDER BY observed_at DESC, created_at DESC LIMIT ?",
                (str(chain_id), limit),
            ).fetchall()
        return [_row(row) for row in rows]

    def record_identity_alias(
        self,
        chain_id,
        previous_artifact_key,
        current_artifact_key,
        *,
        artifact=None,
    ):
        artifact = artifact or {}
        artifact_type = artifact.get("type") or "identity_alias"
        source = artifact.get("source") or "task-chain"
        external_id = artifact.get("externalId") or ""
        chain_id = _text(chain_id, 120)
        previous_key = _text(previous_artifact_key, 220)
        current_key = _text(current_artifact_key, 220)
        if not chain_id or not current_key or previous_key == current_key:
            raise ValueError("身份升级需要资源链和两个不同的产物身份")
        now_text = _iso(self.clock())
        payload = {
            "previousArtifactKey": previous_key,
            "currentArtifactKey": current_key,
        }
        idempotency_key = hashlib.sha256(
            _json({"chainId": chain_id, **payload}).encode("utf-8")
        ).hexdigest()
        with self.runtime.transaction(immediate=True) as connection:
            if not connection.execute(
                "SELECT 1 FROM resource_chains WHERE chain_id=?", (chain_id,)
            ).fetchone():
                raise ValueError("资源链不存在")
            self._upsert_artifact(
                connection,
                chain_id,
                (
                    current_key,
                    _safe_code(artifact_type, "identity_alias"),
                    _text(source, 160),
                    _text(external_id, 180),
                ),
                now_text,
            )
            cursor = connection.execute(
                "INSERT OR IGNORE INTO resource_events ("
                "event_id, chain_id, artifact_key, stage, status, health_state, evidence, observed_at, fresh_until, "
                "source, reason_code, reason_text, idempotency_key, payload_json, created_at) "
                "VALUES (?, ?, ?, 'identity', 'done', 'normal', 'verified', ?, ?, ?, "
                "'ARTIFACT_IDENTITY_UPGRADED', '产物身份已升级并保留原身份关联', ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    chain_id,
                    current_key,
                    now_text,
                    _iso(self.clock() + timedelta(minutes=5)),
                    _text(source, 160),
                    idempotency_key,
                    _json(payload),
                    now_text,
                ),
            )
        return {"created": bool(cursor.rowcount), "chainId": chain_id, **payload}
