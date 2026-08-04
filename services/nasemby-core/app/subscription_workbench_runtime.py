from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request

from app.http_runtime import current_request_id
from app import discover_runtime
from app.contract_mapping import map_subscription_item
from app.task_public_runtime import (
    present_media_result,
    present_pipeline_fact,
    present_pipeline_outcome,
    present_residual_issues,
)
from app.statistic_metadata_runtime import statistic_metadata


SHANGHAI_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
CANDIDATE_SCHEDULE_GRACE = timedelta(hours=2)


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _as_datetime(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
    return parsed.astimezone(timezone.utc)


def _candidate_time_detail(value, now_value):
    parsed = _as_datetime(value)
    current = _as_datetime(now_value)
    if not parsed or not current:
        return "时间暂未确认"
    elapsed_seconds = (current - parsed).total_seconds()
    if elapsed_seconds < -60:
        return "时间暂未确认"
    elapsed_seconds = max(0, elapsed_seconds)
    minutes = int(elapsed_seconds // 60)
    if minutes < 1:
        relative = "刚刚"
    elif minutes < 60:
        relative = f"{minutes} 分钟前"
    else:
        hours = minutes // 60
        relative = f"{hours} 小时前" if hours < 24 else f"{hours // 24} 天前"
    clock = parsed.astimezone(SHANGHAI_TZ).strftime("%H:%M:%S")
    return f"{relative} · 北京时间 {clock}"


def _candidate_scan_snapshot(environment, douban, scheduler, now_value=None):
    now_value = now_value or datetime.now(timezone.utc)
    if now_value.tzinfo is None:
        now_value = now_value.replace(tzinfo=timezone.utc)
    local_now = now_value.astimezone(SHANGHAI_TZ)
    douban = douban if isinstance(douban, dict) else {}
    scheduler = scheduler if isinstance(scheduler, dict) else {}
    candidate_scheduler = bool(scheduler.get("candidateSource"))
    rule_enabled = bool(douban.get("enabled"))
    if candidate_scheduler:
        scheduler_configured = True
        scheduler_enabled = bool(scheduler.get("enabled"))
        scheduler_started = bool(scheduler.get("schedulerStarted"))
    else:
        scheduler_configured = "MCC_SUBSCRIPTION_SCHEDULER_ENABLED" in environment
        scheduler_enabled = (
            _truthy(environment.get("MCC_SUBSCRIPTION_SCHEDULER_ENABLED"))
            if scheduler_configured else bool(scheduler.get("enabled"))
        )
        scheduler_started = bool(scheduler.get("started"))
    scheduler_running = bool(scheduler_enabled and scheduler_started)
    task_time = str(douban.get("task_time") or "08:30")
    try:
        hour, minute = [int(part) for part in task_time.split(":", 1)]
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (TypeError, ValueError):
        hour, minute = 8, 30
        task_time = "08:30"
    today_schedule = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    grace_until = today_schedule + CANDIDATE_SCHEDULE_GRACE
    expected = today_schedule if local_now > grace_until else today_schedule - timedelta(days=1)
    last_run_at = str(scheduler.get("lastRunAt") or douban.get("last_run_at") or "")
    last_success_at = str(scheduler.get("lastSuccessAt") or douban.get("last_success_at") or "")
    configured_error = str(douban.get("last_error") or "")
    scheduler_error = str(scheduler.get("lastError") or "")
    last_error = scheduler_error or configured_error
    if not last_success_at and last_run_at and not configured_error:
        last_success_at = last_run_at
    last_success = _as_datetime(last_success_at)
    overdue = (
        bool(scheduler.get("overdue"))
        if candidate_scheduler else bool(last_success and last_success < expected.astimezone(timezone.utc))
    )
    if not rule_enabled:
        state = "rules_disabled"
        label = "候选规则未启用"
        detail = ""
    elif not scheduler_enabled:
        state = "scheduler_disabled"
        label = "候选自动更新未开启"
        detail = ""
    elif not scheduler_running:
        state = "scheduler_stopped"
        label = "候选自动更新已开启"
        detail = "服务端调度未启动"
    elif candidate_scheduler and scheduler.get("running"):
        state = "running"
        label = "候选来源更新中"
        detail = _candidate_time_detail(scheduler.get("startedAt"), now_value)
    elif last_error:
        state = "error"
        label = "候选调度异常"
        detail = "最近运行失败"
    elif not last_run_at:
        state = "waiting_first_run"
        label = "调度已启动"
        detail = "等待首次运行"
    elif overdue:
        state = "overdue"
        label = "候选调度逾期"
        detail = f"最近成功 {_candidate_time_detail(last_success_at, now_value)}"
    else:
        state = "healthy"
        label = "候选自动更新正常"
        detail = _candidate_time_detail(last_run_at, now_value)
    return {
        "configured": bool(douban),
        "ruleEnabled": rule_enabled,
        "schedulerConfigured": scheduler_configured,
        "schedulerEnabled": scheduler_enabled,
        "schedulerStarted": scheduler_started,
        "running": scheduler_running,
        "refreshRunning": bool(scheduler.get("running")) if candidate_scheduler else False,
        "state": state,
        "label": label,
        "detail": detail,
        "taskTime": task_time,
        "lastRunAt": last_run_at,
        "lastSuccessAt": last_success_at,
        "lastError": last_error,
        "expectedRunAt": expected.isoformat(timespec="seconds"),
        "graceUntil": grace_until.isoformat(timespec="seconds"),
        "overdue": overdue,
        "nextRunAt": str(scheduler.get("nextRunAt") or ""),
        "lastResult": scheduler.get("lastResult") if isinstance(scheduler.get("lastResult"), dict) else {},
    }


def _state(key, label, state, detail, *, enabled=False, configured=False, checked_at=""):
    return {
        "key": key,
        "label": label,
        "state": state,
        "enabled": bool(enabled),
        "configured": bool(configured),
        "detail": str(detail or ""),
        "checkedAt": checked_at,
    }


def _first_value(row, *keys):
    for key in keys:
        value = row.get(key)
        if isinstance(value, list) and value:
            return value
        if value not in (None, "", []):
            return value
    return None


# 订阅模式到 activation provider 的映射；资源类模式统一归为 resource_rule
MANUAL_FOLLOW_PROVIDERS = {
    "torra": "torra",
    "moviepilot": "moviepilot",
    "symedia": "symedia",
    "resource": "resource_rule",
    "resource_then_pt": "resource_rule",
}


def manual_follow_snapshot(environment, config=None):
    """手动加入追更能力：只描述保存后能否进入后续队列，不参与后台扫描判定。"""
    write_enabled = _truthy(environment.get("NASEMBY_CORE_WRITE_ENABLED"))
    if not write_enabled:
        return {"state": "write_disabled", "provider": "none", "blockers": ["本地订阅写入已关闭"]}
    if config is None:
        try:
            config = discover_runtime.load_subscription_config() or {}
        except Exception:
            config = {}
    mode = discover_runtime.normalize_subscription_mode(
        config.get("mode") if isinstance(config, dict) else ""
    )
    blockers = []
    if mode == "torra" and not _truthy(environment.get("TORRA_PUSH_ENABLED")):
        blockers.append("允许向 Torra 创建订阅已关闭")
    elif mode == "resource":
        rules = discover_runtime.normalize_resource_rules(
            config.get("resource_rules") if isinstance(config, dict) else None
        )
        if not (rules.get("enabled") and rules.get("auto_transfer")):
            blockers.append("资源规则未启用")
    if blockers:
        return {"state": "saved_only", "provider": "none", "blockers": blockers}
    return {"state": "queued_ready", "provider": MANUAL_FOLLOW_PROVIDERS.get(mode, "none"), "blockers": []}


def _missing_episodes(row):
    value = _first_value(
        row,
        "missing_episode_numbers",
        "missing_episodes",
        "missingEpisodes",
        "episode_missing",
    )
    if isinstance(value, str):
        return [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
    if isinstance(value, (tuple, list, set)):
        return [str(part) for part in value if str(part).strip()]
    return []


def _scope(row):
    media_type = str(row.get("media_type") or row.get("mediaType") or "").lower()
    if media_type == "movie":
        return "整部电影"
    season = _first_value(row, "target_season", "current_season", "latest_season", "season_number", "season")
    if season not in (None, ""):
        return f"第 {season} 季"
    return "按剧集持续追更"


def _fact_map(chain_item):
    return {
        str(fact.get("stage")): fact
        for fact in (chain_item or {}).get("pipelineFacts", [])
        if isinstance(fact, dict) and fact.get("stage")
    }


def _fact_stage(fact, fallback_detail):
    row = fact if isinstance(fact, dict) else {}
    state = str(row.get("state") or "unknown")
    status = {
        "succeeded": "done",
        "active": "active",
        "waiting": "waiting",
        "failed": "blocked",
        "protected": "protected",
        "not_applicable": "waiting",
    }.get(state, "unknown")
    return {
        "status": status,
        "detail": str(row.get("reasonText") or fallback_detail),
    }


def _outcome(chain_item):
    return present_pipeline_outcome((chain_item or {}).get("pipelineOutcome"))


def _legacy_chain_state(outcome_state):
    return {
        "playable": "completed",
        "action_required": "blocked",
        "in_progress": "active",
    }.get(str(outcome_state or ""), "waiting")


def _reconciliation_composition(items):
    counts = {
        "linked": 0,
        "onlyTorra": 0,
        "onlyFluxa": 0,
        "attention": 0,
        "unclassified": 0,
    }
    for item in items or []:
        state = str(item.get("reconciliationState") or "")
        if state == "linked":
            counts["linked"] += 1
        elif state == "only_torra":
            counts["onlyTorra"] += 1
        elif state == "only_fluxa":
            counts["onlyFluxa"] += 1
        elif state in {"conflict", "remote_missing"}:
            counts["attention"] += 1
        else:
            counts["unclassified"] += 1
    return counts


def _reconciliation_action_required(items):
    seen = set()
    for item in items or []:
        if str(item.get("reconciliationState") or "") not in {"conflict", "remote_missing"}:
            continue
        key = str(item.get("id") or item.get("subscriptionKey") or "").strip()
        if key:
            seen.add(key)
    return len(seen)


def _chain_item_for_row(row, chain):
    mapped = map_subscription_item(row) or {}
    candidates = [
        mapped.get("id"),
        row.get("key"),
        row.get("subscription_key"),
        row.get("dedupe_key"),
        row.get("id"),
        discover_runtime.get_subscription_item_key(row),
    ]
    return next((chain.get(str(value)) for value in candidates if value and chain.get(str(value))), None)


TORRA_PUSH_DETAILS = {
    "queued": "追更已保存 · 等待推送 Torra",
    "submitted": "已提交 Torra · 等待确认",
    "linked": "追更已保存 · 已在 Torra",
    "disabled": "追更已保存 · Torra 自动推送已关闭",
    "failed": "追更已保存 · Torra 推送失败",
    "unknown": "追更已保存 · 推送状态暂未确认",
}


def _torra_push_snapshot(row, reconciliation=None, push_enabled=True):
    row = row if isinstance(row, dict) else {}
    reconciliation = reconciliation if isinstance(reconciliation, dict) else {}
    remote = reconciliation.get("torra") if isinstance(reconciliation.get("torra"), dict) else {}
    reliably_linked = bool(
        reconciliation.get("reconciliationState") in {"linked", "only_torra"}
        and reconciliation.get("remoteRef")
        and remote.get("present")
        and remote.get("mappingStatus") == "mapped"
    )
    stored = str(row.get("torra_push_state") or "").strip().lower()
    if reliably_linked:
        state = "linked"
    elif stored in {"queued", "submitted", "failed"}:
        state = stored
    elif not push_enabled:
        state = "disabled"
    else:
        state = "unknown"
    return {
        "status": "linked" if state == "linked" else "not_linked",
        "pushState": state,
        "detail": TORRA_PUSH_DETAILS[state],
        "observedAt": str(
            reconciliation.get("observedAt")
            or row.get("torra_push_observed_at")
            or ""
        ),
    }


def _item_snapshot(row, chain_item=None):
    mapped = map_subscription_item(row) or {}
    chain_item = chain_item or {}
    facts = _fact_map(chain_item)
    pipeline_outcome = _outcome(chain_item)
    outcome_state = pipeline_outcome["state"]
    torra_fact = present_pipeline_fact(facts["torra"]) if facts.get("torra") else None
    return {
        **mapped,
        "status": "done" if outcome_state == "playable" else "pending",
        "scope": _scope(row),
        "missingEpisodes": _missing_episodes(row),
        "torra": _torra_push_snapshot(row),
        "qb": _fact_stage(facts.get("qb"), "未接入 qB 任务证据"),
        "cloud115": _fact_stage(facts.get("cloud115"), "未接入 115 文件级证据"),
        "library": _fact_stage(facts.get("symedia"), "尚无 Symedia 整理证据"),
        "torraFact": torra_fact,
        "pipelineOutcome": pipeline_outcome,
        "mediaResult": present_media_result(chain_item.get("mediaResult")),
        "residualIssues": present_residual_issues(chain_item.get("residualIssues")),
        "outcomeState": outcome_state,
        "blockingReason": pipeline_outcome["reasonText"] if outcome_state == "action_required" else "",
        "chainState": _legacy_chain_state(outcome_state),
        "chainProgress": int(chain_item.get("progress") or 0),
    }


class SubscriptionWorkbenchService:
    def __init__(self, app: Flask, environment=None, clock=None):
        self.app = app
        self.environment = environment if environment is not None else {}
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _candidate_scheduler_snapshot(self):
        registry = self.app.extensions.get("mcc_scheduler_status")
        heartbeat = registry.snapshot("candidate-source") if registry else {}
        runtime = self.app.extensions.get("mcc_candidate_source_scheduler")
        if runtime:
            return {
                **runtime.snapshot(now=self.clock()),
                "candidateSource": True,
                "schedulerStarted": bool(heartbeat.get("started")),
                "heartbeatAt": str(heartbeat.get("lastRunAt") or heartbeat.get("checkedAt") or ""),
            }
        return registry.snapshot("subscription-task") if registry else {}

    def capability_snapshot(self):
        checked_at = _now()
        scheduler = self._candidate_scheduler_snapshot()
        try:
            config = discover_runtime.load_subscription_config() or {}
        except Exception:
            config = {}
        douban = config.get("douban") if isinstance(config, dict) else {}
        douban = douban if isinstance(douban, dict) else {}
        source_scan = _candidate_scan_snapshot(
            self.environment, douban, scheduler, self.clock()
        )
        return {
            "ok": True,
            "checkedAt": checked_at,
            "localWrite": {
                "enabled": _truthy(self.environment.get("NASEMBY_CORE_WRITE_ENABLED")),
            },
            "torraPush": {
                "enabled": _truthy(self.environment.get("TORRA_PUSH_ENABLED")),
            },
            "scheduler": {
                "configured": source_scan["schedulerConfigured"],
                "enabled": source_scan["schedulerEnabled"],
                "started": source_scan["schedulerStarted"],
                "running": source_scan["running"],
                "lastRunAt": source_scan["lastRunAt"],
                "heartbeatAt": str(scheduler.get("heartbeatAt") or scheduler.get("lastRunAt") or ""),
                "lastError": source_scan["lastError"],
            },
            # 手动加入结果只看 manualFollow；sourceScan 只描述后台来源扫描
            "manualFollow": manual_follow_snapshot(self.environment, config),
            "sourceScan": source_scan,
        }

    @staticmethod
    def _requested_visual_ids(item_ids):
        requested = []
        for value in item_ids or []:
            key = str(value or "").strip()
            if key and key not in requested:
                requested.append(key)
        return requested[:100]

    def _visual_rows_by_key(self):
        write_enabled = _truthy(self.environment.get("NASEMBY_CORE_WRITE_ENABLED"))
        raw = discover_runtime.load_subscription_items(
            with_progress=False,
            remove_completed=False,
            persist_progress=False,
        )
        rows = [
            {**row, "_visual_read_only": not write_enabled}
            for row in (raw.get("items") or [])
            if isinstance(row, dict)
        ]
        by_key = {}
        for row in rows:
            storage_key = str(discover_runtime.get_subscription_item_key(row) or "")
            if not storage_key:
                continue
            row["_visual_storage_key"] = storage_key
            by_key[storage_key] = row
            public_key = str((map_subscription_item(row) or {}).get("id") or "")
            if public_key:
                by_key[public_key] = row
        reconciliation_service = self.app.extensions.get("mcc_subscription_reconciliation")
        if reconciliation_service:
            try:
                reconciliation = reconciliation_service.snapshot() or {}
            except Exception:
                reconciliation = {}
            for row in reconciliation.get("items") or []:
                if not isinstance(row, dict) or row.get("reconciliationState") != "only_torra":
                    continue
                key = str(row.get("id") or "").strip()
                if not key:
                    continue
                by_key[key] = {
                    "title": row.get("title") or "",
                    "media_type": row.get("mediaType") or "unknown",
                    "tmdb_id": row.get("tmdbId") or "",
                    "_visual_read_only": True,
                }
        return by_key

    @staticmethod
    def _visual_response_item(key, visuals, mapped=None):
        mapped = mapped or {}
        return {
            "id": mapped.get("id") or key,
            "posterUrl": mapped.get("posterUrl") or visuals.get("poster_url") or "",
            "backdropUrl": mapped.get("backdropUrl") or visuals.get("backdrop_url") or "",
        }

    def _backfill_visual(self, key, row):
        visuals = discover_runtime.resolve_subscription_visuals(row, fetch=True)
        if not visuals.get("poster_url"):
            return "unchanged", None
        if row.get("_visual_read_only"):
            return "updated", self._visual_response_item(key, visuals)
        saved = discover_runtime.supplement_subscription_visuals(
            str(row.get("_visual_storage_key") or key),
            visuals,
        )
        if not saved:
            return "unchanged", None
        return "updated", self._visual_response_item(key, visuals, map_subscription_item(saved) or {})

    def backfill_visuals(self, item_ids):
        requested = self._requested_visual_ids(item_ids)
        by_key = self._visual_rows_by_key()
        result = {"ok": True, "scanned": 0, "updated": 0, "unchanged": 0, "items": [], "errors": []}
        for key in requested:
            row = by_key.get(key)
            if not row:
                continue
            result["scanned"] += 1
            try:
                status, item = self._backfill_visual(key, row)
            except Exception:
                result["errors"].append(key)
                continue
            result[status] += 1
            if item:
                result["items"].append(item)
        return result

    def snapshot(self, *, limit=None, offset=0, media_type="", query=""):
        checked_at = _now()
        write_enabled = _truthy(self.environment.get("NASEMBY_CORE_WRITE_ENABLED"))
        raw = discover_runtime.load_subscription_items(
            with_progress=False,
            remove_completed=False,
            persist_progress=False,
        )
        rows = [row for row in (raw.get("items") or []) if isinstance(row, dict)]
        reconciliation = None
        reconciliation_error = ""
        reconciliation_service = self.app.extensions.get("mcc_subscription_reconciliation")
        if reconciliation_service:
            try:
                reconciliation = reconciliation_service.snapshot()
            except Exception:
                reconciliation_error = "追更对账读取失败"
        chain = {}
        chain_error = ""
        task_service = self.app.extensions.get("mcc_task_chain_v2_service")
        legacy_task_service = self.app.extensions.get("mcc_task_chain_service")
        if task_service:
            try:
                chain_payload = task_service.full_snapshot()
            except Exception:
                chain_error = "任务链读取失败"
        elif legacy_task_service:
            try:
                chain_payload = legacy_task_service.get_chain()
            except Exception:
                chain_error = "任务链读取失败"
        if 'chain_payload' in locals() and isinstance(chain_payload, dict):
            for item in chain_payload.get("items") or []:
                if not isinstance(item, dict):
                    continue
                source_ids = item.get("sourceIds") or {}
                subscription_ids = [
                    item.get("subscriptionId"),
                    source_ids.get("subscriptionId"),
                    *(source_ids.get("subscriptionIds") or []),
                ]
                for subscription_id in subscription_ids:
                    if subscription_id:
                        chain[str(subscription_id)] = item

        reconciliation_items = {
            str(item.get("localId") or ""): item
            for item in (reconciliation or {}).get("items", [])
            if isinstance(item, dict) and item.get("localId")
        }
        mapped_items = []
        for source_row in rows:
            row = dict(source_row)
            poster_missing = not str(row.get("poster_url") or row.get("poster") or "").strip()
            visuals = discover_runtime.resolve_subscription_visuals(row, fetch=False)
            if visuals:
                row.update(visuals)
            chain_item = _chain_item_for_row(row, chain)
            mapped = _item_snapshot(row, chain_item)
            local_key = str(mapped.get("id") or discover_runtime.get_subscription_item_key(row) or "")
            if (
                poster_missing
                and local_key
                and str(mapped.get("tmdbId") or "").isdigit()
            ):
                mapped["_posterBackfillId"] = local_key
            recon = reconciliation_items.get(local_key)
            if recon:
                mapped.update({
                    "reconciliationState": recon.get("reconciliationState"),
                    "fulfillmentState": recon.get("fulfillmentState"),
                    "healthState": recon.get("healthState"),
                    "reasonCode": recon.get("reasonCode"),
                    "reasonText": recon.get("reasonText"),
                    "observedAt": recon.get("observedAt"),
                    "freshUntil": recon.get("freshUntil"),
                })
                mapped["torra"] = _torra_push_snapshot(
                    row, recon, _truthy(self.environment.get("TORRA_PUSH_ENABLED"))
                )
                if not chain_item:
                    mapped["torraFact"] = recon.get("torraFact")
                    mapped["pipelineOutcome"] = present_pipeline_outcome(recon.get("pipelineOutcome"))
                    mapped["mediaResult"] = present_media_result(recon.get("mediaResult"))
                    mapped["residualIssues"] = present_residual_issues(recon.get("residualIssues"))
                    mapped["outcomeState"] = mapped["pipelineOutcome"]["state"]
                    mapped["chainState"] = _legacy_chain_state(mapped["outcomeState"])
                    mapped["status"] = "done" if mapped["outcomeState"] == "playable" else "pending"
            else:
                mapped["torra"] = _torra_push_snapshot(
                    row, None, _truthy(self.environment.get("TORRA_PUSH_ENABLED"))
                )
            mapped_items.append(mapped)

        for recon in (reconciliation or {}).get("items", []):
            if not isinstance(recon, dict) or recon.get("localId") or recon.get("reconciliationState") != "only_torra":
                continue
            fulfillment_state = str(recon.get("fulfillmentState") or "following")
            pipeline_outcome = present_pipeline_outcome(recon.get("pipelineOutcome"))
            outcome_state = pipeline_outcome["state"]
            torra_fact = recon.get("torraFact")
            remote_visuals = discover_runtime.resolve_subscription_visuals({
                "title": recon.get("title") or "",
                "media_type": recon.get("mediaType") or "unknown",
                "tmdb_id": recon.get("tmdbId") or "",
            }, fetch=False)
            remote_item = {
                "id": recon.get("id"),
                "title": recon.get("title") or "未命名订阅",
                "seasonName": f"第 {recon.get('seasonNumber', 0)} 季" if recon.get("mediaType") == "tv" else "",
                "seasonNumber": recon.get("seasonNumber"),
                "mediaType": recon.get("mediaType") or "unknown",
                "tmdbId": recon.get("tmdbId") or "",
                "posterUrl": remote_visuals.get("poster_url") or "",
                "progress": {
                    "state": "unconfirmed",
                    "confirmed": None,
                    "total": None,
                    "text": "集数进度未确认" if recon.get("mediaType") == "tv" else "进度未确认",
                },
                "progressText": "集数进度未确认" if recon.get("mediaType") == "tv" else "进度未确认",
                "inLibrary": False,
                "updatedAt": recon.get("observedAt") or checked_at,
                "createdAt": recon.get("observedAt") or checked_at,
                "sourceLabel": "Torra 已有订阅",
                "status": "done" if outcome_state == "playable" else "pending",
                "origin": "torra",
                "readOnly": True,
                "chainState": _legacy_chain_state(outcome_state),
                "outcomeState": outcome_state,
                "pipelineOutcome": pipeline_outcome,
                "mediaResult": present_media_result(recon.get("mediaResult")),
                "residualIssues": present_residual_issues(recon.get("residualIssues")),
                "torraFact": torra_fact,
                "torra": _torra_push_snapshot(
                    {}, recon, _truthy(self.environment.get("TORRA_PUSH_ENABLED"))
                ),
                "torraSyncState": "current",
                "torraMappingStatus": "mapped",
                "reconciliationState": recon.get("reconciliationState"),
                "fulfillmentState": fulfillment_state,
                "healthState": recon.get("healthState"),
                "reasonCode": recon.get("reasonCode"),
                "reasonText": recon.get("reasonText"),
                "observedAt": recon.get("observedAt"),
                "freshUntil": recon.get("freshUntil"),
            }
            if (
                not remote_item["posterUrl"]
                and str(remote_item.get("tmdbId") or "").isdigit()
            ):
                remote_item["_posterBackfillId"] = str(remote_item.get("id") or "")
            mapped_items.append(remote_item)
        playable_count = sum(item.get("outcomeState") == "playable" for item in mapped_items)
        stats = {
            "total": len(mapped_items),
            "movie": sum(item.get("mediaType") == "movie" for item in mapped_items),
            "tv": sum(item.get("mediaType") == "tv" for item in mapped_items),
            "pending": len(mapped_items) - playable_count,
            "following": sum(
                (item.get("torraFact") or {}).get("state") in {"waiting", "active"}
                and item.get("outcomeState") != "action_required"
                for item in mapped_items
            ),
            "playable": playable_count,
            "completed": playable_count,
            "actionRequired": sum(
                item.get("outcomeState") == "action_required"
                for item in mapped_items
            ),
            "inLibrary": sum(item.get("library", {}).get("status") == "done" for item in mapped_items),
            "reconciliationActionRequired": _reconciliation_action_required(mapped_items),
            **_reconciliation_composition(mapped_items),
        }
        chain_available = 'chain_payload' in locals() and isinstance(chain_payload, dict) and not chain_error
        unknown_outcomes = sum(
            item.get("outcomeState") == "evidence_insufficient"
            for item in mapped_items
        )
        outcome_confirmation = (
            "unknown" if not chain_available
            else "partial" if unknown_outcomes > 0
            else "confirmed"
        )
        statistics_meta = {
            "total": statistic_metadata(
                scope="current_subscription_ledger", unit="subscription",
                observed_at=checked_at, confirmation="confirmed",
            ),
            **{
                key: statistic_metadata(
                    scope="current_subscription_ledger", unit="subscription",
                    observed_at=checked_at, confirmation=outcome_confirmation,
                )
                for key in ("following", "playable", "actionRequired", "inLibrary")
            },
        }
        filtered_items = mapped_items
        if media_type in {"movie", "tv"}:
            filtered_items = [item for item in filtered_items if item.get("mediaType") == media_type]
        normalized_query = str(query or "").strip().casefold()
        if normalized_query:
            filtered_items = [
                item for item in filtered_items
                if normalized_query in " ".join((
                    str(item.get("title") or ""),
                    str(item.get("tmdbId") or ""),
                    str(item.get("sourceLabel") or ""),
                )).casefold()
            ]
        page_total = len(filtered_items)
        page_offset = max(0, int(offset or 0))
        page_limit = max(1, min(100, int(limit))) if limit is not None else max(1, page_total or 1)
        paged_items = filtered_items[page_offset:page_offset + page_limit] if limit is not None else filtered_items
        poster_backfill_ids = [
            str(item.pop("_posterBackfillId"))
            for item in paged_items
            if item.get("_posterBackfillId")
        ]
        next_offset = page_offset + len(paged_items)

        torra_sync = self.app.extensions.get("mcc_torra_subscription_sync")
        torra_status = torra_sync.status() if torra_sync else {"enabled": False, "linked": 0, "current": 0, "remoteMissing": 0, "errors": 0, "lastSyncedAt": ""}
        torra_client = self.app.extensions.get("mcc_torra_client")
        torra_configured = bool(torra_client and torra_client.is_configured())
        task_services = (chain_payload.get("services") if 'chain_payload' in locals() and isinstance(chain_payload, dict) else {}) or {}
        torra_service = task_services.get("torra") or {}
        torra_connected = bool(torra_service.get("connected")) if task_service else torra_configured
        torra_error = str(torra_service.get("error") or chain_error or "")
        reconciliation_counts = ((reconciliation or {}).get("summary") or {}).get("reconciliation") or {}
        try:
            reconciliation_linked = int(reconciliation_counts.get("linked") or 0)
        except (TypeError, ValueError):
            reconciliation_linked = 0
        reconciliation_readable = bool(reconciliation and not reconciliation.get("sourceError"))
        if reconciliation_readable:
            mirror_state = "ready" if torra_status.get("enabled") and not torra_status.get("errors") else ("error" if torra_status.get("errors") else "disabled")
            mirror_detail = (
                f"当前对账已关联 {reconciliation_linked} 条；历史镜像链接 {torra_status.get('linked', 0)} 条"
                + ("；镜像同步未开启" if not torra_status.get("enabled") else "")
            )
        elif reconciliation and reconciliation.get("sourceError"):
            mirror_state = "error"
            mirror_detail = "对账暂不可用；历史镜像链接仅供参考"
        elif torra_status.get("enabled"):
            mirror_state = "error" if torra_status.get("errors") else "ready"
            mirror_detail = f"历史镜像链接 {torra_status.get('linked', 0)} 条，最近同步 {torra_status.get('lastSyncedAt') or '尚未同步'}"
        else:
            mirror_state = "disabled"
            mirror_detail = "Torra 订阅镜像未开启"
        rss = self.app.extensions.get("mcc_private_rss")
        rss_summary = rss.repository.summary(rss.collection_enabled()) if rss else {
            "enabled": False,
            "sources": 0,
            "activeSources": 0,
            "errorSources": 0,
            "items": 0,
            "lastSuccessAt": "",
            "matches": 0,
            "matcherRan": False,
            "lastMatchAt": "",
        }
        config = discover_runtime.load_subscription_config() or {}
        douban = config.get("douban") if isinstance(config, dict) else {}
        douban = douban if isinstance(douban, dict) else {}
        scheduler_runtime = self._candidate_scheduler_snapshot()
        source_scan = _candidate_scan_snapshot(
            self.environment, douban, scheduler_runtime, self.clock()
        )
        scheduler_enabled = bool(source_scan["ruleEnabled"] and source_scan["running"])
        scheduler_state = {
            "rules_disabled": "disabled",
            "scheduler_disabled": "disabled",
            "scheduler_stopped": "unknown",
            "running": "ready",
            "waiting_first_run": "unknown",
            "error": "error",
            "overdue": "error",
            "healthy": "ready",
        }[source_scan["state"]]
        scheduler_detail = " · ".join(
            value for value in (source_scan["label"], source_scan["detail"]) if value
        )
        if not rss_summary.get("enabled"):
            rss_state = "disabled"
            rss_detail = "RSS 采集未开启"
        elif rss_summary.get("errorSources"):
            rss_state = "error"
            rss_detail = f"{rss_summary.get('errorSources')} 个来源最近采集失败"
        elif not rss_summary.get("matcherRan"):
            rss_state = "unknown"
            rss_detail = f"已采集 {rss_summary.get('items', 0)} 条，匹配器尚未运行"
        elif not rss_summary.get("matches"):
            rss_state = "ready"
            rss_detail = f"采集正常，匹配器已运行，当前暂无命中（{rss_summary.get('items', 0)} 条种子）"
        else:
            rss_state = "ready"
            rss_detail = f"{rss_summary.get('activeSources', 0)} 个来源，已命中 {rss_summary.get('matches', 0)} 条"
        capabilities = [
            _state("local_write", "本地写入", "ready" if write_enabled else "disabled", "可保存和管理 Fluxa 本地订阅" if write_enabled else "本地订阅写入已关闭", enabled=write_enabled, configured=True, checked_at=checked_at),
            _state("torra_connection", "Torra 连接", "ready" if torra_connected else ("error" if torra_error else "disabled"), "连接正常" if torra_connected else (torra_error or ("Torra 未配置" if not torra_configured else "暂未建立连接")), enabled=torra_connected, configured=torra_configured, checked_at=checked_at),
            _state("torra_mirror", "镜像同步", mirror_state, mirror_detail, enabled=bool(torra_status.get("enabled")), configured=torra_configured, checked_at=checked_at),
            _state("rss", "RSS", rss_state, rss_detail, enabled=bool(rss_summary.get("enabled")), configured=bool(rss_summary.get("sources")), checked_at=checked_at),
            _state("scheduler", "定时任务", scheduler_state, scheduler_detail, enabled=scheduler_enabled, configured=bool(douban), checked_at=checked_at),
        ]
        return {
            "ok": True,
            "lastReadAt": checked_at,
            "capabilities": capabilities,
            "stats": stats,
            "statisticsMeta": statistics_meta,
            "items": paged_items,
            "posterBackfillIds": poster_backfill_ids,
            "page": {
                "total": page_total,
                "limit": page_limit,
                "offset": page_offset,
                "nextOffset": next_offset if next_offset < page_total else None,
                "hasMore": next_offset < page_total,
            },
            "blockedTitles": discover_runtime.subscription_blocked_titles(),
            "errors": list(raw.get("errors") or []) + ([chain_error] if chain_error else []) + ([reconciliation_error] if reconciliation_error else []),
            "torraSync": torra_status,
            "rss": rss_summary,
            "scheduler": {
                "enabled": scheduler_enabled,
                "state": scheduler_state,
                "taskTime": source_scan["taskTime"],
                "lastRunAt": source_scan["lastRunAt"],
                "lastSuccessAt": source_scan["lastSuccessAt"],
                "lastError": source_scan["lastError"],
                "sourceScan": source_scan,
            },
            "reconciliation": reconciliation or {
                "ok": False,
                "sourceError": reconciliation_error,
                "summary": {},
                "items": [],
            },
        }


def register_subscription_workbench(app: Flask, environment=None):
    service = SubscriptionWorkbenchService(app, environment=environment)
    app.extensions["mcc_subscription_workbench"] = service

    @app.get("/api/v2/subscriptions/capabilities")
    def subscription_capabilities():
        return jsonify(service.capability_snapshot())

    @app.get("/api/v2/subscriptions/workbench")
    def subscription_workbench():
        try:
            limit_value = int(request.args.get("limit", "24"))
            offset_value = int(request.args.get("offset", "0"))
        except ValueError:
            return jsonify({"code": "SUBSCRIPTION_PAGE_INVALID", "error": "分页参数无效", "request_id": current_request_id()}), 400
        if not 1 <= limit_value <= 100 or offset_value < 0:
            return jsonify({"code": "SUBSCRIPTION_PAGE_INVALID", "error": "分页参数无效", "request_id": current_request_id()}), 400
        media_type = str(request.args.get("mediaType") or "").strip().lower()
        if media_type and media_type not in {"movie", "tv"}:
            return jsonify({"code": "SUBSCRIPTION_MEDIA_TYPE_INVALID", "error": "媒体类型无效", "request_id": current_request_id()}), 400
        try:
            return jsonify(service.snapshot(
                limit=limit_value,
                offset=offset_value,
                media_type=media_type,
                query=str(request.args.get("query") or ""),
            ))
        except Exception:
            return jsonify({"code": "SUBSCRIPTION_WORKBENCH_READ_FAILED", "error": "订阅工作台读取失败", "request_id": current_request_id()}), 502

    @app.post("/api/v2/subscriptions/visual-backfills")
    def subscription_visual_backfills():
        item_ids = (request.get_json(silent=True) or {}).get("ids")
        if not isinstance(item_ids, list) or len(item_ids) > 100:
            return jsonify({
                "code": "SUBSCRIPTION_VISUAL_BACKFILL_INVALID",
                "error": "订阅海报补齐目标无效",
                "request_id": current_request_id(),
            }), 422
        try:
            return jsonify(service.backfill_visuals(item_ids))
        except Exception:
            return jsonify({
                "code": "SUBSCRIPTION_VISUAL_BACKFILL_FAILED",
                "error": "订阅海报补齐失败",
                "request_id": current_request_id(),
            }), 500

    return service
