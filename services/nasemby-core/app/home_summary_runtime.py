from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify

from app.health_state_runtime import combine_health, evidence
from app.http_runtime import current_request_id
from app.resource_identity_runtime import target_key as resource_target_key
from app.task_chain_v2_runtime import adapt_task_chain


TARGET_SCOPE_PATTERN = re.compile(r":season:(\d+)(?::episode:(\d+))?$")


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _target_key(item: dict) -> str:
    return str(item.get("targetKey") or resource_target_key(
        item.get("mediaType"),
        item.get("tmdbId"),
        item.get("title") or item.get("id"),
        item.get("seasonNumber", 0),
        item.get("episodeNumber"),
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
    result = evidence(
        state=str(item.get("healthState") or "evidence_insufficient"),
        source=str(item.get("source") or "task-chain"),
        reason_code=str(item.get("reasonCode") or ""),
        reason_text=str(item.get("reasonText") or ""),
        observed_at=str(item.get("observedAt") or now),
        fresh_until=str(item.get("freshUntil") or ""),
    )
    result.update({
        "identityState": str(item.get("identityState") or "unidentified"),
        "executionState": str(item.get("executionState") or "waiting"),
        "userReasonText": str(item.get("userReasonText") or item.get("reasonText") or ""),
    })
    return result


def _identity_only_issue(result: dict) -> bool:
    return (
        result.get("healthState") == "evidence_insufficient"
        and result.get("identityState") in {"unidentified", "conflict"}
        and result.get("executionState") not in {"action_required", "confirmed_failed"}
        and result.get("reasonCode") in {
            "TASK_IDENTITY_UNLINKED",
            "TASK_IDENTITY_CONFLICT",
            "TASK_SUSPECTED_BLOCKED",
        }
    )


def _problem_stage(item: dict) -> dict:
    stages = [row for row in item.get("stages") or [] if isinstance(row, dict)]
    return next((
        row
        for row in stages
        if row.get("healthState") == "action_required" or row.get("status") == "blocked"
    ), next((row for row in stages if row.get("healthState") == "evidence_insufficient"), {}))


def _integer(value):
    try:
        return int(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def _problem_episode_evidence(item: dict, stage: dict) -> dict:
    rows = [row for row in item.get("episodeEvidence") or [] if isinstance(row, dict)]
    if not rows:
        return {}
    stage_name = str(stage.get("stage") or stage.get("key") or "")
    reason_code = str(stage.get("reasonCode") or "")
    matching_stage = [row for row in rows if stage_name and str(row.get("stage") or "") == stage_name]
    candidates = matching_stage or rows
    matching_reason = [row for row in candidates if reason_code and str(row.get("reasonCode") or "") == reason_code]
    candidates = matching_reason or candidates
    return max(candidates, key=lambda row: str(row.get("observedAt") or ""))


def _issue_scope(item: dict, stage: dict) -> tuple[int | None, int | None, int | None]:
    episode_evidence = _problem_episode_evidence(item, stage)
    season = _integer(episode_evidence.get("seasonNumber"))
    episode = _integer(episode_evidence.get("episodeStart"))
    episode_end = _integer(episode_evidence.get("episodeEnd"))
    if season is not None and episode is not None:
        return season, episode, episode_end

    season = _integer(stage.get("seasonNumber"))
    episode = _integer(stage.get("episodeNumber") or stage.get("episodeStart"))
    episode_end = _integer(stage.get("episodeEnd"))
    if season is not None and episode is not None:
        return season, episode, episode_end

    target_match = TARGET_SCOPE_PATTERN.search(str(item.get("targetKey") or ""))
    if target_match:
        season = _integer(target_match.group(1))
        episode = _integer(target_match.group(2))
        if episode is not None:
            return season, episode, episode

    return _integer(item.get("seasonNumber")), _integer(item.get("episodeNumber")), None


def _episode_label(season: int | None, episode: int | None, episode_end: int | None = None) -> str:
    if season is None:
        return ""
    if episode is None:
        return f"第 {season} 季" if season else ""
    suffix = f"-E{episode_end:02d}" if episode_end is not None and episode_end != episode else ""
    return f"S{season:02d}E{episode:02d}{suffix}"


def _secondary_issue_reason(result: dict) -> str:
    identity_state = str(result.get("identityState") or "")
    if identity_state == "unidentified":
        return "任务尚未关联到可靠媒体身份"
    if identity_state == "conflict":
        return "任务对应多个媒体身份候选"
    return ""


def _safe_issue_copy(item: dict, result: dict) -> dict:
    title = str(item.get("title") or "未命名媒体").strip()
    stage = _problem_stage(item)
    season, episode, episode_end = _issue_scope(item, stage)
    episode_label = _episode_label(season, episode, episode_end)
    source = str(stage.get("source") or result.get("source") or "").strip()
    raw_reason = str(
        stage.get("technicalReasonText")
        or stage.get("reasonText")
        or stage.get("detail")
        or result.get("reasonText")
        or ""
    )
    result_reason_code = str(result.get("reasonCode") or "")
    stage_reason_code = str(stage.get("reasonCode") or "")
    reason_code = stage_reason_code or result_reason_code
    label = f"《{title}》{episode_label}"
    display_title = f"{title} {episode_label}".strip()
    base = {
        "displayTitle": display_title,
        "seasonNumber": season,
        "episodeNumber": episode,
        "secondaryReasonText": _secondary_issue_reason(result),
    }
    if result_reason_code == "EVIDENCE_OWNER_CONFLICT":
        return {**base, "headline": f"{label}证据存在冲突", "reasonText": "同一条处理证据对应多个媒体候选，当前没有自动绑定"}
    if result.get("executionState") == "suspected_blocked" or result_reason_code == "TASK_SUSPECTED_BLOCKED":
        return {**base, "headline": f"{label}疑似阻塞", "reasonText": "已有处理阶段长时间没有形成后续证据"}
    if source.casefold() == "symedia" or "SYMEDIA" in reason_code or stage.get("stage") == "library":
        if any(marker in raw_reason for marker in ("未找到", "未查询到", "识别", "TMDB", "媒体信息")):
            return {**base, "headline": f"{label}识别失败", "reasonText": "Symedia 未查询到对应媒体信息"}
        if result.get("healthState") == "action_required":
            return {**base, "headline": f"{label}入库失败", "reasonText": "Symedia 未完成媒体入库"}
    if result_reason_code == "TASK_IDENTITY_UNLINKED":
        return {**base, "headline": f"{label}尚未识别", "reasonText": "暂时无法确认这条记录对应的媒体作品"}
    if source == "qBittorrent" or "DOWNLOAD" in reason_code:
        return {**base, "headline": f"{label}下载需要检查", "reasonText": "qB 下载任务没有正常继续"}
    if source == "Torra":
        return {**base, "headline": f"{label}获取需要检查", "reasonText": "Torra 未能确认资源处理状态"}
    return {**base, "headline": f"{label}需要检查", "reasonText": "当前步骤没有形成可验证结果"}


class HomeSummaryService:
    def __init__(self, app: Flask, clock=None):
        self.app = app
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def snapshot(self) -> dict:
        now_value = self.clock()
        now = _iso(now_value)
        chain_v2_service = self.app.extensions.get("mcc_task_chain_v2_service")
        chain_service = self.app.extensions.get("mcc_task_chain_service")
        if not chain_v2_service and not chain_service:
            raise RuntimeError("任务链尚未注册")
        chain = chain_v2_service.full_snapshot() if chain_v2_service else adapt_task_chain(chain_service.get_chain(), now=now_value)
        unique_items = {}
        for item in chain.get("items") or []:
            if isinstance(item, dict):
                key = _target_key(item)
                unique_items[key] = _latest_item(unique_items.get(key), item)

        item_evidence = [(_target_key(item), item, _item_evidence(item, now)) for item in unique_items.values()]
        identity_only = [row for row in item_evidence if _identity_only_issue(row[2])]
        visible_item_evidence = [row for row in item_evidence if not _identity_only_issue(row[2])]
        identity_evidence = evidence(
            state="evidence_insufficient",
            source="task-identity",
            reason_code="TASK_IDENTITY_AGGREGATION_INCOMPLETE",
            reason_text=f"{len(identity_only)} 条任务身份尚未完成关联，当前无法准确判断秒传积压",
            observed_at=str(chain.get("generatedAt") or now),
            fresh_until=_fresh_until(now_value),
        ) if identity_only else None
        completed_targets_today = sum(
            _step(item, "library").get("status") == "done"
            and str(_step(item, "library").get("timestamp") or "")[:10] == now[:10]
            for item in unique_items.values()
        )
        symedia_totals = (((chain.get("services") or {}).get("symedia") or {}).get("totals") or {})
        try:
            archived_today = max(0, int(symedia_totals.get("archivedToday") or 0))
        except (TypeError, ValueError):
            archived_today = 0
        counts = {
            "ingestedToday": completed_targets_today,
            "archivedToday": archived_today,
            "completedTargetsToday": completed_targets_today,
            "downloading": sum(
                any(step.get("key") == "download" and step.get("status") == "active" for step in item.get("steps") or [])
                for item in unique_items.values()
            ),
            "activeDownloadTasks": sum(int(item.get("activeDownloadTasks") or (item.get("qbControl") or {}).get("active") or 0) for item in unique_items.values()),
            "concurrentDownloadGroups": sum(int(item.get("activeDownloadTasks") or (item.get("qbControl") or {}).get("active") or 0) > 1 for item in unique_items.values()),
            "pending": (
                sum(result["healthState"] in {"waiting", "evidence_insufficient"} for _, _, result in visible_item_evidence)
                + (1 if identity_evidence else 0)
            ),
            "waiting": sum(result["healthState"] == "waiting" for _, _, result in visible_item_evidence),
            "evidenceInsufficient": (
                sum(result["healthState"] == "evidence_insufficient" for _, _, result in visible_item_evidence)
                + (1 if identity_evidence else 0)
            ),
            "identityPending": len(identity_only),
            "actionRequired": 0,
            "suspectedBlocked": sum(
                result.get("executionState") == "suspected_blocked" for _, _, result in visible_item_evidence
            ),
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
                    reason_text=f"{name} 当前不可用",
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

        all_evidence = [result for _, _, result in visible_item_evidence] + [scheduler_evidence] + service_evidence
        if identity_evidence:
            all_evidence.append(identity_evidence)
        if rss_evidence:
            all_evidence.append(rss_evidence)
        health_state = combine_health(*(result["healthState"] for result in all_evidence))
        issues = []
        for target_key, item, result in visible_item_evidence:
            if result["healthState"] in {"action_required", "evidence_insufficient"}:
                issue_copy = _safe_issue_copy(item, result)
                issues.append({
                    **result,
                    **issue_copy,
                    "targetKey": target_key,
                    "chainId": str(item.get("chainId") or item.get("id") or ""),
                    "title": str(item.get("title") or "未命名媒体"),
                })
        if identity_evidence:
            issues.append({
                **identity_evidence,
                "targetKey": "",
                "chainId": "",
                "title": "任务身份",
                "headline": "任务身份尚未完成关联",
                "displayTitle": "任务身份尚未完成关联",
                "reasonText": identity_evidence["reasonText"],
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
            f"今日成功归档 {counts['archivedToday']} 条 · 完成目标 {counts['completedTargetsToday']} 个 · "
            f"下载任务 {counts['activeDownloadTasks']} 个 · 并发目标 {counts['concurrentDownloadGroups']} 个 · "
            f"等待 {counts['waiting']} 条 · 证据不足 {counts['evidenceInsufficient']} 条"
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
