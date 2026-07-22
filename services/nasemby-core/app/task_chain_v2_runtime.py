from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request

from app.http_runtime import current_request_id
from app.resource_identity_runtime import artifact_key, chain_id, media_key, target_key
from app.task_exception_runtime import classify_stage, classify_task


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _health(item: dict, observed_at: str, fresh_until: str, now=None) -> dict:
    return classify_task(item, now=now, observed_at=observed_at, fresh_until=fresh_until)


def _stage(step: dict, observed_at: str, fresh_until: str, now=None) -> dict:
    status = str(step.get("status") or "unknown")
    result = {
        "stage": str(step.get("key") or "unknown"),
        "label": str(step.get("label") or "未命名阶段"),
        "status": status,
        "healthState": "evidence_insufficient",
        "evidence": str(step.get("evidence") or "missing"),
        "observedAt": str(step.get("timestamp") or observed_at),
        "freshUntil": fresh_until,
        "source": str(step.get("source") or ""),
        "reasonCode": f"{str(step.get('key') or 'task').upper()}_{status.upper()}",
        "reasonText": str(step.get("detail") or ""),
        "actions": {"preview": False, "retry": False},
    }
    result.update(classify_stage(result, now=now, observed_at=observed_at, fresh_until=fresh_until))
    result["actions"] = {"preview": False, "retry": False}
    return result


def adapt_task_chain(chain: dict, *, now: datetime | None = None, health_filter: str = "") -> dict:
    now_value = now or datetime.now(timezone.utc)
    if now_value.tzinfo is None:
        now_value = now_value.replace(tzinfo=timezone.utc)
    observed_at = _iso(now_value)
    fresh_until = _iso(now_value + timedelta(minutes=5))
    items = []
    health_counts = {
        state: 0
        for state in ("normal", "waiting", "protected", "action_required", "evidence_insufficient")
    }
    for item in chain.get("items") or []:
        if not isinstance(item, dict):
            continue
        media = media_key(item.get("mediaType"), item.get("tmdbId"), item.get("title"))
        target = target_key(item.get("mediaType"), item.get("tmdbId"), item.get("title"), item.get("seasonNumber", 0))
        source_ids = item.get("sourceIds") or {}
        artifact_keys = [artifact_key(qb_hash=value) for value in source_ids.get("qbHashes") or []]
        artifact_keys.extend(artifact_key(remote_file_id=value) for value in source_ids.get("symediaIds") or [])
        artifact_keys = sorted(set(artifact_keys))
        stages = [_stage(step, observed_at, fresh_until, now_value) for step in item.get("steps") or []]
        item_health = _health({**item, "stages": stages}, observed_at, fresh_until, now_value)
        health_counts[item_health["healthState"]] += 1
        if health_filter and item_health["healthState"] != health_filter:
            continue
        items.append({
            **item,
            "chainId": chain_id(media, target, artifact_keys),
            "mediaKey": media,
            "targetKey": target,
            "artifactKeys": artifact_keys,
            "subscriptionId": str(source_ids.get("subscriptionId") or ""),
            **item_health,
            "stages": stages,
        })
    return {
        **chain,
        "items": items,
        "healthCounts": health_counts,
        "generatedAt": str(chain.get("generatedAt") or observed_at),
        "contractVersion": 2,
    }


class TaskChainV2Service:
    def __init__(self, app: Flask, repository=None, clock=None):
        self.app = app
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def snapshot(self, health_filter=""):
        service = self.app.extensions.get("mcc_task_chain_service")
        if not service:
            raise RuntimeError("任务链尚未注册")
        payload = adapt_task_chain(service.get_chain(), now=self.clock())
        if self.repository:
            payload["ledger"] = self.repository.record_snapshot(payload)
        if health_filter:
            payload["items"] = [
                item for item in payload.get("items") or []
                if item.get("healthState") == health_filter
            ]
        return payload


def register_task_chain_v2(app: Flask, repository=None, clock=None):
    service = TaskChainV2Service(app, repository=repository, clock=clock)
    app.extensions["mcc_task_chain_v2_service"] = service

    @app.get("/api/v2/tasks/chains")
    def task_chains_v2():
        health_filter = str(request.args.get("health") or "").strip()
        allowed = {"normal", "waiting", "protected", "action_required", "evidence_insufficient"}
        if health_filter and health_filter not in allowed:
            return jsonify({
                "code": "TASK_HEALTH_FILTER_INVALID",
                "error": "健康状态筛选无效",
                "request_id": current_request_id(),
            }), 400
        try:
            return jsonify(service.snapshot(health_filter=health_filter))
        except Exception:
            return jsonify({
                "code": "TASK_CHAIN_V2_READ_FAILED",
                "error": "任务链读取失败",
                "request_id": current_request_id(),
            }), 502

    return service
