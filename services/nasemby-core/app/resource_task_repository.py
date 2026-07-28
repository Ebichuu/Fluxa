from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone

from app.pipeline_fact_runtime import PIPELINE_STAGES, normalize_pipeline_fact
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


def _opaque_ref(namespace, value) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    digest = hashlib.sha256(f"{namespace}\0{value}".encode("utf-8")).hexdigest()[:24]
    return f"{namespace}:{digest}"


def _pipeline_fact_health(fact) -> str:
    state = str(fact.get("state") or "unknown")
    if str(fact.get("evidence") or "missing") != "verified":
        return "evidence_insufficient"
    if state == "failed":
        return "waiting" if fact.get("plannedRetryAt") else "action_required"
    return {
        "unknown": "evidence_insufficient",
        "waiting": "waiting",
        "active": "waiting",
        "succeeded": "normal",
        "protected": "protected",
        "not_applicable": "normal",
    }.get(state, "evidence_insufficient")


def _pipeline_fact_payload(fact) -> dict:
    stage = str(fact.get("stage") or "")

    def unit_payload(unit):
        return {
            "unitKey": _opaque_ref("unit", unit.get("unitKey")),
            "state": str(unit.get("state") or "unknown"),
            "scope": str(unit.get("scope") or "system-category"),
            "evidence": str(unit.get("evidence") or "missing"),
            "eventAt": _text(unit.get("eventAt"), 80),
            "sourceRef": _opaque_ref(f"fact-{stage}", unit.get("sourceRef")),
            "reasonCode": _safe_code(unit.get("reasonCode"), ""),
            "plannedRetryAt": _text(unit.get("plannedRetryAt"), 80),
            "retryEligible": bool(unit.get("retryEligible")),
        }

    return {
        "kind": "pipeline_fact",
        "scope": str(fact.get("scope") or "system-category"),
        "eventAt": _text(fact.get("eventAt"), 80),
        "firstConfirmedPlayableAt": _text(fact.get("firstConfirmedPlayableAt"), 80),
        "unitKey": _opaque_ref("unit", fact.get("unitKey")),
        "sourceRef": _opaque_ref(f"fact-{stage}", fact.get("sourceRef")),
        "plannedRetryAt": _text(fact.get("plannedRetryAt"), 80),
        "retryEligible": bool(fact.get("retryEligible")),
        "units": [unit_payload(unit) for unit in fact.get("units") or [] if isinstance(unit, dict)],
    }


class ResourceIdentityConflict(RuntimeError):
    pass


class ResourceMigrationConflict(RuntimeError):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


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
                "event_at TEXT NOT NULL DEFAULT '', observed_at TEXT NOT NULL, fresh_until TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', "
                "reason_code TEXT NOT NULL DEFAULT '', reason_text TEXT NOT NULL DEFAULT '', "
                "idempotency_key TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL DEFAULT '{}', "
                "created_at TEXT NOT NULL, "
                "FOREIGN KEY(chain_id) REFERENCES resource_chains(chain_id) ON DELETE CASCADE)"
            )
            event_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(resource_events)").fetchall()
            }
            if "event_at" not in event_columns:
                connection.execute(
                    "ALTER TABLE resource_events ADD COLUMN event_at TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_events_chain "
                "ON resource_events(chain_id, observed_at DESC, created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_events_health "
                "ON resource_events(health_state, observed_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_events_history "
                "ON resource_events(stage, event_at DESC, observed_at DESC)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS resource_chain_aliases ("
                "alias_chain_id TEXT PRIMARY KEY, canonical_chain_id TEXT NOT NULL, "
                "reason_code TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
                "payload_json TEXT NOT NULL DEFAULT '{}', "
                "FOREIGN KEY(canonical_chain_id) REFERENCES resource_chains(chain_id))"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_chain_aliases_canonical "
                "ON resource_chain_aliases(canonical_chain_id)"
            )

    def _event_key(self, chain_id, artifact_key_value, stage):
        return hashlib.sha256(
            _json({
                "chainId": chain_id,
                "artifactKey": artifact_key_value,
                "stage": stage.get("stage"),
                "status": stage.get("status") or stage.get("state"),
                "healthState": stage.get("healthState"),
                "evidence": stage.get("evidence"),
                "eventAt": _text(stage.get("eventAt"), 80),
                "source": _text(stage.get("source"), 160),
                "reasonCode": _safe_code(stage.get("reasonCode"), ""),
                "reasonText": _text(stage.get("reasonText")),
                "payload": stage.get("_eventPayload") or {},
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
        event_at = _text(stage.get("eventAt"), 80)
        fresh_until = _text(stage.get("freshUntil"), 80) or _iso(self.clock() + timedelta(minutes=5))
        values = (
            str(uuid.uuid4()),
            chain_id,
            artifact_key_value,
            stage_name,
            _safe_code(stage.get("status") or stage.get("state"), "unknown"),
            _safe_code(stage.get("healthState"), "evidence_insufficient"),
            _safe_code(stage.get("evidence"), "missing"),
            event_at,
            observed_at,
            fresh_until,
            _text(stage.get("source"), 160),
            _safe_code(stage.get("reasonCode"), ""),
            _text(stage.get("reasonText")),
            event_key,
            _json(stage.get("_eventPayload") or {"evidence": _safe_code(stage.get("evidence"), "missing")}),
            now_text,
        )
        cursor = connection.execute(
            "INSERT OR IGNORE INTO resource_events ("
            "event_id, chain_id, artifact_key, stage, status, health_state, evidence, event_at, observed_at, fresh_until, "
            "source, reason_code, reason_text, idempotency_key, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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

    @staticmethod
    def _pipeline_fact_artifact_key(item, fact):
        source_ref = str(fact.get("sourceRef") or "").strip()
        source_ids = item.get("sourceIds") or {}
        if fact.get("stage") == "qb" and source_ref in (source_ids.get("qbHashes") or []):
            return artifact_key(qb_hash=source_ref)
        if fact.get("stage") == "symedia" and source_ref in (source_ids.get("symediaIds") or []):
            return artifact_key(remote_file_id=source_ref)
        return ""

    def _record_pipeline_facts(self, connection, chain_id, item, now_text):
        event_count = 0
        for fact in item.get("pipelineFacts") or []:
            if not isinstance(fact, dict):
                continue
            candidate = dict(fact)
            candidate.pop("isStale", None)
            fact = normalize_pipeline_fact(candidate)
            stage = fact["stage"]
            if stage not in PIPELINE_STAGES:
                raise ValueError(f"pipeline fact stage 值无效: {stage}")
            artifact_key_value = self._pipeline_fact_artifact_key(item, fact)
            event_count += self._append_event(
                connection,
                chain_id,
                artifact_key_value,
                {
                    "stage": stage,
                    "status": str(fact.get("state") or "unknown"),
                    "healthState": _pipeline_fact_health(fact),
                    "evidence": str(fact.get("evidence") or "missing"),
                    "eventAt": fact.get("eventAt"),
                    "observedAt": fact.get("observedAt"),
                    "freshUntil": fact.get("freshUntil"),
                    "source": fact.get("source"),
                    "reasonCode": fact.get("reasonCode"),
                    "reasonText": fact.get("reasonText"),
                    "_eventPayload": _pipeline_fact_payload(fact),
                },
                now_text,
            )
            if stage == "emby":
                for unit in fact.get("units") or []:
                    if unit.get("state") != "succeeded" or unit.get("evidence") != "verified":
                        continue
                    if not unit.get("eventAt"):
                        continue
                    unit_key_value = str(unit.get("unitKey") or "")
                    episode_match = re.fullmatch(r"tv:[^:]+:s(\d+):e(\d+)", unit_key_value)
                    episode_payload = {
                        "seasonNumber": int(episode_match.group(1)),
                        "episodeStart": int(episode_match.group(2)),
                        "episodeEnd": int(episode_match.group(2)),
                    } if episode_match else {}
                    event_count += self._append_event(
                        connection,
                        chain_id,
                        "",
                        {
                            "stage": "emby",
                            "status": "succeeded",
                            "healthState": "normal",
                            "evidence": "verified",
                            "eventAt": unit.get("eventAt"),
                            "observedAt": unit.get("observedAt"),
                            "freshUntil": unit.get("freshUntil"),
                            "source": fact.get("source") or "Emby",
                            "reasonCode": unit.get("reasonCode") or "EMBY_EPISODE_INDEXED",
                            "reasonText": unit.get("reasonText") or "Emby 已收录目标集",
                            "_eventPayload": {
                                "kind": "pipeline_fact_unit",
                                "unitKey": _opaque_ref("unit", unit_key_value),
                                **episode_payload,
                            },
                        },
                        now_text,
                    )
        return event_count

    def _record_episode_evidence(self, connection, chain_id, item, now_text):
        event_count = 0
        stage_names = {"download": "qb", "library": "symedia", "strm": "strm", "emby": "emby"}
        statuses = {"done": "succeeded", "blocked": "failed", "active": "active", "waiting": "waiting"}
        for row in item.get("episodeEvidence") or []:
            if not isinstance(row, dict):
                continue
            owner_target = _text(row.get("ownerTargetKey"), 240)
            artifact = _text(row.get("artifactKey"), 240)
            event_at = _text(row.get("eventAt"), 80)
            stage = stage_names.get(str(row.get("stage") or ""))
            status = statuses.get(str(row.get("status") or ""))
            if not owner_target or not artifact or not event_at or not stage or not status:
                continue
            reason_code = _safe_code(row.get("reasonCode"), "")
            protected = status == "failed" and reason_code.startswith("QUALITY_")
            event_status = "protected" if protected else status
            event_count += self._append_event(
                connection,
                chain_id,
                artifact,
                {
                    "stage": stage,
                    "status": event_status,
                    "healthState": "protected" if protected else "action_required" if status == "failed" else "normal" if status == "succeeded" else "waiting",
                    "evidence": "verified",
                    "eventAt": event_at,
                    "observedAt": row.get("observedAt") or now_text,
                    "freshUntil": item.get("freshUntil") or _iso(self.clock() + timedelta(minutes=5)),
                    "source": row.get("source"),
                    "reasonCode": reason_code or f"{stage.upper()}_{status.upper()}",
                    "reasonText": row.get("reasonText"),
                    "_eventPayload": {
                        "kind": "episode_evidence",
                        "ownerScope": _text(row.get("ownerScope"), 40),
                        "ownerTargetKey": owner_target,
                        "parentTargetKey": _text(row.get("parentTargetKey"), 240),
                        "seasonNumber": int(row.get("seasonNumber") or 0),
                        "episodeStart": int(row.get("episodeStart") or 0),
                        "episodeEnd": int(row.get("episodeEnd") or 0),
                    },
                },
                now_text,
            )
        return event_count

    @staticmethod
    def _migration_result(**updates):
        result = {
            "artifactMigrations": 0,
            "chainAliases": 0,
            "artifactConflicts": 0,
            "migrationSkipped": 0,
            "migrationSkipReasons": {},
            "deletedEmptyChains": 0,
            "migrationBackupCreated": False,
            "migrationPlans": [],
            "migrationSkips": [],
        }
        result.update(updates)
        return result

    @staticmethod
    def _skip_migration(skips, artifact_key_value, old_chain_id, new_chain_id, reason_code):
        skips.append({
            "artifactKey": artifact_key_value,
            "expectedOldChainId": old_chain_id,
            "newChainId": new_chain_id,
            "reasonCode": reason_code,
        })

    @staticmethod
    def _is_tmdb_target(item):
        tmdb_id = _text(item.get("tmdbId"), 80)
        target_value = _text(item.get("targetKey"), 180)
        media_type = _text(item.get("mediaType"), 40).lower()
        return bool(
            item.get("identityState") == "linked"
            and tmdb_id
            and media_type in {"tv", "movie"}
            and target_value.startswith(f"{media_type}:tmdb:{tmdb_id}:season:")
        )

    @staticmethod
    def _is_legacy_chain(row):
        return bool(
            row
            and not _text(row.get("tmdb_id"), 80)
            and ":title:" in _text(row.get("target_key"), 180)
        )

    @staticmethod
    def _anchor_matches(anchor, item, target_value):
        return bool(
            anchor.get("ownerTargetKey") == target_value
            and anchor.get("matchMethod") == "symedia_tmdb_anchor"
            and anchor.get("confidence") == "strong"
            and anchor.get("source") == "Symedia"
            and anchor.get("mediaType") == item.get("mediaType") == "tv"
            and int(anchor.get("seasonNumber") or 0) == int(item.get("seasonNumber") or 0)
        )

    def _snapshot_anchors(self, items):
        result = {}
        for item in items:
            target_value = _text(item.get("targetKey"), 180)
            result.setdefault(target_value, []).extend(
                record
                for record in item.get("evidenceOwnership") or []
                if isinstance(record, dict)
                and self._anchor_matches(record, item, target_value)
            )
        return result

    @staticmethod
    def _ownership_by_artifact(item):
        result = {}
        for record in item.get("evidenceOwnership") or []:
            if isinstance(record, dict):
                result.setdefault(_text(record.get("artifactKey"), 220), []).append(record)
        return result

    def _old_chain_rejection(self, connection, old_chain_id, new_chain_id):
        old_chain = connection.execute(
            "SELECT * FROM resource_chains WHERE chain_id=?",
            (old_chain_id,),
        ).fetchone()
        if not self._is_legacy_chain(_row(old_chain)):
            return "OLD_CHAIN_NOT_LEGACY"
        alias = connection.execute(
            "SELECT canonical_chain_id FROM resource_chain_aliases WHERE alias_chain_id=?",
            (old_chain_id,),
        ).fetchone()
        if alias and alias["canonical_chain_id"] != new_chain_id:
            return "OLD_CHAIN_NOT_LEGACY"
        return ""

    @staticmethod
    def _match_rejection(item, record, anchors):
        if record is None or record.get("conflictCandidates"):
            return "OWNERSHIP_RECORD_MISSING"
        method = record.get("matchMethod")
        if method == "symedia_tmdb_anchor" and not ResourceTaskRepository._anchor_matches(
            record,
            item,
            _text(item.get("targetKey"), 180),
        ):
            return "TARGET_SCOPE_MISMATCH"
        if method in {"artifact_exact", "tmdb_exact", "symedia_tmdb_anchor"}:
            return "" if record.get("confidence") == "strong" else "MATCH_METHOD_NOT_ALLOWED"
        if method != "symedia_title_season_unique":
            return "MATCH_METHOD_NOT_ALLOWED"
        same_scope = bool(
            record.get("mediaType") == item.get("mediaType") == "tv"
            and int(record.get("seasonNumber") or 0) == int(item.get("seasonNumber") or 0)
        )
        if not same_scope:
            return "TARGET_SCOPE_MISMATCH"
        return "" if anchors else "SYMEDIA_ANCHOR_MISSING"

    def _migration_rejection(self, connection, item, candidate, anchors):
        if not self._is_tmdb_target(item):
            return (
                "NEW_CHAIN_NOT_LINKED"
                if item.get("identityState") != "linked"
                else "NEW_TARGET_WITHOUT_TMDB"
            )
        match_rejection = self._match_rejection(item, candidate.get("record"), anchors)
        if match_rejection:
            return match_rejection
        return self._old_chain_rejection(
            connection,
            candidate["expectedOldChainId"],
            candidate["newChainId"],
        )

    @staticmethod
    def _migration_plan(candidate):
        record = candidate["record"]
        return {
            key: candidate[key]
            for key in ("artifactKey", "expectedOldChainId", "newChainId", "targetKey")
        } | {
            "matchMethod": record.get("matchMethod"),
            "confidence": record.get("confidence"),
            "migrationMode": "single_artifact",
        }

    @staticmethod
    def _migration_reason_counts(skips):
        counts = {}
        for skip in skips:
            reason = skip["reasonCode"]
            counts[reason] = counts.get(reason, 0) + 1
        return counts

    def _mark_whole_chain_plans(self, connection, plans):
        plans_by_old_chain = {}
        for plan in plans:
            plans_by_old_chain.setdefault(plan["expectedOldChainId"], []).append(plan)
        whole_chain_aliases = 0
        for old_chain_id, chain_plans in plans_by_old_chain.items():
            existing_artifacts = {
                row["artifact_key"]
                for row in connection.execute(
                    "SELECT artifact_key FROM resource_artifacts WHERE chain_id=?",
                    (old_chain_id,),
                ).fetchall()
            }
            planned_artifacts = {plan["artifactKey"] for plan in chain_plans}
            new_owners = {plan["newChainId"] for plan in chain_plans}
            if existing_artifacts and existing_artifacts == planned_artifacts and len(new_owners) == 1:
                whole_chain_aliases += 1
                for plan in chain_plans:
                    plan["migrationMode"] = "whole_chain"
        return whole_chain_aliases

    @staticmethod
    def _migration_candidate(connection, context, artifact_key_value):
        existing = connection.execute(
            "SELECT chain_id FROM resource_artifacts WHERE artifact_key=?",
            (artifact_key_value,),
        ).fetchone()
        if not existing or existing["chain_id"] == context["newChainId"]:
            return None
        records = [
            record
            for record in context["ownershipByArtifact"].get(artifact_key_value, [])
            if record.get("ownerTargetKey") == context["targetKey"]
        ]
        return {
            "artifactKey": artifact_key_value,
            "expectedOldChainId": existing["chain_id"],
            "newChainId": context["newChainId"],
            "targetKey": context["targetKey"],
            "record": records[0] if len(records) == 1 else None,
        }

    def _plan_snapshot_migrations(self, connection, payload):
        items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
        anchors = self._snapshot_anchors(items)
        plans = []
        skips = []
        for item in items:
            target_value = _text(item.get("targetKey"), 180)
            context = {
                "newChainId": _text(item.get("chainId"), 120),
                "targetKey": target_value,
                "ownershipByArtifact": self._ownership_by_artifact(item),
            }
            for artifact_key_value in sorted(row[0] for row in self._artifact_rows(item)):
                candidate = self._migration_candidate(connection, context, artifact_key_value)
                if candidate is None:
                    continue
                reason_code = self._migration_rejection(
                    connection,
                    item,
                    candidate,
                    anchors.get(target_value),
                )
                if reason_code:
                    self._skip_migration(
                        skips,
                        artifact_key_value,
                        candidate["expectedOldChainId"],
                        candidate["newChainId"],
                        reason_code,
                    )
                    continue
                plans.append(self._migration_plan(candidate))

        whole_chain_aliases = self._mark_whole_chain_plans(connection, plans)
        return self._migration_result(
            artifactMigrations=len(plans),
            chainAliases=whole_chain_aliases,
            artifactConflicts=len(skips),
            migrationSkipped=len(skips),
            migrationSkipReasons=self._migration_reason_counts(skips),
            migrationPlans=plans,
            migrationSkips=skips,
        )

    def preview_snapshot_migrations(self, payload):
        with closing(self.runtime.connect()) as connection:
            return self._plan_snapshot_migrations(connection, payload)

    def _ensure_migration_backup(self):
        backup_path = self.database_path.with_name(
            f"{self.database_path.stem}.resource-chain-migration-v1.sqlite3"
        )
        if backup_path.exists():
            return False
        temporary_path = backup_path.with_suffix(f"{backup_path.suffix}.{uuid.uuid4().hex}.tmp")
        try:
            with closing(self.runtime.connect()) as source, closing(sqlite3.connect(temporary_path)) as target:
                source.backup(target)
            temporary_path.replace(backup_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        return True

    def _conditional_reassign_artifact(self, connection, plan, now_text):
        cursor = connection.execute(
            "UPDATE resource_artifacts SET chain_id=?, last_seen_at=? "
            "WHERE artifact_key=? AND chain_id=?",
            (
                plan["newChainId"],
                now_text,
                plan["artifactKey"],
                plan["expectedOldChainId"],
            ),
        )
        if int(cursor.rowcount or 0) != 1:
            raise ResourceMigrationConflict("OWNER_CHANGED_CONCURRENTLY")

    def _migrate_events(self, connection, old_chain_id, new_chain_id, now_text, artifact_key_value=None):
        if artifact_key_value is None:
            events = connection.execute(
                "SELECT * FROM resource_events WHERE chain_id=?",
                (old_chain_id,),
            ).fetchall()
        else:
            events = connection.execute(
                "SELECT * FROM resource_events WHERE chain_id=? AND artifact_key=?",
                (old_chain_id, artifact_key_value),
            ).fetchall()
        for event in events:
            try:
                event_payload = json.loads(event["payload_json"] or "{}")
            except (TypeError, ValueError):
                event_payload = {}
            stage = {
                "stage": event["stage"],
                "status": event["status"],
                "healthState": event["health_state"],
                "evidence": event["evidence"],
                "eventAt": event["event_at"],
                "source": event["source"],
                "reasonCode": event["reason_code"],
                "reasonText": event["reason_text"],
                "_eventPayload": event_payload if isinstance(event_payload, dict) else {},
            }
            canonical_key = self._event_key(new_chain_id, event["artifact_key"], stage)
            duplicate = connection.execute(
                "SELECT event_id FROM resource_events WHERE idempotency_key=? AND event_id<>?",
                (canonical_key, event["event_id"]),
            ).fetchone()
            if duplicate:
                connection.execute("DELETE FROM resource_events WHERE event_id=?", (event["event_id"],))
            else:
                connection.execute(
                    "UPDATE resource_events SET chain_id=?, idempotency_key=? WHERE event_id=?",
                    (new_chain_id, canonical_key, event["event_id"]),
                )

    def _record_migration_event(self, connection, new_chain_id, plan, now_text):
        return self._append_event(
            connection,
            new_chain_id,
            plan["artifactKey"],
            {
                "stage": "identity",
                "status": "done",
                "healthState": "normal",
                "evidence": "verified",
                "observedAt": now_text,
                "freshUntil": _iso(self.clock() + timedelta(minutes=5)),
                "source": "resource-ledger",
                "reasonCode": "ARTIFACT_CHAIN_MIGRATED",
                "reasonText": "历史产物身份已迁移到标准媒体链",
            },
            now_text,
        )

    def _execute_snapshot_migrations(self, connection, preview, now_text):
        plans = preview.get("migrationPlans") or []
        for plan in plans:
            self._conditional_reassign_artifact(connection, plan, now_text)

        event_count = alias_count = deleted_count = 0
        whole_groups = {}
        for plan in plans:
            if plan["migrationMode"] == "whole_chain":
                whole_groups.setdefault(
                    (plan["expectedOldChainId"], plan["newChainId"]),
                    [],
                ).append(plan)
            else:
                self._migrate_events(
                    connection,
                    plan["expectedOldChainId"],
                    plan["newChainId"],
                    now_text,
                    plan["artifactKey"],
                )
                event_count += self._record_migration_event(connection, plan["newChainId"], plan, now_text)

        for (old_chain_id, new_chain_id), chain_plans in whole_groups.items():
            self._migrate_events(connection, old_chain_id, new_chain_id, now_text)
            for plan in chain_plans:
                event_count += self._record_migration_event(connection, new_chain_id, plan, now_text)
            connection.execute(
                "UPDATE resource_chain_aliases SET canonical_chain_id=?, updated_at=? "
                "WHERE canonical_chain_id=?",
                (new_chain_id, now_text, old_chain_id),
            )
            cursor = connection.execute(
                "INSERT INTO resource_chain_aliases ("
                "alias_chain_id, canonical_chain_id, reason_code, created_at, updated_at, payload_json) "
                "VALUES (?, ?, 'CHAIN_IDENTITY_MIGRATED', ?, ?, ?) "
                "ON CONFLICT(alias_chain_id) DO UPDATE SET canonical_chain_id=excluded.canonical_chain_id, "
                "reason_code=excluded.reason_code, updated_at=excluded.updated_at, payload_json=excluded.payload_json",
                (
                    old_chain_id,
                    new_chain_id,
                    now_text,
                    now_text,
                    _json({"artifactCount": len(chain_plans), "mode": "whole_chain"}),
                ),
            )
            alias_count += int(cursor.rowcount or 0) > 0
            cursor = connection.execute(
                "DELETE FROM resource_chains WHERE chain_id=? "
                "AND NOT EXISTS (SELECT 1 FROM resource_artifacts WHERE chain_id=?) "
                "AND NOT EXISTS (SELECT 1 FROM resource_events WHERE chain_id=?)",
                (old_chain_id, old_chain_id, old_chain_id),
            )
            deleted_count += int(cursor.rowcount or 0)
        return {
            "artifactMigrations": len(plans),
            "chainAliases": alias_count,
            "deletedEmptyChains": deleted_count,
            "migrationEvents": event_count,
        }

    def record_snapshot(self, payload):
        now_text = _iso(self.clock())
        chain_count = artifact_count = event_count = conflict_count = 0
        preview = self.preview_snapshot_migrations(payload)
        backup_created = False
        if preview["artifactMigrations"]:
            try:
                backup_created = self._ensure_migration_backup()
            except Exception:
                return {
                    "persisted": False,
                    "chains": 0,
                    "artifacts": 0,
                    "events": 0,
                    "observedAt": now_text,
                    **self._migration_result(
                        migrationSkipped=1,
                        migrationSkipReasons={"BACKUP_FAILED": 1},
                    ),
                }
        try:
            with self.runtime.transaction(immediate=True) as connection:
                migration_preview = self._plan_snapshot_migrations(connection, payload)
                if migration_preview["migrationPlans"] != preview["migrationPlans"]:
                    raise ResourceMigrationConflict("OWNER_CHANGED_CONCURRENTLY")
                items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
                for item in items:
                    self._upsert_chain(connection, item, now_text)
                migration_result = self._execute_snapshot_migrations(connection, migration_preview, now_text)
                event_count += migration_result.pop("migrationEvents")
                for item in items:
                    chain_id_value = _text(item.get("chainId"), 120)
                    artifacts, conflicts_events, conflicts, _artifact_keys = self._record_artifacts(
                        connection, chain_id_value, item, now_text
                    )
                    chain_count += 1
                    artifact_count += artifacts
                    conflict_count += conflicts
                    event_count += conflicts_events
                    event_count += self._record_pipeline_facts(
                        connection, chain_id_value, item, now_text
                    )
                    event_count += self._record_episode_evidence(
                        connection, chain_id_value, item, now_text
                    )
        except ResourceMigrationConflict as exc:
            return {
                "persisted": False,
                "chains": 0,
                "artifacts": 0,
                "events": 0,
                "observedAt": now_text,
                **self._migration_result(
                    migrationSkipped=1,
                    migrationSkipReasons={exc.reason_code: 1},
                    migrationBackupCreated=backup_created,
                ),
            }
        return {
            "persisted": True,
            "chains": chain_count,
            "artifacts": artifact_count,
            "events": event_count,
            "artifactConflicts": conflict_count,
            "observedAt": now_text,
            **{
                **self._migration_result(),
                **migration_result,
                "artifactConflicts": conflict_count,
                "migrationSkipped": migration_preview["migrationSkipped"],
                "migrationSkipReasons": migration_preview["migrationSkipReasons"],
                "migrationBackupCreated": backup_created,
            },
        }

    def resolve_chain_id(self, chain_id):
        current = _text(chain_id, 120)
        visited = set()
        with closing(self.runtime.connect()) as connection:
            while current and current not in visited:
                visited.add(current)
                row = connection.execute(
                    "SELECT canonical_chain_id FROM resource_chain_aliases WHERE alias_chain_id=?",
                    (current,),
                ).fetchone()
                if not row:
                    break
                current = row["canonical_chain_id"]
        return current

    def get_chain(self, chain_id):
        chain_id = self.resolve_chain_id(chain_id)
        with closing(self.runtime.connect()) as connection:
            row = connection.execute("SELECT * FROM resource_chains WHERE chain_id=?", (str(chain_id),)).fetchone()
        return _row(row)

    def project_historical_fact_times(self, payload):
        items = [item for item in (payload or {}).get("items") or [] if isinstance(item, dict)]
        if not items:
            return payload
        chain_ids = [self.resolve_chain_id(item.get("chainId")) for item in items]
        chain_ids = [value for value in chain_ids if value]
        if not chain_ids:
            return payload
        placeholders = ",".join("?" for _ in chain_ids)
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT chain_id, event_at, observed_at, payload_json "
                "FROM resource_events "
                f"WHERE chain_id IN ({placeholders}) AND stage='emby' AND status='succeeded' "
                "AND evidence='verified'",
                tuple(chain_ids),
            ).fetchall()
        first_by_chain = {}
        first_by_unit = {}
        for row in rows:
            try:
                event_payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError):
                event_payload = {}
            event_at = str(row["event_at"] or row["observed_at"] or "")
            if not event_at:
                continue
            if event_payload.get("kind") == "pipeline_fact_unit":
                key = (row["chain_id"], str(event_payload.get("unitKey") or ""))
                if key[1]:
                    first_by_unit[key] = min(first_by_unit.get(key, event_at), event_at)
                continue
            chain_id_value = row["chain_id"]
            first_by_chain[chain_id_value] = min(first_by_chain.get(chain_id_value, event_at), event_at)
        for item in items:
            chain_id_value = self.resolve_chain_id(item.get("chainId"))
            for fact in item.get("pipelineFacts") or []:
                if not isinstance(fact, dict) or fact.get("stage") != "emby":
                    continue
                if fact.get("state") == "succeeded" and fact.get("evidence") == "verified":
                    current = str(
                        fact.get("firstConfirmedPlayableAt")
                        or fact.get("eventAt")
                        or fact.get("observedAt")
                        or ""
                    )
                    first = min(
                        value for value in (first_by_chain.get(chain_id_value, ""), current) if value
                    ) if first_by_chain.get(chain_id_value) or current else ""
                    if first:
                        fact["eventAt"] = first
                        fact["firstConfirmedPlayableAt"] = first
                for unit in fact.get("units") or []:
                    if unit.get("state") != "succeeded" or unit.get("evidence") != "verified":
                        continue
                    unit_ref = _opaque_ref("unit", unit.get("unitKey"))
                    current = str(unit.get("eventAt") or unit.get("observedAt") or "")
                    first = min(
                        value for value in (first_by_unit.get((chain_id_value, unit_ref), ""), current) if value
                    ) if first_by_unit.get((chain_id_value, unit_ref)) or current else ""
                    if first:
                        unit["eventAt"] = first
        return payload

    def list_episode_events(self, chain_id, limit=1000):
        result = []
        for row in self.list_events(chain_id, limit=limit):
            try:
                payload = json.loads(row.get("payload_json") or "{}")
            except (TypeError, ValueError):
                continue
            if payload.get("kind") not in {"episode_evidence", "pipeline_fact", "pipeline_fact_unit"}:
                continue
            result.append({
                "stage": row.get("stage"),
                "status": row.get("status"),
                "healthState": row.get("health_state"),
                "artifactKey": row.get("artifact_key") or "",
                "eventAt": row.get("event_at") or row.get("observed_at") or "",
                "observedAt": row.get("observed_at") or "",
                "freshUntil": row.get("fresh_until") or "",
                "source": row.get("source") or "",
                "reasonCode": row.get("reason_code") or "",
                "reasonText": row.get("reason_text") or "",
                **payload,
            })
        return result

    def list_events(self, chain_id, limit=100):
        chain_id = self.resolve_chain_id(chain_id)
        try:
            limit = max(1, min(int(limit or 100), 1000))
        except (TypeError, ValueError):
            limit = 100
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM resource_events WHERE chain_id=? "
                "ORDER BY COALESCE(NULLIF(event_at, ''), observed_at) DESC, created_at DESC LIMIT ?",
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
        chain_id = self.resolve_chain_id(chain_id)
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
