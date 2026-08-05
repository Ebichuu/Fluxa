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
    ("provider_actions", "request_summary_json", "$.targetKey"),
    ("provider_actions", "response_summary_json", "$.unitId"),
    ("provider_actions", "response_summary_json", "$.subscriptionId"),
    ("scheduler_state", "payload_json", "$.cursor"),
    ("scheduler_state", "payload_json", "$.lastSubscription"),
}
PUBLIC_REFERENCE_PATTERN = re.compile(r"torra:[0-9a-f]{10}(?=$|:)")
BLOCKED_RECEIPT_SUFFIX = f".{MIGRATION_ID}.blocked.json"


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
            key = str(key)
            key_has_reference = _has_public_reference(key)
            safe_key = f"<key:{_hash_ref(key)}>" if key_has_reference else key
            key_path = f"{path}.{safe_key}"
            if key_has_reference:
                yield "key", key_path, key
            yield from _walk_json(item, key_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_json(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield "value", path, value


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
    if path.endswith(".unitId") or path.endswith(".targetKey"):
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


def _media_type(value):
    normalized = str(value or "").strip().lower()
    if normalized in {"tv", "series", "电视剧", "剧集"}:
        return "tv"
    if normalized in {"movie", "电影"}:
        return "movie"
    return ""


def _json_object(value):
    try:
        payload = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _rss_unit_scope(row, item):
    """Return a fully verified RSS unit scope; never infer from a title."""
    if not item:
        return None, "rss_item_missing"
    reason = _json_object(row.get("match_reason_json"))
    item_type = _media_type(item.get("media_type"))
    reason_type = _media_type(reason.get("mediaType"))
    if item_type not in {"movie", "tv"} or reason_type != item_type:
        return None, "rss_media_type_unconfirmed"
    if item_type == "movie":
        if any(_integer(item.get(field)) > 0 for field in (
            "season_number", "episode_start", "episode_end"
        )):
            return None, "rss_movie_scope_invalid"
        return ("movie", None, None), ""

    season_reason = reason.get("season") if isinstance(reason.get("season"), dict) else {}
    episode_reason = reason.get("episode") if isinstance(reason.get("episode"), dict) else {}
    item_season = _integer(item.get("season_number"))
    item_start = _integer(item.get("episode_start"))
    item_end = _integer(item.get("episode_end"))
    unit_season = _integer(season_reason.get("unit"))
    unit_episode = _integer(episode_reason.get("unit"))
    if (
        item_season <= 0
        or item_start <= 0
        or item_end < item_start
        or _integer(season_reason.get("item")) != item_season
        or unit_season != item_season
        or _integer(episode_reason.get("start")) != item_start
        or _integer(episode_reason.get("end")) != item_end
        or not item_start <= unit_episode <= item_end
    ):
        return None, "rss_episode_scope_unconfirmed"
    return ("tv", unit_season, unit_episode), ""


def _orphan_archive_subscription(public_key):
    return f"rss-archive:{_hash_ref(MIGRATION_ID, public_key)}"


def _safe_orphan_match(row, actions_by_id):
    if not (
        not str(row.get("torra_subscription_id") or "").strip()
        and str(row.get("match_status") or "") == "candidate"
        and str(row.get("evaluation_status") or "") == "blocked"
        and str(row.get("evaluation_reason") or "") == "subscription_missing"
        and str(row.get("archive_state") or "active") == "active"
        and not str(row.get("trigger_action_id") or "").strip()
        and not str(row.get("download_action_id") or "").strip()
        and not bool(_integer(row.get("is_best_candidate")))
    ):
        return False
    action_id = str(row.get("evaluation_action_id") or "").strip()
    action = actions_by_id.get(action_id)
    return bool(
        action
        and str(action.get("provider") or "") == "fluxa"
        and str(action.get("action_type") or "") == "rss-candidate-evaluation"
        and str(action.get("status") or "") == "succeeded"
        and not str(action.get("external_job_id") or "").strip()
    )


def _build_plan(connection):
    """Build the v4 plan from every authoritative row, including RSS-only candidates."""
    units = _rows(connection, "quality_watch_units")
    matches = _rows(connection, "rss_subscription_matches")
    actions = _rows(connection, "provider_actions")
    scheduler = _rows(connection, "scheduler_state")
    match_item_ids = {str(row.get("item_id") or "") for row in matches}
    items = [
        row for row in _rows(connection, "rss_items")
        if str(row.get("id") or "") in match_item_ids
    ]
    source_rows = {
        "quality_watch_units": units,
        "rss_subscription_matches": matches,
        "provider_actions": actions,
        "scheduler_state": scheduler,
        "rss_items": items,
    }
    fingerprint = _plan_fingerprint(source_rows)
    conflicts = []
    legacy_found = False
    items_by_id = {str(row.get("id") or ""): row for row in items}
    actions_by_id = {str(row.get("action_id") or ""): row for row in actions}

    public_remote_ids = {}
    invalid_identity_rows = set()
    for table, rows, primary in (
        ("quality_watch_units", units, "unit_key"),
        ("rss_subscription_matches", matches, "id"),
    ):
        for row in rows:
            public_key = str(row.get("subscription_key") or "").strip()
            if not is_torra_public_subscription_key(public_key):
                continue
            legacy_found = True
            remote_id = str(row.get("torra_subscription_id") or "").strip()
            if not remote_id:
                continue
            if torra_public_subscription_key(remote_id) != public_key:
                invalid_identity_rows.add((table, str(row.get(primary) or "")))
                conflicts.append(_conflict(
                    "public_key_remote_id_mismatch", table=table,
                    row_id=row.get(primary), counts={table: 1},
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
    archived_public_keys = set()
    missing_matches_by_key = {}
    for row in matches:
        row_id = str(row.get("id") or "")
        public_key = str(row.get("subscription_key") or "").strip()
        if (
            not is_torra_public_subscription_key(public_key)
            or public_key in subscription_map
            or ("rss_subscription_matches", row_id) in invalid_identity_rows
        ):
            continue
        missing_matches_by_key.setdefault(public_key, []).append(row)
    for public_key, missing_rows in missing_matches_by_key.items():
        if not all(_safe_orphan_match(row, actions_by_id) for row in missing_rows):
            for row in missing_rows:
                conflicts.append(_conflict(
                    "rss_subscription_mapping_missing", table="rss_subscription_matches",
                    row_id=row.get("id"), counts={"rss_subscription_matches": 1},
                ))
            continue
        archive_key = _orphan_archive_subscription(public_key)
        previous = subscription_map.setdefault(public_key, archive_key)
        if previous != archive_key:
            conflicts.append(_conflict(
                "rss_orphan_owner_conflict", table="rss_subscription_matches",
                row_id=_hash_ref(public_key),
                counts={"rss_subscription_matches": len(missing_rows)},
            ))
            continue
        archived_public_keys.add(public_key)

    unit_map = {}
    target_sources = {}

    def register_unit(old_unit, new_unit, *, table, row_id):
        previous = unit_map.setdefault(old_unit, new_unit)
        if previous != new_unit:
            conflicts.append(_conflict(
                "unit_mapping_conflict", table=table, row_id=row_id, counts={table: 1},
            ))
            return
        target_sources.setdefault(new_unit, set()).add(old_unit)

    existing_quality_units = {str(row.get("unit_key") or "") for row in units}
    for row in units:
        old_subscription = str(row.get("subscription_key") or "").strip()
        if not is_torra_public_subscription_key(old_subscription):
            continue
        new_subscription = subscription_map.get(old_subscription)
        if not new_subscription or old_subscription in archived_public_keys:
            conflicts.append(_conflict(
                "unit_subscription_mapping_missing", table="quality_watch_units",
                row_id=row.get("unit_key"), counts={"quality_watch_units": 1},
            ))
            continue
        season = _integer(row.get("season_number"))
        episode = _integer(row.get("episode_number"))
        media_type = "tv" if season > 0 else "movie"
        try:
            old_expected = make_unit_key(old_subscription, media_type, season or None, episode or None)
            new_expected = make_unit_key(new_subscription, media_type, season or None, episode or None)
        except ValueError:
            conflicts.append(_conflict(
                "unit_scope_invalid", table="quality_watch_units",
                row_id=row.get("unit_key"), counts={"quality_watch_units": 1},
            ))
            continue
        old_unit = str(row.get("unit_key") or "").strip()
        if old_unit != old_expected:
            conflicts.append(_conflict(
                "public_unit_key_invalid", table="quality_watch_units",
                row_id=old_unit, counts={"quality_watch_units": 1},
            ))
            continue
        if new_expected in existing_quality_units:
            conflicts.append(_conflict(
                "canonical_and_public_unit_conflict", table="quality_watch_units",
                row_id=old_unit, counts={"quality_watch_units": 2},
            ))
        register_unit(
            old_unit, new_expected, table="quality_watch_units", row_id=old_unit
        )

    archived_match_ids = set()
    for row in matches:
        row_id = str(row.get("id") or "")
        old_subscription = str(row.get("subscription_key") or "").strip()
        old_unit = str(row.get("unit_key") or "").strip()
        if not is_torra_public_subscription_key(old_subscription):
            continue
        new_subscription = subscription_map.get(old_subscription)
        if not new_subscription:
            continue
        if old_unit not in unit_map:
            scope, error = _rss_unit_scope(
                row, items_by_id.get(str(row.get("item_id") or ""))
            )
            if error:
                conflicts.append(_conflict(
                    error, table="rss_subscription_matches", row_id=row_id,
                    counts={"rss_subscription_matches": 1},
                ))
                continue
            media_type, season, episode = scope
            try:
                old_expected = make_unit_key(
                    old_subscription, media_type, season, episode
                )
                new_expected = make_unit_key(
                    new_subscription, media_type, season, episode
                )
            except ValueError:
                conflicts.append(_conflict(
                    "rss_unit_scope_invalid", table="rss_subscription_matches",
                    row_id=row_id, counts={"rss_subscription_matches": 1},
                ))
                continue
            if old_unit != old_expected:
                conflicts.append(_conflict(
                    "rss_public_unit_key_invalid", table="rss_subscription_matches",
                    row_id=row_id, counts={"rss_subscription_matches": 1},
                ))
                continue
            register_unit(
                old_unit, new_expected, table="rss_subscription_matches", row_id=row_id
            )
        if old_subscription in archived_public_keys:
            archived_match_ids.add(row_id)

    for target, sources in target_sources.items():
        if len(sources) > 1:
            conflicts.append(_conflict(
                "multiple_units_share_canonical_target", table="quality_watch_units",
                row_id=target, counts={"unitReferences": len(sources)},
            ))

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

    unique_matches = {}
    for row in matches:
        row_id = str(row.get("id") or "")
        old_subscription = str(row.get("subscription_key") or "").strip()
        old_unit = str(row.get("unit_key") or "").strip()
        new_subscription = subscription_map.get(old_subscription, old_subscription)
        new_unit = unit_map.get(old_unit, old_unit)
        if _has_public_reference(old_unit) and old_unit not in unit_map:
            conflicts.append(_conflict(
                "rss_unit_mapping_missing", table="rss_subscription_matches",
                row_id=row_id, counts={"rss_subscription_matches": 1},
            ))
        archive = row_id in archived_match_ids
        if (new_subscription, new_unit) != (old_subscription, old_unit) or archive:
            changes["rss_subscription_matches"].append({
                "key": row_id,
                "subscription_key": new_subscription,
                "unit_key": new_unit,
                "archive": archive,
            })
        unique_matches.setdefault((str(row.get("item_id") or ""), new_unit), []).append(row_id)
    for target, row_ids in unique_matches.items():
        if len(row_ids) > 1:
            conflicts.append(_conflict(
                "rss_unique_key_conflict", table="rss_subscription_matches",
                row_id=_hash_ref(*target), counts={"rss_subscription_matches": len(row_ids)},
            ))

    old_keys = set(subscription_map) | set(unit_map)
    idempotency_targets = {}
    for row in actions:
        action_id = str(row.get("action_id") or "")
        old_subscription = str(row.get("subscription_key") or "").strip()
        old_unit = str(row.get("unit_key") or "").strip()
        new_subscription = subscription_map.get(old_subscription, old_subscription)
        new_unit = unit_map.get(old_unit, old_unit)
        if is_torra_public_subscription_key(old_subscription) and old_subscription not in subscription_map:
            conflicts.append(_conflict(
                "action_subscription_mapping_missing", table="provider_actions",
                row_id=action_id, counts={"provider_actions": 1},
            ))
        if _has_public_reference(old_unit) and old_unit not in unit_map:
            conflicts.append(_conflict(
                "action_unit_mapping_missing", table="provider_actions",
                row_id=action_id, counts={"provider_actions": 1},
            ))
        old_idempotency = str(row.get("idempotency_key") or "")
        new_idempotency = old_idempotency
        references = [key for key in old_keys if key and key in old_idempotency]
        if references:
            for old_key, replacement in unit_map.items():
                prefix = f"scheduled-rewash-analysis:{old_key}:"
                suffix = old_idempotency.removeprefix(prefix)
                if old_idempotency.startswith(prefix) and suffix.isdigit():
                    new_idempotency = f"scheduled-rewash-analysis:{replacement}:{suffix}"
                    break
            else:
                conflicts.append(_conflict(
                    "unknown_idempotency_reference", table="provider_actions",
                    row_id=action_id, column="idempotency_key", counts={"provider_actions": 1},
                ))
        if (new_subscription, new_unit, new_idempotency) != (
            old_subscription, old_unit, old_idempotency
        ):
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
                "action_idempotency_conflict", table="provider_actions",
                row_id=_hash_ref(value), column="idempotency_key",
                counts={"provider_actions": len(action_ids)},
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
                    if any(key in raw for key in old_keys if key) or _has_public_reference(raw):
                        conflicts.append(_conflict(
                            "invalid_json", table=table, row_id=row_id,
                            column=column, counts={table: 1},
                        ))
                    continue
                changed = False
                for kind, path, value in list(_walk_json(payload)):
                    references = [key for key in old_keys if key and key in value]
                    if not references and not _has_public_reference(value):
                        continue
                    legacy_found = True
                    if kind == "key":
                        conflicts.append(_conflict(
                            "unknown_json_key_reference", table=table, row_id=row_id,
                            column=column, path=path, counts={table: 1},
                        ))
                        continue
                    exact = next((key for key in references if value == key), "")
                    replacement = _json_replacement(
                        table, column, path, exact, subscription_map, unit_map
                    )
                    if (table, column, path) in JSON_REWRITE_PATHS and exact and replacement:
                        _replace_json_path(payload, path, replacement)
                        changed = True
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
        "archivedMatches": len(archived_match_ids),
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


def _blocked_receipt_path(repository):
    database_path = Path(repository.database_path)
    return database_path.with_name(f"{database_path.stem}{BLOCKED_RECEIPT_SUFFIX}")


def _load_blocked_receipt(repository, fingerprint):
    path = _blocked_receipt_path(repository)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("migrationVersion") != MIGRATION_ID
        or payload.get("fingerprint") != fingerprint
    ):
        return None
    database_path = Path(repository.database_path)
    backup_ref = str(payload.get("backupRef") or "")
    report_ref = str(payload.get("reportRef") or "")
    if not backup_ref or Path(backup_ref).name != backup_ref:
        return None
    if not report_ref or Path(report_ref).name != report_ref:
        return None
    backup_path = database_path.parent / "migrations" / backup_ref
    report_path = database_path.parent / report_ref
    if not backup_path.is_file() or backup_path.stat().st_size <= 0 or not report_path.is_file():
        return None
    return {"backup": str(backup_path), "report": str(report_path)}


def _write_blocked_receipt(repository, fingerprint, backup_path, report_path, clock):
    target = _blocked_receipt_path(repository)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    payload = {
        "migrationVersion": MIGRATION_ID,
        "fingerprint": fingerprint,
        "backupRef": Path(backup_path).name,
        "reportRef": Path(report_path).name,
        "createdAt": _iso(clock()),
    }
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(target)


def _clear_blocked_receipt(repository):
    try:
        _blocked_receipt_path(repository).unlink(missing_ok=True)
    except OSError:
        LOGGER.warning("质量观察规范键迁移阻断收据清理失败")


def _apply_plan(connection, plan, backup_path, clock):
    counts = {}
    for change in plan["changes"]["quality_watch_units"]:
        cursor = connection.execute(
            "UPDATE quality_watch_units SET unit_key=?, subscription_key=? WHERE unit_key=?",
            (change["unit_key"], change["subscription_key"], change["key"]),
        )
        counts["quality_watch_units"] = counts.get("quality_watch_units", 0) + cursor.rowcount
    for change in plan["changes"]["rss_subscription_matches"]:
        if change.get("archive"):
            cursor = connection.execute(
                "UPDATE rss_subscription_matches SET subscription_key=?, unit_key=?, "
                "archive_state='archived', archived_at=?, "
                "archive_reason_code='canonical_key_identity_unavailable', archive_run_id=? "
                "WHERE id=?",
                (
                    change["subscription_key"], change["unit_key"], _iso(clock()),
                    MIGRATION_ID, change["key"],
                ),
            )
        else:
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
        _clear_blocked_receipt(repository)
        return {"status": "success", "applied": False, "updated": 0, "backupCreated": False}

    cached_block = _load_blocked_receipt(repository, plan["fingerprint"])
    if plan["conflicts"] and cached_block:
        raise QualityWatchKeyMigrationError({
            "status": "blocked", "reasonCode": "migration_conflict",
            "message": "质量观察规范键存在冲突，服务已停止启动",
            "conflicts": plan["conflicts"], "backupCreated": False,
            **cached_block,
        })

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
        if report_path and result.get("reasonCode") == "migration_conflict":
            try:
                _write_blocked_receipt(
                    repository, plan["fingerprint"], backup_path, report_path, clock
                )
            except Exception:
                LOGGER.exception("质量观察规范键迁移阻断收据写入失败")
        if isinstance(exc, QualityWatchKeyMigrationError):
            migration_result = dict(exc.result)
            migration_result.update({
                "backup": str(backup_path), "report": str(report_path or ""),
                "backupCreated": True,
            })
            raise QualityWatchKeyMigrationError(migration_result) from exc
        raise QualityWatchKeyMigrationError({
            "status": "failed", "reasonCode": "migration_failed",
            "message": "质量观察规范键迁移失败，服务已停止启动",
            "backup": str(backup_path), "report": str(report_path or ""),
        }) from exc

    _clear_blocked_receipt(repository)
    return {
        "status": "success", "applied": True,
        "updated": sum(counts.values()), "counts": counts,
        "backupCreated": True, "backup": str(backup_path),
    }
