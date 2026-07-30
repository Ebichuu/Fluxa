from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from app.quality_watch_repository import make_unit_key
from app.quality_watch_runtime import plan_reconcile, resolve_watch_policy
from app.resource_identity_runtime import artifact_key as canonical_artifact_key


BRIDGE_VERSION = "2"
SUCCESS_STAGES = {"torra", "qb", "symedia"}
EPISODE_EVIDENCE_STAGES = {
    "torra": "download",
    "qb": "download",
    "symedia": "library",
}


def _text(value):
    return str(value or "").strip()


def _integer(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _utc(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _iso(value):
    parsed = _utc(value)
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z") if parsed else ""


def _hash(prefix, value, length=32):
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:length]
    return f"{prefix}:{digest}"


def _stable_hash(value):
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _subscription_key(row):
    for key in ("key", "subscription_key", "id"):
        value = _text(row.get(key))
        if value:
            return value
    return ""


def _subscriptions(value):
    rows = value.get("items") if isinstance(value, dict) else value
    rows = rows if isinstance(rows, list) else []
    return {_subscription_key(row): row for row in rows if isinstance(row, dict) and _subscription_key(row)}


def _fact_units(fact):
    units = [row for row in fact.get("units") or [] if isinstance(row, dict)]
    return units or [fact]


def _artifact_candidates(stage, value):
    value = _text(value)
    if not value:
        return []
    result = [value] if value.startswith("artifact:") else []
    if stage == "qb":
        result.append(canonical_artifact_key(qb_hash=value))
    elif stage == "symedia":
        result.extend((
            canonical_artifact_key(remote_file_id=f"symedia:{value}"),
            canonical_artifact_key(remote_file_id=value),
        ))
    elif stage == "torra":
        result.append(canonical_artifact_key(remote_file_id=f"torra:{value}"))
    return list(dict.fromkeys(result))


def _artifact_for_fact(item, fact, unit, ownership):
    stage = _text(fact.get("stage"))
    source_ids = item.get("sourceIds") if isinstance(item.get("sourceIds"), dict) else {}
    allowed_source_ids = {
        "qb": {_text(value) for value in source_ids.get("qbHashes") or [] if _text(value)},
        "symedia": {_text(value) for value in source_ids.get("symediaIds") or [] if _text(value)},
        "torra": {_text(source_ids.get("torraId"))} if _text(source_ids.get("torraId")) else set(),
    }.get(stage, set())
    ownership_artifacts = {
        _text(row.get("artifactKey"))
        for row in ownership
        if _text(row.get("artifactKey"))
    }
    item_artifacts = {
        _text(value) for value in item.get("artifactKeys") or [] if _text(value)
    }
    identities = list(dict.fromkeys(
        _text(value)
        for value in (
            unit.get("unitKey"),
            unit.get("sourceRef"),
            unit.get("resultRef"),
            fact.get("unitKey"),
            fact.get("sourceRef"),
            fact.get("resultRef"),
        )
        if _text(value)
    ))
    ownership_candidates = {
        candidate
        for identity in identities
        for candidate in _artifact_candidates(stage, identity)
        if candidate in ownership_artifacts
        and (identity.startswith("artifact:") or identity in allowed_source_ids)
    }
    if len(ownership_candidates) == 1:
        return next(iter(ownership_candidates))
    if len(ownership_candidates) > 1:
        return ""
    item_candidates = {
        candidate
        for identity in identities
        for candidate in _artifact_candidates(stage, identity)
        if candidate in item_artifacts
        and (identity.startswith("artifact:") or identity in allowed_source_ids)
    }
    if len(item_candidates) == 1:
        return next(iter(item_candidates))
    return ""


def _episode_targets(item, artifact_key, stage):
    item_season = _integer(item.get("seasonNumber"))
    item_episode = _integer(item.get("episodeNumber"))
    target_key = _text(item.get("targetKey") or item.get("ownerTargetKey"))
    targets = set()
    if item_season > 0 and item_episode > 0:
        return [(item_season, item_episode)]
    expected_stage = EPISODE_EVIDENCE_STAGES.get(stage)
    for row in item.get("episodeEvidence") or []:
        if (
            not isinstance(row, dict)
            or _text(row.get("artifactKey")) != artifact_key
            or _text(row.get("stage")) != expected_stage
            or _text(row.get("status")) != "done"
        ):
            continue
        parent_target = _text(row.get("parentTargetKey"))
        owner_target = _text(row.get("ownerTargetKey"))
        if not owner_target or (parent_target and parent_target != target_key):
            continue
        season = _integer(row.get("seasonNumber"))
        start = _integer(row.get("episodeStart"))
        end = _integer(row.get("episodeEnd"))
        if (
            season <= 0
            or (item_season > 0 and season != item_season)
            or start <= 0
            or end < start
            or end - start > 200
        ):
            continue
        targets.update((season, episode) for episode in range(start, end + 1))
    return sorted(targets) or [(item_season, item_episode)]


def _project_facts(payload):
    projected = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        source_ids = item.get("sourceIds") if isinstance(item.get("sourceIds"), dict) else {}
        ownership = [row for row in item.get("evidenceOwnership") or [] if isinstance(row, dict)]
        for fact in item.get("pipelineFacts") or []:
            if not isinstance(fact, dict):
                continue
            stage = _text(fact.get("stage"))
            if (
                stage not in SUCCESS_STAGES
                or fact.get("state") != "succeeded"
                or fact.get("evidence") != "verified"
            ):
                continue
            for unit in _fact_units(fact):
                artifact_key = _artifact_for_fact(item, fact, unit, ownership)
                occurred_at = _text(unit.get("eventAt") or fact.get("eventAt"))
                source_result_id = _text(unit.get("sourceRef") or fact.get("sourceRef"))
                target_key = _text(item.get("targetKey") or item.get("ownerTargetKey"))
                owners = sorted({
                    _text(row.get("ownerTargetKey"))
                    for row in ownership
                    if _text(row.get("artifactKey")) == artifact_key and _text(row.get("ownerTargetKey"))
                })
                for season_number, episode_number in _episode_targets(item, artifact_key, stage):
                    projected.append({
                        "stage": stage,
                        "fact_type": "archive_succeeded" if stage == "symedia" else "download_completed",
                        "owner_target_key": target_key,
                        "artifact_key": artifact_key,
                        "source_result_id": source_result_id,
                        "upstream_occurred_at": occurred_at,
                        "media_type": _text(item.get("mediaType")),
                        "tmdb_id": _text(item.get("tmdbId")),
                        "season_number": season_number,
                        "episode_number": episode_number,
                        "identity_state": _text(item.get("identityState")),
                        "subscription_key": _text(item.get("subscriptionId") or source_ids.get("subscriptionId")),
                        "torra_subscription_id": _text(source_ids.get("torraId")),
                        "owners": owners,
                        "item": item,
                        "evidence_version": _stable_hash({
                            "stage": stage, "state": fact.get("state"), "evidence": fact.get("evidence"),
                            "eventAt": occurred_at, "reasonCode": fact.get("reasonCode"),
                            "seasonNumber": season_number, "episodeNumber": episode_number,
                        }),
                        "ownership_version": _stable_hash(ownership),
                    })
    return projected


class QualityWatchBridgeRuntime:
    def __init__(self, repository, quality_runtime, subscription_loader, config_loader=None, clock=None):
        self.repository = repository
        self.quality_runtime = quality_runtime
        self.subscription_loader = subscription_loader or (lambda: [])
        self.config_loader = config_loader or (lambda: {})
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def set_mode(self, mode):
        return self.repository.set_bridge_mode(mode, bridge_version=BRIDGE_VERSION)

    def summary(self):
        state = self.repository.get_bridge_state()
        receipts = self.repository.summarize_bridge_receipts(BRIDGE_VERSION)
        return {
            "bridgeVersion": BRIDGE_VERSION,
            "mode": state["mode"],
            "activatedAt": state["activatedAt"],
            "updatedAt": state["updatedAt"],
            "receiptCounts": receipts["counts"],
            "receiptTotal": receipts["total"],
            "lastReceiptAt": receipts["lastReceiptAt"],
        }

    def _receipt(self, fact):
        source_identity = fact["source_result_id"] or fact["upstream_occurred_at"]
        components = {
            "bridgeVersion": BRIDGE_VERSION,
            "stage": fact["stage"],
            "factType": fact["fact_type"],
            "subscriptionKey": fact["subscription_key"],
            "ownerTargetKey": fact["owner_target_key"],
            "artifactKey": fact["artifact_key"],
            "seasonNumber": fact["season_number"],
            "episodeNumber": fact["episode_number"],
            "sourceIdentity": source_identity,
            "upstreamOccurredAt": _iso(fact["upstream_occurred_at"]),
        }
        key = _stable_hash(components)
        return {
            "receipt_id": f"bridge:{key[:24]}",
            "receipt_key": key,
            "bridge_version": BRIDGE_VERSION,
            "stage": fact["stage"],
            "fact_type": fact["fact_type"],
            "owner_target_key": fact["owner_target_key"],
            "artifact_key": fact["artifact_key"],
            "source_result_ref": _hash("result", fact["source_result_id"]) if fact["source_result_id"] else "",
            "upstream_occurred_at": _iso(fact["upstream_occurred_at"]),
            "evidence_version": fact["evidence_version"],
            "ownership_version": fact["ownership_version"],
        }

    def _classification(self, fact, subscription, state, existing_units):
        if (
            not subscription
            or fact["identity_state"] != "linked"
            or fact["media_type"] != "tv"
            or not fact["tmdb_id"].isdigit()
            or fact["season_number"] <= 0
            or fact["episode_number"] <= 0
        ):
            return "needs_review", "identity_incomplete"
        remote_id = _text(subscription.get("torra_remote_id"))
        if not remote_id or remote_id != fact["torra_subscription_id"]:
            return "needs_review", "torra_binding_unconfirmed"
        if not fact["artifact_key"] or fact["owners"] != [fact["owner_target_key"]]:
            return "needs_review", "artifact_owner_unconfirmed"
        occurred_at = _utc(fact["upstream_occurred_at"])
        if occurred_at is None:
            return "needs_review", "occurred_at_missing"
        if occurred_at > _utc(self.clock()):
            return "needs_review", "occurred_at_in_future"
        activated_at = _utc(state.get("activatedAt"))
        if activated_at is None or occurred_at <= activated_at:
            return "historical", "before_bridge_activation"
        unit_key = make_unit_key(
            fact["subscription_key"], "tv", fact["season_number"], fact["episode_number"]
        )
        if fact["stage"] == "symedia" and not any(unit["unit_key"] == unit_key for unit in existing_units):
            return "historical", "symedia_without_observation_unit"
        return "pending", "eligible"

    @staticmethod
    def _task_item(fact):
        return {
            "mediaType": "tv",
            "tmdbId": fact["tmdb_id"],
            "seasonNumber": fact["season_number"],
            "episodeNumber": fact["episode_number"],
            "steps": [{
                "key": "download", "status": "done", "evidence": "verified",
                "source": "Symedia" if fact["stage"] == "symedia" else fact["stage"],
                "timestamp": fact["upstream_occurred_at"],
            }],
            "sourceIds": {
                "subscriptionId": fact["subscription_key"],
                "torraId": fact["torra_subscription_id"],
            },
        }

    @staticmethod
    def _torra_row(fact):
        return {
            "id": fact["torra_subscription_id"],
            "media_type": "tv",
            "tmdb_id": fact["tmdb_id"],
            "season_number": fact["season_number"],
        }

    def _apply(self, fact, receipt, subscription, policy, state):
        now = self.clock()
        backfill = []
        try:
            with self.repository.runtime.transaction(immediate=True) as connection:
                existing_receipt = self.repository.get_bridge_receipt_in_connection(
                    connection, receipt["receipt_key"]
                )
                if existing_receipt and existing_receipt["status"] in {"applied", "historical", "rejected"}:
                    return existing_receipt["status"]
                if (
                    existing_receipt
                    and existing_receipt["status"] == "retryable_failed"
                    and (retry_at := _utc(existing_receipt.get("next_retry_at")))
                    and retry_at > _utc(now)
                ):
                    return "retryable_failed"
                existing = self.repository.list_watch_units_in_connection(
                    connection, fact["subscription_key"]
                )
                status, reason = self._classification(fact, subscription, state, existing)
                if status != "pending":
                    self.repository.upsert_bridge_receipt(connection, receipt, status, reason)
                    return status
                first_success = next((
                    unit.get("first_success_at") for unit in existing
                    if int(unit.get("season_number") or 0) == fact["season_number"]
                    and int(unit.get("episode_number") or 0) == fact["episode_number"]
                ), fact["upstream_occurred_at"])
                evidence = {
                    "is_new": fact["stage"] in {"torra", "qb"},
                    "episode_numbers": [fact["episode_number"]],
                    "first_download_at": first_success,
                    "upstream_occurred_at": fact["upstream_occurred_at"],
                    "time_source": f"{fact['stage']}_completed",
                    "require_reliable_times": True,
                }
                if fact["stage"] == "symedia":
                    evidence.update({
                        "baseline_success": True,
                        "baseline_episode_numbers": [fact["episode_number"]],
                        "baseline_ready_at": fact["upstream_occurred_at"],
                    })
                plan = plan_reconcile(
                    now=now,
                    subscription=subscription,
                    task_item=self._task_item(fact),
                    torra_row=self._torra_row(fact),
                    evidence=evidence,
                    policy=policy,
                    existing_units=existing,
                )
                if plan["status"] == "needs_review":
                    self.repository.upsert_bridge_receipt(
                        connection, receipt, "needs_review", plan["reason"]
                    )
                    return "needs_review"
                self.repository.apply_reconcile_plan(connection, plan, now=now)
                self.repository.upsert_bridge_receipt(connection, receipt, "applied", "applied")
                backfill = plan["backfillUnitKeys"]
            for unit_key in backfill:
                self.quality_runtime._backfill_candidates(self.repository.get_watch_unit(unit_key))
            return "applied"
        except Exception:
            try:
                self.repository.record_bridge_retryable_failure(
                    receipt,
                    "bridge_apply_failed",
                    next_retry_at=_iso(_utc(now) + timedelta(minutes=5)),
                )
            except Exception:
                pass
            return "retryable_failed"

    def process_snapshot(self, payload):
        state = self.repository.get_bridge_state()
        result = {
            "mode": state["mode"], "processed": 0,
            **{key: 0 for key in ("pending", "applied", "historical", "needs_review", "rejected", "retryable_failed")},
        }
        if state["mode"] == "off":
            return result
        subscriptions = _subscriptions(self.subscription_loader())
        config = self.config_loader()
        for fact in _project_facts(payload if isinstance(payload, dict) else {}):
            result["processed"] += 1
            subscription = subscriptions.get(fact["subscription_key"])
            existing = self.repository.list_watch_units(fact["subscription_key"])
            status, reason = self._classification(fact, subscription, state, existing)
            receipt = self._receipt(fact)
            handled = False
            if state["mode"] == "apply" and status == "pending":
                try:
                    policy = resolve_watch_policy(subscription, config)
                except ValueError:
                    status, reason = "needs_review", "policy_invalid"
                else:
                    status = self._apply(fact, receipt, subscription, policy, state)
                    handled = True
            if not handled:
                with self.repository.runtime.transaction(immediate=True) as connection:
                    stored = self.repository.upsert_bridge_receipt(connection, receipt, status, reason)
                    status = stored["status"]
            result[status] = result.get(status, 0) + 1
        return result
