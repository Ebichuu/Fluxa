from __future__ import annotations

import re
from dataclasses import dataclass

from flask import jsonify, request

from app.http_runtime import current_request_id
from app.rss_subscription_match_runtime import qb_task_matches


MOVIEPILOT_BACKUP_ACTION_TYPE = "backup-push"
MOVIEPILOT_BACKUP_COOLDOWN_SECONDS = 60


class MoviePilotBackupError(RuntimeError):
    def __init__(self, code, message, status):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.status = int(status)


def _text(value):
    return str(value or "").strip()


def _truthy(value):
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _positive_integer(value):
    if isinstance(value, bool):
        return 0
    match = re.fullmatch(r"\d+", _text(value))
    number = int(match.group(0)) if match else 0
    return number if number > 0 else 0


def _media_type(value):
    normalized = _text(value).lower()
    if normalized in {"movie", "电影"}:
        return "movie"
    if normalized in {"tv", "series", "show", "电视剧", "剧集"}:
        return "tv"
    return ""


def _year(item):
    for key in ("year", "release_date", "first_air_date", "air_date", "date"):
        match = re.search(r"(?:19|20)\d{2}", _text(item.get(key)))
        if match:
            return match.group(0)
    return ""


def _seasons(item):
    values = item.get("seasons")
    if isinstance(values, (list, tuple, set)):
        seasons = [_positive_integer(value) for value in values]
    else:
        seasons = []
    if not any(seasons):
        for key in ("target_season", "current_season", "season", "season_number"):
            number = _positive_integer(item.get(key))
            if number:
                seasons = [number]
                break
    return sorted(set(number for number in seasons if number > 0))


def _subscription_key(item):
    for field in ("key", "subscription_key", "id"):
        value = _text(item.get(field))
        if value:
            return value
    return ""


def _torra_row_id(row):
    for field in ("id", "subscription_id", "subscriptionId"):
        value = _text(row.get(field))
        if value:
            return value
    return ""


def _torra_row_busy(row):
    state = _text(row.get("state") or row.get("status")).lower()
    return row.get("is_running") is True or row.get("is_mutating") is True or state in {
        "running",
        "mutating",
        "updating",
    }


@dataclass(frozen=True)
class MoviePilotBackupDependencies:
    environment: object
    repository: object
    subscription_loader: object
    torra: object
    qb: object
    inspect_moviepilot: object
    search_existing: object
    create_subscription: object


class MoviePilotBackupService:
    def __init__(self, dependencies):
        self.environment = dependencies.environment or {}
        self.repository = dependencies.repository
        self.subscription_loader = dependencies.subscription_loader or (lambda: [])
        self.torra = dependencies.torra
        self.qb = dependencies.qb
        self.inspect_moviepilot = dependencies.inspect_moviepilot
        self.search_existing = dependencies.search_existing
        self.create_subscription = dependencies.create_subscription

    def _require_enabled(self):
        if not _truthy(self.environment.get("MCC_MOVIEPILOT_BACKUP_ENABLED")):
            raise MoviePilotBackupError(
                "MOVIEPILOT_BACKUP_DISABLED",
                "MoviePilot 人工备用入口未启用",
                503,
            )

    @staticmethod
    def _body(body, allowed, code):
        if body is None:
            body = {}
        if not isinstance(body, dict) or set(body) - set(allowed):
            raise MoviePilotBackupError(code, "请求包含不支持的字段", 422)
        return body

    def _subscriptions(self):
        payload = self.subscription_loader()
        if isinstance(payload, dict):
            payload = payload.get("items") or []
        return {
            _subscription_key(item): item
            for item in payload if isinstance(item, dict) and _subscription_key(item)
        }

    def _target(self, key):
        item = self._subscriptions().get(_text(key))
        if not item:
            raise MoviePilotBackupError("SUBSCRIPTION_NOT_FOUND", "订阅不存在", 404)
        title = _text(item.get("title") or item.get("name"))
        media_type = _media_type(item.get("media_type") or item.get("type"))
        tmdb_id = _positive_integer(item.get("tmdb_id") or item.get("tmdbId") or item.get("tmdbid"))
        if not tmdb_id and _text(item.get("source")).lower() == "tmdb":
            tmdb_id = _positive_integer(item.get("id"))
        seasons = _seasons(item) if media_type == "tv" else []
        if not title or not media_type or not tmdb_id or (media_type == "tv" and not seasons):
            raise MoviePilotBackupError(
                "MOVIEPILOT_SUBSCRIPTION_INVALID",
                "订阅缺少标题、媒体类型、TMDB ID 或季信息",
                422,
            )
        return item, {
            "title": title,
            "media_type": media_type,
            "tmdb_id": tmdb_id,
            "seasons": seasons,
            "year": _year(item),
        }

    def _watch_units(self, key):
        units = self.repository.list_watch_units(key)
        blockers = []
        if not units:
            blockers.append("订阅没有质量观察记录")
        elif any(_text(unit.get("state")) != "observation_expired" for unit in units):
            blockers.append("质量观察尚未全部结束")
        return units, blockers

    def _torra_rows(self):
        try:
            configured = self.torra is not None and self.torra.is_configured()
        except Exception as exc:
            raise MoviePilotBackupError(
                "MOVIEPILOT_TORRA_UNAVAILABLE",
                "Torra 状态检查失败",
                502,
            ) from exc
        if not configured:
            raise MoviePilotBackupError(
                "MOVIEPILOT_TORRA_UNAVAILABLE",
                "Torra 未配置或不可用",
                502,
            )
        try:
            rows = self.torra.list_subscriptions()
        except Exception as exc:
            raise MoviePilotBackupError(
                "MOVIEPILOT_TORRA_UNAVAILABLE",
                "Torra 状态检查失败",
                502,
            ) from exc
        if not isinstance(rows, list):
            raise MoviePilotBackupError(
                "MOVIEPILOT_TORRA_UNAVAILABLE",
                "Torra 状态响应无效",
                502,
            )
        return rows

    def _torra_blockers(self, units):
        rows = self._torra_rows()
        rows_by_id = {_torra_row_id(row): row for row in rows if isinstance(row, dict) and _torra_row_id(row)}
        blockers = []
        for unit in units:
            torra_id = _text(unit.get("torra_subscription_id"))
            row = rows_by_id.get(torra_id) if torra_id else None
            if not row:
                blockers.append("Torra 订阅映射不存在")
            elif _torra_row_busy(row):
                blockers.append("Torra 正在处理该订阅")
        return list(dict.fromkeys(blockers))

    def _qb_blockers(self, item, units):
        try:
            summary = self.qb.summary() if self.qb else None
        except Exception as exc:
            raise MoviePilotBackupError(
                "MOVIEPILOT_QB_UNAVAILABLE",
                "qBittorrent 状态检查失败",
                502,
            ) from exc
        if not isinstance(summary, dict) or summary.get("connected") is not True:
            raise MoviePilotBackupError(
                "MOVIEPILOT_QB_UNAVAILABLE",
                "qBittorrent 未配置或不可用",
                502,
            )
        raw_tasks = summary.get("tasks")
        if not isinstance(raw_tasks, list):
            raise MoviePilotBackupError(
                "MOVIEPILOT_QB_UNAVAILABLE",
                "qBittorrent 状态响应无效",
                502,
            )
        tasks = [task for task in raw_tasks if isinstance(task, dict)]
        if any(qb_task_matches(task, item, unit) for task in tasks for unit in units):
            return ["该订阅已有活动下载"]
        return []

    @staticmethod
    def _public_plan(key, target, ready, mode="", blockers=None):
        return {
            "subscriptionId": _text(key),
            "ready": bool(ready),
            "mode": _text(mode),
            "title": target["title"],
            "mediaType": target["media_type"],
            "tmdbId": str(target["tmdb_id"]),
            "seasons": list(target["seasons"]),
            "blockers": list(blockers or []),
        }

    def _plan(self, key):
        item, target = self._target(key)
        units, blockers = self._watch_units(key)
        if not blockers:
            blockers.extend(self._torra_blockers(units))
        if not blockers:
            blockers.extend(self._qb_blockers(item, units))
        if blockers:
            return self._public_plan(key, target, False, blockers=blockers), target, None
        try:
            inspection = self.inspect_moviepilot(dict(target))
        except Exception as exc:
            raise MoviePilotBackupError(
                "MOVIEPILOT_UPSTREAM_UNAVAILABLE",
                "MoviePilot 状态检查失败",
                502,
            ) from exc
        if (
            not isinstance(inspection, dict)
            or not isinstance(inspection.get("exists"), bool)
            or (inspection.get("exists") is True and not inspection.get("subscribe_id"))
        ):
            raise MoviePilotBackupError(
                "MOVIEPILOT_UPSTREAM_INVALID",
                "MoviePilot 状态响应无效",
                502,
            )
        mode = "search-existing" if inspection["exists"] else "create-and-search"
        return self._public_plan(key, target, True, mode), target, inspection

    def preview(self, key, body):
        self._require_enabled()
        self._body(body, set(), "MOVIEPILOT_PREVIEW_FIELDS_INVALID")
        plan, _, _ = self._plan(key)
        return plan

    @staticmethod
    def _idempotency(body):
        key = _text(body.get("idempotencyKey"))
        if not 12 <= len(key) <= 128:
            raise MoviePilotBackupError(
                "MOVIEPILOT_IDEMPOTENCY_INVALID",
                "幂等键长度必须为 12 到 128 个字符",
                422,
            )
        return key

    @staticmethod
    def _replay(action):
        if action.get("status") == "succeeded":
            summary = action.get("response_summary") or {}
            mode = _text(summary.get("mode"))
            if mode not in {"search-existing", "create-and-search"}:
                raise MoviePilotBackupError(
                    "MOVIEPILOT_REPLAY_INVALID",
                    "MoviePilot 备用动作记录无效",
                    502,
                )
            already_exists = summary.get("alreadyExists") is True
            response = {
                "ok": True,
                "mode": mode,
                "alreadyExists": already_exists,
                "searchTriggered": True,
                "message": (
                    "MoviePilot 已有订阅，已触发搜索"
                    if already_exists
                    else "已创建 MoviePilot 订阅并触发搜索"
                ),
                "actionId": _text(action.get("action_id")),
            }
            return response, 200
        raise MoviePilotBackupError(
            "MOVIEPILOT_PUSH_FAILED",
            "MoviePilot 备用推送失败",
            502,
        )

    def _claim_existing(self, idempotency_key, key):
        action = self.repository.get_action_by_idempotency(idempotency_key)
        if not action:
            return None
        if any((
            action.get("subscription_key") != key,
            action.get("unit_key") not in {None, ""},
            action.get("provider") != "moviepilot",
            action.get("action_type") != MOVIEPILOT_BACKUP_ACTION_TYPE,
        )):
            raise MoviePilotBackupError(
                "MOVIEPILOT_IDEMPOTENCY_CONFLICT",
                "幂等键已用于其他动作",
                409,
            )
        if action.get("status") in {"succeeded", "failed", "cancelled"}:
            return self._replay(action)
        raise MoviePilotBackupError(
            "MOVIEPILOT_IN_PROGRESS",
            "相同 MoviePilot 备用动作正在执行",
            409,
        )

    def _claim_new(self, idempotency_key, key, target):
        claim = self.repository.claim_action(
            idempotency_key,
            key,
            "moviepilot",
            MOVIEPILOT_BACKUP_ACTION_TYPE,
            request_summary={
                "source": "manual-backup",
                "subscriptionId": key,
                "mediaType": target["media_type"],
                "seasons": list(target["seasons"]),
            },
            cooldown_seconds=MOVIEPILOT_BACKUP_COOLDOWN_SECONDS,
        )
        disposition = claim["disposition"]
        if disposition == "claimed":
            return claim["action"]
        if disposition == "replay":
            return self._replay(claim["action"])
        if disposition == "cooldown":
            raise MoviePilotBackupError(
                "MOVIEPILOT_COOLDOWN",
                "该订阅刚执行过 MoviePilot 备用动作，请稍后重试",
                409,
            )
        if disposition == "conflict":
            raise MoviePilotBackupError(
                "MOVIEPILOT_IDEMPOTENCY_CONFLICT",
                "幂等键已用于其他动作",
                409,
            )
        raise MoviePilotBackupError(
            "MOVIEPILOT_IN_PROGRESS",
            "相同 MoviePilot 备用动作正在执行",
            409,
        )

    @staticmethod
    def _safe_result(result, mode, action_id):
        source = result if isinstance(result, dict) else {}
        ok = source.get("ok") is True or source.get("success") is True
        search_triggered = source.get("searchTriggered") is True or source.get("search_triggered") is True
        already_exists = (
            mode == "search-existing"
            or source.get("alreadyExists") is True
            or source.get("already_exists") is True
        )
        if not ok or not search_triggered:
            return None
        message = (
            "MoviePilot 已有订阅，已触发搜索"
            if already_exists
            else "已创建 MoviePilot 订阅并触发搜索"
        )
        return {
            "ok": True,
            "mode": mode,
            "alreadyExists": already_exists,
            "searchTriggered": True,
            "message": message,
            "actionId": action_id,
        }

    def _execute_claimed(self, action_id, target, inspection, mode):
        try:
            if mode == "search-existing":
                raw_result = self.search_existing(dict(target), dict(inspection or {}))
            else:
                raw_result = self.create_subscription(dict(target))
            response = self._safe_result(raw_result, mode, action_id)
            if response is None:
                raise RuntimeError("MoviePilot 备用动作未完成")
        except Exception as exc:
            summary = {
                "ok": False,
                "mode": mode,
                "alreadyExists": mode == "search-existing",
                "searchTriggered": False,
                "message": "MoviePilot 备用推送失败",
                "code": "MOVIEPILOT_PUSH_FAILED",
                "actionId": action_id,
            }
            self.repository.complete_action(
                action_id,
                "failed",
                summary,
                http_status=502,
                error_code="MOVIEPILOT_PUSH_FAILED",
                error_message="MoviePilot 备用推送失败",
            )
            raise MoviePilotBackupError(
                "MOVIEPILOT_PUSH_FAILED",
                "MoviePilot 备用推送失败",
                502,
            ) from exc
        self.repository.complete_action(action_id, "succeeded", response, http_status=200)
        return response, 200

    def push(self, key, body):
        self._require_enabled()
        body = self._body(
            body,
            {"confirm", "idempotencyKey"},
            "MOVIEPILOT_PUSH_FIELDS_INVALID",
        )
        if body.get("confirm") is not True:
            raise MoviePilotBackupError(
                "MOVIEPILOT_CONFIRMATION_REQUIRED",
                "需要明确确认 MoviePilot 备用推送",
                422,
            )
        idempotency_key = self._idempotency(body)
        immediate = self._claim_existing(idempotency_key, key)
        if immediate is not None:
            return immediate
        plan, target, inspection = self._plan(key)
        if not plan["ready"]:
            raise MoviePilotBackupError(
                "MOVIEPILOT_BACKUP_BLOCKED",
                "；".join(plan["blockers"]),
                409,
            )
        claimed = self._claim_new(idempotency_key, key, target)
        if isinstance(claimed, tuple):
            return claimed
        return self._execute_claimed(
            claimed["action_id"],
            target,
            inspection,
            plan["mode"],
        )


def _error_response(error):
    return jsonify({
        "code": error.code,
        "error": error.message,
        "request_id": current_request_id(),
    }), error.status


def register_moviepilot_backup(app, service):
    app.extensions["mcc_moviepilot_backup"] = service

    def execute(callback):
        try:
            return callback()
        except MoviePilotBackupError as exc:
            return _error_response(exc)

    @app.post("/api/v2/subscriptions/<path:key>/moviepilot-previews")
    def moviepilot_backup_preview(key):
        return execute(lambda: jsonify(service.preview(key, request.get_json(silent=True))))

    @app.post("/api/v2/subscriptions/<path:key>/moviepilot-pushes")
    def moviepilot_backup_push(key):
        def response():
            payload, status = service.push(key, request.get_json(silent=True))
            return jsonify(payload), status

        return execute(response)

    return service
