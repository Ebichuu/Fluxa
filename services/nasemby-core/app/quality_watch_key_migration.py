from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app.quality_watch_repository import make_unit_key
from app.torra_subscription_keys import (
    is_torra_public_subscription_key,
    torra_canonical_subscription_key,
    torra_public_subscription_key,
)


MIGRATION_ID = "quality-watch-canonical-key-v4"
LOGGER = logging.getLogger(__name__)
JSON_COLUMNS = {
    "quality_watch_units": ("unit_key", ("current_evidence_json", "last_result_json")),
    "provider_actions": ("action_id", ("request_summary_json", "response_summary_json")),
    "rss_subscription_matches": (
        "id",
        ("match_reason_json", "candidate_summary_json", "baseline_summary_json"),
    ),
    "scheduler_state": ("state_key", ("payload_json",)),
}
JSON_REWRITE_PATHS = {
    ("provider_actions", "request_summary_json", "$.unitId"),
    ("provider_actions", "request_summary_json", "$.subscriptionId"),
    ("provider_actions", "response_summary_json", "$.unitId"),
    ("provider_actions", "response_summary_json", "$.subscriptionId"),
    ("scheduler_state", "payload_json", "$.cursor"),
    ("scheduler_state", "payload_json", "$.lastSubscription"),
}
PUBLIC_REFERENCE_PATTERN = re.compile(r"torra:[0-9a-f]{10}(?=$|:)")


class QualityWatchKeyMigrationError(RuntimeError):
    def __init__(self, result):
        self.result = dict(result or {})
        super().__init__(self.result.get("message") or "质量观察规范键迁移失败")


def _utc_now():
    return datetime.now(timezone.utc)


def _iso(value):
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_ref(*parts):
    return hashlib.sha256("\0".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()[:16]


def _table_exists(connection, table):
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _rows(connection, table):
    if not _table_exists(connection, table):
        return []
    return [dict(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall()]


def _integer(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _walk_json(value, path="$"):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_json(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_json(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def _has_public_reference(value):
    return PUBLIC_REFERENCE_PATTERN.search(str(value or "")) is not None


def _replace_json_path(value, path, replacement):
    key = path.removeprefix("$.")
    if not isinstance(value, dict) or "." in key or "[" in key:
        raise ValueError("unsupported_json_path")
    value[key] = replacement


def _json_replacement(table, column, path, value, subscription_map, unit_map):
    if (table, column, path) not in JSON_REWRITE_PATHS:
        return ""
    if path.endswith(".unitId"):
        return unit_map.get(value, "")
    if path.endswith(".subscriptionId") or path.endswith(".lastSubscription"):
        return subscription_map.get(value, "")
    if path.endswith(".cursor"):
        return unit_map.get(value) or subscription_map.get(value, "")
    return ""


def _conflict(reason, *, table="", row_id="", column="", path="", counts=None):
    return {
        "reasonCode": reason,
        "targetRef": _hash_ref(table, row_id, column, path, reason),
        "table": table,
        "column": column,
        "jsonPath": path,
        "counts": dict(counts or {}),
        "advice": "修复重复身份或遗留引用后重新启动",
    }


def _plan_fingerprint(payload):
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def _build_plan(connection):
    units = _rows(connection, "quality_watch_units")
    matches = _rows(connection, "rss_subscription_matches")
    actions = _rows(connection, "provider_actions")
    scheduler = _rows(connection, "scheduler_state")
    source_rows = {
        "quality_watch_units": units,
        "rss_subscription_matches": matches,
        "provider_actions": actions,
        "scheduler_state": scheduler,
    }
    fingerprint = _plan_fingerprint(source_rows)
    conflicts = []
    public_remote_ids = {}
    legacy_found = False

    for table, rows in (("quality_watch_units", units), ("rss_subscription_matches", matches)):
        primary = "unit_key" if table == "quality_watch_units" else "id"
        for row in rows:
            public_key = str(row.get("subscription_key") or "").strip()
            if not is_torra_public_subscription_key(public_key):
                continue
            legacy_found = True
            remote_id = str(row.get("torra_subscription_id") or "").strip()
            if not remote_id or torra_public_subscription_key(remote_id) != public_key:
                conflicts.append(_conflict(
                    "public_key_remote_id_mismatch", table=table, row_id=row.get(primary),
                    counts={table: 1},
                ))
                continue
            public_remote_ids.setdefault(public_key, set()).add(remote_id)

    for public_key, remote_ids in public_remote_ids.items():
        if len(remote_ids) > 1:
            conflicts.append(_conflict(
                "public_key_digest_collision", table="quality_watch_units",
                row_id=_hash_ref(public_key), counts={"remoteSubscriptions": len(remote_ids)},
            ))

    subscription_map = {
        public_key: torra_canonical_subscription_key(next(iter(remote_ids)))
        for public_key, remote_ids in public_remote_ids.items()
        if len(remote_ids) == 1
    }
    unit_map = {}
    unit_targets = {}
    existing_unit_keys = {str(row.get("unit_key") or "") for row in units}
    for row in units:
        old_subscription = str(row.get("subscription_key") or "").strip()
        if old_subscription not in subscription_map:
            continue
        season = _integer(row.get("season_number"))
        episode = _integer(row.get("episode_number"))
        media_type = "tv" if season > 0 else "movie"
        try:
            expected_old = make_unit_key(old_subscription, media_type, season or None, episode or None)
            expected_new = make_unit_key(
                subscription_map[old_subscription], media_type, season or None, episode or None
            )
        except ValueError:
            conflicts.append(_conflict(
                "unit_scope_invalid", table="quality_watch_units", row_id=row.get("unit_key"),
                counts={"quality_watch_units": 1},
            ))
            continue
        old_unit = str(row.get("unit_key") or "").strip()
        if old_unit != expected_old:
            conflicts.append(_conflict(
                "public_unit_key_invalid", table="quality_watch_units", row_id=old_unit,
                counts={"quality_watch_units": 1},
            ))
            continue
        if expected_new in existing_unit_keys:
            conflicts.append(_conflict(
                "canonical_and_public_unit_conflict", table="quality_watch_units", row_id=old_unit,
                counts={"quality_watch_units": 2},
            ))
        unit_targets.setdefault(expected_new, []).append(old_unit)
        unit_map[old_unit] = expected_new

    for target, sources in unit_targets.items():
        if len(sources) > 1:
            conflicts.append(_conflict(
                "multiple_units_share_canonical_target", table="quality_watch_units",
                row_id=target, counts={"quality_watch_units": len(sources)},
            ))

    old_keys = set(subscription_map) | set(unit_map)
    changes = {
        "quality_watch_units": [],
        "rss_subscription_matches": [],
        "provider_actions": [],
        "scheduler_state": [],
    }

    for row in units:
        old_subscription = str(row.get("subscription_key") or "").strip()
        old_unit = str(row.get("unit_key") or "").strip()
        if old_subscription in subscription_map and old_unit in unit_map:
            changes["quality_watch_units"].append({
                "key": old_unit,
                "subscription_key": subscription_map[old_subscription],
                "unit_key": unit_map[old_unit],
            })

    rss_unique_targets = {}
    for row in matches:
        old_subscription = str(row.get("subscription_key") or "").strip()
        old_unit = str(row.get("unit_key") or "").strip()
        new_subscription = subscription_map.get(old_subscription, old_subscription)
        new_unit = unit_map.get(old_unit, old_unit)
        if is_torra_public_subscription_key(old_subscription) and old_subscription not in subscription_map:
            legacy_found = True
            conflicts.append(_conflict(
                "rss_subscription_mapping_missing", table="rss_subscription_matches", row_id=row.get("id"),
                counts={"rss_subscription_matches": 1},
            ))
        if _has_public_reference(old_unit) and old_unit not in unit_map:
            legacy_found = True
            conflicts.append(_conflict(
                "rss_unit_mapping_missing", table="rss_subscription_matches", row_id=row.get("id"),
                counts={"rss_subscription_matches": 1},
            ))
        if (new_subscription, new_unit) != (old_subscription, old_unit):
            legacy_found = True
            changes["rss_subscription_matches"].append({
                "key": str(row.get("id") or ""),
                "subscription_key": new_subscription,
                "unit_key": new_unit,
            })
        unique_key = (str(row.get("item_id") or ""), new_unit)
        rss_unique_targets.setdefault(unique_key, []).append(str(row.get("id") or ""))

    for target, row_ids in rss_unique_targets.items():
        if len(row_ids) > 1:
            conflicts.append(_conflict(
                "rss_unique_key_conflict", table="rss_subscription_matches",
                row_id=_hash_ref(*target), counts={"rss_subscription_matches": len(row_ids)},
            ))

    idempotency_targets = {}
    for row in actions:
        action_id = str(row.get("action_id") or "")
        old_subscription = str(row.get("subscription_key") or "").strip()
        old_unit = str(row.get("unit_key") or "").strip()
        new_subscription = subscription_map.get(old_subscription, old_subscription)
        new_unit = unit_map.get(old_unit, old_unit)
        if is_torra_public_subscription_key(old_subscription) and old_subscription not in subscription_map:
            legacy_found = True
            conflicts.append(_conflict(
                "action_subscription_mapping_missing", table="provider_actions", row_id=action_id,
                counts={"provider_actions": 1},
            ))
        if _has_public_reference(old_unit) and old_unit not in unit_map:
            legacy_found = True
            conflicts.append(_conflict(
                "action_unit_mapping_missing", table="provider_actions", row_id=action_id,
                counts={"provider_actions": 1},
            ))
        old_idempotency = str(row.get("idempotency_key") or "")
        new_idempotency = old_idempotency
        referenced = [key for key in old_keys if key and key in old_idempotency]
        if referenced:
            matched = False
            for old_key, replacement in unit_map.items():
                prefix = f"scheduled-rewash-analysis:{old_key}:"
                if old_idempotency.startswith(prefix) and old_idempotency[len(prefix):].isdigit():
                    new_idempotency = f"scheduled-rewash-analysis:{replacement}:{old_idempotency[len(prefix):]}"
                    matched = True
                    break
            if not matched:
                conflicts.append(_conflict(
                    "unknown_idempotency_reference", table="provider_actions", row_id=action_id,
                    column="idempotency_key", counts={"provider_actions": 1},
                ))
        if (new_subscription, new_unit, new_idempotency) != (
            old_subscription, old_unit, old_idempotency
        ):
            legacy_found = True
            changes["provider_actions"].append({
                "key": action_id,
                "subscription_key": new_subscription,
                "unit_key": new_unit,
                "idempotency_key": new_idempotency,
            })
        idempotency_targets.setdefault(new_idempotency, []).append(action_id)

    for value, action_ids in idempotency_targets.items():
        if value and len(action_ids) > 1:
            conflicts.append(_conflict(
                "action_idempotency_conflict", table="provider_actions", row_id=_hash_ref(value),
                column="idempotency_key", counts={"provider_actions": len(action_ids)},
            ))

    json_changes = {}
    table_rows = {
        "quality_watch_units": units,
        "provider_actions": actions,
        "rss_subscription_matches": matches,
        "scheduler_state": scheduler,
    }
    for table, (primary, columns) in JSON_COLUMNS.items():
        for row in table_rows.get(table, []):
            row_id = str(row.get(primary) or "")
            for column in columns:
                raw = str(row.get(column) or "{}")
                try:
                    payload = json.loads(raw)
                except (TypeError, ValueError):
                    if any(key and key in raw for key in old_keys) or _has_public_reference(raw):
                        legacy_found = True
                        conflicts.append(_conflict(
                            "invalid_json", table=table, row_id=row_id, column=column,
                            counts={table: 1},
                        ))
                    continue
                changed = False
                for path, value in list(_walk_json(payload)):
                    matches_in_value = [key for key in old_keys if key and key in value]
                    if not matches_in_value and not _has_public_reference(value):
                        continue
                    legacy_found = True
                    exact = next((key for key in matches_in_value if value == key), "")
                    registered = (table, column, path) in JSON_REWRITE_PATHS
                    replacement = _json_replacement(
                        table,
                        column,
                        path,
                        exact,
                        subscription_map,
                        unit_map,
                    )
                    if registered and exact and replacement:
                        _replace_json_path(payload, path, replacement)
                        changed = True
                        legacy_found = True
                    else:
                        conflicts.append(_conflict(
                            "unknown_json_reference", table=table, row_id=row_id,
                            column=column, path=path, counts={table: 1},
                        ))
                if changed:
                    json_changes[(table, row_id, column)] = _json(payload)

    for row in scheduler:
        state_key = str(row.get("state_key") or "")
        columns = {
            column: value for (table, key, column), value in json_changes.items()
            if table == "scheduler_state" and key == state_key
        }
        if columns:
            changes["scheduler_state"].append({"key": state_key, **columns})

    return {
        "fingerprint": fingerprint,
        "legacyFound": legacy_found,
        "subscriptionMap": subscription_map,
        "unitMap": unit_map,
        "changes": changes,
        "jsonChanges": json_changes,
        "conflicts": conflicts,
    }


def _backup_database(repository, clock):
    stamp = clock().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = Path(repository.database_path).parent / "migrations"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{MIGRATION_ID}.{stamp}.{uuid.uuid4().hex[:8]}.sqlite3"
    temporary_path = backup_path.with_suffix(".sqlite3.tmp")
    try:
        with closing(repository.runtime.connect()) as source, closing(sqlite3.connect(temporary_path)) as target:
            source.backup(target)
        with closing(sqlite3.connect(temporary_path)) as verification:
            integrity = verification.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError("backup_integrity_check_failed")
        temporary_path.replace(backup_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        backup_path.unlink(missing_ok=True)
        raise
    return backup_path


def _write_conflict_report(repository, conflicts, clock):
    stamp = clock().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report_path = Path(repository.database_path).with_name(
        f"{Path(repository.database_path).stem}.{MIGRATION_ID}.conflict.{stamp}.json"
    )
    report = {
        "migrationVersion": MIGRATION_ID,
        "status": "blocked",
        "createdAt": _iso(clock()),
        "conflictCount": len(conflicts),
        "conflicts": conflicts,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for conflict in conflicts:
        LOGGER.error(
            "质量观察规范键迁移冲突 target=%s counts=%s advice=%s",
            conflict["targetRef"], conflict["counts"], conflict["advice"],
        )
    return report_path


def _apply_plan(connection, plan, backup_path, clock):
    counts = {}
    for change in plan["changes"]["quality_watch_units"]:
        cursor = connection.execute(
            "UPDATE quality_watch_units SET unit_key=?, subscription_key=? WHERE unit_key=?",
            (change["unit_key"], change["subscription_key"], change["key"]),
        )
        counts["quality_watch_units"] = counts.get("quality_watch_units", 0) + cursor.rowcount
    for change in plan["changes"]["rss_subscription_matches"]:
        cursor = connection.execute(
            "UPDATE rss_subscription_matches SET subscription_key=?, unit_key=? WHERE id=?",
            (change["subscription_key"], change["unit_key"], change["key"]),
        )
        counts["rss_subscription_matches"] = counts.get("rss_subscription_matches", 0) + cursor.rowcount
    for change in plan["changes"]["provider_actions"]:
        cursor = connection.execute(
            "UPDATE provider_actions SET subscription_key=?, unit_key=?, idempotency_key=? WHERE action_id=?",
            (
                change["subscription_key"], change["unit_key"],
                change["idempotency_key"], change["key"],
            ),
        )
        counts["provider_actions"] = counts.get("provider_actions", 0) + cursor.rowcount
    primary_keys = {
        "quality_watch_units": "unit_key",
        "provider_actions": "action_id",
        "rss_subscription_matches": "id",
        "scheduler_state": "state_key",
    }
    for (table, row_id, column), value in plan["jsonChanges"].items():
        key = primary_keys[table]
        if table == "quality_watch_units" and row_id in plan["unitMap"]:
            row_id = plan["unitMap"][row_id]
        cursor = connection.execute(
            f"UPDATE {table} SET {column}=? WHERE {key}=?", (value, row_id)
        )
        counts[f"{table}.{column}"] = counts.get(f"{table}.{column}", 0) + cursor.rowcount
    connection.execute(
        "INSERT INTO quality_watch_key_migrations "
        "(migration_id, status, backup_ref, counts_json, created_at) VALUES (?, 'success', ?, ?, ?)",
        (MIGRATION_ID, backup_path.name, _json(counts), _iso(clock())),
    )
    return counts


def run_quality_watch_key_migration(repository, *, clock=None, backup_creator=None, report_writer=None):
    clock = clock or _utc_now
    backup_creator = backup_creator or _backup_database
    report_writer = report_writer or _write_conflict_report
    with closing(repository.runtime.connect()) as connection:
        plan = _build_plan(connection)
    if not plan["legacyFound"] and not plan["conflicts"]:
        return {"status": "success", "applied": False, "updated": 0, "backupCreated": False}

    try:
        backup_path = backup_creator(repository, clock)
    except Exception as exc:
        raise QualityWatchKeyMigrationError({
            "status": "failed", "reasonCode": "backup_failed",
            "message": "质量观察规范键迁移备份失败，服务已停止启动",
        }) from exc

    try:
        with repository.runtime.transaction(immediate=True) as connection:
            current = _build_plan(connection)
            if current["fingerprint"] != plan["fingerprint"]:
                raise QualityWatchKeyMigrationError({
                    "status": "blocked", "reasonCode": "migration_plan_stale",
                    "message": "质量观察规范键迁移计划已过期，服务已停止启动",
                })
            if current["conflicts"]:
                raise QualityWatchKeyMigrationError({
                    "status": "blocked", "reasonCode": "migration_conflict",
                    "message": "质量观察规范键存在冲突，服务已停止启动",
                    "conflicts": current["conflicts"],
                })
            counts = _apply_plan(connection, current, backup_path, clock)
    except Exception as exc:
        result = getattr(exc, "result", {})
        conflicts = result.get("conflicts") or [
            _conflict(
                result.get("reasonCode") or "migration_transaction_failed",
                counts={"migration": 1},
            )
        ]
        try:
            report_path = report_writer(repository, conflicts, clock)
        except Exception:
            report_path = ""
        if isinstance(exc, QualityWatchKeyMigrationError):
            migration_result = dict(exc.result)
            migration_result.update({"backup": str(backup_path), "report": str(report_path or "")})
            raise QualityWatchKeyMigrationError(migration_result) from exc
        raise QualityWatchKeyMigrationError({
            "status": "failed", "reasonCode": "migration_failed",
            "message": "质量观察规范键迁移失败，服务已停止启动",
            "backup": str(backup_path), "report": str(report_path or ""),
        }) from exc

    return {
        "status": "success", "applied": True,
        "updated": sum(counts.values()), "counts": counts,
        "backupCreated": True, "backup": str(backup_path),
    }
