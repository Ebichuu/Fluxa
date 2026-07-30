from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from app.quality_watch_bridge_runtime import BRIDGE_VERSION, _project_facts, _subscriptions
from app.quality_watch_repository import QualityWatchVersionConflict, make_unit_key
from app.quality_watch_runtime import plan_reconcile, resolve_watch_policy


IDEMPOTENCY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{12,128}")
FINGERPRINT_PATTERN = re.compile(r"[a-f0-9]{64}")
MAX_BATCH_SIZE = 200


class BaselineInitializationError(RuntimeError):
    def __init__(self, code, message, status):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.status = int(status)


def _text(value):
    return str(value or "").strip()


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


def _stable_hash(value):
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _public_target_id(subscription_key, target_key):
    return f"baseline:{_stable_hash([subscription_key, target_key])[:24]}"


def _public_group_id(subscription_key):
    return f"baseline-group:{_stable_hash(subscription_key)[:24]}"


def _artifact_ref(artifact_key):
    return f"artifact:{_stable_hash(artifact_key)[:24]}"


def _subscription_season(subscription):
    try:
        return int(subscription.get("target_season") or subscription.get("season_number") or 0)
    except (TypeError, ValueError):
        return 0


def _identity_classification(subscription, facts, season, episode):
    subscription = subscription or {}
    subscription_tmdb = _text(subscription.get("tmdb_id"))
    if (
        not subscription
        or any(fact["identity_state"] != "linked" for fact in facts)
        or any(fact["media_type"] != "tv" or not fact["tmdb_id"].isdigit() for fact in facts)
        or season <= 0
        or episode <= 0
        or not _text(subscription.get("torra_remote_id"))
    ):
        return "needs_review", "identity_incomplete"
    if (
        _text(subscription.get("media_type")).lower() != "tv"
        or not subscription_tmdb.isdigit()
        or any(fact["tmdb_id"] != subscription_tmdb for fact in facts)
        or (_subscription_season(subscription) > 0 and _subscription_season(subscription) != season)
    ):
        return "needs_review", "identity_conflict"
    return "safe_to_initialize", "eligible"


def _evidence_summary(facts):
    symedia = [fact for fact in facts if fact["stage"] == "symedia" and fact["artifact_key"]]
    downloads = [
        fact for fact in facts
        if fact["stage"] in {"torra", "qb"} and fact["artifact_key"]
    ]
    preferred = symedia or downloads
    artifacts = sorted({fact["artifact_key"] for fact in preferred})
    reason = ""
    if not preferred:
        reason = "success_file_missing"
    elif len(artifacts) != 1:
        reason = "success_file_conflict"
    occurred = sorted(
        [
            (_utc(fact["upstream_occurred_at"]), fact)
            for fact in preferred if _utc(fact["upstream_occurred_at"])
        ],
        key=lambda row: (row[0], row[1]["stage"], row[1]["evidence_version"]),
    )
    if len(occurred) != len(preferred):
        reason = "success_time_missing"
    download_times = [
        _utc(fact["upstream_occurred_at"])
        for fact in downloads if _utc(fact["upstream_occurred_at"])
    ]
    first_at = min(download_times) if download_times else (occurred[0][0] if occurred else None)
    baseline_at = occurred[-1][0] if occurred else None
    if first_at and baseline_at and baseline_at < first_at:
        reason = "success_time_inverted"
    return {
        "artifacts": artifacts,
        "baselineFact": occurred[-1][1] if occurred else None,
        "firstAt": first_at,
        "baselineAt": baseline_at,
        "reasonCode": reason,
    }


def _persisted_fact(row, subscriptions):
    subscription_key = _text(row.get("subscriptionId"))
    subscription = subscriptions.get(subscription_key) or {}
    target = _text(row.get("ownerTargetKey"))
    artifact = _text(row.get("artifactKey"))
    return {
        "stage": _text(row.get("stage")),
        "fact_type": _text(row.get("factType")),
        "owner_target_key": target,
        "artifact_key": artifact,
        "source_result_id": _text(row.get("sourceResultId")),
        "upstream_occurred_at": _text(row.get("upstreamOccurredAt")),
        "media_type": _text(row.get("mediaType")),
        "tmdb_id": _text(row.get("tmdbId")),
        "season_number": int(row.get("seasonNumber") or 0),
        "episode_number": int(row.get("episodeNumber") or 0),
        "identity_state": _text(row.get("identityState")),
        "subscription_key": subscription_key,
        "torra_subscription_id": _text(subscription.get("torra_remote_id")),
        "owners": [target] if target and artifact else [],
        "evidence_version": _text(row.get("evidenceVersion")),
        "ownership_version": _text(row.get("ownershipVersion")),
    }


class QualityWatchBaselineInitializationService:
    def __init__(
        self,
        repository,
        resource_repository,
        quality_runtime,
        *,
        snapshot_loader,
        subscription_loader,
        config_loader=None,
        clock=None,
    ):
        self.repository = repository
        self.resource_repository = resource_repository
        self.quality_runtime = quality_runtime
        self.snapshot_loader = snapshot_loader
        self.subscription_loader = subscription_loader
        self.config_loader = config_loader or (lambda: {})
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _facts(self, subscriptions):
        snapshot = self.snapshot_loader()
        facts = list(_project_facts(snapshot if isinstance(snapshot, dict) else {}))
        if self.resource_repository:
            rows = self.resource_repository.list_quality_watch_success_evidence(limit=10000)
            facts.extend(_persisted_fact(row, subscriptions) for row in rows if isinstance(row, dict))
        unique = {}
        for fact in facts:
            key = (
                fact["stage"], fact["owner_target_key"], fact["artifact_key"],
                _iso(fact["upstream_occurred_at"]),
            )
            current = unique.get(key)
            if current is None or fact["evidence_version"] > current["evidence_version"]:
                unique[key] = fact
        return list(unique.values())

    def _classify(self):
        subscriptions = _subscriptions(self.subscription_loader())
        config = self.config_loader()
        existing = {
            unit["unit_key"]
            for subscription_key in subscriptions
            for unit in self.repository.list_watch_units(subscription_key)
        }
        grouped = {}
        for fact in self._facts(subscriptions):
            key = (
                fact["subscription_key"], fact["season_number"], fact["episode_number"],
                fact["owner_target_key"],
            )
            grouped.setdefault(key, []).append(fact)
        candidates = []
        for (subscription_key, season, episode, target), facts in sorted(grouped.items()):
            subscription = subscriptions.get(subscription_key)
            public_id = _public_target_id(subscription_key, target)
            category, reason = _identity_classification(subscription, facts, season, episode)
            unit_key = ""
            if subscription_key and season > 0 and episode > 0:
                unit_key = make_unit_key(subscription_key, "tv", season, episode)
            if unit_key in existing:
                category, reason = "skipped", "observation_unit_exists"
            if any(fact["owners"] != [target] for fact in facts):
                category, reason = "needs_review", "artifact_owner_unconfirmed"
            evidence = _evidence_summary(facts)
            if evidence["reasonCode"]:
                category, reason = "needs_review", evidence["reasonCode"]
            try:
                policy = resolve_watch_policy(subscription or {}, config)
            except ValueError:
                policy = None
                category, reason = "needs_review", "policy_invalid"
            policy_version = _stable_hash(policy or {})
            evidence_version = _stable_hash(sorted(fact["evidence_version"] for fact in facts))
            ownership_version = _stable_hash(sorted(fact["ownership_version"] for fact in facts))
            artifacts = evidence["artifacts"]
            baseline_fact = evidence["baselineFact"]
            candidates.append({
                "id": public_id,
                "category": category,
                "reasonCode": reason,
                "subscriptionKey": subscription_key,
                "subscriptionTitle": _text((subscription or {}).get("title") or (subscription or {}).get("name")),
                "ownerTargetKey": target,
                "tmdbId": _text((subscription or {}).get("tmdb_id")),
                "seasonNumber": season,
                "episodeNumber": episode,
                "artifactKey": artifacts[0] if len(artifacts) == 1 else "",
                "artifactRef": _artifact_ref(artifacts[0]) if len(artifacts) == 1 else "",
                "evidenceSource": baseline_fact["stage"] if baseline_fact else "",
                "firstSuccessAt": _iso(evidence["firstAt"]),
                "baselineReadyAt": _iso(evidence["baselineAt"]),
                "evidenceVersion": evidence_version,
                "ownershipVersion": ownership_version,
                "policyVersion": policy_version,
                "policy": policy,
                "subscription": subscription,
            })
        return candidates

    @staticmethod
    def _public_item(item):
        return {
            key: item[key]
            for key in (
                "id", "category", "reasonCode", "subscriptionTitle", "tmdbId",
                "seasonNumber", "episodeNumber", "artifactRef", "evidenceSource",
                "firstSuccessAt", "baselineReadyAt",
            )
        }

    def _preview_data(self):
        candidates = self._classify()
        counts = {
            "safeToInitialize": sum(item["category"] == "safe_to_initialize" for item in candidates),
            "needsReview": sum(item["category"] == "needs_review" for item in candidates),
            "skipped": sum(item["category"] == "skipped" for item in candidates),
            "alreadyExisting": sum(item["reasonCode"] == "observation_unit_exists" for item in candidates),
            "insufficientEvidence": sum(item["category"] == "needs_review" for item in candidates),
            "conflicts": sum("conflict" in item["reasonCode"] or "owner" in item["reasonCode"] for item in candidates),
        }
        reason_counts = {}
        for item in candidates:
            reason_counts[item["reasonCode"]] = reason_counts.get(item["reasonCode"], 0) + 1
        groups = {}
        for item in candidates:
            group_key = (item["subscriptionKey"], item["seasonNumber"])
            groups.setdefault(group_key, {
                "id": _public_group_id(item["subscriptionKey"]),
                "subscriptionTitle": item["subscriptionTitle"],
                "seasonNumber": item["seasonNumber"],
                "items": [],
            })["items"].append(self._public_item(item))
        fingerprint_rows = [{
            "id": item["id"], "target": item["ownerTargetKey"], "artifact": item["artifactKey"],
            "evidenceVersion": item["evidenceVersion"], "ownershipVersion": item["ownershipVersion"],
            "successTime": item["baselineReadyAt"], "policyVersion": item["policyVersion"],
            "category": item["category"], "reason": item["reasonCode"],
        } for item in candidates]
        fingerprint = _stable_hash({
            "bridgeVersion": BRIDGE_VERSION,
            "targets": sorted(fingerprint_rows, key=lambda row: row["id"]),
        })
        return {
            "fingerprint": fingerprint,
            "counts": counts,
            "reasonCounts": reason_counts,
            "groups": [groups[key] for key in sorted(groups)],
            "candidates": candidates,
        }

    def preview(self):
        data = self._preview_data()
        run_id = f"baseline-init:{uuid.uuid4().hex}"
        public = {
            "runId": run_id,
            "status": "previewed",
            "previewFingerprint": data["fingerprint"],
            "bridgeVersion": BRIDGE_VERSION,
            "generatedAt": _iso(self.clock()),
            "counts": data["counts"],
            "reasonCounts": data["reasonCounts"],
            "groups": data["groups"],
            "maxSelectedTargets": MAX_BATCH_SIZE,
            "requiresConfirmation": True,
        }
        policy_version = _stable_hash(self.config_loader())
        self.repository.create_baseline_init_preview(
            run_id, data["fingerprint"], BRIDGE_VERSION, policy_version, public
        )
        return public

    def get_run(self, run_id):
        run = self.repository.get_baseline_init_run(_text(run_id))
        if not run:
            raise BaselineInitializationError(
                "BASELINE_INITIALIZATION_RUN_NOT_FOUND", "历史基线初始化批次不存在", 404
            )
        public = run["response"] if run["status"] == "applied" else run["preview"]
        result = {
            **public,
            "runId": run["run_id"],
            "status": run["status"],
            "previewFingerprint": run["preview_fingerprint"],
            "bridgeVersion": run["bridge_version"],
            "createdAt": run["created_at"],
            "updatedAt": run["updated_at"],
            "completedAt": run["completed_at"],
        }
        if run["status"] == "applied":
            result["items"] = [{
                "id": item["public_target_id"],
                "artifactRef": item["artifact_ref"],
                "seasonNumber": int(item["season_number"]),
                "episodeNumber": int(item["episode_number"]),
                "evidenceSource": item["evidence_source"],
                "firstSuccessAt": item["first_success_at"],
                "baselineReadyAt": item["baseline_ready_at"],
                "result": item["result"],
                "reasonCode": item["reason_code"],
            } for item in self.repository.list_baseline_init_items(run["run_id"])]
        return result

    @staticmethod
    def _validate_body(body):
        body = body if isinstance(body, dict) else {}
        allowed = {"confirm", "runId", "previewFingerprint", "selectedTargetIds", "idempotencyKey"}
        if set(body) - allowed:
            raise BaselineInitializationError("BASELINE_INITIALIZATION_FIELDS_INVALID", "请求包含不支持的字段", 422)
        if body.get("confirm") is not True:
            raise BaselineInitializationError("BASELINE_INITIALIZATION_CONFIRM_REQUIRED", "需要明确确认历史基线初始化", 422)
        run_id = _text(body.get("runId"))
        fingerprint = _text(body.get("previewFingerprint")).lower()
        idempotency_key = _text(body.get("idempotencyKey"))
        selected = sorted({_text(value) for value in body.get("selectedTargetIds") or [] if _text(value)})
        if not run_id:
            raise BaselineInitializationError("BASELINE_INITIALIZATION_RUN_INVALID", "预览批次无效", 422)
        if not FINGERPRINT_PATTERN.fullmatch(fingerprint):
            raise BaselineInitializationError("BASELINE_INITIALIZATION_FINGERPRINT_INVALID", "预览指纹无效", 422)
        if not IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
            raise BaselineInitializationError("BASELINE_INITIALIZATION_IDEMPOTENCY_INVALID", "幂等键无效", 422)
        if not selected:
            raise BaselineInitializationError("BASELINE_INITIALIZATION_SELECTION_EMPTY", "至少选择一个目标", 422)
        if len(selected) > MAX_BATCH_SIZE:
            raise BaselineInitializationError("BASELINE_INITIALIZATION_SELECTION_TOO_LARGE", "单批最多选择 200 个目标", 422)
        return run_id, fingerprint, idempotency_key, selected

    def _mark_failed(self, run_id, status, code):
        try:
            self.repository.mark_baseline_init_run(run_id, status, {"code": code})
        except Exception:
            pass

    def _validated_candidates(self, run_id, fingerprint, selected):
        run = self.repository.get_baseline_init_run(run_id)
        if not run:
            raise BaselineInitializationError(
                "BASELINE_INITIALIZATION_RUN_NOT_FOUND", "预览批次不存在", 404
            )
        if run["status"] != "previewed" or run["preview_fingerprint"] != fingerprint:
            raise BaselineInitializationError(
                "BASELINE_INITIALIZATION_PREVIEW_STALE", "预览已过期，请重新预览", 409
            )
        current = self._preview_data()
        if current["fingerprint"] != fingerprint:
            self._mark_failed(run_id, "stale", "PREVIEW_CHANGED")
            raise BaselineInitializationError(
                "BASELINE_INITIALIZATION_PREVIEW_STALE", "证据已变化，请重新预览", 409
            )
        candidates = {item["id"]: item for item in current["candidates"]}
        selection_changed = any(
            target not in candidates
            or candidates[target]["category"] != "safe_to_initialize"
            for target in selected
        )
        if selection_changed:
            self._mark_failed(run_id, "stale", "SELECTION_CHANGED")
            raise BaselineInitializationError(
                "BASELINE_INITIALIZATION_PREVIEW_STALE", "选中目标已变化，请重新预览", 409
            )
        return current, candidates

    @staticmethod
    def _target_plan(item, existing, now):
        remote_id = _text(item["subscription"].get("torra_remote_id"))
        task_item = {
            "mediaType": "tv",
            "tmdbId": item["tmdbId"],
            "seasonNumber": item["seasonNumber"],
            "episodeNumber": item["episodeNumber"],
            "steps": [{
                "key": "download",
                "status": "done",
                "evidence": "verified",
                "source": item["evidenceSource"],
                "timestamp": item["firstSuccessAt"],
            }],
            "sourceIds": {
                "subscriptionId": item["subscriptionKey"],
                "torraId": remote_id,
            },
        }
        return plan_reconcile(
            now=now,
            subscription=item["subscription"],
            task_item=task_item,
            torra_row={
                "id": remote_id,
                "media_type": "tv",
                "tmdb_id": item["tmdbId"],
                "season_number": item["seasonNumber"],
            },
            evidence={
                "is_new": True,
                "episode_numbers": [item["episodeNumber"]],
                "first_download_at": item["firstSuccessAt"],
                "baseline_ready_at": item["baselineReadyAt"],
                "baseline_success": True,
                "baseline_episode_numbers": [item["episodeNumber"]],
                "time_source": f"{item['evidenceSource']}_completed",
                "require_reliable_times": True,
            },
            policy=item["policy"],
            existing_units=existing,
        )

    def _apply_target(self, connection, target_id, item, now):
        existing = self.repository.list_watch_units_in_connection(
            connection, item["subscriptionKey"]
        )
        unit_key = make_unit_key(
            item["subscriptionKey"], "tv", item["seasonNumber"], item["episodeNumber"]
        )
        if any(unit["unit_key"] == unit_key for unit in existing):
            raise QualityWatchVersionConflict("historical target changed")
        plan = self._target_plan(item, existing, now)
        if plan["status"] in {"blocked", "needs_review", "ignored"}:
            raise QualityWatchVersionConflict(plan["reason"] or "historical target invalid")
        self.repository.apply_reconcile_plan(connection, plan, now=now)
        unit_write = next(
            (write for write in plan["writes"] if write["unitKey"] == unit_key),
            None,
        )
        if not unit_write:
            raise QualityWatchVersionConflict("historical target produced no write")
        state = unit_write["values"].get("state", "")
        return {
            "publicTargetId": target_id,
            "ownerTargetKey": item["ownerTargetKey"],
            "artifactRef": item["artifactRef"],
            "seasonNumber": item["seasonNumber"],
            "episodeNumber": item["episodeNumber"],
            "evidenceSource": item["evidenceSource"],
            "firstSuccessAt": item["firstSuccessAt"],
            "baselineReadyAt": item["baselineReadyAt"],
            "result": "observation_expired" if state == "observation_expired" else "initialized",
            "reasonCode": "",
        }

    def _apply_batch(self, execution, current, candidates, now):
        run_id = execution["runId"]
        idempotency_key = execution["idempotencyKey"]
        selected = execution["selectedTargetIds"]
        plan_items = []
        with self.repository.runtime.transaction(immediate=True) as connection:
            for target_id in selected:
                plan_items.append(
                    self._apply_target(connection, target_id, candidates[target_id], now)
                )
            expired = sum(item["result"] == "observation_expired" for item in plan_items)
            response = {
                "runId": run_id,
                "status": "applied",
                "initialized": len(plan_items) - expired,
                "processed": len(plan_items),
                "alreadyExisting": current["counts"]["alreadyExisting"],
                "insufficientEvidence": current["counts"]["insufficientEvidence"],
                "expired": expired,
                "conflicts": current["counts"]["conflicts"],
                "reasonCounts": current["reasonCounts"],
                "replayed": False,
            }
            self.repository.apply_baseline_init_run(
                connection,
                run_id,
                idempotency_key,
                selected,
                plan_items,
                response,
                now=now,
            )
        return response, plan_items

    def execute(self, body):
        run_id, fingerprint, idempotency_key, selected = self._validate_body(body)
        replay = self.repository.get_baseline_init_run(idempotency_key=idempotency_key)
        if replay:
            if replay["run_id"] != run_id or replay["preview_fingerprint"] != fingerprint:
                raise BaselineInitializationError("BASELINE_INITIALIZATION_IDEMPOTENCY_CONFLICT", "幂等键已用于其他批次", 409)
            return {**replay["response"], "replayed": True}
        current, candidates = self._validated_candidates(run_id, fingerprint, selected)
        now = self.clock()
        try:
            response, plan_items = self._apply_batch(
                {
                    "runId": run_id,
                    "idempotencyKey": idempotency_key,
                    "selectedTargetIds": selected,
                },
                current,
                candidates,
                now,
            )
        except Exception as exc:
            conflict = isinstance(exc, QualityWatchVersionConflict)
            self._mark_failed(run_id, "stale" if conflict else "failed", "APPLY_FAILED")
            code = (
                "BASELINE_INITIALIZATION_PREVIEW_STALE"
                if conflict else "BASELINE_INITIALIZATION_FAILED"
            )
            status = 409 if isinstance(exc, QualityWatchVersionConflict) else 500
            raise BaselineInitializationError(code, "历史基线初始化未执行，请重新预览", status) from exc
        for item in plan_items:
            self.quality_runtime._backfill_candidates(
                self.repository.get_watch_unit(make_unit_key(
                    candidates[item["publicTargetId"]]["subscriptionKey"], "tv",
                    item["seasonNumber"], item["episodeNumber"],
                ))
            )
        return response
