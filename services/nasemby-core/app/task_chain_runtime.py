from __future__ import annotations

import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from flask import Flask, jsonify

from app import discover_runtime
from app.evidence_ownership_runtime import adjudicate_task_evidence, compare_legacy_ownership
from app.episode_evidence_runtime import build_episode_evidence
from app.task_exception_runtime import protection_rule


STATE_PRIORITY = {"blocked": 0, "active": 1, "waiting": 2, "completed": 3}


def _number(value) -> float:
    try:
        result = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _string(value) -> str:
    return str(value if value is not None else "").strip()


def _first_text(row: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _string(row.get(key))
        if value:
            return value
    return ""


def _flatten_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [item for part in value for item in _flatten_strings(part)]
    if isinstance(value, dict):
        return [item for part in value.values() for item in _flatten_strings(part)]
    return []


def _season_from_text(value: str) -> int:
    match = re.search(r"(?:^|[.\s_-])S(?:eason)?[.\s_-]*0*(\d{1,2})(?:[.\s_-]|$)", value, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _torra_media_type(row: dict) -> str:
    value = _string(row.get("media_type") or row.get("type")).lower()
    if value in {"movie", "film", "电影"}:
        return "movie"
    if value in {"tv", "series", "电视剧", "剧集"}:
        return "tv"
    return "unknown"


def _torra_files(row: dict | None) -> list[str]:
    if not row:
        return []
    return [
        *_flatten_strings(row.get("downloaded_file_names")),
        *_flatten_strings(row.get("downloaded_episode_files")),
        *_flatten_strings(row.get("library_file_names")),
        *_flatten_strings(row.get("library_episode_files")),
    ]


def _torra_has_download_evidence(row: dict | None) -> bool:
    return bool(
        row
        and (
            _torra_files(row)
            or _flatten_strings(row.get("downloaded_episode_numbers"))
        )
    )


def _symedia_season(row: dict) -> int:
    if _number(row.get("season")):
        return int(_number(row.get("season")))
    match = re.search(r"S0*(\d{1,2})", _string(row.get("season_episode")), re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _iso_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def _iso_from_seconds(value) -> str:
    seconds = _number(value)
    return _iso_datetime(datetime.fromtimestamp(seconds, timezone.utc)) if seconds > 0 else ""


def _newest(values) -> str:
    normalized = [_string(value) for value in values]
    normalized = [value for value in normalized if value]
    return sorted(normalized)[-1] if normalized else ""


def _qb_control(tasks: list[dict]) -> dict:
    paused = sum("pause" in _string(task.get("state")).lower() for task in tasks)
    return {
        "total": len(tasks),
        "paused": paused,
        "canPause": len(tasks) > paused,
        "canResume": bool(tasks) and paused == len(tasks),
    }


def _download_step(tasks: list[dict], torra: dict | None) -> dict:
    stalled = next((task for task in tasks if task.get("status") == "stalled"), None)
    if stalled:
        return {
            "key": "download",
            "label": "获取 / 下载",
            "status": "blocked",
            "evidence": "verified",
            "detail": f"qB 卡住在 {round(_number(stalled.get('progress')) * 100)}%",
            "timestamp": _iso_from_seconds(stalled.get("addedOn")),
            "source": "qBittorrent",
        }
    if tasks and all(task.get("status") == "completed" for task in tasks):
        return {
            "key": "download",
            "label": "获取 / 下载",
            "status": "done",
            "evidence": "verified",
            "detail": f"{len(tasks)} 个 qB 任务已完成",
            "timestamp": _newest(_iso_from_seconds(task.get("completionOn")) for task in tasks),
            "source": "qBittorrent",
        }
    if any(task.get("status") in {"downloading", "queued"} for task in tasks):
        progress = round(sum(_number(task.get("progress")) for task in tasks) / len(tasks) * 100)
        return {
            "key": "download",
            "label": "获取 / 下载",
            "status": "active",
            "evidence": "verified",
            "detail": f"{progress}% · {len(tasks)} 个 qB 任务",
            "timestamp": _newest(_iso_from_seconds(task.get("addedOn")) for task in tasks),
            "source": "qBittorrent",
        }
    if tasks:
        return {
            "key": "download",
            "label": "获取 / 下载",
            "status": "waiting",
            "evidence": "verified",
            "detail": "qB 任务已暂停或等待",
            "timestamp": _newest(_iso_from_seconds(task.get("addedOn")) for task in tasks),
            "source": "qBittorrent",
        }
    if _torra_has_download_evidence(torra):
        return {
            "key": "download", "label": "获取 / 下载", "status": "done",
            "evidence": "verified", "detail": "Torra 保留有下载记录", "timestamp": "", "source": "Torra",
        }
    if torra:
        return {
            "key": "download", "label": "获取 / 下载", "status": "waiting",
            "evidence": "verified", "detail": "Torra 订阅存在，尚无下载记录", "timestamp": "", "source": "Torra",
        }
    return {
        "key": "download", "label": "获取 / 下载", "status": "unknown",
        "evidence": "missing", "detail": "未关联 Torra 或 qB 任务", "timestamp": "", "source": "",
    }


def _parse_timestamp(value: str):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _cloud_step(download: dict, symedia_rows: list[dict], upload_summary: dict | None = None) -> dict:
    if symedia_rows:
        return {
            "key": "cloud115", "label": "进入 115", "status": "done", "evidence": "verified",
            "detail": f"Symedia 已收到 {len(symedia_rows)} 条源文件记录；具体上传方式未确认",
            "timestamp": _newest(row.get("date") for row in symedia_rows), "source": "Symedia",
        }
    if download["status"] == "done":
        plugin_readable = bool((upload_summary or {}).get("readable"))
        return {
            "key": "cloud115", "label": "进入 115", "status": "unknown", "evidence": "missing",
            "detail": (
                "下载已完成，但 Torra 秒传插件暂未提供逐文件证据"
                if plugin_readable
                else "下载已完成，但当前无法读取逐文件秒传证据"
            ),
            "timestamp": download["timestamp"],
            "source": "Torra secupload_115" if plugin_readable else "",
            "reasonCode": "TORRA_SECUPLOAD_FILE_EVIDENCE_UNAVAILABLE",
        }
    return {
        "key": "cloud115", "label": "进入 115", "status": "waiting", "evidence": "missing",
        "detail": "等待下载完成", "timestamp": "", "source": "",
    }


def _library_step(rows: list[dict], emby_indexed: bool) -> dict:
    if not rows:
        return {
            "key": "library", "label": "入库", "status": "waiting", "evidence": "missing",
            "detail": "尚无 Symedia 入库记录", "timestamp": "", "source": "",
        }
    failed = [row for row in rows if row.get("status") is False]
    success = len(rows) - len(failed)
    timestamp = _newest(row.get("date") for row in rows)
    if failed:
        classified = [
            (row, protection_rule(row.get("reasonCode"), row.get("errmsg")))
            for row in failed
        ]
        real_failures = [row for row, rule in classified if not rule]
        protected_rules = sorted({rule for _, rule in classified if rule})
        selected = real_failures[0] if real_failures else failed[0]
        reason = _string(selected.get("errmsg")) or "Symedia 返回失败"
        reason_code = (
            _string(selected.get("reasonCode")) or "SYMEDIA_LIBRARY_FAILED"
            if real_failures
            else protected_rules[0]
        )
        return {
            "key": "library", "label": "入库", "status": "blocked", "evidence": "verified",
            "detail": f"{success} 成功 / {len(failed)} 失败 · {reason}",
            "timestamp": timestamp, "source": "Symedia", "reasonCode": reason_code,
            "matchedProtectionRule": protected_rules[0] if protected_rules and not real_failures else "",
            "protectionRules": protected_rules if not real_failures else [],
        }
    detail = f"{success} 条 Symedia 成功 · Emby {'已索引' if emby_indexed else '尚未确认'}"
    return {
        "key": "library", "label": "入库", "status": "done", "evidence": "verified",
        "detail": detail, "timestamp": timestamp,
        "source": "Symedia + Emby" if emby_indexed else "Symedia",
    }


def _item_state(steps: list[dict]) -> str:
    if any(step["status"] == "blocked" for step in steps):
        return "blocked"
    if steps[-1]["status"] == "done":
        return "completed"
    if any(step["status"] == "active" for step in steps):
        return "active"
    return "waiting"


def _current_step(steps: list[dict]) -> str:
    for status in ("blocked", "active"):
        match = next((step for step in steps if step["status"] == status), None)
        if match:
            return match["key"]
    return next((step["key"] for step in steps if step["status"] != "done"), steps[-1]["key"])


def _task_progress(tasks: list[dict], download: dict, library: dict) -> int:
    if library["status"] == "done":
        return 100
    if tasks:
        return round(sum(_number(task.get("progress")) for task in tasks) / len(tasks) * 100)
    return 100 if download["status"] == "done" else 0


def _suggestion(steps: list[dict], urls: dict):
    blocked = next((step for step in steps if step["status"] == "blocked"), None)
    if not blocked:
        return None
    if blocked["key"] == "download":
        return {"label": "打开 qB", "url": urls["qb"]}
    if blocked["key"] == "cloud115":
        return {"label": "打开 Torra", "url": urls["torra"]}
    return {"label": "打开 Symedia", "url": urls["symedia"]}


def _emby_has(index, media_type: str, tmdb_id: str) -> bool:
    if not index or not tmdb_id:
        return False
    return tmdb_id in index["movies" if media_type == "movie" else "series"]


def _acquisition_state(subscription, torra, qb_tasks, symedia_rows, indexed, policy, now):
    enabled = bool(policy.get("enabled"))
    subscription_enabled = bool(subscription.get("allowCloudFallback"))
    if indexed or symedia_rows:
        state, detail = "completed", "已经进入入库链路"
    elif torra or qb_tasks:
        state, detail = "blocked_by_pt", "PT 主链已有证据，不启动网盘"
    elif not enabled:
        state, detail = "disabled", "全局网盘通道关闭"
    elif not subscription_enabled:
        state, detail = "subscription_disabled", "当前订阅未允许网盘兜底"
    elif not policy.get("auto_fallback_enabled"):
        state = "manual_only" if policy.get("manual_actions_enabled") else "disabled"
        detail = "只允许人工预览" if state == "manual_only" else "网盘人工与自动动作均关闭"
    else:
        created_at = _parse_timestamp(subscription.get("createdAt") or "")
        waited_minutes = max(0, (now - created_at).total_seconds() / 60) if created_at else 0
        wait_minutes = max(30, int(_number(policy.get("wait_minutes")) or 360))
        if waited_minutes >= wait_minutes:
            state, detail = "cloud_allowed", "PT 等待达到阈值，允许进入网盘候选"
        else:
            state, detail = "pt_waiting", f"继续等待 PT，阈值 {wait_minutes} 分钟"
    return {
        "primary": "pt",
        "cloudState": state,
        "cloudDetail": detail,
        "cloudEnabled": enabled,
        "subscriptionCloudEnabled": subscription_enabled,
        "autoFallbackEnabled": bool(policy.get("auto_fallback_enabled")),
        "manualActionsEnabled": bool(policy.get("manual_actions_enabled")),
    }


def _build_subscription_item(
    subscription,
    torra,
    matched_qb,
    matched_symedia,
    ownership_records,
    episode_evidence,
    emby_index,
    urls,
    source,
    now,
    cloud_policy,
    upload_summary,
):
    subscription_source = _string(subscription.get("sourceLabel")) or source
    subscription_step = {
        "key": "subscription", "label": "订阅", "status": "done", "evidence": "verified",
        "detail": f"{subscription_source} 与 Torra 订阅已关联" if torra else f"{subscription_source} 订阅存在，Torra 尚未关联",
        "timestamp": subscription["createdAt"],
        "source": f"{subscription_source} + Torra" if torra else subscription_source,
    }
    download = _download_step(matched_qb, torra)
    cloud = _cloud_step(download, matched_symedia, upload_summary)
    indexed = _emby_has(emby_index, subscription["mediaType"], subscription["tmdbId"])
    library = _library_step(matched_symedia, indexed)
    steps = [subscription_step, download, cloud, library]
    record_confidences = {str(record.get("confidence") or "") for record in ownership_records}
    confidence = (
        "strong"
        if "strong" in record_confidences or subscription["tmdbId"]
        else "fallback"
        if "fallback" in record_confidences
        else "unlinked"
    )
    item = {
        "id": f"subscription:{subscription['id']}",
        "title": subscription["title"],
        "mediaType": subscription["mediaType"],
        "tmdbId": subscription["tmdbId"],
        "seasonNumber": subscription.get("seasonNumber") or 0,
        "posterUrl": subscription["posterUrl"],
        "origin": "subscription",
        "channel": "PT",
        "state": _item_state(steps),
        "confidence": confidence,
        "progress": _task_progress(matched_qb, download, library),
        "currentStep": _current_step(steps),
        "steps": steps,
        "embyIndexed": indexed,
        "suggestion": _suggestion(steps, urls),
        "qbControl": _qb_control(matched_qb),
        "sourceIds": {
            "subscriptionId": subscription["id"],
            "torraId": _string((torra or {}).get("id")),
            "qbHashes": [_string(task.get("hash")) for task in matched_qb],
            "symediaIds": [_string(row.get("id") or f"{row.get('date')}:{row.get('src')}") for row in matched_symedia],
        },
        "evidenceOwnership": ownership_records,
        "episodeEvidence": episode_evidence,
        "updatedAt": _newest([subscription["updatedAt"], *(step["timestamp"] for step in steps)]),
        "acquisition": _acquisition_state(
            subscription,
            torra,
            matched_qb,
            matched_symedia,
            indexed,
            cloud_policy,
            now,
        ),
    }
    return item, matched_qb, matched_symedia


def _orphan_qb_item(task: dict, ownership: dict, urls: dict, upload_summary: dict | None = None) -> dict:
    subscription = {
        "key": "subscription", "label": "订阅", "status": "unknown", "evidence": "missing",
        "detail": "未关联订阅中枢", "timestamp": "", "source": "",
    }
    download = _download_step([task], None)
    cloud = _cloud_step(download, [], upload_summary)
    library = _library_step([], False)
    steps = [subscription, download, cloud, library]
    episode_evidence = build_episode_evidence(qb_pairs=[(task, ownership)])
    episode_number = (
        episode_evidence[0]["episodeStart"]
        if len(episode_evidence) == 1 and episode_evidence[0]["episodeStart"] == episode_evidence[0]["episodeEnd"]
        else None
    )
    return {
        "id": f"qb:{_string(task.get('hash'))}", "title": _string(task.get("name")),
        "mediaType": "unknown", "tmdbId": "", "seasonNumber": _season_from_text(_string(task.get("name"))),
        "episodeNumber": episode_number,
        "posterUrl": "", "origin": "download", "channel": "PT", "state": _item_state(steps),
        "confidence": ownership.get("confidence") or "unlinked", "progress": round(_number(task.get("progress")) * 100),
        "currentStep": _current_step(steps), "steps": steps, "embyIndexed": False,
        "suggestion": _suggestion(steps, urls), "qbControl": _qb_control([task]),
        "sourceIds": {"subscriptionId": "", "torraId": "", "qbHashes": [_string(task.get("hash"))], "symediaIds": []},
        "evidenceOwnership": [ownership],
        "episodeEvidence": episode_evidence,
        "reasonCode": "EVIDENCE_OWNER_CONFLICT" if ownership.get("confidence") == "conflict" else "TASK_IDENTITY_UNLINKED",
        "reasonText": "下载证据存在多个媒体候选，暂未绑定" if ownership.get("confidence") == "conflict" else "下载证据尚未关联到媒体目标",
        "updatedAt": _newest([_iso_from_seconds(task.get("completionOn")), _iso_from_seconds(task.get("addedOn"))]),
    }


def _orphan_symedia_item(row: dict, ownership: dict, urls: dict) -> dict:
    subscription = {
        "key": "subscription", "label": "订阅", "status": "unknown", "evidence": "missing",
        "detail": "未关联订阅中枢", "timestamp": "", "source": "",
    }
    download = {
        "key": "download", "label": "获取 / 下载", "status": "unknown", "evidence": "missing",
        "detail": "没有上游下载关联", "timestamp": "", "source": "",
    }
    cloud = {
        "key": "cloud115", "label": "进入 115", "status": "done", "evidence": "verified",
        "detail": "Symedia 已收到源文件", "timestamp": _string(row.get("date")), "source": "Symedia",
    }
    library = _library_step([row], False)
    steps = [subscription, download, cloud, library]
    row_id = _string(row.get("id") or f"{row.get('date')}:{row.get('src')}")
    media_type = _string(row.get("type"))
    episode_evidence = build_episode_evidence(symedia_pairs=[(row, ownership)])
    episode_number = (
        episode_evidence[0]["episodeStart"]
        if len(episode_evidence) == 1 and episode_evidence[0]["episodeStart"] == episode_evidence[0]["episodeEnd"]
        else None
    )
    return {
        "id": f"symedia:{row_id}",
        "title": _string(row.get("title")) or os.path.basename(_string(row.get("src"))) or "未识别入库记录",
        "mediaType": media_type if media_type in {"movie", "tv"} else "unknown",
        "tmdbId": _string(row.get("tmdbid")), "seasonNumber": _symedia_season(row),
        "episodeNumber": episode_number,
        "posterUrl": "", "origin": "library", "channel": "PT", "state": _item_state(steps),
        "confidence": ownership.get("confidence") or "unlinked", "progress": 100 if library["status"] == "done" else 0,
        "currentStep": _current_step(steps), "steps": steps, "embyIndexed": False,
        "suggestion": _suggestion(steps, urls), "qbControl": _qb_control([]),
        "sourceIds": {"subscriptionId": "", "torraId": "", "qbHashes": [], "symediaIds": [row_id]},
        "evidenceOwnership": [ownership],
        "episodeEvidence": episode_evidence,
        "reasonCode": "EVIDENCE_OWNER_CONFLICT" if ownership.get("confidence") == "conflict" else "TASK_IDENTITY_UNLINKED",
        "reasonText": "入库证据存在多个媒体候选，暂未绑定" if ownership.get("confidence") == "conflict" else "入库证据尚未关联到媒体目标",
        "updatedAt": _string(row.get("date")),
    }


def _orphan_torra_item(row: dict, ownership: dict, urls: dict, upload_summary: dict | None = None) -> dict:
    has_download = _torra_has_download_evidence(row)
    subscription = {
        "key": "subscription", "label": "订阅", "status": "done", "evidence": "verified",
        "detail": "Torra 订阅存在，但尚未关联 Fluxa 目标", "timestamp": "", "source": "Torra",
    }
    download = {
        "key": "download", "label": "获取 / 下载", "status": "done" if has_download else "waiting",
        "evidence": "verified", "detail": "Torra 保留有下载记录" if has_download else "Torra 尚无下载记录",
        "timestamp": "", "source": "Torra",
    }
    cloud = _cloud_step(download, [], upload_summary)
    library = _library_step([], False)
    steps = [subscription, download, cloud, library]
    episode_evidence = build_episode_evidence(torra_pairs=[(row, ownership)])
    episode_number = (
        episode_evidence[0]["episodeStart"]
        if len(episode_evidence) == 1 and episode_evidence[0]["episodeStart"] == episode_evidence[0]["episodeEnd"]
        else None
    )
    return {
        "id": f"torra:{_string(row.get('id')) or ownership.get('artifactKey')}",
        "title": _string(row.get("name") or row.get("keyword")) or "未识别 Torra 订阅",
        "mediaType": _torra_media_type(row),
        "tmdbId": _string(row.get("tmdb_id")),
        "seasonNumber": int(_number(row.get("season_number"))),
        "episodeNumber": episode_number,
        "posterUrl": "",
        "origin": "subscription",
        "channel": "PT",
        "state": _item_state(steps),
        "confidence": ownership.get("confidence") or "unlinked",
        "progress": 0,
        "currentStep": _current_step(steps),
        "steps": steps,
        "embyIndexed": False,
        "suggestion": _suggestion(steps, urls),
        "qbControl": _qb_control([]),
        "sourceIds": {
            "subscriptionId": "",
            "torraId": _string(row.get("id")),
            "qbHashes": [],
            "symediaIds": [],
        },
        "evidenceOwnership": [ownership],
        "episodeEvidence": episode_evidence,
        "reasonCode": "EVIDENCE_OWNER_CONFLICT" if ownership.get("confidence") == "conflict" else "TASK_IDENTITY_UNLINKED",
        "reasonText": "Torra 证据存在多个媒体候选，暂未绑定" if ownership.get("confidence") == "conflict" else "Torra 证据尚未关联到媒体目标",
        "updatedAt": _string(row.get("updated_at") or row.get("created_at")),
    }


def build_task_chain(input_data: dict) -> dict:
    now = input_data.get("now") or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    ownership = adjudicate_task_evidence(
        input_data["subscriptions"],
        input_data["torraRows"],
        input_data["qb"]["tasks"],
        input_data["symediaRows"],
    )
    ownership_comparison = compare_legacy_ownership(
        input_data["subscriptions"],
        input_data["torraRows"],
        input_data["qb"]["tasks"],
        input_data["symediaRows"],
        ownership,
    )
    subscription_items = []
    for subscription in input_data["subscriptions"]:
        target = ownership["subscriptionTargets"].get(str(subscription.get("id") or ""))
        bucket = ownership["owned"].get(target) or {"torra": [], "qb": [], "symedia": [], "records": []}
        torra_candidates = sorted(
            bucket["torra"],
            key=lambda value: (
                0 if value[1].get("matchMethod") == "tmdb_exact" else 1,
                str(value[1].get("artifactKey") or ""),
            ),
        )
        torra = torra_candidates[0][0] if torra_candidates else None
        qb_tasks = [row for row, _ in bucket["qb"]]
        symedia_rows = [row for row, _ in bucket["symedia"]]
        item, qb_tasks, symedia_rows = _build_subscription_item(
            subscription,
            torra,
            qb_tasks,
            symedia_rows,
            bucket["records"],
            build_episode_evidence(
                torra_pairs=bucket["torra"],
                qb_pairs=bucket["qb"],
                symedia_pairs=bucket["symedia"],
            ),
            input_data.get("embyIndex"),
            input_data["urls"],
            input_data.get("subscriptionSource") or "中控",
            now,
            input_data.get("cloudPolicy") or {},
            input_data.get("torraUpload") or {},
        )
        subscription_items.append(item)
    orphan_qb = [
        _orphan_qb_item(row, record, input_data["urls"], input_data.get("torraUpload") or {})
        for row, record in ownership["unowned"]["qb"]
    ]
    orphan_symedia = [
        _orphan_symedia_item(row, record, input_data["urls"])
        for row, record in ownership["unowned"]["symedia"]
    ][:50]
    orphan_torra = [
        _orphan_torra_item(row, record, input_data["urls"], input_data.get("torraUpload") or {})
        for row, record in ownership["unowned"]["torra"]
    ][:50]
    items = [*subscription_items, *orphan_qb, *orphan_symedia, *orphan_torra]
    items.sort(key=lambda item: item["updatedAt"], reverse=True)
    items.sort(key=lambda item: STATE_PRIORITY[item["state"]])
    errors = input_data.get("serviceErrors") or {}
    qb = input_data["qb"]
    emby_index = input_data.get("embyIndex")
    urls = input_data["urls"]
    return {
        "generatedAt": _iso_datetime(now),
        "evidenceOwnership": {
            "summary": ownership["summary"],
            "records": ownership["records"],
        },
        "ownershipComparison": ownership_comparison,
        "items": items,
        "counts": {
            "total": len(items),
            "active": sum(item["state"] == "active" for item in items),
            "blocked": sum(item["state"] == "blocked" for item in items),
            "completed": sum(item["state"] == "completed" for item in items),
            "waiting": sum(item["state"] == "waiting" for item in items),
            "unlinked": sum(item["confidence"] == "unlinked" for item in items),
        },
        "services": {
            "qb": {
                "connected": bool(qb.get("connected")), "error": _string(qb.get("error")),
                "total": qb["counts"]["total"], "active": qb["counts"]["active"],
                "downloadSpeed": qb["transfer"]["downloadSpeed"], "webUrl": urls["qb"],
            },
            "torra": {
                "connected": bool(urls["torra"]) and not errors.get("torra"),
                "error": errors.get("torra") or "", "total": len(input_data["torraRows"]), "webUrl": urls["torra"],
                "secupload115": input_data.get("torraUpload") or {},
            },
            "symedia": {
                "connected": bool(urls["symedia"]) and not errors.get("symedia"),
                "error": errors.get("symedia") or "", "total": input_data.get("symediaTotal", len(input_data["symediaRows"])),
                "sampled": len(input_data["symediaRows"]), "webUrl": urls["symedia"],
            },
            "emby": {
                "connected": emby_index is not None and not errors.get("emby"),
                "error": errors.get("emby") or "", "indexedMovies": len((emby_index or {}).get("movies", set())),
                "indexedSeries": len((emby_index or {}).get("series", set())), "webUrl": urls["emby"],
            },
        },
    }


def _subscription_media_type(row: dict) -> str:
    value = _first_text(row, ("media_type", "type")).lower()
    if value in {"tv", "series"} or "剧" in value:
        return "tv"
    if value in {"movie", "film"} or "电影" in value:
        return "movie"
    return "unknown"


def _subscription_season(row: dict):
    for key in ("target_season", "current_season", "latest_season", "season_number", "season"):
        if row.get(key) in {None, ""}:
            continue
        value = _number(row.get(key))
        if value >= 0 and value.is_integer():
            return int(value)
    return None


def map_task_subscriptions(payload: dict) -> list[dict]:
    rows = payload.get("items") if isinstance(payload, dict) else []
    result = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        media_type = _subscription_media_type(row)
        item = {
            "id": _first_text(row, ("key", "subscription_key", "dedupe_key", "id")),
            "title": _first_text(row, ("title", "name")),
            "mediaType": media_type,
            "tmdbId": _first_text(row, ("tmdb_id",)),
            "posterUrl": _first_text(row, ("poster_url", "poster")),
            "year": _first_text(row, ("year",)) or _first_text(row, ("release_date", "air_date"))[:4],
            "seasonNumber": _subscription_season(row),
            "createdAt": _first_text(row, ("created_at",)),
            "updatedAt": _first_text(row, ("updated_at",)),
            "allowCloudFallback": bool(row.get("allow_cloud_fallback")),
            "sourceLabel": _first_text(row, ("source_label", "sourceLabel", "source")),
        }
        if item["id"] and item["title"] and media_type in {"movie", "tv"}:
            result.append(item)
    return result


def _read_subscriptions():
    return map_task_subscriptions(discover_runtime.load_subscription_items(
        with_progress=False,
        remove_completed=False,
        persist_progress=False,
    ))


def _task_target_identity(subscription: dict) -> tuple:
    media_type = _string(subscription.get("mediaType"))
    tmdb_id = _string(subscription.get("tmdbId"))
    season = int(_number(subscription.get("seasonNumber")) or 0) if media_type == "tv" else 0
    return media_type, tmdb_id, season


def _torra_task_subscriptions(rows: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        media_type = _torra_media_type(row)
        remote_id = _string(row.get("id"))
        title = _first_text(row, ("name", "keyword"))
        tmdb_id = _first_text(row, ("tmdb_id", "tmdbid"))
        season = int(_number(row.get("season_number", row.get("season"))) or 0)
        if not remote_id or not title or media_type not in {"movie", "tv"}:
            continue
        result.append({
            "id": f"torra:{remote_id}",
            "title": title,
            "mediaType": media_type,
            "tmdbId": tmdb_id,
            "posterUrl": "",
            "year": _first_text(row, ("year", "release_year")),
            "seasonNumber": season if media_type == "tv" else 0,
            "createdAt": _first_text(row, ("created_at",)),
            "updatedAt": _first_text(row, ("updated_at",)),
            "allowCloudFallback": False,
            "sourceLabel": "Torra 只读订阅",
        })
    return result


def merge_task_subscriptions(local_subscriptions: list[dict], torra_rows: list[dict]) -> list[dict]:
    """把 Torra 远端订阅作为只读目标补入任务链，不建立本地台账。"""
    merged = [dict(item) for item in local_subscriptions if isinstance(item, dict)]
    local_identities = {
        identity
        for item in merged
        if (identity := _task_target_identity(item))[0] in {"movie", "tv"} and identity[1]
    }
    seen_remote = set()
    for remote in _torra_task_subscriptions(torra_rows):
        identity = _task_target_identity(remote)
        remote_key = identity if identity[1] else ("remote", remote["id"])
        if remote_key in seen_remote or (identity[1] and identity in local_identities):
            continue
        seen_remote.add(remote_key)
        merged.append(remote)
    return merged


def _optional_read(configured, reader, fallback):
    if not configured:
        return fallback, ""
    try:
        return reader(), ""
    except Exception as exc:
        return fallback, str(exc)


class TaskChainService:
    def __init__(self, app: Flask, subscription_loader=None, clock=None):
        self.qb = app.extensions["mcc_qbittorrent_client"]
        self.torra = app.extensions["mcc_torra_client"]
        self.symedia = app.extensions["mcc_symedia_client"]
        self.emby = app.extensions["mcc_emby_client"]
        self.subscription_loader = subscription_loader or _read_subscriptions
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def get_chain(self):
        with ThreadPoolExecutor(max_workers=6) as executor:
            subscription_future = executor.submit(self.subscription_loader)
            qb_future = executor.submit(self.qb.summary)
            torra_future = executor.submit(
                _optional_read,
                self.torra.is_configured(),
                self.torra.list_subscriptions,
                [],
            )
            torra_upload_reader = getattr(self.torra, "get_secupload_summary", None)
            torra_upload_future = executor.submit(
                _optional_read,
                self.torra.is_configured() and callable(torra_upload_reader),
                torra_upload_reader if callable(torra_upload_reader) else lambda: {},
                {},
            )
            symedia_future = executor.submit(
                _optional_read,
                self.symedia.is_configured(),
                lambda: self.symedia.list_transfer_history(200),
                {"rows": [], "total": 0},
            )
            emby_future = executor.submit(
                _optional_read,
                self.emby.is_configured(),
                self.emby.get_tmdb_library_index,
                None,
            )
            subscriptions = subscription_future.result()
            qb = qb_future.result()
            torra_rows, torra_error = torra_future.result()
            subscriptions = merge_task_subscriptions(subscriptions, torra_rows)
            torra_upload, torra_upload_error = torra_upload_future.result()
            symedia_page, symedia_error = symedia_future.result()
            emby_index, emby_error = emby_future.result()
        return build_task_chain({
            "subscriptions": subscriptions,
            "subscriptionSource": "Fluxa 本地订阅",
            "torraRows": torra_rows,
            "torraUpload": torra_upload,
            "qb": qb,
            "symediaRows": symedia_page["rows"],
            "symediaTotal": symedia_page["total"],
            "embyIndex": emby_index,
            "urls": {
                "qb": qb.get("webUrl") or self.qb.base_url,
                "torra": self.torra.base_url,
                "symedia": self.symedia.base_url,
                "emby": self.emby.server_url,
            },
            "serviceErrors": {
                "torra": torra_error,
                "torraUpload": torra_upload_error,
                "symedia": symedia_error,
                "emby": emby_error,
            },
            "cloudPolicy": discover_runtime.normalize_cloud_acquisition(
                discover_runtime.load_subscription_config().get("cloud_acquisition")
            ),
            "now": self.clock(),
        })


def register_task_chain(app: Flask, subscription_loader=None, clock=None):
    service = TaskChainService(app, subscription_loader=subscription_loader, clock=clock)
    app.extensions["mcc_task_chain_service"] = service

    @app.get("/api/tasks/chain")
    def task_chain():
        try:
            return jsonify(service.get_chain())
        except Exception:
            return jsonify({
                "code": "TASK_CHAIN_READ_FAILED",
                "error": "任务链读取失败",
            }), 502

    return service
