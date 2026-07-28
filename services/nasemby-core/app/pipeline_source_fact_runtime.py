from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.pipeline_fact_runtime import PIPELINE_STAGES, target_scope_for_item
from app.secupload_result_runtime import secupload_file_path_key
from app.symedia_evidence_runtime import normalize_symedia_status, symedia_protection_rule


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
    return _fact(
        "torra",
        state,
        scope,
        window,
        source="Torra",
        source_ref=_text(row.get("id")),
        reason_code=code,
        reason_text=text,
    )


def _qb_unit(task, window):
    status = _text(task.get("status"))
    if status == "completed":
        state, evidence, code, text = "succeeded", "verified", "QB_DOWNLOAD_SUCCEEDED", "qB 下载完成"
    elif status in {"downloading", "queued"}:
        state, evidence, code, text = "active", "verified", "QB_DOWNLOAD_ACTIVE", "qB 正在下载或排队"
    elif status == "stalled":
        missing_files = "missing" in _text(task.get("state")).lower()
        state, evidence = "failed", "verified"
        code = "QB_MISSING_FILES" if missing_files else "QB_DOWNLOAD_FAILED"
        text = "qB 文件缺失，任务无法继续" if missing_files else "qB 下载无法继续"
    elif status == "paused":
        state, evidence, code, text = "waiting", "verified", "QB_DOWNLOAD_PAUSED", "qB 下载已暂停"
    else:
        state, evidence, code, text = "unknown", "missing", "QB_STATUS_UNKNOWN", "qB 状态无法确认"
    return {
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
    code = {
        "failed": "QB_DOWNLOAD_FAILED",
        "active": "QB_DOWNLOAD_ACTIVE",
        "waiting": "QB_DOWNLOAD_WAITING",
        "succeeded": "QB_DOWNLOAD_SUCCEEDED",
        "unknown": "QB_STATUS_UNKNOWN",
    }[state]
    return _fact(
        "qb",
        state,
        "file",
        window,
        source="qBittorrent",
        source_ref=units[0]["sourceRef"] if len(units) == 1 else "",
        reason_code=code,
        reason_text=f"{len(units)} 个 qB 文件任务",
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
    }
    if planned_retry_at:
        unit["plannedRetryAt"] = planned_retry_at
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
        retry_eligible=all_retrying,
        units=units,
    )
    if all_retrying:
        fact["plannedRetryAt"] = min(planned_values, key=_parse)
    return fact


def _cloud115_fact(context, window):
    summary = context.get("cloud115") or {}
    matched = _cloud115_matched_files(context, summary)
    if matched:
        return _cloud115_failure_fact(matched, window)
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
    return {
        "unitKey": reference,
        "state": state,
        "scope": "file",
        "evidence": evidence,
        "observedAt": window["observedAt"],
        "freshUntil": window["freshUntil"],
        "sourceRef": reference,
        "reasonCode": code,
        "reasonText": text,
    }


def _symedia_fact(context, window):
    rows = [row for row in context.get("symediaRows") or [] if isinstance(row, dict)]
    if not rows:
        return _unknown(
            "symedia", "file", window, "SYMEDIA_EVIDENCE_MISSING",
            reason_text="尚无 Symedia 整理记录",
        )
    units = [_symedia_unit(row, index, window) for index, row in enumerate(rows)]
    state = _summary_state(units, ("failed", "succeeded", "protected", "unknown"))
    code = {
        "failed": "SYMEDIA_LIBRARY_FAILED",
        "succeeded": "SYMEDIA_ORGANIZED",
        "protected": "SYMEDIA_PROTECTED",
        "unknown": "SYMEDIA_STATUS_UNKNOWN",
    }[state]
    return _fact(
        "symedia",
        state,
        "file",
        window,
        source="Symedia",
        source_ref=units[0]["sourceRef"] if len(units) == 1 else "",
        reason_code=code,
        reason_text=f"{len(units)} 条 Symedia 文件记录",
        units=units,
    )


def _strm_fact(scope, window):
    return _unknown(
        "strm",
        scope,
        window,
        "STRM_SERVICE_EVIDENCE_MISSING",
        reason_text="尚未接入独立 STRM 服务结果",
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
        _strm_fact(scope, window),
        _emby_fact(context, scope, window),
    ]
    if tuple(fact["stage"] for fact in facts) != PIPELINE_STAGES:
        raise RuntimeError("pipeline source adapter stage 顺序无效")
    return facts
