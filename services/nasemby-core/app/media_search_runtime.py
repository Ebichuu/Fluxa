from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlsplit

from flask import Flask, jsonify, request

from app.http_runtime import current_request_id


BEIJING_TZ = timezone(timedelta(hours=8))
MEDIA_KEY_PATTERN = re.compile(r"^(movie|tv):(?:(?:tmdb):)?([1-9]\d*)$")
SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?:token|api[_-]?key|cookie|passkey|authorization|signature|secret|password)",
    re.IGNORECASE,
)
USER_STATE_PRIORITY = {
    "action_required": 0,
    "in_progress": 1,
    "completed": 2,
    "no_action": 3,
}
ACTION_LABELS = {
    "reidentify": "重新识别",
    "resume_download": "恢复下载",
    "pause_download": "暂停下载",
    "retry_stage": "重试当前步骤",
    "refresh_source": "刷新来源",
    "open_qb": "打开 qB 检查",
    "open_torra": "打开 Torra 检查",
    "view_details": "查看处理方法",
    "view_subscription": "查看追更",
}


def _text(value) -> str:
    return str(value or "").strip()


def _integer(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize(value) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", _text(value).casefold())


def _canonical_media_key(media_type, tmdb_id) -> str:
    kind = _text(media_type).lower()
    identity = _text(tmdb_id)
    if kind not in {"movie", "tv"} or not identity.isdigit() or int(identity) <= 0:
        return ""
    return f"{kind}:{int(identity)}"


def _parse_media_key(value) -> tuple[str, str] | None:
    matched = MEDIA_KEY_PATTERN.fullmatch(_text(value).lower())
    if not matched:
        return None
    return matched.group(1), str(int(matched.group(2)))


def _safe_image_url(value) -> str:
    candidate = _text(value)
    if not candidate or SENSITIVE_TEXT_PATTERN.search(candidate):
        return ""
    lowered = candidate.casefold()
    if lowered.startswith(("file:", "\\\\", "/volume/")) or re.match(r"^[a-z]:[\\/]", candidate, re.I):
        return ""
    if candidate.startswith("/"):
        return candidate
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return ""
    return candidate


def _read_tmdb_candidates(query: str, target: tuple[str, str] | None, limit: int) -> list[dict]:
    from app.discover_runtime import (
        http_json,
        load_tmdb_config,
        tmdb_credentials_available,
        tmdb_image,
    )

    config = load_tmdb_config()
    if not tmdb_credentials_available(config):
        return []
    base_url = _text(config.get("api_base_url")).rstrip("/")
    if not base_url:
        return []
    params = {
        "api_key": _text(config.get("api_key")),
        "language": "zh-CN",
    }
    if target:
        media_type, tmdb_id = target
        payload = http_json(
            f"{base_url}/{media_type}/{int(tmdb_id)}?{urlencode(params)}",
            timeout=12,
        )
        rows = [payload] if isinstance(payload, dict) else []
    else:
        params.update({
            "include_adult": "false",
            "query": query,
            "page": "1",
        })
        payload = http_json(
            f"{base_url}/search/multi?{urlencode(params)}",
            timeout=12,
        )
        rows = payload.get("results") or [] if isinstance(payload, dict) else []

    candidates = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        media_type = target[0] if target else _text(item.get("media_type")).lower()
        tmdb_id = _text(item.get("id"))
        key = _canonical_media_key(media_type, tmdb_id)
        if not key:
            continue
        title = _text(
            item.get("title")
            or item.get("name")
            or item.get("original_title")
            or item.get("original_name")
        )
        date = _text(item.get("release_date") or item.get("first_air_date"))
        candidates.append({
            "mediaType": media_type,
            "tmdbId": tmdb_id,
            "title": title,
            "year": date[:4],
            "posterUrl": tmdb_image(item.get("poster_path"), "w342"),
        })
        if len(candidates) >= limit:
            break
    return candidates


def _latest(values) -> str:
    return max((_text(value) for value in values if _text(value)), default="")


def _as_utc(value):
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _evidence_is_current(item: dict, current: datetime) -> bool:
    deadline = _as_utc(item.get("freshUntil"))
    return not deadline or deadline > current.astimezone(timezone.utc)


def _task_evidence_is_current(task: dict, current: datetime) -> bool:
    return _evidence_is_current(task, current)


def _stage_name(stage: dict) -> str:
    return _text(stage.get("stage") or stage.get("key")).lower()


def _trusted_stage(stage: dict, *, current: datetime | None = None, verified_only=False) -> bool:
    if current is not None and not _evidence_is_current(stage, current):
        return False
    status = _text(stage.get("status")).lower()
    health = _text(stage.get("healthState")).lower()
    expected_health = {
        "done": "normal",
        "active": "waiting",
        "waiting": "waiting",
        "blocked": "action_required",
    }.get(status)
    if not expected_health or health != expected_health:
        return False
    evidence = _text(stage.get("evidence")).lower()
    if status == "blocked":
        return evidence == "verified"
    return evidence == "verified" if verified_only else evidence in {"verified", "inferred"}


def _all_stages(tasks: list[dict], names: set[str]) -> list[dict]:
    return [
        stage
        for task in tasks
        for stage in (task.get("stages") or [])
        if isinstance(stage, dict) and _stage_name(stage) in names
    ]


def _episode_candidate(row: dict, stages: set[str]) -> tuple[int, int] | None:
    stage = _text(row.get("stage")).lower()
    source = _text(row.get("source")).lower()
    if stage not in stages or _text(row.get("status")).lower() != "done":
        return None
    if not _text(row.get("observedAt")):
        return None
    if stage == "download" and source != "qbittorrent":
        return None
    if stage in {"library", "symedia", "strm"} and source not in {"symedia", "strm"}:
        return None
    season = _integer(row.get("seasonNumber"), -1)
    episode = _integer(row.get("episodeEnd") or row.get("episodeStart"), -1)
    return (season, episode) if season >= 0 and episode >= 0 else None


def _episode_marker(tasks: list[dict], stages: set[str], current: datetime) -> dict | None:
    candidates = [
        candidate
        for task in tasks
        if any(
            _stage_name(stage) in stages
            and _text(stage.get("status")).lower() == "done"
            and _trusted_stage(stage, current=current)
            for stage in (task.get("stages") or [])
            if isinstance(stage, dict)
        )
        for row in (task.get("episodeEvidence") or [])
        if isinstance(row, dict)
        if (candidate := _episode_candidate(row, stages))
    ]
    if not candidates:
        return None
    season, episode = max(candidates)
    return {
        "seasonNumber": season,
        "episodeNumber": episode,
        "label": f"S{season:02d}E{episode:02d}",
    }


def _stage_projection_status(
    stages: list[dict],
    current: datetime,
    *,
    verified_only=False,
    include_waiting=False,
) -> str:
    trusted = [
        stage for stage in stages
        if _trusted_stage(stage, current=current, verified_only=verified_only)
    ]
    statuses = {_text(stage.get("status")).lower() for stage in trusted}
    if "blocked" in statuses:
        return "action_required"
    if "active" in statuses:
        return "in_progress"
    if "done" in statuses:
        return "completed"
    return "in_progress" if include_waiting and "waiting" in statuses else "unknown"


def _stage_observed_at(stages: list[dict], current: datetime, *, verified_only=False) -> str:
    return _latest(
        stage.get("observedAt")
        for stage in stages
        if _trusted_stage(stage, current=current, verified_only=verified_only)
    )


def _effective_task_user_state(task: dict, current: datetime) -> str:
    state = _text(task.get("userState")) or "no_action"
    if not _task_evidence_is_current(task, current):
        return "no_action"
    if state != "action_required":
        return state
    if _text(task.get("healthState")).lower() == "protected" or _text(
        task.get("executionState")
    ).lower() == "protected":
        return "no_action"
    blocked = [
        stage for stage in (task.get("stages") or [])
        if isinstance(stage, dict) and _text(stage.get("status")).lower() == "blocked"
    ]
    if blocked and not any(
        _trusted_stage(stage, current=current, verified_only=True)
        for stage in blocked
    ):
        return "no_action"
    return state


def _safe_primary_action(tasks: list[dict], has_subscription: bool, current: datetime) -> dict:
    ordered = sorted(
        tasks,
        key=lambda item: (
            USER_STATE_PRIORITY.get(_effective_task_user_state(item, current), 9),
            _text(item.get("updatedAt")),
        ),
    )
    for task in ordered:
        if _text(task.get("healthState")).lower() == "protected" or _text(
            task.get("executionState")
        ).lower() == "protected":
            continue
        if (
            _text(task.get("userState")) == "action_required"
            and _effective_task_user_state(task, current) != "action_required"
        ):
            continue
        action = task.get("primaryAction") or {}
        kind = _text(action.get("kind")).lower()
        if action.get("available") is True and kind in ACTION_LABELS:
            return {
                "kind": kind,
                "label": ACTION_LABELS[kind],
                "available": True,
                "reason": "当前证据允许执行此操作",
            }
    if has_subscription:
        return {
            "kind": "view_subscription",
            "label": ACTION_LABELS["view_subscription"],
            "available": True,
            "reason": "可查看该作品的追更生命周期",
        }
    return {
        "kind": "none",
        "label": "",
        "available": False,
        "reason": "当前没有可验证的人工操作",
    }


def _torra_status(subscriptions: list[dict]) -> str:
    states = {
        _text((item.get("torra") or {}).get("status")).lower()
        for item in subscriptions
    }
    if "linked" in states or any(
        _text(item.get("torraMappingStatus")).lower() == "mapped"
        or _text(item.get("torraSyncState")).lower() == "current"
        or _text(item.get("origin")).lower() == "torra"
        for item in subscriptions
    ):
        return "linked"
    return "not_linked" if "not_linked" in states else "unknown"


def _subscription_projection(record: dict, readable: bool) -> dict:
    subscriptions = record["subscriptions"]
    if not subscriptions:
        return {
            "status": "not_following" if readable else "unknown",
            "torraStatus": "unknown",
            "lastCheckedAt": "",
            "seasonNumbers": [],
        }
    seasons = sorted({
        _integer(item.get("seasonNumber"))
        for item in subscriptions
        if item.get("seasonNumber") not in {None, ""}
    })
    return {
        "status": "following",
        "torraStatus": _torra_status(subscriptions),
        "lastCheckedAt": _latest(
            item.get("observedAt") or item.get("updatedAt") or item.get("createdAt")
            for item in subscriptions
        ),
        "seasonNumbers": seasons,
    }


def _download_projection(tasks: list[dict], current: datetime) -> dict:
    stages = _all_stages(tasks, {"download"})
    current_tasks = [
        item for item in tasks
        if any(
            _stage_name(stage) == "download" and _trusted_stage(stage, current=current)
            for stage in item.get("stages") or []
            if isinstance(stage, dict)
        )
    ]
    completed = sum(max(0, _integer(item.get("completedDownloadTasks"))) for item in current_tasks)
    active = sum(max(0, _integer(item.get("activeDownloadTasks"))) for item in current_tasks)
    trusted_status = _stage_projection_status(stages, current)
    if trusted_status == "action_required":
        status = "action_required"
    elif active or trusted_status == "in_progress":
        status = "in_progress"
    elif completed or trusted_status == "completed":
        status = "completed"
    else:
        status = "unknown"
    result = {
        "status": status,
        "activeTasks": active,
        "completedTasks": completed,
        "observedAt": _stage_observed_at(stages, current),
    }
    marker = _episode_marker(tasks, {"download"}, current)
    if marker:
        result["latestEpisode"] = marker
    return result


def _cloud115_projection(tasks: list[dict], current: datetime) -> dict:
    stages = _all_stages(tasks, {"cloud115"})
    return {
        "status": _stage_projection_status(stages, current, verified_only=True),
        "observedAt": _stage_observed_at(stages, current, verified_only=True),
    }


def _library_projection(tasks: list[dict], calendar_entries: list[dict], current: datetime) -> dict:
    names = {"library", "symedia", "strm"}
    stages = _all_stages(tasks, names)
    status = _stage_projection_status(stages, current, include_waiting=True)
    if any(bool(entry.get("inLibrary") or entry.get("libraryAt")) for entry in calendar_entries):
        status = "completed"
    result = {
        "status": status,
        "observedAt": _latest([
            _stage_observed_at(stages, current),
            *(entry.get("libraryAt") for entry in calendar_entries if entry.get("inLibrary") or entry.get("libraryAt")),
        ]),
    }
    marker = _episode_marker(tasks, names, current)
    if marker:
        result["latestEpisode"] = marker
    return result


def _calendar_status(entries: list[dict], today: str) -> str:
    statuses = {_text(entry.get("status")).lower() for entry in entries}
    if any(_text(entry.get("healthState")).lower() == "action_required" for entry in entries):
        return "action_required"
    if "acquiring" in statuses:
        return "in_progress"
    if statuses == {"library"}:
        return "completed"
    return "scheduled" if any(_text(entry.get("date")) >= today for entry in entries) else "unknown"


def _calendar_projection(entries: list[dict], today: str) -> dict:
    if not entries:
        return {"status": "unknown", "entryCount": 0, "inLibraryCount": 0}
    upcoming = sorted(
        (entry for entry in entries if _text(entry.get("date")) >= today),
        key=lambda item: (
            _text(item.get("date")),
            _integer(item.get("seasonNumber")),
            _integer(item.get("episodeNumber")),
        ),
    )
    result = {
        "status": _calendar_status(entries, today),
        "entryCount": len(entries),
        "inLibraryCount": sum(bool(entry.get("inLibrary") or entry.get("libraryAt")) for entry in entries),
    }
    if upcoming:
        result["nextAirAt"] = _text(upcoming[0].get("airAt") or upcoming[0].get("date"))
        if _text(upcoming[0].get("episodeLabel")):
            result["nextEpisodeLabel"] = _text(upcoming[0].get("episodeLabel"))
    return result


def _links(record: dict) -> dict:
    query = [
        ("mediaType", record["mediaType"]),
        ("tmdbId", record["tmdbId"]),
        ("title", record["title"]),
    ]
    task_query = (
        [("chainId", record["chainId"]), ("title", record["title"])]
        if record.get("chainId") and not record["tmdbId"]
        else query
    )
    tasks_url = f"/tasks?{urlencode(task_query)}"
    return {
        "overview": f"/media/{record['mediaType']}/{record['tmdbId']}" if record["tmdbId"] else tasks_url,
        "tasks": tasks_url,
        "calendar": f"/calendar?{urlencode([('type', record['mediaType']), ('q', record['title'])])}",
        "subscription": f"/following?{urlencode(query)}",
        "rss": f"/rss-library?{urlencode([('q', record['title']), ('identityStatus', 'identified'), ('window', 'all')])}",
        "api": f"/api/v2/media/{quote(record['mediaKey'], safe=':')}" if record["tmdbId"] else "",
    }


def _user_state(tasks: list[dict], current: datetime) -> str:
    states = {_effective_task_user_state(item, current) for item in tasks}
    return min(states, key=lambda state: USER_STATE_PRIORITY.get(state, 9)) if states else "no_action"


def _emby_identities(index: dict) -> dict[str, set[str]]:
    return {
        "movie": {_text(value) for value in index.get("movies") or [] if _text(value)},
        "tv": {_text(value) for value in index.get("series") or [] if _text(value)},
    }


def _result_text(lifecycle: dict, tasks: list[dict], current: datetime) -> str:
    if _user_state(tasks, current) == "action_required":
        return "当前作品有任务需要处理"
    parts = []
    if lifecycle["subscription"]["status"] == "following":
        parts.append("追更中")
    if lifecycle["subscription"]["torraStatus"] == "linked":
        parts.append("Torra 已同步")
    if lifecycle["download"]["status"] == "in_progress":
        count = lifecycle["download"]["activeTasks"]
        parts.append(f"正在下载 {count} 个" if count else "正在下载")
    elif lifecycle["download"]["status"] == "completed":
        count = lifecycle["download"]["completedTasks"]
        parts.append(f"已下载 {count} 个" if count else "下载已完成")
    if lifecycle["cloud115"]["status"] == "completed":
        parts.append("已进入 115")
    if lifecycle["library"]["status"] == "completed":
        parts.append("已入库")
    if lifecycle["emby"]["status"] == "available":
        parts.append("Emby 可看")
    return " · ".join(parts) if parts else "暂未形成可验证的处理证据"


class MediaSearchService:
    def __init__(self, app: Flask, clock=None, tmdb_reader=None):
        self.app = app
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.tmdb_reader = tmdb_reader or _read_tmdb_candidates

    @staticmethod
    def _record(catalog: dict, media_type, tmdb_id) -> dict | None:
        key = _canonical_media_key(media_type, tmdb_id)
        if not key:
            return None
        return catalog.setdefault(key, {
            "mediaKey": key,
            "mediaType": key.split(":", 1)[0],
            "tmdbId": key.split(":", 1)[1],
            "title": "",
            "posterUrl": "",
            "year": "",
            "sources": set(),
            "subscriptions": [],
            "tasks": [],
            "calendar": [],
            "embyAvailable": False,
        })

    @staticmethod
    def _unlinked_task_record(catalog: dict, item: dict) -> dict | None:
        media_type = _text(item.get("mediaType")).lower()
        chain_id_value = _text(item.get("chainId"))
        title = _text(item.get("title"))
        if not chain_id_value or not title:
            return None
        if media_type not in {"movie", "tv"}:
            media_type = "unknown"
        catalog_key = f"task:{chain_id_value}"
        public_media_key = _text(item.get("mediaKey")) or catalog_key
        return catalog.setdefault(catalog_key, {
            "mediaKey": public_media_key,
            "mediaType": media_type,
            "tmdbId": "",
            "chainId": chain_id_value,
            "title": "",
            "posterUrl": "",
            "year": "",
            "sources": set(),
            "subscriptions": [],
            "tasks": [],
            "calendar": [],
            "embyAvailable": False,
        })

    @staticmethod
    def _identity(record: dict, item: dict, source: str):
        record["sources"].add(source)
        if not record["title"] and _text(item.get("title")):
            record["title"] = _text(item.get("title"))
        if not record["posterUrl"]:
            record["posterUrl"] = _safe_image_url(item.get("posterUrl"))
        if not record["year"] and _text(item.get("year")):
            record["year"] = _text(item.get("year"))[:4]

    def _subscriptions(self, catalog: dict, query: str) -> bool:
        service = self.app.extensions.get("mcc_subscription_workbench")
        if not service:
            return False
        try:
            payload = service.snapshot(limit=None, query=query)
        except Exception:
            return False
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            record = self._record(catalog, item.get("mediaType"), item.get("tmdbId"))
            if not record:
                continue
            self._identity(record, item, "subscription")
            record["subscriptions"].append(item)
        return True

    def _rss(self, catalog: dict, query: str, target: tuple[str, str] | None) -> bool:
        service = self.app.extensions.get("mcc_private_rss")
        repository = getattr(service, "repository", None)
        if not repository or not hasattr(repository, "search_items"):
            return False
        options = {
            "query": "" if target else query,
            "identity_status": "identified",
            "limit": 100,
        }
        if target:
            options.update({"media_type": target[0], "tmdb_id": target[1]})
        try:
            payload = repository.search_items(**options)
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        for item in payload.get("items") or []:
            if not isinstance(item, dict) or _text(item.get("identityStatus")).lower() != "identified":
                continue
            record = self._record(catalog, item.get("mediaType"), item.get("tmdbId"))
            if not record:
                continue
            self._identity(record, item, "rss")
        return True

    def _tasks(self, catalog: dict, query: str) -> bool:
        service = self.app.extensions.get("mcc_task_chain_v2_service")
        if not service:
            return False
        try:
            payload = service.full_snapshot()
        except Exception:
            return False
        wanted = _normalize(query)
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            key = _canonical_media_key(item.get("mediaType"), item.get("tmdbId"))
            if (
                wanted
                and wanted not in _normalize(item.get("title"))
                and wanted not in _normalize(key or item.get("mediaKey"))
            ):
                continue
            record = (
                self._record(catalog, item.get("mediaType"), item.get("tmdbId"))
                if key
                else self._unlinked_task_record(catalog, item)
            )
            if not record:
                continue
            self._identity(record, item, "task")
            record["tasks"].append(item)
        return True

    def _calendar(self, catalog: dict, query: str) -> bool:
        service = self.app.extensions.get("mcc_calendar_timeline")
        if not service or not hasattr(service, "cached_snapshot"):
            return False
        now = self.clock().astimezone(BEIJING_TZ)
        try:
            payload = service.cached_snapshot(now.year, now.month, "all")
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        wanted = _normalize(query)
        for item in (payload.get("calendar") or {}).get("entries") or []:
            if not isinstance(item, dict):
                continue
            key = _canonical_media_key(item.get("mediaType"), item.get("tmdbId"))
            if not key:
                continue
            if (
                wanted
                and wanted not in _normalize(item.get("title"))
                and wanted not in _normalize(key)
            ):
                continue
            record = self._record(catalog, item.get("mediaType"), item.get("tmdbId"))
            self._identity(record, item, "calendar")
            record["calendar"].append(item)
        return True

    @staticmethod
    def _apply_emby_identities(catalog: dict, identities: dict[str, set[str]]):
        for record in catalog.values():
            available = record["tmdbId"] in identities.get(record["mediaType"], set())
            record["embyAvailable"] = available
            if available:
                record["sources"].add("emby")

    def _emby(
        self,
        catalog: dict,
        query: str,
        target: tuple[str, str] | None,
    ) -> tuple[bool, dict[str, set[str]]]:
        client = self.app.extensions.get("mcc_emby_client")
        if not client or not client.is_configured():
            return False, {"movie": set(), "tv": set()}
        try:
            index = client.get_tmdb_library_index() or {}
            identities = _emby_identities(index)
        except Exception:
            return False, {"movie": set(), "tv": set()}
        if target and target[1] in identities[target[0]]:
            self._record(catalog, target[0], target[1])
        elif _text(query).isdigit():
            for media_type, values in identities.items():
                for tmdb_id in values:
                    if _text(query) in tmdb_id:
                        self._record(catalog, media_type, tmdb_id)
        self._apply_emby_identities(catalog, identities)
        return True, identities

    def _tmdb(
        self,
        catalog: dict,
        query: str,
        target: tuple[str, str] | None,
        limit: int,
        emby_identities: dict[str, set[str]],
    ) -> list[str]:
        try:
            items = self.tmdb_reader(query, target, limit)
        except Exception:
            return []
        keys = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            record = self._record(catalog, item.get("mediaType"), item.get("tmdbId"))
            if not record:
                continue
            self._identity(record, item, "tmdb")
            if record["mediaKey"] not in keys:
                keys.append(record["mediaKey"])
        self._apply_emby_identities(catalog, emby_identities)
        return keys

    def _catalog(
        self,
        query: str,
        target: tuple[str, str] | None = None,
    ) -> tuple[dict, dict]:
        catalog = {}
        readable = {
            "subscription": self._subscriptions(catalog, query),
            "rss": self._rss(catalog, query, target),
            "task": self._tasks(catalog, query),
            "calendar": self._calendar(catalog, query),
        }
        readable["emby"], readable["embyIdentities"] = self._emby(catalog, query, target)
        return catalog, readable

    def _project(self, record: dict, readable: dict) -> dict:
        current = self.clock()
        current_tasks = [
            item for item in record["tasks"]
            if _task_evidence_is_current(item, current)
        ]
        subscription = _subscription_projection(record, readable["subscription"])
        download = _download_projection(current_tasks, current)
        cloud115 = _cloud115_projection(current_tasks, current)
        library = _library_projection(current_tasks, record["calendar"], current)
        task_emby = any(item.get("embyIndexed") is True for item in current_tasks)
        episode_evidence = any(
            _text(item.get("embyEvidenceScope")) == "episode"
            for item in current_tasks
        )
        scope = "episode" if episode_evidence else "title"
        emby_available = task_emby or record["embyAvailable"]
        emby = {
            "status": "available" if emby_available else "unknown",
            "evidenceScope": scope if emby_available else "none",
        }
        playback = {
            "status": "available" if emby_available else "unknown",
            "directLinkAvailable": False,
        }
        today = current.astimezone(BEIJING_TZ).strftime("%Y-%m-%d")
        calendar = _calendar_projection(record["calendar"], today)
        result = {
            "ok": True,
            "media": {
                "mediaKey": record["mediaKey"],
                "title": record["title"] or f"TMDB {record['tmdbId']}",
                "mediaType": record["mediaType"],
                "tmdbId": record["tmdbId"],
                "posterUrl": record["posterUrl"],
                "sources": sorted(record["sources"]),
            },
            "userState": _user_state(current_tasks, current),
            "subscription": subscription,
            "download": download,
            "cloud115": cloud115,
            "library": library,
            "emby": emby,
            "playback": playback,
            "calendar": calendar,
            "primaryAction": _safe_primary_action(
                current_tasks,
                bool(record["subscriptions"]),
                current,
            ),
        }
        if record.get("chainId"):
            result["media"]["chainId"] = record["chainId"]
        if record["year"]:
            result["media"]["year"] = record["year"]
        result["resultText"] = _result_text(result, current_tasks, current)
        result["links"] = _links({**record, "title": result["media"]["title"]})
        return result

    @staticmethod
    def _rank(record: dict, query: str) -> tuple:
        title = _normalize(record["title"])
        wanted = _normalize(query)
        if title == wanted:
            title_rank = 0
        elif title.startswith(wanted):
            title_rank = 1
        elif wanted in title:
            title_rank = 2
        else:
            title_rank = 3
        source_rank = min(
            (
                {
                    "subscription": 0,
                    "task": 1,
                    "calendar": 2,
                    "rss": 3,
                    "emby": 4,
                    "tmdb": 5,
                }.get(source, 9)
                for source in record["sources"]
            ),
            default=9,
        )
        return title_rank, source_rank, title, record["mediaKey"]

    @staticmethod
    def _matching_records(catalog: dict, wanted: str) -> list[dict]:
        return [
            record for record in catalog.values()
            if wanted in _normalize(record["title"])
            or wanted in record["tmdbId"]
            or wanted in _normalize(record["mediaKey"])
        ]

    def search(self, query: str, limit: int) -> dict:
        wanted = _normalize(query)
        if not wanted:
            return {
                "ok": True,
                "query": "",
                "items": [],
                "page": {"total": 0, "limit": limit},
            }
        parsed_key = _parse_media_key(query)
        catalog_query = parsed_key[1] if parsed_key else query
        catalog, readable = self._catalog(catalog_query, parsed_key)
        records = self._matching_records(catalog, wanted)
        if not records or (parsed_key and all(not record["title"] for record in records)):
            fallback_keys = self._tmdb(
                catalog,
                catalog_query,
                parsed_key,
                limit,
                readable["embyIdentities"],
            )
            records = self._matching_records(catalog, wanted)
            if not records:
                records = [catalog[key] for key in fallback_keys]
        records.sort(key=lambda record: self._rank(record, query))
        items = []
        for record in records[:limit]:
            detail = self._project(record, readable)
            items.append({
                **detail["media"],
                "userState": detail["userState"],
                "resultText": detail["resultText"],
                "subscriptionStatus": detail["subscription"]["status"],
                "embyStatus": detail["emby"]["status"],
                "primaryAction": detail["primaryAction"],
                "links": detail["links"],
            })
        return {
            "ok": True,
            "query": _text(query),
            "items": items,
            "page": {"total": len(records), "limit": limit},
        }

    def detail(self, media_key_value: str) -> dict | None:
        parsed = _parse_media_key(media_key_value)
        if not parsed:
            raise ValueError("mediaKey")
        media_type, tmdb_id = parsed
        canonical = _canonical_media_key(media_type, tmdb_id)
        target = (media_type, tmdb_id)
        catalog, readable = self._catalog(tmdb_id, target)
        record = catalog.get(canonical)
        if record is None or not record["title"]:
            self._tmdb(catalog, tmdb_id, target, 1, readable["embyIdentities"])
            record = catalog.get(canonical)
        return self._project(record, readable) if record else None


def _error(code: str, message: str, status: int):
    return jsonify({
        "code": code,
        "error": message,
        "request_id": current_request_id(),
    }), status


def register_media_search(app: Flask, clock=None, tmdb_reader=None):
    service = MediaSearchService(app, clock=clock, tmdb_reader=tmdb_reader)
    app.extensions["mcc_media_search"] = service

    @app.get("/api/v2/search")
    def media_search():
        query = request.args.get("q", "")
        if len(query) > 200:
            return _error("MEDIA_SEARCH_QUERY_INVALID", "搜索词不能超过 200 个字符", 400)
        raw_limit = request.args.get("limit", "10")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return _error("MEDIA_SEARCH_LIMIT_INVALID", "搜索数量无效", 400)
        if not 1 <= limit <= 20:
            return _error("MEDIA_SEARCH_LIMIT_INVALID", "搜索数量必须在 1 到 20 之间", 400)
        try:
            return jsonify(service.search(query, limit))
        except Exception:
            return _error("MEDIA_SEARCH_READ_FAILED", "本地作品搜索暂时不可用", 502)

    @app.get("/api/v2/media/<path:media_key_value>")
    def media_overview(media_key_value):
        try:
            payload = service.detail(media_key_value)
        except ValueError:
            return _error("MEDIA_KEY_INVALID", "作品标识必须是 mediaType:tmdbId", 400)
        except Exception:
            return _error("MEDIA_OVERVIEW_READ_FAILED", "作品总览暂时不可用", 502)
        if payload is None:
            return _error("MEDIA_NOT_FOUND", "没有找到该作品的本地证据", 404)
        return jsonify(payload)

    return service
