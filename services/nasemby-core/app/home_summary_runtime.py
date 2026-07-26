from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify

from app.health_state_runtime import evidence
from app.http_runtime import current_request_id
from app.resource_identity_runtime import target_key as resource_target_key
from app.secupload_issue_runtime import build_secupload_issue
from app.task_chain_v2_runtime import adapt_task_chain
from app.task_public_runtime import present_system_issue


TARGET_SCOPE_PATTERN = re.compile(r":season:(\d+)(?::episode:(\d+))?$")
SHANGHAI_TZ = timezone(timedelta(hours=8))


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
    for collection in (item.get("stages") or [], item.get("steps") or []):
        match = next((
            step
            for step in collection
            if isinstance(step, dict) and str(step.get("stage") or step.get("key") or "") == key
        ), None)
        if match is not None:
            return match
    return {}


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
        if row.get("healthState") == "action_required"
    ), next((row for row in stages if row.get("healthState") == "evidence_insufficient"), {}))


def _integer(value):
    try:
        return int(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def _active_download_count(item: dict) -> int:
    stage = _step(item, "download")
    if (
        stage.get("status") != "active"
        or stage.get("healthState") != "waiting"
        or stage.get("evidence") not in {"verified", "inferred"}
    ):
        return 0
    return max(0, _integer(
        item.get("activeDownloadTasks") or (item.get("qbControl") or {}).get("active") or 0
    ) or 0)


def _focus_item(key, label, unit, value, state, detail, href):
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "value": value,
        "state": state,
        "detail": detail,
        "href": href,
    }


def _today_key(value) -> str:
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SHANGHAI_TZ).date().isoformat()


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
        secupload_issue = next((
            row for row in chain.get("systemIssues") or []
            if isinstance(row, dict) and row.get("id") == "secupload_failures"
        ), None)
        if secupload_issue is None:
            secupload_source = ((((chain.get("services") or {}).get("torra") or {}).get("secupload115")) or {})
            secupload_issue = build_secupload_issue(secupload_source, now=now_value)
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
        today_key = _today_key(now_value)
        completed_targets_today = sum(
            _step(item, "library").get("status") == "done"
            and _today_key(
                _step(item, "library").get("timestamp")
                or _step(item, "library").get("observedAt")
            ) == today_key
            for item in unique_items.values()
        )
        symedia_totals = (((chain.get("services") or {}).get("symedia") or {}).get("totals") or {})
        archived_today = _integer(symedia_totals.get("archivedToday")) if "archivedToday" in symedia_totals else None
        if archived_today is not None and archived_today < 0:
            archived_today = None
        counts = {
            "ingestedToday": completed_targets_today,
            "archivedToday": archived_today,
            "completedTargetsToday": completed_targets_today,
            "downloading": sum(
                _active_download_count(item) > 0
                for item in unique_items.values()
            ),
            "activeDownloadTasks": sum(_active_download_count(item) for item in unique_items.values()),
            "concurrentDownloadGroups": sum(_active_download_count(item) > 1 for item in unique_items.values()),
            "pending": sum(result["healthState"] == "waiting" for _, _, result in visible_item_evidence),
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

        # 秒传状态只通过关注项与 systemIssues 表达：
        # recovering 使用处理中语义，action_required 只影响秒传关注项本身，
        # 均不改变基线的红色真实异常计数口径。
        secupload_state = str(secupload_issue.get("state") or "unknown")
        issues = []
        for target_key, item, result in visible_item_evidence:
            if result["healthState"] == "action_required":
                issue_copy = _safe_issue_copy(item, result)
                issues.append({
                    **result,
                    **issue_copy,
                    "targetKey": target_key,
                    "chainId": str(item.get("chainId") or item.get("id") or ""),
                    "title": str(item.get("title") or "未命名媒体"),
                })
        for result in [scheduler_evidence, *service_evidence, *([rss_evidence] if rss_evidence else [])]:
            if result["healthState"] == "action_required":
                issues.append({**result, "targetKey": "", "chainId": "", "title": result["source"]})

        # 口径统一：actionRequired 计数（首页指标与移动端角标共用，深链 /tasks?userState=action_required）
        # 只统计任务中心该筛选实际会列出的任务链；RSS 来源失败、调度与服务异常
        # 保留在 issues 列表（各自有独立深链），不再计入该计数。
        counts["actionRequired"] = sum(
            str(item.get("userState") or "") == "action_required"
            for _, item, _ in visible_item_evidence
        )

        services = chain.get("services") or {}
        qb_status = services.get("qb") if isinstance(services.get("qb"), dict) else {}
        symedia_status = services.get("symedia") if isinstance(services.get("symedia"), dict) else {}
        torra_status = services.get("torra") if isinstance(services.get("torra"), dict) else {}
        if qb_status.get("connected") is True:
            current_downloads_value = counts["activeDownloadTasks"]
            current_downloads_state = "processing" if current_downloads_value > 0 else "normal"
            current_downloads_detail = (
                f"qB 当前有 {current_downloads_value} 个下载任务正在执行"
                if current_downloads_value > 0
                else "qB 已连接，当前没有正在下载的任务"
            )
        else:
            current_downloads_value = None
            current_downloads_state = "unknown"
            current_downloads_detail = "qB 当前没有提供可验证的下载任务状态"
        counts["activeDownloadTasks"] = current_downloads_value

        secupload_failures = _integer(secupload_issue.get("failedTotal"))
        category_labels = "、".join(
            str(row.get("label") or "") for row in secupload_issue.get("categories") or [] if row.get("label")
        )
        focus_secupload_state = {
            "normal": "normal",
            "recovering": "processing",
            "action_required": "action_required",
            "unknown": "unknown",
        }.get(secupload_state, "unknown")
        secupload_detail = {
            "normal": "Torra 最近批次明确记录 0 个秒传失败",
            "recovering": f"{category_labels or '秒传失败分类'}正在自动恢复；下次计划已由 Torra 提供",
            "action_required": f"{category_labels or '秒传失败分类'}没有可用的自动恢复计划",
            "unknown": "Torra 尚未提供完整的分类批次证据；缺少逐文件证据不等于失败",
        }.get(secupload_state, "Torra 秒传状态尚不可确认")
        secupload_label = {
            "recovering": "秒传待恢复",
            "action_required": "秒传需要处理",
            "unknown": "秒传状态",
        }.get(secupload_state, "秒传失败")

        download_done_not_archived = [
            item
            for item in unique_items.values()
            if _step(item, "download").get("status") == "done"
            and _step(item, "download").get("evidence") == "verified"
            and _step(item, "download").get("healthState") == "normal"
            and _step(item, "library").get("status") != "done"
            and _step(item, "library").get("healthState") != "protected"
            and item.get("healthState") != "protected"
            and item.get("userState") in {"in_progress", "action_required"}
        ]
        if qb_status.get("connected") is True and symedia_status.get("connected") is True:
            downloaded_not_archived_value = len(download_done_not_archived)
            has_blocked_archive = any(
                _step(item, "library").get("healthState") == "action_required"
                for item in download_done_not_archived
            )
            downloaded_not_archived_state = (
                "action_required" if has_blocked_archive
                else "processing" if downloaded_not_archived_value > 0
                else "normal"
            )
            downloaded_not_archived_detail = (
                f"{downloaded_not_archived_value} 个任务已有下载完成证据，但入库尚未完成"
                if downloaded_not_archived_value > 0
                else "已核对下载与入库证据，没有下载完成后仍未入库的任务"
            )
        else:
            downloaded_not_archived_value = None
            downloaded_not_archived_state = "unknown"
            downloaded_not_archived_detail = "qB 或 Symedia 未提供完整连接证据，暂时无法核对"

        archived_today_value = archived_today
        if archived_today_value is not None and archived_today_value >= 0:
            archived_today_state = "normal"
            archived_today_detail = f"Symedia 今日明确记录 {archived_today_value} 个归档文件"
        else:
            archived_today_value = None
            archived_today_state = "unknown"
            archived_today_detail = "Symedia 尚未提供今日归档文件统计"

        missing_episodes_value = None
        missing_episodes_state = "unknown"
        missing_episodes_detail = "追更记录尚未提供可验证的缺集统计"
        subscription_workbench = self.app.extensions.get("mcc_subscription_workbench")
        if subscription_workbench:
            try:
                subscription_snapshot = subscription_workbench.snapshot(limit=None)
                subscription_items = [
                    item for item in subscription_snapshot.get("items") or [] if isinstance(item, dict)
                ]
                subscription_errors = [value for value in subscription_snapshot.get("errors") or [] if value]
                coverage_complete = all(
                    "missingEpisodes" in item and isinstance(item.get("missingEpisodes"), list)
                    for item in subscription_items
                )
                if not subscription_errors and coverage_complete:
                    missing_episodes_value = sum(
                        len(item.get("missingEpisodes") or []) for item in subscription_items
                    )
                    missing_episodes_state = "action_required" if missing_episodes_value > 0 else "normal"
                    missing_episodes_detail = (
                        f"追更记录明确标记 {missing_episodes_value} 集缺失"
                        if missing_episodes_value > 0
                        else "已核对追更记录，当前没有明确缺集"
                    )
            except Exception:
                pass

        focus_items = [
            _focus_item(
                "current_downloads", "当前下载", "个", current_downloads_value, current_downloads_state,
                current_downloads_detail, "/tasks?userState=in_progress",
            ),
            _focus_item(
                "secupload_failures", secupload_label, "个", secupload_failures, focus_secupload_state,
                secupload_detail, "/tasks?systemIssue=secupload_failures",
            ),
            _focus_item(
                "downloaded_not_archived", "下载完成未入库", "个", downloaded_not_archived_value,
                downloaded_not_archived_state, downloaded_not_archived_detail, "/tasks?userState=in_progress",
            ),
            _focus_item(
                "archived_today", "今日入库", "个文件", archived_today_value, archived_today_state,
                archived_today_detail, f"/tasks?userState=completed&completedDate={today_key}",
            ),
            _focus_item(
                "missing_episodes", "追更缺集", "集", missing_episodes_value, missing_episodes_state,
                missing_episodes_detail, "/following?missingEpisodes=1",
            ),
            _focus_item(
                "action_required", "真实异常", "项", counts["actionRequired"],
                "action_required" if counts["actionRequired"] > 0 else "normal",
                f"当前有 {counts['actionRequired']} 项具备明确失败或阻塞证据",
                "/tasks?userState=action_required",
            ),
        ]
        diagnostics = []
        if identity_evidence:
            diagnostics.append({
                "code": "TASK_IDENTITY_PENDING",
                "count": len(identity_only),
                "label": f"{len(identity_only)} 条记录尚未完成身份整理",
                "reasonText": identity_evidence["reasonText"],
                "href": "/tasks?advanced=1&identityState=unidentified&identityState=conflict",
            })
        for result in [
            *(value for _, _, value in visible_item_evidence),
            scheduler_evidence,
            *service_evidence,
            *([rss_evidence] if rss_evidence else []),
        ]:
            if result["healthState"] != "evidence_insufficient":
                continue
            diagnostics.append({
                "code": result.get("reasonCode") or "EVIDENCE_INSUFFICIENT",
                "count": 1,
                "label": result.get("reasonText") or "部分状态尚未形成可验证证据",
                "reasonText": result.get("reasonText") or "",
                "source": result.get("source") or "",
            })

        processing_targets = sum(
            str(item.get("userState") or "") == "in_progress"
            for item in unique_items.values()
        )
        if secupload_state == "recovering":
            processing_targets += 1
        critical_unknown = any(
            not isinstance(services.get(name), dict)
            or (
                services[name].get("connected") is not True
                and not services[name].get("error")
            )
            for name in ("qb", "symedia", "torra", "emby")
        )
        if issues:
            health_state = "action_required"
            # headline 跟随首页 issues 列表（含 RSS/服务/调度深链项），可以多于任务中心计数
            headline = f"有 {len(issues)} 项需要处理"
        elif processing_targets > 0 or (counts["activeDownloadTasks"] or 0) > 0:
            health_state = "waiting"
            headline = f"有 {max(processing_targets, counts['activeDownloadTasks'] or 0)} 个任务正在处理"
        elif critical_unknown:
            health_state = "evidence_insufficient"
            headline = "核心服务状态尚待确认"
        elif (counts["archivedToday"] or 0) > 0 or counts["completedTargetsToday"] > 0:
            health_state = "normal"
            headline = "今日媒体处理正常"
        else:
            health_state = "normal"
            headline = "影音中心运行正常"
        archived_today_text = counts["archivedToday"] if counts["archivedToday"] is not None else "未知"
        active_downloads_text = counts["activeDownloadTasks"] if counts["activeDownloadTasks"] is not None else "未知"
        detail = (
            f"归档文件 {archived_today_text} · 完成作品/季 {counts['completedTargetsToday']} · "
            f"qB 下载任务 {active_downloads_text} · 需要处理 {counts['actionRequired']}"
        )
        return {
            "ok": True,
            "generatedAt": now,
            "healthState": health_state,
            "headline": headline,
            "detail": detail,
            "counts": counts,
            "focusItems": focus_items,
            "issueTotal": len(issues),
            "issues": issues[:8],
            "diagnostics": diagnostics[:12],
            "diagnosticTotal": len(diagnostics),
            "systemIssues": [present_system_issue(secupload_issue)],
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
