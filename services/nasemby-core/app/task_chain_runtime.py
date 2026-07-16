from __future__ import annotations

import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from flask import Flask, jsonify

from app import discover_runtime


CLOUD_STUCK_HOURS = 6
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


def _compact(value) -> str:
    return discover_runtime.compact_match_text(value)


def _file_key(value: str) -> str:
    basename = os.path.basename(value.replace("\\", "/"))
    return _compact(re.sub(r"\.[A-Za-z0-9]{2,5}$", "", basename, flags=re.IGNORECASE))


def _season_from_text(value: str) -> int:
    match = re.search(r"(?:^|[.\s_-])S(?:eason)?[.\s_-]*0*(\d{1,2})(?:[.\s_-]|$)", value, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _torra_media_type(row: dict) -> str:
    value = _string(row.get("media_type"))
    return value if value in {"movie", "tv"} else "unknown"


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


def _same_season(expected, actual) -> bool:
    expected_number = _number(expected)
    actual_number = _number(actual)
    return expected is None or expected_number == 0 or actual_number == 0 or expected_number == actual_number


def _match_torra(subscription: dict, rows: list[dict]):
    exact = next((
        row
        for row in rows
        if _string(row.get("tmdb_id")) == subscription["tmdbId"]
        and _torra_media_type(row) == subscription["mediaType"]
        and _same_season(subscription.get("seasonNumber"), row.get("season_number"))
    ), None)
    if exact:
        return exact
    title = _compact(subscription["title"])
    return next((
        row
        for row in rows
        if _torra_media_type(row) == subscription["mediaType"]
        and _same_season(subscription.get("seasonNumber"), row.get("season_number"))
        and title
        and (candidate := _compact(row.get("name") or row.get("keyword")))
        and (candidate in title or title in candidate)
    ), None)


def _symedia_season(row: dict) -> int:
    if _number(row.get("season")):
        return int(_number(row.get("season")))
    match = re.search(r"S0*(\d{1,2})", _string(row.get("season_episode")), re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _match_symedia(subscription: dict, rows: list[dict]) -> list[dict]:
    exact = [
        row
        for row in rows
        if _string(row.get("tmdbid")) == subscription["tmdbId"]
        and _same_season(subscription.get("seasonNumber"), _symedia_season(row))
    ]
    if exact:
        return exact
    title = _compact(subscription["title"])
    return [
        row
        for row in rows
        if _same_season(subscription.get("seasonNumber"), _symedia_season(row))
        and title
        and (candidate := _compact(row.get("title")))
        and (candidate in title or title in candidate)
    ]


def _task_matches_title(task: dict, title: str, season_number) -> bool:
    task_title = _compact(task.get("name"))
    wanted = _compact(title)
    if not task_title or not wanted or wanted not in task_title:
        return False
    return _same_season(season_number, _season_from_text(_string(task.get("name"))))


def _match_qb(subscription: dict, torra: dict | None, tasks: list[dict]):
    evidence_keys = [key for key in map(_file_key, _torra_files(torra)) if len(key) >= 8]
    by_file = [
        task
        for task in tasks
        if (task_key := _file_key(_string(task.get("name"))))
        and any(task_key in key or key in task_key for key in evidence_keys)
    ]
    if by_file:
        return by_file, "strong"
    by_title = [
        task
        for task in tasks
        if _task_matches_title(task, subscription["title"], subscription.get("seasonNumber"))
    ]
    return by_title, "fallback" if by_title else "strong"


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


def _cloud_step(download: dict, symedia_rows: list[dict], now: datetime) -> dict:
    if symedia_rows:
        return {
            "key": "cloud115", "label": "进入 115", "status": "done", "evidence": "verified",
            "detail": f"Symedia 已收到 {len(symedia_rows)} 条源文件记录",
            "timestamp": _newest(row.get("date") for row in symedia_rows), "source": "Symedia",
        }
    if download["status"] == "done":
        completed_at = _parse_timestamp(download["timestamp"]) if download["timestamp"] else None
        age_hours = max(0, (now - completed_at).total_seconds() / 3600) if completed_at else 0
        if age_hours >= CLOUD_STUCK_HOURS:
            return {
                "key": "cloud115", "label": "进入 115", "status": "blocked", "evidence": "inferred",
                "detail": f"推断已等待 {math.floor(age_hours)} 小时，仍无 Symedia 记录",
                "timestamp": download["timestamp"], "source": "相邻证据推断",
            }
        return {
            "key": "cloud115", "label": "进入 115", "status": "active", "evidence": "inferred",
            "detail": "下载已完成，推断正在硬链接或秒传",
            "timestamp": download["timestamp"], "source": "相邻证据推断",
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
        reason = _string(failed[0].get("errmsg")) or "Symedia 返回失败"
        return {
            "key": "library", "label": "入库", "status": "blocked", "evidence": "verified",
            "detail": f"{success} 成功 / {len(failed)} 失败 · {reason}",
            "timestamp": timestamp, "source": "Symedia",
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


def _build_subscription_item(subscription, torra_rows, qb_tasks, symedia_rows, emby_index, urls, source, now):
    torra = _match_torra(subscription, torra_rows)
    matched_symedia = _match_symedia(subscription, symedia_rows)
    matched_qb, qb_confidence = _match_qb(subscription, torra, qb_tasks)
    subscription_step = {
        "key": "subscription", "label": "订阅", "status": "done", "evidence": "verified",
        "detail": f"{source} 与 Torra 订阅已关联" if torra else f"{source} 订阅存在，Torra 尚未关联",
        "timestamp": subscription["createdAt"],
        "source": f"{source} + Torra" if torra else source,
    }
    download = _download_step(matched_qb, torra)
    cloud = _cloud_step(download, matched_symedia, now)
    indexed = _emby_has(emby_index, subscription["mediaType"], subscription["tmdbId"])
    library = _library_step(matched_symedia, indexed)
    steps = [subscription_step, download, cloud, library]
    confidence = "strong" if (
        torra
        or any(_string(row.get("tmdbid")) == subscription["tmdbId"] for row in matched_symedia)
    ) else qb_confidence
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
        "updatedAt": _newest([subscription["updatedAt"], *(step["timestamp"] for step in steps)]),
    }
    return item, matched_qb, matched_symedia


def _orphan_qb_item(task: dict, urls: dict, now: datetime) -> dict:
    subscription = {
        "key": "subscription", "label": "订阅", "status": "unknown", "evidence": "missing",
        "detail": "未关联订阅中枢", "timestamp": "", "source": "",
    }
    download = _download_step([task], None)
    cloud = _cloud_step(download, [], now)
    library = _library_step([], False)
    steps = [subscription, download, cloud, library]
    return {
        "id": f"qb:{_string(task.get('hash'))}", "title": _string(task.get("name")),
        "mediaType": "unknown", "tmdbId": "", "seasonNumber": _season_from_text(_string(task.get("name"))),
        "posterUrl": "", "origin": "download", "channel": "PT", "state": _item_state(steps),
        "confidence": "unlinked", "progress": round(_number(task.get("progress")) * 100),
        "currentStep": _current_step(steps), "steps": steps, "embyIndexed": False,
        "suggestion": _suggestion(steps, urls), "qbControl": _qb_control([task]),
        "sourceIds": {"subscriptionId": "", "torraId": "", "qbHashes": [_string(task.get("hash"))], "symediaIds": []},
        "updatedAt": _newest([_iso_from_seconds(task.get("completionOn")), _iso_from_seconds(task.get("addedOn"))]),
    }


def _orphan_symedia_item(row: dict, urls: dict) -> dict:
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
    return {
        "id": f"symedia:{row_id}",
        "title": _string(row.get("title")) or os.path.basename(_string(row.get("src"))) or "未识别入库记录",
        "mediaType": media_type if media_type in {"movie", "tv"} else "unknown",
        "tmdbId": _string(row.get("tmdbid")), "seasonNumber": _symedia_season(row),
        "posterUrl": "", "origin": "library", "channel": "PT", "state": _item_state(steps),
        "confidence": "unlinked", "progress": 100 if library["status"] == "done" else 0,
        "currentStep": _current_step(steps), "steps": steps, "embyIndexed": False,
        "suggestion": _suggestion(steps, urls), "qbControl": _qb_control([]),
        "sourceIds": {"subscriptionId": "", "torraId": "", "qbHashes": [], "symediaIds": [row_id]},
        "updatedAt": _string(row.get("date")),
    }


def build_task_chain(input_data: dict) -> dict:
    now = input_data.get("now") or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    used_qb = set()
    used_symedia = set()
    subscription_items = []
    for subscription in input_data["subscriptions"]:
        item, qb_tasks, symedia_rows = _build_subscription_item(
            subscription,
            input_data["torraRows"],
            input_data["qb"]["tasks"],
            input_data["symediaRows"],
            input_data.get("embyIndex"),
            input_data["urls"],
            input_data.get("subscriptionSource") or "中控",
            now,
        )
        subscription_items.append(item)
        used_qb.update(_string(task.get("hash")) for task in qb_tasks)
        used_symedia.update(id(row) for row in symedia_rows)
    orphan_qb = [
        _orphan_qb_item(task, input_data["urls"], now)
        for task in input_data["qb"]["tasks"]
        if _string(task.get("hash")) not in used_qb
    ]
    orphan_symedia = [
        _orphan_symedia_item(row, input_data["urls"])
        for row in input_data["symediaRows"]
        if id(row) not in used_symedia
    ][:50]
    items = [*subscription_items, *orphan_qb, *orphan_symedia]
    items.sort(key=lambda item: item["updatedAt"], reverse=True)
    items.sort(key=lambda item: STATE_PRIORITY[item["state"]])
    errors = input_data.get("serviceErrors") or {}
    qb = input_data["qb"]
    emby_index = input_data.get("embyIndex")
    urls = input_data["urls"]
    return {
        "generatedAt": _iso_datetime(now),
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
        with ThreadPoolExecutor(max_workers=5) as executor:
            subscription_future = executor.submit(self.subscription_loader)
            qb_future = executor.submit(self.qb.summary)
            torra_future = executor.submit(
                _optional_read,
                self.torra.is_configured(),
                self.torra.list_subscriptions,
                [],
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
            symedia_page, symedia_error = symedia_future.result()
            emby_index, emby_error = emby_future.result()
        return build_task_chain({
            "subscriptions": subscriptions,
            "subscriptionSource": "NasEmby Core",
            "torraRows": torra_rows,
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
                "symedia": symedia_error,
                "emby": emby_error,
            },
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
