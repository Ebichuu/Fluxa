from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import date, datetime, timedelta, timezone

from flask import Flask, Response, jsonify, request

from app import discover_runtime
from app.contract_mapping import map_calendar_payload
from app.http_runtime import current_request_id
from app.task_exception_runtime import protection_rule


ALLOWED_MEDIA_TYPES = {"all", "movie", "tv"}
ALLOWED_VIEWS = {"", "summary", "detail"}
ACQUISITION_STAGES = {"resource", "download", "cloud115"}
LIBRARY_STAGES = {"symedia", "strm", "library", "emby"}
BEIJING_TZ = timezone(timedelta(hours=8))


def _text(value) -> str:
    return str(value or "").strip()


def _integer(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_datetime(value):
    text = _text(value)
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text[:19], pattern)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BEIJING_TZ)
    return parsed.astimezone(timezone.utc)


def _parse_date(value) -> date | None:
    try:
        return datetime.strptime(_text(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def _month_keys(start: date, end: date) -> list[tuple[int, int]]:
    values = []
    current = date(start.year, start.month, 1)
    while current <= end:
        values.append((current.year, current.month))
        current = date(current.year + (1 if current.month == 12 else 0), 1 if current.month == 12 else current.month + 1, 1)
    return values


def _stage_time(item: dict, names: set[str], statuses: set[str]) -> tuple[str, str]:
    candidates = [
        stage
        for stage in item.get("stages") or []
        if isinstance(stage, dict)
        and _text(stage.get("stage")) in names
        and _text(stage.get("status")) in statuses
        and _text(stage.get("evidence")) != "missing"
        and _text(stage.get("observedAt"))
    ]
    if not candidates:
        return "", ""
    stage = max(candidates, key=lambda row: _text(row.get("observedAt")))
    return _text(stage.get("observedAt")), _text(stage.get("source"))


def _matches_identity(entry: dict, item: dict) -> bool:
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
    return not (
        entry_media == "tv"
        and entry_season
        and item_season
        and entry_season != item_season
    )


def _match_rank(entry: dict, item: dict) -> tuple[int, str]:
    same_subscription = _text(entry.get("key")) in {
        _text(item.get("subscriptionId")),
        *(_text(value) for value in (item.get("sourceIds") or {}).get("subscriptionIds") or []),
    }
    return (0 if same_subscription else 1, _text(item.get("updatedAt")))


def _episode_rows(entry: dict, item: dict) -> list[dict]:
    season = _integer(entry.get("seasonNumber"))
    episode = _integer(entry.get("episodeNumber"))
    if not episode and episode != 0:
        return []
    return [
        row
        for row in item.get("episodeEvidence") or []
        if isinstance(row, dict)
        and _text(row.get("numberingScheme")) in {"season_episode", "special"}
        and _integer(row.get("seasonNumber")) == season
        and _integer(row.get("episodeStart")) <= episode <= _integer(row.get("episodeEnd"))
    ]


def _latest_episode_time(rows: list[dict], stages: set[str], statuses: set[str]) -> tuple[str, str]:
    candidates = [
        row
        for row in rows
        if _text(row.get("stage")) in stages
        and _text(row.get("status")) in statuses
        and _text(row.get("observedAt"))
    ]
    if not candidates:
        return "", ""
    latest = max(candidates, key=lambda row: _text(row.get("observedAt")))
    return _text(latest.get("observedAt")), _text(latest.get("source"))


def _episode_health(entry: dict, rows: list[dict]) -> tuple[str, str, str]:
    if entry.get("inLibrary"):
        return "normal", "CALENDAR_EPISODE_IN_LIBRARY", "该集已在媒体库中"
    real_failures = [
        row
        for row in rows
        if _text(row.get("status")) == "blocked"
        and not protection_rule(row.get("reasonCode"), row.get("reasonText"))
    ]
    if real_failures:
        source = _text(real_failures[0].get("source"))
        return (
            "action_required",
            _text(real_failures[0].get("reasonCode")) or "CALENDAR_EPISODE_BLOCKED",
            "Symedia 未完成该集入库" if source == "Symedia" else "该集获取过程发生阻塞",
        )
    protected = [
        row
        for row in rows
        if protection_rule(row.get("reasonCode"), row.get("reasonText"))
    ]
    if protected:
        return "protected", protection_rule(
            protected[0].get("reasonCode"),
            protected[0].get("reasonText"),
        ), "已有更高质量版本，未执行覆盖"
    if any(_text(row.get("status")) in {"active", "waiting"} for row in rows):
        return "waiting", "CALENDAR_EPISODE_IN_PROGRESS", "该集正在获取"
    if any(
        _text(row.get("stage")) in LIBRARY_STAGES and _text(row.get("status")) == "done"
        for row in rows
    ):
        return "normal", "CALENDAR_EPISODE_LIBRARY_DONE", "该集已完成入库"
    if rows:
        return "waiting", "CALENDAR_EPISODE_ACQUIRED", "已找到该集资源，等待入库"
    return "evidence_insufficient", "CALENDAR_EPISODE_EVIDENCE_MISSING", "尚无该集的明确获取或入库证据"


def _empty_task() -> dict:
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


def _public_task(entry: dict, items: list[dict]) -> dict:
    matches = [item for item in items if isinstance(item, dict) and _matches_identity(entry, item)]
    if not matches:
        return _empty_task()
    item = sorted(matches, key=lambda row: _match_rank(entry, row))[0]
    common = {
        "chainId": _text(item.get("chainId")),
        "targetKey": _text(item.get("targetKey")),
        "freshUntil": _text(item.get("freshUntil")),
    }
    if _text(entry.get("mediaType")) != "tv":
        acquired_at, acquisition_source = _stage_time(item, ACQUISITION_STAGES, {"active", "done", "blocked"})
        library_at, library_source = _stage_time(item, LIBRARY_STAGES, {"done"})
        return {
            **common,
            "healthState": _text(item.get("healthState")) or "evidence_insufficient",
            "reasonCode": _text(item.get("reasonCode")),
            "reasonText": _text(item.get("reasonText")),
            "observedAt": _text(item.get("observedAt")),
            "acquiredAt": acquired_at,
            "acquisitionSource": acquisition_source,
            "libraryAt": library_at,
            "librarySource": library_source,
        }
    episode_rows = _episode_rows(entry, item)
    acquired_at, acquisition_source = _latest_episode_time(
        episode_rows,
        ACQUISITION_STAGES,
        {"active", "done", "blocked"},
    )
    library_at, library_source = _latest_episode_time(episode_rows, LIBRARY_STAGES, {"done"})
    health_state, reason_code, reason_text = _episode_health(entry, episode_rows)
    observed_at = max((_text(row.get("observedAt")) for row in episode_rows), default="")
    return {
        **common,
        "healthState": health_state,
        "reasonCode": reason_code,
        "reasonText": reason_text,
        "observedAt": observed_at,
        "acquiredAt": acquired_at,
        "acquisitionSource": acquisition_source,
        "libraryAt": library_at,
        "librarySource": library_source,
    }


def _normalize_entry_evidence(entry: dict) -> dict:
    value = dict(entry)
    library_at = _as_datetime(value.get("libraryAt"))
    acquired_at = _as_datetime(value.get("acquiredAt"))
    if library_at:
        value["inLibrary"] = True
    if acquired_at and library_at and acquired_at > library_at:
        value["acquiredAt"] = ""
        value["acquisitionSource"] = ""
    return value


def _entry_status(entry: dict, today: str) -> str:
    current = datetime.now(timezone.utc)
    if entry.get("inLibrary") or entry.get("libraryAt"):
        return "library"
    if entry.get("acquiredAt"):
        return "acquiring"
    if _text(entry.get("healthState")) == "protected" or protection_rule(
        entry.get("reasonCode"), entry.get("reasonText")
    ):
        return "protected"
    if _text(entry.get("date")) >= today:
        return "upcoming"
    if not entry.get("followScopeExplicit"):
        return "unknown"
    created_at = _as_datetime(entry.get("subscriptionCreatedAt"))
    aired_at = _as_datetime(entry.get("airAt")) or _as_datetime(entry.get("date"))
    if not created_at and not entry.get("includePastEpisodes"):
        return "unknown"
    if created_at and aired_at and aired_at < created_at and not entry.get("includePastEpisodes"):
        return "unknown"
    fresh_until = _as_datetime(entry.get("freshUntil"))
    if fresh_until and fresh_until < current:
        return "unknown"
    delay_hours = max(0, _integer(entry.get("allowedDelayHours"), 24))
    if aired_at and current < aired_at + timedelta(hours=delay_hours):
        return "unknown"
    return "missing"


def _is_pre_subscription_episode(entry: dict) -> bool:
    if entry.get("includePastEpisodes"):
        return False
    created_at = _as_datetime(entry.get("subscriptionCreatedAt"))
    entry_date = _parse_date(entry.get("date"))
    if created_at and entry_date:
        return entry_date < created_at.astimezone(BEIJING_TZ).date()
    aired_at = _as_datetime(entry.get("airAt")) or _as_datetime(entry.get("date"))
    return bool(created_at and aired_at and aired_at < created_at)


def _summary_calendar(calendar: dict) -> dict:
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    grouped = {}
    for entry in calendar.get("entries") or []:
        grouped.setdefault(_text(entry.get("date")), []).append(entry)
    days = []
    for date_key in sorted(grouped):
        entries = grouped[date_key]
        status_counts = {
            state: sum(_entry_status(entry, today) == state for entry in entries)
            for state in ("upcoming", "acquiring", "library", "protected", "missing", "unknown")
        }
        days.append({
            "date": date_key,
            "total": len(entries),
            "statusCounts": status_counts,
            "preview": [{
                "key": entry.get("key"),
                "title": entry.get("title"),
                "episodeLabel": entry.get("episodeLabel"),
                "posterUrl": entry.get("posterUrl"),
                "mediaType": entry.get("mediaType"),
                "healthState": entry.get("healthState"),
                "status": _entry_status(entry, today),
            } for entry in entries[:3]],
            "hasMore": len(entries) > 3,
        })
    return {
        **calendar,
        "entries": [],
        "days": days,
        "view": "summary",
    }


def _torra_calendar_source_item(row: dict) -> dict | None:
    if not isinstance(row, dict) or _text(row.get("reconciliationState")) != "only_torra":
        return None
    media_type = _text(row.get("mediaType"))
    tmdb_id = _text(row.get("tmdbId"))
    if media_type not in {"movie", "tv"} or not tmdb_id.isdigit():
        return None
    season = _integer(row.get("seasonNumber")) if media_type == "tv" else 0
    remote_ref = _text(row.get("remoteRef")) or tmdb_id
    return {
        "subscription_key": f"torra:{remote_ref}",
        "title": _text(row.get("title")),
        "media_type": media_type,
        "tmdb_id": tmdb_id,
        "target_season": season,
        "season_number": season,
        "source": "torra",
        "source_label": "Torra 只读追更",
        "read_only": True,
        # 远端创建时间不在公开订阅响应中，使用本次可靠读取时间避免把历史集误判为缺集。
        "subscribed_at": _text(row.get("observedAt")),
        "follow_scope_explicit": True,
        "include_past_episodes": False,
        "allowed_delay_hours": 24,
        "in_library": False,
    }


def _calendar_entry_identity(entry: dict) -> tuple:
    return (
        _text(entry.get("date")),
        _text(entry.get("mediaType")),
        _text(entry.get("tmdbId")),
        _integer(entry.get("seasonNumber")),
        _integer(entry.get("episodeNumber")),
    )


class CalendarTimelineService:
    def __init__(self, app: Flask, calendar_loader=None):
        self.app = app
        self.calendar_loader = calendar_loader or discover_runtime.build_subscription_calendar
        self._torra_cache = {}
        self._torra_cache_lock = threading.RLock()

    def _torra_calendar_entries(self, year: int, month: int, media_type: str) -> tuple[list[dict], list[str]]:
        cache_key = (year, month, media_type)
        with self._torra_cache_lock:
            cached = self._torra_cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < 300:
                return cached[1], cached[2]

        reconciliation = self.app.extensions.get("mcc_subscription_reconciliation")
        if not reconciliation:
            return [], []
        try:
            snapshot = reconciliation.snapshot() or {}
        except Exception:
            return [], ["Torra 只读追更暂时无法生成日历"]
        if snapshot.get("sourceError"):
            return [], ["Torra 只读追更暂时无法读取"]

        raw_entries = []
        errors = []
        seen_targets = set()
        for row in snapshot.get("items") or []:
            item = _torra_calendar_source_item(row)
            if not item:
                continue
            if media_type in {"movie", "tv"} and item["media_type"] != media_type:
                continue
            target = (item["media_type"], item["tmdb_id"], _integer(item.get("target_season")))
            if target in seen_targets:
                continue
            seen_targets.add(target)
            try:
                rows, error = discover_runtime.build_subscription_calendar_entries_for_item(
                    item,
                    year,
                    month,
                    media_type,
                )
            except Exception:
                rows, error = [], ""
            raw_entries.extend(rows)
            if error:
                errors.append(error)

        payload = {
            "success": True,
            "year": year,
            "month": month,
            "type": media_type,
            "entries": raw_entries,
            "stats": {
                "entries": len(raw_entries),
                "titles": len({str(row.get("key") or row.get("title") or "") for row in raw_entries}),
                "in_library": 0,
                "pending": len(raw_entries),
            },
            "errors": errors,
        }
        mapped = map_calendar_payload(payload).get("calendar") or {}
        entries = [entry for entry in mapped.get("entries") or [] if isinstance(entry, dict)]
        public_errors = ["部分 Torra 只读追更缺少可验证播出日历"] if errors else []
        with self._torra_cache_lock:
            self._torra_cache[cache_key] = (time.monotonic(), entries, public_errors)
        return entries, public_errors

    def _base_calendar(
        self,
        year: int,
        month: int,
        media_type: str,
        start: date | None,
        end: date | None,
    ) -> dict:
        range_start = start or date(year, month, 1)
        range_end = end or (
            date(year + 1, 1, 1) - timedelta(days=1)
            if month == 12
            else date(year, month + 1, 1) - timedelta(days=1)
        )
        entries = []
        errors = []
        for current_year, current_month in _month_keys(range_start, range_end):
            mapped = map_calendar_payload(self.calendar_loader(current_year, current_month, media_type))
            current = mapped.get("calendar") or {}
            entries.extend(
                entry
                for entry in current.get("entries") or []
                if range_start.isoformat() <= _text(entry.get("date")) <= range_end.isoformat()
            )
            errors.extend(current.get("errors") or [])
            torra_entries, torra_errors = self._torra_calendar_entries(
                current_year,
                current_month,
                media_type,
            )
            existing = {_calendar_entry_identity(entry) for entry in entries}
            entries.extend(
                entry for entry in torra_entries
                if _calendar_entry_identity(entry) not in existing
                and range_start.isoformat() <= _text(entry.get("date")) <= range_end.isoformat()
            )
            errors.extend(torra_errors)
        entries.sort(key=lambda entry: (
            _text(entry.get("date")),
            _text(entry.get("title")),
            _integer(entry.get("seasonNumber")),
            _integer(entry.get("episodeNumber")),
        ))
        return {
            "year": year,
            "month": month,
            "mediaType": media_type,
            "entries": entries,
            "errors": errors[:20],
            "errorCount": len(errors),
            "stats": {
                "entries": len(entries),
                "titles": len({_text(entry.get("key")) or _text(entry.get("title")) for entry in entries}),
                "inLibrary": sum(bool(entry.get("inLibrary")) for entry in entries),
                "pending": sum(not bool(entry.get("inLibrary")) for entry in entries),
            },
        }

    def snapshot(
        self,
        year: int,
        month: int,
        media_type: str,
        *,
        view: str = "",
        start: date | None = None,
        end: date | None = None,
        detail_date: date | None = None,
    ) -> dict:
        if detail_date:
            start = end = detail_date
            year, month = detail_date.year, detail_date.month
        calendar = self._base_calendar(year, month, media_type, start, end)
        task_service = self.app.extensions.get("mcc_task_chain_v2_service")
        task_payload = task_service.full_snapshot() if task_service else {"items": [], "version": ""}
        task_items = task_payload.get("items") or []
        raw_entries = [_normalize_entry_evidence({
            **entry,
            "airAt": f"{entry.get('date')}T00:00:00+08:00" if entry.get("date") else "",
            **_public_task(entry, task_items),
        }) for entry in calendar.get("entries") or []]
        excluded_before_subscription = sum(_is_pre_subscription_episode(entry) for entry in raw_entries)
        entries = [entry for entry in raw_entries if not _is_pre_subscription_episode(entry)]
        today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
        entries = [{**entry, "status": _entry_status(entry, today)} for entry in entries]
        calendar = {
            **calendar,
            "timeZone": "Asia/Shanghai",
            "entries": entries,
            "view": view or "legacy",
            "stats": {
                **(calendar.get("stats") or {}),
                "entries": len(entries),
                "titles": len({_text(entry.get("key")) or _text(entry.get("title")) for entry in entries}),
                "inLibrary": sum(bool(entry.get("inLibrary")) for entry in entries),
                "pending": sum(not bool(entry.get("inLibrary")) for entry in entries),
                "acquired": sum(bool(entry.get("acquiredAt")) for entry in entries),
                "libraryEvidence": sum(bool(entry.get("libraryAt")) for entry in entries),
                "actionRequired": sum(entry.get("healthState") == "action_required" for entry in entries),
                "excludedBeforeSubscription": excluded_before_subscription,
                "statusCounts": {
                    state: sum(entry.get("status") == state for entry in entries)
                    for state in ("upcoming", "acquiring", "library", "protected", "missing", "unknown")
                },
            },
        }
        if view == "summary":
            calendar = _summary_calendar(calendar)
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
        view = _text(request.args.get("view")).lower()
        detail_date = _parse_date(request.args.get("date")) if request.args.get("date") else None
        from_value = _parse_date(request.args.get("from")) if request.args.get("from") else None
        to_value = _parse_date(request.args.get("to")) if request.args.get("to") else None
        invalid_date = (
            bool(request.args.get("date")) and not detail_date
            or bool(request.args.get("from")) and not from_value
            or bool(request.args.get("to")) and not to_value
        )
        invalid_range = (
            bool(from_value) != bool(to_value)
            or bool(from_value and to_value and (from_value > to_value or (to_value - from_value).days > 62))
        )
        if (
            not 2000 <= year <= 2100
            or not 1 <= month <= 12
            or media_type not in ALLOWED_MEDIA_TYPES
            or view not in ALLOWED_VIEWS
            or invalid_date
            or invalid_range
            or (view == "detail" and not detail_date)
        ):
            return _error("CALENDAR_RANGE_INVALID", "日历范围、日期或视图无效", 400)
        try:
            payload = service.snapshot(
                year,
                month,
                media_type,
                view=view,
                start=from_value,
                end=to_value,
                detail_date=detail_date,
            )
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
