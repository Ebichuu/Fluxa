from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify

from app.health_state_runtime import combine_health, evidence
from app.http_runtime import current_request_id
from app.task_chain_v2_runtime import adapt_task_chain


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _target_key(item: dict) -> str:
    identity = str(item.get("tmdbId") or item.get("title") or item.get("id") or "unknown").strip().lower()
    return ":".join((
        str(item.get("mediaType") or "unknown"),
        identity,
        str(item.get("seasonNumber") or 0),
    ))


def _fresh_until(now: datetime, minutes: int = 5) -> str:
    return _iso(now + timedelta(minutes=minutes))


def _latest_item(current: dict | None, candidate: dict) -> dict:
    if current is None:
        return candidate
    current_updated = str(current.get("updatedAt") or "")
    candidate_updated = str(candidate.get("updatedAt") or "")
    return candidate if candidate_updated >= current_updated else current


def _step(item: dict, key: str) -> dict:
    return next(
        (step for step in item.get("steps") or [] if isinstance(step, dict) and step.get("key") == key),
        {},
    )


def _item_evidence(item: dict, now: str) -> dict:
    return evidence(
        state=str(item.get("healthState") or "evidence_insufficient"),
        source=str(item.get("source") or "task-chain"),
        reason_code=str(item.get("reasonCode") or ""),
        reason_text=str(item.get("reasonText") or ""),
        observed_at=str(item.get("observedAt") or now),
        fresh_until=str(item.get("freshUntil") or ""),
    )


class HomeSummaryService:
    def __init__(self, app: Flask, clock=None):
        self.app = app
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def snapshot(self) -> dict:
        now_value = self.clock()
        now = _iso(now_value)
        chain_service = self.app.extensions.get("mcc_task_chain_service")
        if not chain_service:
            raise RuntimeError("任务链尚未注册")
        chain = adapt_task_chain(chain_service.get_chain(), now=now_value)
        unique_items = {}
        for item in chain.get("items") or []:
            if isinstance(item, dict):
                key = _target_key(item)
                unique_items[key] = _latest_item(unique_items.get(key), item)

        item_evidence = [(_target_key(item), item, _item_evidence(item, now)) for item in unique_items.values()]
        counts = {
            "ingestedToday": sum(
                _step(item, "library").get("status") == "done"
                and str(_step(item, "library").get("timestamp") or "")[:10] == now[:10]
                for item in unique_items.values()
            ),
            "downloading": sum(
                any(step.get("key") == "download" and step.get("status") == "active" for step in item.get("steps") or [])
                for item in unique_items.values()
            ),
            "pending": sum(result["healthState"] in {"waiting", "evidence_insufficient"} for _, _, result in item_evidence),
            "actionRequired": 0,
            "protected": sum(result["healthState"] == "protected" for _, _, result in item_evidence),
        }

        scheduler_registry = self.app.extensions.get("mcc_scheduler_status")
        scheduler = scheduler_registry.snapshot("subscription-task") if scheduler_registry else {}
        if scheduler.get("lastError"):
            scheduler_evidence = evidence(
                state="action_required",
                source="subscription-scheduler",
                reason_code="SCHEDULER_LAST_RUN_FAILED",
                reason_text="自动追更最近一次执行失败",
                observed_at=str(scheduler.get("lastRunAt") or scheduler.get("checkedAt") or now),
                fresh_until=_fresh_until(now_value),
            )
        elif scheduler.get("enabled") and scheduler.get("started"):
            scheduler_evidence = evidence(
                state="normal",
                source="subscription-scheduler",
                observed_at=str(scheduler.get("lastRunAt") or scheduler.get("checkedAt") or now),
                fresh_until=_fresh_until(now_value),
            )
        elif scheduler.get("enabled"):
            scheduler_evidence = evidence(
                state="evidence_insufficient",
                source="subscription-scheduler",
                reason_code="SCHEDULER_NOT_STARTED",
                reason_text="自动追更已开启，但未检测到调度器运行",
                observed_at=str(scheduler.get("checkedAt") or now),
                fresh_until=_fresh_until(now_value),
            )
        elif scheduler:
            scheduler_evidence = evidence(
                state="waiting",
                source="subscription-scheduler",
                reason_code="SCHEDULER_DISABLED",
                reason_text="自动追更调度当前未运行",
                observed_at=str(scheduler.get("checkedAt") or now),
                fresh_until=_fresh_until(now_value),
            )
        else:
            scheduler_evidence = evidence(
                state="evidence_insufficient",
                source="subscription-scheduler",
                reason_code="SCHEDULER_STATUS_UNKNOWN",
                reason_text="无法确认自动追更调度是否运行",
                observed_at=now,
                fresh_until=_fresh_until(now_value),
            )

        service_evidence = []
        for name, status in (chain.get("services") or {}).items():
            if not isinstance(status, dict):
                continue
            if status.get("connected"):
                service_evidence.append(evidence(
                    state="normal",
                    source=name,
                    observed_at=str(chain.get("generatedAt") or now),
                    fresh_until=_fresh_until(now_value),
                ))
            elif status.get("error"):
                service_evidence.append(evidence(
                    state="action_required",
                    source=name,
                    reason_code=f"{str(name).upper()}_UNAVAILABLE",
                    reason_text=str(status.get("error") or f"{name} 当前不可用"),
                    observed_at=now,
                    fresh_until=_fresh_until(now_value),
                ))
            else:
                service_evidence.append(evidence(
                    state="evidence_insufficient",
                    source=name,
                    reason_code=f"{str(name).upper()}_NOT_CONNECTED",
                    reason_text=f"{name} 尚未提供可验证状态",
                    observed_at=now,
                    fresh_until=_fresh_until(now_value),
                ))

        rss_evidence = None
        rss_service = self.app.extensions.get("mcc_private_rss")
        if rss_service:
            try:
                rss_summary = rss_service.repository.summary(rss_service.collection_enabled())
                if not rss_summary.get("enabled"):
                    rss_evidence = evidence(
                        state="normal", source="private-rss", reason_code="RSS_DISABLED",
                        reason_text="RSS 未启用，不影响 PT 主链", observed_at=now,
                        fresh_until=_fresh_until(now_value),
                    )
                elif rss_summary.get("errorSources"):
                    rss_evidence = evidence(
                        state="action_required", source="private-rss", reason_code="RSS_COLLECTION_FAILED",
                        reason_text=f"{rss_summary.get('errorSources')} 个 RSS 来源最近采集失败",
                        observed_at=str(rss_summary.get("lastSuccessAt") or now),
                        fresh_until=_fresh_until(now_value),
                    )
                elif not rss_summary.get("matcherRan"):
                    rss_evidence = evidence(
                        state="evidence_insufficient", source="private-rss", reason_code="RSS_MATCHER_NOT_RUN",
                        reason_text=f"RSS 已采集 {rss_summary.get('items', 0)} 条，但匹配器尚未运行",
                        observed_at=str(rss_summary.get("lastSuccessAt") or now),
                        fresh_until=_fresh_until(now_value),
                    )
                else:
                    rss_evidence = evidence(
                        state="normal", source="private-rss", reason_code="RSS_MATCHER_OK",
                        reason_text=f"RSS 匹配器已运行，当前命中 {rss_summary.get('matches', 0)} 条",
                        observed_at=str(rss_summary.get("lastMatchAt") or rss_summary.get("lastSuccessAt") or now),
                        fresh_until=_fresh_until(now_value),
                    )
            except Exception:
                rss_evidence = evidence(
                    state="evidence_insufficient", source="private-rss", reason_code="RSS_STATUS_READ_FAILED",
                    reason_text="RSS 状态暂时无法读取", observed_at=now, fresh_until=_fresh_until(now_value),
                )

        all_evidence = [result for _, _, result in item_evidence] + [scheduler_evidence] + service_evidence
        if rss_evidence:
            all_evidence.append(rss_evidence)
        health_state = combine_health(*(result["healthState"] for result in all_evidence))
        issues = []
        for target_key, item, result in item_evidence:
            if result["healthState"] in {"action_required", "evidence_insufficient"}:
                issues.append({
                    **result,
                    "targetKey": target_key,
                    "chainId": str(item.get("chainId") or item.get("id") or ""),
                    "title": str(item.get("title") or "未命名媒体"),
                })
        for result in [scheduler_evidence, *service_evidence, *([rss_evidence] if rss_evidence else [])]:
            if result["healthState"] in {"action_required", "evidence_insufficient"}:
                issues.append({**result, "targetKey": "", "chainId": "", "title": result["source"]})

        counts["actionRequired"] = sum(
            result["healthState"] == "action_required" for result in all_evidence
        )

        if health_state == "action_required":
            headline = f"有 {max(1, counts['actionRequired'])} 项需要处理"
        elif health_state == "evidence_insufficient":
            headline = "部分状态缺少证据"
        elif health_state == "waiting":
            headline = "影音中心正在等待或处理中"
        else:
            headline = "影音中心运行正常"
        detail = (
            f"今日入库 {counts['ingestedToday']} 条 · 下载中 {counts['downloading']} 条 · "
            f"待处理 {counts['pending']} 条"
        )
        return {
            "ok": True,
            "generatedAt": now,
            "healthState": health_state,
            "headline": headline,
            "detail": detail,
            "counts": counts,
            "issues": issues[:8],
        }


def register_home_summary(app: Flask, clock=None):
    service = HomeSummaryService(app, clock=clock)
    app.extensions["mcc_home_summary"] = service

    @app.get("/api/v2/home/summary")
    def home_summary():
        try:
            return jsonify(service.snapshot())
        except Exception:
            return jsonify({
                "code": "HOME_SUMMARY_READ_FAILED",
                "error": "首页状态读取失败",
                "request_id": current_request_id(),
            }), 502

    return service
