from __future__ import annotations

import hashlib
import json
from datetime import datetime

from flask import Flask, Response, jsonify, request

from app import discover_runtime
from app.contract_mapping import map_calendar_payload
from app.http_runtime import current_request_id


ALLOWED_MEDIA_TYPES = {"all", "movie", "tv"}


def _text(value) -> str:
    return str(value or "").strip()


def _integer(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _stage_time(item: dict, names: tuple[str, ...], statuses: set[str]) -> tuple[str, str]:
    stages = [stage for stage in item.get("stages") or [] if isinstance(stage, dict)]
    for name in names:
        stage = next((row for row in stages if _text(row.get("stage")) == name), None)
        if not stage:
            continue
        if _text(stage.get("status")) not in statuses or _text(stage.get("evidence")) == "missing":
            continue
        observed_at = _text(stage.get("observedAt"))
        if observed_at:
            return observed_at, _text(stage.get("source"))
    return "", ""


def _matches(entry: dict, item: dict) -> bool:
    entry_tmdb = _text(entry.get("tmdbId"))
    item_tmdb = _text(item.get("tmdbId"))
    if entry_tmdb and item_tmdb:
        if entry_tmdb != item_tmdb:
            return False
    else:
        entry_key = _text(entry.get("key"))
        source_keys = {
            _text(item.get("subscriptionId")),
            *(_text(value) for value in (item.get("sourceIds") or {}).get("subscriptionIds") or []),
        }
        if not entry_key or entry_key not in source_keys:
            return False
    entry_media = _text(entry.get("mediaType"))
    if entry_media and entry_media != _text(item.get("mediaType")):
        return False
    entry_season = _integer(entry.get("seasonNumber"))
    item_season = _integer(item.get("seasonNumber"))
    if entry_media == "tv" and entry_season and item_season and entry_season != item_season:
        return False
    entry_episode = _integer(entry.get("episodeNumber"))
    item_episode = _integer(item.get("episodeNumber"))
    return not (entry_episode and item_episode and entry_episode != item_episode)


def _match_rank(entry: dict, item: dict) -> tuple[int, int, str]:
    exact_episode = bool(
        _integer(entry.get("episodeNumber"))
        and _integer(entry.get("episodeNumber")) == _integer(item.get("episodeNumber"))
    )
    same_subscription = _text(entry.get("key")) in {
        _text(item.get("subscriptionId")),
        *(_text(value) for value in (item.get("sourceIds") or {}).get("subscriptionIds") or []),
    }
    return (0 if exact_episode else 1, 0 if same_subscription else 1, _text(item.get("updatedAt")))


def _public_task(entry: dict, items: list[dict]) -> dict:
    matches = [item for item in items if isinstance(item, dict) and _matches(entry, item)]
    if not matches:
        return {
            "chainId": "",
            "targetKey": "",
            "healthState": "evidence_insufficient",
            "reasonCode": "CALENDAR_TASK_NOT_FOUND",
            "reasonText": "尚未形成可关联的任务链",
            "observedAt": "",
            "freshUntil": "",
            "acquiredAt": "",
            "acquisitionSource": "",
            "libraryAt": "",
            "librarySource": "",
        }
    item = sorted(matches, key=lambda row: _match_rank(entry, row))[0]
    acquired_at, acquisition_source = _stage_time(
        item,
        ("resource", "download", "cloud115"),
        {"active", "done", "blocked"},
    )
    library_at, library_source = _stage_time(
        item,
        ("emby", "library", "strm", "symedia"),
        {"done"},
    )
    return {
        "chainId": _text(item.get("chainId")),
        "targetKey": _text(item.get("targetKey")),
        "healthState": _text(item.get("healthState")) or "evidence_insufficient",
        "reasonCode": _text(item.get("reasonCode")),
        "reasonText": _text(item.get("reasonText")),
        "observedAt": _text(item.get("observedAt")),
        "freshUntil": _text(item.get("freshUntil")),
        "acquiredAt": acquired_at,
        "acquisitionSource": acquisition_source,
        "libraryAt": library_at,
        "librarySource": library_source,
    }


class CalendarTimelineService:
    def __init__(self, app: Flask, calendar_loader=None):
        self.app = app
        self.calendar_loader = calendar_loader or discover_runtime.build_subscription_calendar

    def snapshot(self, year: int, month: int, media_type: str) -> dict:
        mapped = map_calendar_payload(self.calendar_loader(year, month, media_type))
        calendar = mapped.get("calendar") or {}
        task_service = self.app.extensions.get("mcc_task_chain_v2_service")
        task_payload = task_service.full_snapshot() if task_service else {"items": [], "version": ""}
        task_items = task_payload.get("items") or []
        entries = []
        for entry in calendar.get("entries") or []:
            task = _public_task(entry, task_items)
            entries.append({
                **entry,
                "airAt": f"{entry.get('date')}T00:00:00+08:00" if entry.get("date") else "",
                **task,
            })
        calendar = {
            **calendar,
            "timeZone": "Asia/Shanghai",
            "entries": entries,
            "stats": {
                **(calendar.get("stats") or {}),
                "acquired": sum(bool(entry.get("acquiredAt")) for entry in entries),
                "libraryEvidence": sum(bool(entry.get("libraryAt")) for entry in entries),
                "actionRequired": sum(entry.get("healthState") == "action_required" for entry in entries),
            },
        }
        stable = json.dumps(calendar, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        version = hashlib.sha256(
            f"{task_payload.get('version') or ''}|{stable}".encode("utf-8")
        ).hexdigest()[:24]
        return {"ok": True, "version": version, "calendar": calendar}


def _error(code: str, message: str, status: int):
    return jsonify({"code": code, "error": message, "request_id": current_request_id()}), status


def register_calendar_timeline(app: Flask, calendar_loader=None):
    service = CalendarTimelineService(app, calendar_loader=calendar_loader)
    app.extensions["mcc_calendar_timeline"] = service

    @app.get("/api/v2/calendar")
    def calendar_timeline():
        now = datetime.now()
        try:
            year = int(request.args.get("year", now.year))
            month = int(request.args.get("month", now.month))
        except (TypeError, ValueError):
            return _error("CALENDAR_RANGE_INVALID", "日历年月无效", 400)
        media_type = _text(request.args.get("type") or "all").lower()
        if not 2000 <= year <= 2100 or not 1 <= month <= 12 or media_type not in ALLOWED_MEDIA_TYPES:
            return _error("CALENDAR_RANGE_INVALID", "日历年月或媒体类型无效", 400)
        try:
            payload = service.snapshot(year, month, media_type)
        except Exception:
            return _error("CALENDAR_TIMELINE_READ_FAILED", "日历时间线读取失败", 502)
        etag = payload.get("version") or ""
        if etag and request.if_none_match.contains(etag):
            response = Response(status=304)
        else:
            response = jsonify(payload)
        if etag:
            response.set_etag(etag)
        response.headers["Cache-Control"] = "private, no-cache, must-revalidate"
        return response

    return service
