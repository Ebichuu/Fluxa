from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from flask import Flask, jsonify

from app.health_state_runtime import evidence
from app.http_runtime import current_request_id
from app.resource_identity_runtime import target_key as resource_target_key
from app.secupload_issue_runtime import build_secupload_issue
from app.task_chain_v2_runtime import adapt_task_chain
from app.task_public_runtime import present_system_issue, safe_public_text


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


def _action_required_work_key(item: dict, target_key: str) -> str:
    media_type = str(item.get("mediaType") or "").strip().lower()
    tmdb_id = str(item.get("tmdbId") or "").strip()
    if media_type == "movie" and tmdb_id:
        return f"movie:tmdb:{tmdb_id}"
    season = _integer(item.get("seasonNumber"))
    if media_type == "tv" and tmdb_id and season is not None:
        return f"tv:tmdb:{tmdb_id}:season:{season}"
    resource_identity = str(item.get("chainId") or target_key or item.get("id") or "").strip()
    return f"resource:{resource_identity}"


def _positive_integer(value):
    parsed = _integer(value)
    return parsed if parsed is not None and parsed > 0 else None


def _reliable_media_identity(item: dict) -> bool:
    if str(item.get("identityState") or "") != "linked":
        return False
    media_type = str(item.get("mediaType") or "").strip().lower()
    if media_type not in {"movie", "tv"} or _positive_integer(item.get("tmdbId")) is None:
        return False
    return media_type == "movie" or _positive_integer(item.get("seasonNumber")) is not None


def _mechanical_title_key(value) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace() and not unicodedata.category(character).startswith("P")
    )


def _action_required_group_key(item: dict, target_key: str) -> str:
    media_type = str(item.get("mediaType") or "").strip().lower()
    tmdb_id = _positive_integer(item.get("tmdbId"))
    season = _positive_integer(item.get("seasonNumber"))
    if _reliable_media_identity(item):
        return (
            f"identity:movie:tmdb:{tmdb_id}"
            if media_type == "movie"
            else f"identity:tv:tmdb:{tmdb_id}:season:{season}"
        )
    resource_identity = str(item.get("chainId") or target_key or item.get("id") or "").strip()
    resource_key = f"resource:{resource_identity}"
    if str(item.get("identityState") or "") == "conflict" or media_type not in {"movie", "tv"}:
        return resource_key
    title = _mechanical_title_key(item.get("title"))
    if not title or (media_type == "tv" and season is None):
        return resource_key
    return f"display:{media_type}:{title}" + (f":season:{season}" if media_type == "tv" else "")


def _fresh_until(now: datetime, minutes: int = 5) -> str:
    return _iso(now + timedelta(minutes=minutes))


def _latest_item(current: dict | None, candidate: dict) -> dict:
    if current is None:
        return candidate
    current_updated = str(current.get("updatedAt") or "")
    candidate_updated = str(candidate.get("updatedAt") or "")
    return candidate if candidate_updated >= current_updated else current


def _fact(item: dict, stage: str) -> dict:
    return next((
        fact
        for fact in item.get("pipelineFacts") or []
        if isinstance(fact, dict) and str(fact.get("stage") or "") == stage
    ), {})


def _current_verified_fact(item: dict, stage: str, *states: str) -> dict:
    fact = _fact(item, stage)
    if (
        not fact
        or fact.get("isStale") is True
        or fact.get("evidence") != "verified"
        or (states and fact.get("state") not in states)
    ):
        return {}
    return fact


def _item_evidence(item: dict, now: str) -> dict:
    outcome = item.get("pipelineOutcome") or {}
    outcome_state = str(outcome.get("state") or "evidence_insufficient")
    outcome_fact = _fact(item, str(outcome.get("stage") or ""))
    health_state = {
        "playable": "normal",
        "action_required": "action_required",
        "in_progress": "waiting",
        "waiting": "waiting",
        "protected": "protected",
        "evidence_insufficient": "evidence_insufficient",
    }.get(outcome_state, "evidence_insufficient")
    result = evidence(
        state=health_state,
        source=str(outcome_fact.get("source") or outcome.get("stage") or "task-chain"),
        reason_code=str(outcome.get("reasonCode") or ""),
        reason_text=str(outcome.get("reasonText") or ""),
        observed_at=str(outcome.get("observedAt") or item.get("observedAt") or now),
        fresh_until=str(outcome_fact.get("freshUntil") or item.get("freshUntil") or ""),
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
            "EVIDENCE_INSUFFICIENT",
        }
    )


def _problem_fact(item: dict) -> dict:
    outcome = item.get("pipelineOutcome") or {}
    return _fact(item, str(outcome.get("stage") or ""))


def _integer(value):
    try:
        return int(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def _active_download_count(item: dict) -> int:
    if not _current_verified_fact(item, "qb", "active"):
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
    stage = _problem_fact(item)
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
    if source.casefold() == "symedia" or "SYMEDIA" in reason_code or stage.get("stage") == "symedia":
        if any(marker in raw_reason for marker in ("未找到", "未查询到", "识别", "TMDB", "媒体信息")):
            return {**base, "headline": f"{label}识别失败", "reasonText": "Symedia 未查询到对应媒体信息"}
        if result.get("healthState") == "action_required":
            return {**base, "headline": f"{label}入库失败", "reasonText": "Symedia 未完成媒体入库"}
    if result_reason_code == "TASK_IDENTITY_UNLINKED":
        return {**base, "headline": f"{label}尚未识别", "reasonText": "暂时无法确认这条记录对应的媒体作品"}
    if source == "qBittorrent" or "DOWNLOAD" in reason_code or stage.get("stage") == "qb":
        return {
            **base,
            "headline": f"{label}下载需要检查",
            "reasonText": safe_public_text(raw_reason or result.get("reasonText"), "qB 下载任务没有正常继续"),
        }
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
        chain = (
            chain_v2_service.full_snapshot()
            if chain_v2_service
            else adapt_task_chain(chain_service.get_chain(), now=now_value)
        )
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
        ingested_today = sum(
            _today_key(_current_verified_fact(item, "symedia", "succeeded").get("observedAt")) == today_key
            for item in unique_items.values()
        )
        playable_today = sum(
            str((item.get("pipelineOutcome") or {}).get("state") or "") == "playable"
            and _today_key((item.get("pipelineOutcome") or {}).get("playableAt")) == today_key
            for item in unique_items.values()
        )
        archive_summary = None
        archive_reader = getattr(chain_v2_service, "archive_summary", None)
        if callable(archive_reader):
            try:
                archive_summary = archive_reader(today_key, chain)
            except Exception:
                archive_summary = None
        symedia_totals = (((chain.get("services") or {}).get("symedia") or {}).get("totals") or {})
        archived_today = (
            _integer(archive_summary.get("archivedFiles"))
            if isinstance(archive_summary, dict)
            else _integer(symedia_totals.get("archivedToday"))
            if "archivedToday" in symedia_totals
            else None
        )
        if archived_today is not None and archived_today < 0:
            archived_today = None
        counts = {
            "ingestedToday": ingested_today,
            "archivedToday": archived_today,
            "completedTargetsToday": playable_today,
            "playableToday": playable_today,
            "downloading": sum(
                _active_download_count(item) > 0
                for item in unique_items.values()
            ),
            "activeDownloadTasks": sum(_active_download_count(item) for item in unique_items.values()),
            "concurrentDownloadGroups": sum(_active_download_count(item) > 1 for item in unique_items.values()),
            "pending": sum(
                str((item.get("pipelineOutcome") or {}).get("state") or "") == "waiting"
                for _, item, _ in visible_item_evidence
            ),
            "waiting": sum(
                str((item.get("pipelineOutcome") or {}).get("state") or "") == "waiting"
                for _, item, _ in visible_item_evidence
            ),
            "evidenceInsufficient": (
                sum(result["healthState"] == "evidence_insufficient" for _, _, result in visible_item_evidence)
                + (1 if identity_evidence else 0)
            ),
            "identityPending": len(identity_only),
            "actionRequired": 0,
            "mediaActionRequired": 0,
            "auxiliaryAlerts": 0,
            "inProgress": 0,
            "suspectedBlocked": sum(
                result.get("executionState") == "suspected_blocked" for _, _, result in visible_item_evidence
            ),
            "protected": sum(
                str((item.get("pipelineOutcome") or {}).get("state") or "") == "protected"
                for _, item, _ in item_evidence
            ),
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
        media_issues = []
        for target_key, item, result in visible_item_evidence:
            if result["healthState"] == "action_required":
                issue_copy = _safe_issue_copy(item, result)
                issue_href = "/tasks?" + urlencode({
                    "outcomeState": "action_required",
                    "chainId": str(item.get("chainId") or ""),
                })
                media_issues.append({
                    **result,
                    **issue_copy,
                    "issueKind": "media",
                    "targetKey": target_key,
                    "chainId": str(item.get("chainId") or item.get("id") or ""),
                    "title": str(item.get("title") or "未命名媒体"),
                    "href": issue_href,
                })
        auxiliary_issues = []
        for result in [scheduler_evidence, *service_evidence, *([rss_evidence] if rss_evidence else [])]:
            if result["healthState"] == "action_required":
                source = str(result.get("source") or "")
                auxiliary_issues.append({
                    **result,
                    "issueKind": "auxiliary",
                    "targetKey": "",
                    "chainId": "",
                    "title": source,
                    "href": "/rss-library" if source == "private-rss" else "/control",
                })
        if secupload_state == "action_required":
            auxiliary_issues.append({
                **evidence(
                    state="action_required",
                    source="torra-secupload",
                    reason_code="SECUPLOAD_RETRY_REQUIRED",
                    reason_text=str(secupload_issue.get("stateReason") or "秒传失败需要人工处理"),
                    observed_at=str(secupload_issue.get("observedAt") or now),
                    fresh_until=_fresh_until(now_value),
                ),
                "issueKind": "auxiliary",
                "targetKey": "",
                "chainId": "",
                "title": "Torra 秒传",
                "href": "/tasks?systemIssue=secupload_failures",
            })
        issues = [*media_issues, *auxiliary_issues]

        # 口径统一：actionRequired 计数（首页指标与移动端角标共用，深链 outcomeState=action_required）
        # 只统计任务中心该筛选实际会列出的任务链；RSS 来源失败、调度与服务异常
        # 保留在 issues 列表（各自有独立深链），不再计入该计数。
        action_required_evidence = [
            (target_key, item)
            for target_key, item, result in visible_item_evidence
            if result["healthState"] == "action_required"
        ]
        counts["mediaActionRequired"] = len(media_issues)
        counts["actionRequired"] = counts["mediaActionRequired"]
        counts["actionRequiredResources"] = counts["mediaActionRequired"]
        counts["actionRequiredWorks"] = len({
            _action_required_work_key(item, target_key)
            for target_key, item in action_required_evidence
        })
        counts["actionRequiredGroups"] = len({
            _action_required_group_key(item, target_key)
            for target_key, item in action_required_evidence
        })
        counts["actionRequiredIdentityUnconfirmedResources"] = sum(
            not _reliable_media_identity(item)
            for _, item in action_required_evidence
        )
        counts["auxiliaryAlerts"] = len(auxiliary_issues)

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

        downloaded_candidates = [
            item
            for item in unique_items.values()
            if _current_verified_fact(item, "qb", "succeeded")
        ]
        download_done_not_archived = [
            item for item in downloaded_candidates
            if _current_verified_fact(item, "symedia", "waiting", "active", "failed")
        ]
        downloaded_archive_unknown = any(
            not _current_verified_fact(item, "symedia", "waiting", "active", "succeeded", "failed", "protected")
            for item in downloaded_candidates
        )
        if qb_status.get("connected") is True and symedia_status.get("connected") is True:
            downloaded_not_archived_value = None if downloaded_archive_unknown else len(download_done_not_archived)
            has_blocked_archive = any(
                bool(_current_verified_fact(item, "symedia", "failed"))
                for item in download_done_not_archived
            )
            downloaded_not_archived_state = (
                "unknown" if downloaded_not_archived_value is None
                else "action_required" if has_blocked_archive
                else "processing" if downloaded_not_archived_value > 0
                else "normal"
            )
            downloaded_not_archived_detail = (
                "部分下载完成任务缺少当前 Symedia 证据，暂时无法确认入库结果"
                if downloaded_not_archived_value is None
                else f"{downloaded_not_archived_value} 个任务已有下载完成证据，但入库尚未完成"
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
            archived_today_detail = (
                f"Symedia 今日归档 {archived_today_value} 个文件 · "
                f"关联 {archive_summary.get('linkedTasks', 0)} 个任务 · "
                f"未关联 {archive_summary.get('unlinkedFiles', 0)} 个文件"
                if isinstance(archive_summary, dict)
                else f"Symedia 今日明确记录 {archived_today_value} 个归档文件"
            )
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
                current_downloads_detail, "/tasks?outcomeState=in_progress",
            ),
            _focus_item(
                "secupload_failures", secupload_label, "个", secupload_failures, focus_secupload_state,
                secupload_detail, "/tasks?systemIssue=secupload_failures",
            ),
            _focus_item(
                "downloaded_not_archived", "下载完成未入库", "个", downloaded_not_archived_value,
                downloaded_not_archived_state, downloaded_not_archived_detail, "/tasks?outcomeState=in_progress",
            ),
            _focus_item(
                "archived_today", "今日入库", "个文件", archived_today_value, archived_today_state,
                archived_today_detail, f"/tasks?archivedDate={today_key}",
            ),
            _focus_item(
                "missing_episodes", "追更缺集", "集", missing_episodes_value, missing_episodes_state,
                missing_episodes_detail, "/following?missingEpisodes=1",
            ),
            _focus_item(
                "action_required", "需要处理", "个问题组", counts["actionRequiredGroups"],
                "action_required" if counts["actionRequired"] > 0 else "normal",
                f"{counts['actionRequiredGroups']} 个问题组 · 涉及 {counts['actionRequiredResources']} 个资源"
                f" · 其中 {counts['actionRequiredIdentityUnconfirmedResources']} 条身份未确认",
                "/tasks?outcomeState=action_required",
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

        media_in_progress = sum(
            str((item.get("pipelineOutcome") or {}).get("state") or "") == "in_progress"
            for item in unique_items.values()
        )
        recovering_secupload = (
            max(0, secupload_failures or 0)
            if secupload_state == "recovering" and secupload_failures is not None
            else 0
        )
        counts["inProgress"] = media_in_progress + recovering_secupload
        critical_unknown = any(
            not isinstance(services.get(name), dict)
            or (
                services[name].get("connected") is not True
                and not services[name].get("error")
            )
            for name in ("qb", "symedia", "torra", "emby")
        )
        if counts["mediaActionRequired"] > 0:
            health_state = "action_required"
            headline = (
                f"{counts['actionRequiredGroups']} 个问题组"
                f" · 涉及 {counts['actionRequiredResources']} 个资源"
                f" · 其中 {counts['actionRequiredIdentityUnconfirmedResources']} 条身份未确认"
            )
        elif counts["auxiliaryAlerts"] > 0:
            health_state = "action_required"
            headline = f"有 {counts['auxiliaryAlerts']} 项辅助能力提醒"
        elif counts["inProgress"] > 0:
            health_state = "waiting"
            headline = f"有 {counts['inProgress']} 项任务正在处理"
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
            f"归档文件 {archived_today_text} · 已可播放 {counts['playableToday']} · "
            f"qB 下载任务 {active_downloads_text} · 需处理问题组 {counts['actionRequiredGroups']}"
            f"（{counts['actionRequiredResources']} 个资源，"
            f"{counts['actionRequiredIdentityUnconfirmedResources']} 条身份未确认） · "
            f"辅助提醒 {counts['auxiliaryAlerts']}"
        )
        return {
            "ok": True,
            "generatedAt": now,
            "healthState": health_state,
            "headline": headline,
            "detail": detail,
            "counts": counts,
            "archiveSummary": archive_summary,
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
