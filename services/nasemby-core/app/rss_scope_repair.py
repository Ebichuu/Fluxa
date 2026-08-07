from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app.private_rss_parser import extract_release_scope


REPAIR_VERSION = "rss-scope-repair-v1"
FINGERPRINT_PATTERN = re.compile(r"[a-f0-9]{64}")
CODEC_PATTERN = re.compile(r"(?i)\bx(?:264|265|266)\b")
REPEATED_SEASON_RANGE_PATTERN = re.compile(
    r"(?i)\bS\d{1,2}\s*E\d{1,4}\s*[-~]\s*S\d{1,2}\s*E\d{1,4}\b"
)
LOCAL_EVALUATION_ACTION_TYPES = {"rss-candidate-evaluation"}


class RssScopeRepairError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def _utc_now():
    return datetime.now(timezone.utc)


def _iso(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _json_object(value):
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _table_exists(connection, name):
    return bool(connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone())


def _scope(row):
    return {
        "mediaType": str(row.get("media_type") or ""),
        "seasonNumber": row.get("season_number"),
        "episodeStart": row.get("episode_start"),
        "episodeEnd": row.get("episode_end"),
    }


def _parsed_scope(row):
    media_type, season, episode_start, episode_end = extract_release_scope(
        row.get("title"),
        [row.get("category") or ""],
    )
    return {
        "mediaType": media_type,
        "seasonNumber": season,
        "episodeStart": episode_start,
        "episodeEnd": episode_end,
    }


def _change_type(row, before, after):
    title = str(row.get("title") or "")
    if (
        REPEATED_SEASON_RANGE_PATTERN.search(title)
        and before.get("episodeEnd") != after.get("episodeEnd")
    ):
        return "repeated_season_range"
    if (
        CODEC_PATTERN.search(title)
        and before.get("mediaType") == "tv"
        and after.get("seasonNumber") is None
        and after.get("episodeStart") is None
    ):
        return "codec_false_positive"
    return "scope_changed"


def _match_references(value):
    result = set()
    if not isinstance(value, dict):
        return result
    match_id = str(value.get("matchId") or "").strip()
    if match_id:
        result.add(match_id)
    for candidate in value.get("matchIds") or []:
        candidate = str(candidate or "").strip()
        if candidate:
            result.add(candidate)
    return result


def _action_references(connection):
    references = {}
    actions_by_id = {}
    if not _table_exists(connection, "provider_actions"):
        return references, actions_by_id
    rows = connection.execute(
        "SELECT action_id, action_type, status, external_job_id, request_summary_json "
        "FROM provider_actions"
    ).fetchall()
    for source in rows:
        row = dict(source)
        action_type = str(row.get("action_type") or "")
        external = bool(str(row.get("external_job_id") or "").strip()) or (
            action_type not in LOCAL_EVALUATION_ACTION_TYPES
        )
        action = {
            "actionId": str(row.get("action_id") or ""),
            "actionType": action_type,
            "status": str(row.get("status") or ""),
            "external": external,
        }
        if action["actionId"]:
            actions_by_id[action["actionId"]] = action
        for match_id in _match_references(_json_object(row.get("request_summary_json"))):
            references.setdefault(match_id, []).append(action)
    return references, actions_by_id


def _match_reason(row, action_references, actions_by_id):
    if str(row.get("archive_state") or "active") != "active":
        return "MATCH_ALREADY_ARCHIVED"
    if str(row.get("match_status") or "") != "candidate":
        return "MATCH_NOT_CANDIDATE"
    if str(row.get("trigger_action_id") or "").strip():
        return "MATCH_TRIGGERED"
    if str(row.get("download_action_id") or "").strip():
        return "MATCH_DOWNLOAD_LINKED"
    evaluation_action_id = str(row.get("evaluation_action_id") or "").strip()
    if evaluation_action_id:
        evaluation_action = actions_by_id.get(evaluation_action_id)
        if not evaluation_action:
            return "MATCH_EVALUATION_ACTION_UNVERIFIED"
        if evaluation_action.get("external"):
            return "MATCH_EXTERNAL_ACTION_LINKED"
    if any(reference.get("external") for reference in action_references):
        return "MATCH_EXTERNAL_ACTION_LINKED"
    return ""


def _plan(connection):
    item_rows = [dict(row) for row in connection.execute(
        "SELECT id, fingerprint, guid, title, category, media_type, season_number, "
        "episode_start, episode_end FROM rss_items ORDER BY id"
    ).fetchall()]
    match_rows = [dict(row) for row in connection.execute(
        "SELECT * FROM rss_subscription_matches WHERE archive_state='active' ORDER BY item_id, id"
    ).fetchall()]
    actions, actions_by_id = _action_references(connection)
    matches_by_item = {}
    for row in match_rows:
        matches_by_item.setdefault(str(row.get("item_id") or ""), []).append(row)

    changes = []
    change_types = {
        "repeatedSeasonRange": 0,
        "codecFalsePositive": 0,
        "scopeChanged": 0,
    }
    for row in item_rows:
        before = _scope(row)
        after = _parsed_scope(row)
        if before == after:
            continue
        kind = _change_type(row, before, after)
        change_types[{
            "repeated_season_range": "repeatedSeasonRange",
            "codec_false_positive": "codecFalsePositive",
            "scope_changed": "scopeChanged",
        }[kind]] += 1
        item_matches = matches_by_item.get(str(row["id"]), [])
        match_facts = []
        review_reasons = set()
        for match in item_matches:
            refs = actions.get(str(match.get("id") or ""), [])
            reason = _match_reason(match, refs, actions_by_id)
            if reason:
                review_reasons.add(reason)
            match_facts.append({
                "id": str(match.get("id") or ""),
                "version": int(match.get("version") or 1),
                "status": str(match.get("match_status") or ""),
                "archiveState": str(match.get("archive_state") or "active"),
                "triggerAction": bool(str(match.get("trigger_action_id") or "").strip()),
                "evaluationAction": bool(str(match.get("evaluation_action_id") or "").strip()),
                "downloadAction": bool(str(match.get("download_action_id") or "").strip()),
                "actionRefs": sorted(
                    (
                        str(reference.get("actionType") or ""),
                        str(reference.get("status") or ""),
                        bool(reference.get("external")),
                    )
                    for reference in refs
                ),
                "reasonCode": reason,
            })
        disposition = "needs_review" if review_reasons else (
            "safe_without_matches" if not item_matches else "safe_rematch"
        )
        changes.append({
            "itemId": str(row["id"]),
            "itemFingerprintHash": hashlib.sha256(
                str(row.get("fingerprint") or "").encode("utf-8")
            ).hexdigest(),
            "guidHash": hashlib.sha256(str(row.get("guid") or "").encode("utf-8")).hexdigest(),
            "titleHash": hashlib.sha256(str(row.get("title") or "").encode("utf-8")).hexdigest(),
            "categoryHash": hashlib.sha256(str(row.get("category") or "").encode("utf-8")).hexdigest(),
            "before": before,
            "after": after,
            "changeType": kind,
            "disposition": disposition,
            "reviewReasons": sorted(review_reasons),
            "matches": match_facts,
        })

    fingerprint_facts = [{
        key: change[key]
        for key in (
            "itemId", "itemFingerprintHash", "guidHash", "titleHash", "categoryHash",
            "before", "after", "changeType", "disposition", "reviewReasons", "matches",
        )
    } for change in changes]
    fingerprint = hashlib.sha256(_json({
        "repairVersion": REPAIR_VERSION,
        "changes": fingerprint_facts,
    }).encode("utf-8")).hexdigest()
    safe = [change for change in changes if change["disposition"] != "needs_review"]
    review = [change for change in changes if change["disposition"] == "needs_review"]
    safe_matches = sum(len(change["matches"]) for change in safe)
    review_matches = sum(len(change["matches"]) for change in review)
    return {
        "repairVersion": REPAIR_VERSION,
        "fingerprint": fingerprint,
        "changes": changes,
        "counts": {
            "scannedItems": len(item_rows),
            "changedItems": len(changes),
            "safeItems": len(safe),
            "needsReviewItems": len(review),
            "unmatchedItems": sum(
                1 for change in safe if change["disposition"] == "safe_without_matches"
            ),
            "affectedMatches": safe_matches + review_matches,
            "eligibleMatches": safe_matches,
            "needsReviewMatches": review_matches,
            "changeTypes": change_types,
        },
    }


def _public_plan(plan):
    return {
        "status": "preview",
        "repairVersion": REPAIR_VERSION,
        "previewFingerprint": plan["fingerprint"],
        "canApply": plan["counts"]["safeItems"] > 0,
        "counts": plan["counts"],
    }


def _backup_database(repository, fingerprint, clock):
    database_path = Path(repository.runtime.database_path)
    stamp = clock().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = database_path.parent / "migrations"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / (
        f"{REPAIR_VERSION}.{stamp}.{fingerprint[:12]}.{uuid.uuid4().hex[:8]}.sqlite3"
    )
    temporary_path = backup_path.with_suffix(".sqlite3.tmp")
    try:
        with closing(repository.runtime.connect()) as source, closing(sqlite3.connect(temporary_path)) as target:
            source.backup(target)
        with closing(sqlite3.connect(temporary_path)) as verification:
            integrity = verification.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = verification.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise RuntimeError("backup_validation_failed")
        temporary_path.replace(backup_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        backup_path.unlink(missing_ok=True)
        raise
    return backup_path


def _clear_champion_references(connection, match_ids, now):
    if not match_ids or not _table_exists(connection, "quality_watch_units"):
        return 0
    placeholders = ",".join("?" for _ in match_ids)
    rows = connection.execute(
        "SELECT unit_key, current_evidence_json FROM quality_watch_units "
        f"WHERE best_match_id IN ({placeholders})",
        tuple(match_ids),
    ).fetchall()
    for source in rows:
        row = dict(source)
        evidence = _json_object(row.get("current_evidence_json"))
        evidence.update({
            "candidateDecision": "",
            "bestArtifactKey": "",
            "bestCandidateSummary": {},
        })
        connection.execute(
            "UPDATE quality_watch_units SET best_match_id='', best_candidate_score=NULL, "
            "current_evidence_json=?, updated_at=?, version=version+1 WHERE unit_key=?",
            (_json(evidence), now, row["unit_key"]),
        )
    return len(rows)


class RssScopeRepairService:
    def __init__(self, repository, *, backup_creator=None, clock=None):
        self.repository = repository
        self.backup_creator = backup_creator or _backup_database
        self.clock = clock or _utc_now

    def preview(self):
        with closing(self.repository.runtime.connect()) as connection:
            return _public_plan(_plan(connection))

    def _successful_replay(self, fingerprint):
        with closing(self.repository.runtime.connect()) as connection:
            row = connection.execute(
                "SELECT result_json FROM rss_scope_repair_runs "
                "WHERE fingerprint=? AND status='succeeded' ORDER BY created_at DESC LIMIT 1",
                (fingerprint,),
            ).fetchone()
        if not row:
            return None
        result = _json_object(row["result_json"])
        return {**result, "replayed": True}

    def _record_failure(self, fingerprint, code, backup_ref=""):
        run_id = f"rss-scope-repair:{uuid.uuid4().hex[:24]}"
        now = _iso(self.clock())
        try:
            with self.repository.runtime.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO rss_scope_repair_runs "
                    "(run_id, status, fingerprint, backup_ref, result_json, error_code, created_at) "
                    "VALUES (?, 'failed', ?, ?, '{}', ?, ?)",
                    (run_id, fingerprint, str(backup_ref or ""), str(code or "")[:80], now),
                )
        except Exception:
            pass

    def apply(self, preview_fingerprint):
        fingerprint = str(preview_fingerprint or "").strip().lower()
        if not FINGERPRINT_PATTERN.fullmatch(fingerprint):
            raise RssScopeRepairError("RSS_SCOPE_REPAIR_FINGERPRINT_INVALID", "Invalid preview fingerprint")

        replay = self._successful_replay(fingerprint)
        if replay:
            return replay

        with closing(self.repository.runtime.connect()) as connection:
            initial = _plan(connection)
        if initial["fingerprint"] != fingerprint:
            self._record_failure(fingerprint, "RSS_SCOPE_REPAIR_PREVIEW_STALE")
            raise RssScopeRepairError("RSS_SCOPE_REPAIR_PREVIEW_STALE", "Preview is stale")
        if initial["counts"]["safeItems"] == 0:
            return {
                **_public_plan(initial),
                "status": "not_needed",
                "applied": False,
                "replayed": False,
                "backupCreated": False,
            }

        try:
            backup_path = self.backup_creator(self.repository, fingerprint, self.clock)
        except Exception as exc:
            self._record_failure(fingerprint, "RSS_SCOPE_REPAIR_BACKUP_FAILED")
            raise RssScopeRepairError(
                "RSS_SCOPE_REPAIR_BACKUP_FAILED", "Database backup failed"
            ) from exc

        run_id = f"rss-scope-repair:{uuid.uuid4().hex[:24]}"
        backup_ref = Path(backup_path).name
        try:
            with self.repository.runtime.transaction(immediate=True) as connection:
                current = _plan(connection)
                if current["fingerprint"] != fingerprint:
                    raise RssScopeRepairError(
                        "RSS_SCOPE_REPAIR_PREVIEW_STALE", "Preview changed after backup"
                    )
                now = _iso(self.clock())
                updated_items = archived_matches = cleared_champions = 0
                for change in current["changes"]:
                    if change["disposition"] == "needs_review":
                        continue
                    after = change["after"]
                    cursor = connection.execute(
                        "UPDATE rss_items SET media_type=?, season_number=?, episode_start=?, episode_end=?, "
                        "match_checked_at='' WHERE id=? AND media_type IS ? AND season_number IS ? "
                        "AND episode_start IS ? AND episode_end IS ?",
                        (
                            after["mediaType"], after["seasonNumber"], after["episodeStart"],
                            after["episodeEnd"], change["itemId"], change["before"]["mediaType"],
                            change["before"]["seasonNumber"], change["before"]["episodeStart"],
                            change["before"]["episodeEnd"],
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RssScopeRepairError(
                            "RSS_SCOPE_REPAIR_PREVIEW_STALE", "RSS item changed during repair"
                        )
                    updated_items += 1
                    match_ids = [match["id"] for match in change["matches"]]
                    if match_ids:
                        cleared_champions += _clear_champion_references(connection, match_ids, now)
                        placeholders = ",".join("?" for _ in match_ids)
                        cursor = connection.execute(
                            "UPDATE rss_subscription_matches SET archive_state='archived', archived_at=?, "
                            "archive_reason_code=?, archive_run_id=?, updated_at=?, version=version+1 "
                            f"WHERE id IN ({placeholders}) AND archive_state='active' "
                            "AND match_status='candidate' AND trigger_action_id='' AND download_action_id=''",
                            (now, REPAIR_VERSION, run_id, now, *match_ids),
                        )
                        if cursor.rowcount != len(match_ids):
                            raise RssScopeRepairError(
                                "RSS_SCOPE_REPAIR_PREVIEW_STALE", "RSS matches changed during repair"
                            )
                        archived_matches += cursor.rowcount

                result = {
                    "status": "succeeded",
                    "repairVersion": REPAIR_VERSION,
                    "runId": run_id,
                    "previewFingerprint": fingerprint,
                    "applied": True,
                    "replayed": False,
                    "backupCreated": True,
                    "backupRef": backup_ref,
                    "updatedItems": updated_items,
                    "archivedMatches": archived_matches,
                    "clearedChampions": cleared_champions,
                    "needsReviewItems": current["counts"]["needsReviewItems"],
                    "counts": current["counts"],
                    "appliedAt": now,
                }
                connection.execute(
                    "INSERT INTO rss_scope_repair_runs "
                    "(run_id, status, fingerprint, backup_ref, result_json, error_code, created_at) "
                    "VALUES (?, 'succeeded', ?, ?, ?, '', ?)",
                    (run_id, fingerprint, backup_ref, _json(result), now),
                )
                connection.executemany(
                    "INSERT INTO rss_scope_repair_items "
                    "(run_id, item_id, disposition, reason_code, before_scope_json, after_scope_json, "
                    "match_count, match_ids_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        (
                            run_id,
                            change["itemId"],
                            change["disposition"],
                            ",".join(change["reviewReasons"]),
                            _json(change["before"]),
                            _json(change["after"]),
                            len(change["matches"]),
                            _json([match["id"] for match in change["matches"]]),
                        )
                        for change in current["changes"]
                    ),
                )
                return result
        except Exception as exc:
            code = getattr(exc, "code", "RSS_SCOPE_REPAIR_FAILED")
            self._record_failure(fingerprint, code, backup_ref)
            if isinstance(exc, RssScopeRepairError):
                raise
            raise RssScopeRepairError("RSS_SCOPE_REPAIR_FAILED", "RSS scope repair failed") from exc
