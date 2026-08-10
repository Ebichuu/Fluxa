from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.pipeline_fact_runtime import PIPELINE_STAGES, target_scope_for_item
from app.qbittorrent_assessment_runtime import assess_qb_task
from app.secupload_result_runtime import secupload_file_path_key
from app.symedia_evidence_runtime import normalize_symedia_status, symedia_protection_rule


BEIJING_TZ = timezone(timedelta(hours=8))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse(value) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    return _utc(parsed)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value) -> str:
    return str(value or "").strip()


def _integer(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _event_at(value, fallback="", *, default_timezone=timezone.utc) -> str:
    if value in (None, ""):
        return _text(fallback)
    try:
        if isinstance(value, (int, float)) or str(value).strip().replace(".", "", 1).isdigit():
            seconds = float(value)
            if seconds <= 0:
                return _text(fallback)
            return _iso(datetime.fromtimestamp(seconds, timezone.utc))
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=default_timezone)
        return _iso(parsed)
    except (TypeError, ValueError, OSError):
        return _text(fallback)


def _latest_event_at(units, fallback="") -> str:
    values = [_text(unit.get("eventAt")) for unit in units or [] if _text(unit.get("eventAt"))]
    return max(values, key=_parse) if values else _text(fallback)


def _fact(
    stage,
    state,
    scope,
    window,
    **details,
):
    result = {
        "stage": stage,
        "state": state,
        "scope": scope,
        "evidence": "missing" if state == "unknown" else "verified",
        "observedAt": window["observedAt"],
        "freshUntil": window["freshUntil"],
        "source": details.get("source", ""),
        "sourceRef": details.get("source_ref", ""),
        "reasonCode": details.get("reason_code", ""),
        "reasonText": details.get("reason_text", ""),
    }
    event_at = _text(details.get("event_at"))
    if event_at:
        result["eventAt"] = event_at
    first_playable = _text(details.get("first_confirmed_playable_at"))
    if first_playable:
        result["firstConfirmedPlayableAt"] = first_playable
    result_ref = _text(details.get("result_ref"))
    if result_ref:
        result["resultRef"] = result_ref
    units = details.get("units")
    if units:
        result["units"] = units
    return result


def _unknown(stage, scope, window, reason_code, **details):
    return _fact(
        stage,
        "unknown",
        scope,
        window,
        **details,
        reason_code=reason_code,
    )


def _torra_fact(context, scope, window):
    row = context.get("torra")
    if not isinstance(row, dict):
        return _unknown(
            "torra", scope, window, "TORRA_EVIDENCE_MISSING",
            reason_text="未关联 Torra 订阅",
        )
    if row.get("completed") is True:
        state, code, text = "succeeded", "TORRA_TARGET_SATISFIED", "获取目标已满足"
    elif row.get("is_running") is True:
        state, code, text = "active", "TORRA_ACQUISITION_ACTIVE", "Torra 正在获取目标"
    elif row.get("enabled") is False:
        state, code, text = "not_applicable", "TORRA_SUBSCRIPTION_DISABLED", "Torra 订阅已停用"
    else:
        state, code, text = "waiting", "TORRA_TARGET_WAITING", "Torra 已接收目标，等待获取"
    completed_at = row.get("completedAt") or row.get("completed_at")
    return _fact(
        "torra",
        state,
        scope,
        window,
        source="Torra",
        source_ref=_text(row.get("id")),
        reason_code=code,
        reason_text=text,
        event_at=_event_at(completed_at) if state == "succeeded" else "",
        result_ref=_text(row.get("id")) if state in {"succeeded", "not_applicable"} else "",
    )


def _qb_unit(task, window):
    assessment = assess_qb_task(task, window["observedAt"])
    state = assessment["factState"]
    evidence = assessment["evidence"]
    code = assessment["reasonCode"]
    status_text = assessment["reasonText"]
    action = assessment["actionText"]
    inactive_text = assessment["durationText"] if code in {
        "QB_DOWNLOAD_STALLED", "QB_DOWNLOAD_STALLED_OBSERVING",
    } else ""
    duration_text = inactive_text or "持续时间暂未确认"
    action_text = action if action == "无需处理" else f"建议{action}"
    text = " · ".join((status_text, duration_text, action_text))
    result = {
        "unitKey": _text(task.get("hash")) or _text(task.get("name")),
        "state": state,
        "scope": "file",
        "evidence": evidence,
        "observedAt": window["observedAt"],
        "freshUntil": window["freshUntil"],
        "sourceRef": _text(task.get("hash")),
        "reasonCode": code,
        "reasonText": text,
    }
    if state == "succeeded":
        completed_at = task.get("completedAt") or task.get("completion_on") or task.get("completionOn")
        event_at = _event_at(completed_at)
        if event_at:
            result["eventAt"] = event_at
        result["resultRef"] = _text(task.get("hash"))
    return result


def _summary_state(units, priority):
    states = {unit["state"] for unit in units}
    return next((state for state in priority if state in states), "unknown")


def _qb_fact(context, window):
    tasks = [task for task in context.get("qbTasks") or [] if isinstance(task, dict)]
    if not tasks:
        return _unknown(
            "qb", "file", window, "QB_EVIDENCE_MISSING",
            reason_text="未关联 qB 下载任务",
        )
    units = [_qb_unit(task, window) for task in tasks]
    state = _summary_state(units, ("failed", "active", "waiting", "unknown", "succeeded"))
    relevant_units = [unit for unit in units if unit["state"] == state]
    code = relevant_units[0]["reasonCode"] if len(relevant_units) == 1 else {
        "failed": "QB_DOWNLOAD_FAILED",
        "active": "QB_DOWNLOAD_ACTIVE",
        "waiting": "QB_DOWNLOAD_WAITING",
        "succeeded": "QB_DOWNLOAD_SUCCEEDED",
        "unknown": "QB_STATUS_UNKNOWN",
    }[state]
    if len(relevant_units) == 1:
        reason_text = relevant_units[0]["reasonText"]
    else:
        counts = {}
        for unit in relevant_units:
            label = unit["reasonText"].split(" · ", 1)[0]
            counts[label] = counts.get(label, 0) + 1
        reason_text = "；".join(f"{label} {count} 个" for label, count in sorted(counts.items()))
    return _fact(
        "qb",
        state,
        "file",
        window,
        source="qBittorrent",
        source_ref=units[0]["sourceRef"] if len(units) == 1 else "",
        reason_code=code,
        reason_text=reason_text,
        event_at=_latest_event_at(units),
        units=units,
    )


CLOUD115_REASON_CODES = {
    "authentication_failed": "CLOUD115_AUTHENTICATION_FAILED",
    "network_failed": "CLOUD115_NETWORK_FAILED",
    "file_missing": "CLOUD115_FILE_MISSING",
    "storage_unavailable": "CLOUD115_STORAGE_UNAVAILABLE",
    "instant_upload_failed": "CLOUD115_INSTANT_UPLOAD_FAILED",
    "retry_failed": "CLOUD115_RETRY_FAILED",
}


def _cloud115_path_keys(context):
    return {
        secupload_file_path_key(_text(task.get("savePath")).rstrip("/\\") + "/" + _text(task.get("name")))
        for task in context.get("qbTasks") or []
        if _text(task.get("savePath")) and _text(task.get("name"))
    }


def _cloud115_matched_files(context, summary):
    path_keys = _cloud115_path_keys(context)
    return [
        row for row in summary.get("failureFiles") or []
        if isinstance(row, dict)
        and _text(row.get("fileKey"))
        and _text(row.get("batchKey"))
        and _text(row.get("pathKey")) in path_keys
    ]


def _cloud115_planned_retry(value):
    planned = _text(value)
    if not planned:
        return ""
    try:
        _parse(planned)
    except (TypeError, ValueError):
        return ""
    return planned


def _cloud115_unit(row, window):
    planned_retry_at = _cloud115_planned_retry(row.get("plannedRetryAt"))
    retry_count = row.get("retryCount")
    retry_text = (
        f"，已重试 {retry_count} 次"
        if isinstance(retry_count, int) and not isinstance(retry_count, bool) and retry_count >= 0
        else "，重试次数暂未确认"
    )
    unit = {
        "unitKey": _text(row.get("fileKey")),
        "state": "failed",
        "scope": "file",
        "evidence": "verified",
        **window,
        "sourceRef": _text(row.get("batchKey")),
        "reasonCode": CLOUD115_REASON_CODES.get(
            _text(row.get("errorCategory")),
            "CLOUD115_UPLOAD_FAILED",
        ),
        "reasonText": (
            f"{_text(row.get('displayName')) or '未命名文件'}："
            f"{_text(row.get('errorLabel')) or '秒传失败'}{retry_text}"
        ),
        "retryEligible": bool(planned_retry_at),
        "resultRef": _text(row.get("batchKey")),
    }
    if planned_retry_at:
        unit["plannedRetryAt"] = planned_retry_at
    event_at = _event_at(row.get("observedAt"))
    if event_at:
        unit["eventAt"] = event_at
    return unit


def _cloud115_failure_fact(matched, window):
    units = [_cloud115_unit(row, window) for row in matched]
    planned_values = [unit.get("plannedRetryAt") for unit in units if unit.get("plannedRetryAt")]
    all_retrying = len(planned_values) == len(units)
    fact = _fact(
        "cloud115",
        "failed",
        "file",
        window,
        source="Torra secupload_115",
        source_ref=_text(matched[0].get("batchKey")),
        reason_code="CLOUD115_FILE_FAILURE",
        reason_text=f"{len(units)} 个 115 失败文件已通过完整路径关联当前 qB 任务",
        event_at=_latest_event_at(units, window["observedAt"]),
        retry_eligible=all_retrying,
        units=units,
    )
    if all_retrying:
        fact["plannedRetryAt"] = min(planned_values, key=_parse)
    return fact


def _cloud115_success_files(context, summary):
    path_keys = _cloud115_path_keys(context)
    candidates = {}
    for key in ("successFiles", "completedFiles", "uploadedFiles"):
        for row in summary.get(key) or []:
            if not isinstance(row, dict) or _text(row.get("pathKey")) not in path_keys:
                continue
            reference = _text(row.get("fileKey")) or _text(row.get("pathKey"))
            candidates[reference] = row
    return list(candidates.values())


def _cloud115_success_fact(matched, window):
    units = []
    for row in matched:
        reference = _text(row.get("fileKey")) or _text(row.get("pathKey"))
        unit = {
            "unitKey": reference,
            "state": "succeeded",
            "scope": "file",
            "evidence": "verified",
            **window,
            "sourceRef": _text(row.get("batchKey")) or reference,
            "reasonCode": "CLOUD115_FILE_UPLOADED",
            "reasonText": "115 文件已完成秒传",
            "resultRef": _text(row.get("batchKey")) or reference,
        }
        event_at = _event_at(row.get("observedAt") or row.get("finishedAt"))
        if event_at:
            unit["eventAt"] = event_at
        units.append(unit)
    return _fact(
        "cloud115",
        "succeeded",
        "file",
        window,
        source="Torra secupload_115",
        source_ref=_text(matched[0].get("batchKey")) if len(matched) == 1 else "",
        reason_code="CLOUD115_FILE_UPLOADED",
        reason_text=f"{len(units)} 个 115 文件已通过逐文件结果确认",
        event_at=_latest_event_at(units, window["observedAt"]),
        result_ref=_text(matched[0].get("batchKey")) if len(matched) == 1 else "",
        units=units,
    )


def _symedia_source_paths(row):
    values = [row.get(key) for key in ("src", "source", "source_path", "file_path")]
    for key in ("src_detail", "source_detail"):
        detail = row.get(key) if isinstance(row.get(key), dict) else {}
        values.extend(detail.get(path_key) for path_key in ("file_path", "path", "src"))
    return [_text(value).replace("\\", "/") for value in values if _text(value)]


def _is_cloud115_path(value):
    segments = [segment.casefold() for segment in _text(value).replace("\\", "/").split("/") if segment]
    return "115" in segments


def _cloud115_symedia_arrivals(context, window):
    units = []
    for index, row in enumerate(context.get("symediaRows") or []):
        if not isinstance(row, dict) or normalize_symedia_status(row.get("status")) is not True:
            continue
        if not any(_is_cloud115_path(path) for path in _symedia_source_paths(row)):
            continue
        reference = _text(row.get("id")) or f"symedia-row-{index}"
        unit = {
            "unitKey": reference,
            "state": "succeeded",
            "scope": "file",
            "evidence": "verified",
            **window,
            "sourceRef": reference,
            "reasonCode": "CLOUD115_FILE_ARRIVED",
            "reasonText": "Symedia 源记录确认文件已进入 115；秒传或原始上传方式未确认",
            "resultRef": reference,
        }
        event_at = _event_at(row.get("date"), default_timezone=BEIJING_TZ)
        if event_at:
            unit["eventAt"] = event_at
        units.append(unit)
    return units


def _cloud115_arrival_fact(units, window):
    return _fact(
        "cloud115",
        "succeeded",
        "file",
        window,
        source="Symedia 115 源记录",
        source_ref=units[0]["sourceRef"] if len(units) == 1 else "",
        reason_code="CLOUD115_FILE_ARRIVED",
        reason_text=(
            f"{len(units)} 个文件已由 Symedia 源记录确认进入 115；"
            "秒传或原始上传方式未确认"
        ),
        event_at=_latest_event_at(units, window["observedAt"]),
        result_ref=units[0]["resultRef"] if len(units) == 1 else "",
        units=units,
    )


def _cloud115_fact(context, window):
    summary = context.get("cloud115") or {}
    successful = _cloud115_success_files(context, summary)
    if successful:
        return _cloud115_success_fact(successful, window)
    matched = _cloud115_matched_files(context, summary)
    arrivals = _cloud115_symedia_arrivals(context, window)
    failure_units = [_cloud115_unit(row, window) for row in matched]
    latest_failure = _latest_event_at(failure_units) if failure_units else ""
    latest_arrival = _latest_event_at(arrivals) if arrivals else ""
    if (
        len(arrivals) == 1
        and len(matched) == 1
        and latest_arrival
        and latest_failure
        and _parse(latest_arrival) > _parse(latest_failure)
    ):
        return _cloud115_arrival_fact(arrivals, window)
    if matched:
        return _cloud115_failure_fact(matched, window)
    if arrivals:
        return _cloud115_arrival_fact(arrivals, window)
    if summary.get("readable"):
        return _unknown(
            "cloud115",
            "system-category",
            window,
            "CLOUD115_FILE_EVIDENCE_MISSING",
            reason_text="Torra 秒传摘要尚未提供可绑定当前媒体的文件证据",
            source="Torra secupload_115",
        )
    return _unknown(
        "cloud115",
        "system-category",
        window,
        "CLOUD115_SOURCE_UNAVAILABLE",
        reason_text="当前无法读取 115 文件级秒传证据",
    )


def _symedia_unit(row, index, window):
    date = _text(row.get("date"))
    source_path = _text(row.get("src"))
    reference = _text(row.get("id")) or (f"{date}:{source_path}" if date or source_path else f"row-{index}")
    status = normalize_symedia_status(row.get("status"))
    if status is True:
        state, evidence, code, text = "succeeded", "verified", "SYMEDIA_ORGANIZED", "Symedia 整理入库完成"
    elif status is False:
        rule = symedia_protection_rule(row)
        state = "protected" if rule else "failed"
        evidence = "verified"
        code = rule or _text(row.get("reasonCode")) or "SYMEDIA_LIBRARY_FAILED"
        text = _text(row.get("errmsg")) or ("Symedia 正常保护" if rule else "Symedia 整理失败")
    else:
        state, evidence, code, text = "unknown", "missing", "SYMEDIA_STATUS_UNKNOWN", "Symedia 结果无法确认"
    result = {
        "unitKey": reference,
        "state": state,
        "scope": "file",
        "evidence": evidence,
        "observedAt": window["observedAt"],
        "freshUntil": window["freshUntil"],
        "sourceRef": reference,
        "reasonCode": code,
        "reasonText": text,
        "resultRef": _text(row.get("id")),
    }
    if state != "unknown":
        event_at = _event_at(date, default_timezone=BEIJING_TZ)
        if event_at:
            result["eventAt"] = event_at
    return result


def _symedia_reason_text(units, state):
    fallback_text = {
        "failed": "Symedia 整理失败",
        "succeeded": "Symedia 整理入库完成",
        "protected": "Symedia 正常保护",
        "unknown": "Symedia 结果无法确认",
    }[state]
    if len(units) == 1:
        return _text(units[0].get("reasonText")) or fallback_text
    reason_counts = {}
    for unit in units:
        if unit.get("state") != state:
            continue
        text = _text(unit.get("reasonText")) or fallback_text
        reason_counts[text] = reason_counts.get(text, 0) + 1
    return "；".join(
        f"{text}（{count} 个文件）"
        for text, count in sorted(reason_counts.items())
    ) or fallback_text


def _symedia_fact(context, window):
    rows = [row for row in context.get("symediaRows") or [] if isinstance(row, dict)]
    if not rows:
        return _unknown(
            "symedia", "file", window, "SYMEDIA_EVIDENCE_MISSING",
            reason_text="尚无 Symedia 整理记录",
        )
    units = [_symedia_unit(row, index, window) for index, row in enumerate(rows)]
    state = _summary_state(units, ("failed", "succeeded", "protected", "unknown"))
    default_code = {
        "failed": "SYMEDIA_LIBRARY_FAILED",
        "succeeded": "SYMEDIA_ORGANIZED",
        "protected": "SYMEDIA_PROTECTED",
        "unknown": "SYMEDIA_STATUS_UNKNOWN",
    }[state]
    selected_codes = {
        _text(unit.get("reasonCode"))
        for unit in units
        if unit.get("state") == state and _text(unit.get("reasonCode"))
    }
    code = next(iter(selected_codes)) if state == "protected" and len(selected_codes) == 1 else default_code
    return _fact(
        "symedia",
        state,
        "file",
        window,
        source="Symedia",
        source_ref=units[0]["sourceRef"] if len(units) == 1 else "",
        reason_code=code,
        reason_text=_symedia_reason_text(units, state),
        event_at=_latest_event_at(units),
        units=units,
    )


def _strm_emby_units(context, scope, window):
    index = context.get("embyIndex")
    if not isinstance(index, dict):
        return []
    tmdb_id = _text(context.get("tmdbId"))
    if not tmdb_id:
        return []
    if _text(context.get("mediaType")) == "movie":
        if tmdb_id not in (index.get("strmMovies") or set()):
            return []
        reference = f"movie:{tmdb_id}"
    else:
        season = _integer(context.get("seasonNumber"))
        episode = _integer(context.get("episodeNumber"))
        if scope != "episode" or episode <= 0:
            return []
        reference_key = _episode_key(tmdb_id, season, episode)
        if reference_key not in (index.get("strmEpisodes") or set()):
            return []
        reference = f"tv:{tmdb_id}:s{season}:e{episode}"
    return [{
        "unitKey": reference,
        "state": "succeeded",
        "scope": scope,
        "evidence": "verified",
        **window,
        "sourceRef": reference,
        "reasonCode": "STRM_INDEXED_BY_EMBY",
        "reasonText": "Emby 已索引目标 STRM 播放入口",
        "resultRef": reference,
        "eventAt": window["observedAt"],
    }]


def _strm_fact(context, scope, window):
    units = []
    for index, row in enumerate(context.get("symediaRows") or []):
        if not isinstance(row, dict) or normalize_symedia_status(row.get("status")) is not True:
            continue
        strm_path = next((
            _text(row.get(key))
            for key in ("strmPath", "strm_path", "dest")
            if _text(row.get(key)).lower().endswith(".strm")
        ), "")
        if not strm_path:
            continue
        reference = _text(row.get("id")) or f"row-{index}"
        unit = {
            "unitKey": reference,
            "state": "succeeded",
            "scope": "file",
            "evidence": "verified",
            **window,
            "sourceRef": reference,
            "reasonCode": "STRM_CREATED",
            "reasonText": "STRM 播放入口已生成",
            "resultRef": reference,
            "eventAt": _event_at(row.get("date"), default_timezone=BEIJING_TZ),
        }
        units.append(unit)
    if not units:
        units = _strm_emby_units(context, scope, window)
    if not units:
        return _unknown(
            "strm",
            scope,
            window,
            "STRM_INDEPENDENT_RESULT_MISSING",
            reason_text="Symedia 未提供独立 STRM 结果",
        )
    return _fact(
        "strm",
        "succeeded",
        scope,
        window,
        source="Symedia STRM 结果" if units[0]["reasonCode"] == "STRM_CREATED" else "Emby 媒体路径",
        source_ref=units[0]["sourceRef"] if len(units) == 1 else "",
        reason_code=units[0]["reasonCode"] if len(units) == 1 else "STRM_CREATED",
        reason_text=(
            units[0]["reasonText"]
            if len(units) == 1
            else f"{len(units)} 个 STRM 播放入口已生成"
        ),
        event_at=_latest_event_at(units),
        result_ref=units[0]["resultRef"] if len(units) == 1 else "",
        units=units,
    )


def _episode_key(tmdb_id, season, episode):
    return str(tmdb_id), int(season), int(episode)


def _movie_emby_fact(index, tmdb_id, window):
    state = "succeeded" if tmdb_id in (index.get("movies") or set()) else "waiting"
    return _fact(
        "emby", state, "movie", window, source="Emby",
        source_ref=f"movie:{tmdb_id}",
        reason_code="EMBY_MOVIE_INDEXED" if state == "succeeded" else "EMBY_MOVIE_NOT_INDEXED",
        reason_text="Emby 已收录电影" if state == "succeeded" else "Emby 尚未收录电影",
        event_at=window["observedAt"],
        first_confirmed_playable_at=window["observedAt"] if state == "succeeded" else "",
    )


def _tv_emby_fact(index, context, scope, window):
    tmdb_id = _text(context.get("tmdbId"))
    season = _integer(context.get("seasonNumber"))
    episode = _integer(context.get("episodeNumber"))
    series_present = tmdb_id in (index.get("series") or set())
    if scope == "episode" and episode > 0:
        episode_present = _episode_key(tmdb_id, season, episode) in (index.get("episodes") or set())
        if episode_present:
            reference = f"tv:{tmdb_id}:s{season}:e{episode}"
            return _fact(
                "emby", "succeeded", "episode", window, source="Emby",
                source_ref=reference,
                reason_code="EMBY_EPISODE_INDEXED",
                reason_text="Emby 已收录目标集",
                event_at=window["observedAt"],
                first_confirmed_playable_at=window["observedAt"],
            )
        if series_present:
            return _unknown(
                "emby", "episode", window, "EMBY_EPISODE_EVIDENCE_MISSING",
                reason_text="Emby 已收录剧集作品，但目标集证据未确认", source="Emby",
            )
        return _fact(
            "emby", "waiting", "episode", window, source="Emby",
            reason_code="EMBY_EPISODE_NOT_INDEXED",
            reason_text="Emby 尚未收录目标集",
        )

    episode_targets = sorted({
        (_integer(row.get("seasonNumber")), episode_number)
        for row in context.get("episodeEvidence") or []
        if isinstance(row, dict)
        and _text(row.get("ownerTargetKey"))
        and _integer(row.get("seasonNumber")) == season
        for episode_number in range(
            max(1, _integer(row.get("episodeStart"))),
            min(10000, _integer(row.get("episodeEnd"))) + 1,
        )
    })
    if episode_targets:
        units = []
        for unit_season, unit_episode in episode_targets:
            present = _episode_key(tmdb_id, unit_season, unit_episode) in (index.get("episodes") or set())
            unit = {
                "unitKey": f"tv:{tmdb_id}:s{unit_season}:e{unit_episode}",
                "state": "succeeded" if present else "unknown",
                "scope": "episode",
                "evidence": "verified" if present else "missing",
                "observedAt": window["observedAt"],
                "freshUntil": window["freshUntil"],
                "sourceRef": f"tv:{tmdb_id}:s{unit_season}:e{unit_episode}",
                "reasonCode": "EMBY_EPISODE_INDEXED" if present else "EMBY_EPISODE_EVIDENCE_MISSING",
                "reasonText": "Emby 已收录目标集" if present else "目标集当前没有明确 Emby 命中",
            }
            if present:
                unit["eventAt"] = window["observedAt"]
            units.append(unit)
        return _unknown(
            "emby", scope, window,
            "EMBY_EPISODE_EVIDENCE_MISSING" if series_present else "EMBY_TARGET_RANGE_REQUIRES_EPISODE_PROJECTION",
            reason_text=(
                "Emby 已收录剧集作品，目标范围按集级结果单独确认"
                if series_present
                else "Emby 集级结果按目标范围单独确认"
            ),
            source="Emby",
            units=units,
        )

    if series_present:
        return _unknown(
            "emby", scope, window, "EMBY_EPISODE_EVIDENCE_MISSING",
            reason_text="Emby 作品级命中不能替代集级证据", source="Emby",
        )
    return _fact(
        "emby", "waiting", scope, window, source="Emby",
        reason_code="EMBY_SERIES_NOT_INDEXED",
        reason_text="Emby 尚未收录剧集作品",
    )


def _emby_fact(context, scope, window):
    index = context.get("embyIndex")
    if not isinstance(index, dict):
        return _unknown(
            "emby", scope, window, "EMBY_SOURCE_UNAVAILABLE",
            reason_text="当前无法读取 Emby 索引",
        )
    tmdb_id = _text(context.get("tmdbId"))
    if not tmdb_id:
        return _unknown(
            "emby", scope, window, "EMBY_IDENTITY_MISSING",
            reason_text="缺少 Emby 查询所需的 TMDB 身份",
        )
    if _text(context.get("mediaType")) == "movie":
        return _movie_emby_fact(index, tmdb_id, window)
    return _tv_emby_fact(index, context, scope, window)


def build_torra_source_fact(context: dict, *, observed_at: str) -> dict:
    observed = _parse(observed_at)
    window = {
        "observedAt": _iso(observed),
        "freshUntil": _iso(observed + timedelta(minutes=5)),
    }
    return _torra_fact(context, target_scope_for_item(context), window)


def build_pipeline_source_facts(context: dict, *, observed_at: str) -> list[dict]:
    observed = _parse(observed_at)
    window = {
        "observedAt": _iso(observed),
        "freshUntil": _iso(observed + timedelta(minutes=5)),
    }
    scope = target_scope_for_item(context)
    facts = [
        build_torra_source_fact(context, observed_at=observed_at),
        _qb_fact(context, window),
        _cloud115_fact(context, window),
        _symedia_fact(context, window),
        _strm_fact(context, scope, window),
        _emby_fact(context, scope, window),
    ]
    torra = facts[0]
    downstream = [fact for fact in facts[1:] if fact.get("state") == "succeeded"]
    if torra.get("state") == "waiting" and downstream:
        event_times = [
            _text(fact.get("eventAt"))
            for fact in downstream
            if _text(fact.get("eventAt"))
        ]
        torra.update({
            "state": "succeeded",
            "evidence": "verified",
            "source": "Torra 目标 / 下游证据",
            "reasonCode": "TORRA_TARGET_HANDOFF_CONFIRMED",
            "reasonText": "下游结果已确认获取目标满足",
            "resultRef": _text((context.get("torra") or {}).get("id")),
        })
        if event_times:
            torra["eventAt"] = min(event_times, key=_parse)
    if tuple(fact["stage"] for fact in facts) != PIPELINE_STAGES:
        raise RuntimeError("pipeline source adapter stage 顺序无效")
    return facts
