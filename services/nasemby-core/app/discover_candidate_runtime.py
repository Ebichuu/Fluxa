from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from flask import Flask, jsonify, request

from app import discover_runtime
from app.contract_mapping import map_subscription_item
from app.http_runtime import current_request_id
from app.subscription_compat_runtime import build_save_activation
from app.subscription_workbench_runtime import manual_follow_snapshot


IDEMPOTENCY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{12,128}")
URL_PATTERN = re.compile(r"(?i)https?://[^\s]+")
SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)(password|passwd|token|api[_-]?key|cookie|secret|authorization|passkey|sign)\s*[=:]\s*[^\s&]+"
)


class DiscoverCandidateError(RuntimeError):
    def __init__(self, code, message, status):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.status = int(status)


def _text(value, limit=500):
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = URL_PATTERN.sub("", text)
    text = SENSITIVE_TEXT_PATTERN.sub(r"\1=***", text)
    return " ".join(text.split())[:limit]


def _image_url(value):
    try:
        parsed = urlsplit(str(value or "").strip())
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not any(
        host == allowed or host.endswith(f".{allowed}")
        for allowed in discover_runtime.IMAGE_PROXY_HOSTS
    ):
        return ""
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _iso(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class DiscoverCandidateService:
    def __init__(
        self,
        repository,
        environment=None,
        *,
        subscription_loader=None,
        config_loader=None,
        save_callback=None,
        activity_writer=None,
        clock=None,
    ):
        self.repository = repository
        self.environment = environment or {}
        self.subscription_loader = subscription_loader or (
            lambda: discover_runtime.load_subscription_items(remove_completed=False)
        )
        self.config_loader = config_loader or discover_runtime.load_subscription_config
        self.save_callback = save_callback or discover_runtime.save_subscription_item
        self.activity_writer = activity_writer or discover_runtime.write_activity
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._follow_lock = threading.RLock()

    @staticmethod
    def _public_candidate(row):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        return {
            "id": str(row.get("candidate_id") or ""),
            "title": _text(row.get("title") or payload.get("title") or payload.get("name"), 240),
            "mediaType": str(row.get("media_type") or ""),
            "tmdbId": str(row.get("tmdb_id") or ""),
            "seasonNumber": int(row.get("season_number") or 0),
            "year": _text(row.get("year") or payload.get("year"), 20),
            "posterUrl": _image_url(payload.get("poster_url") or payload.get("poster")),
            "overview": _text(payload.get("overview") or payload.get("summary"), 600),
            "rating": _number(payload.get("rating_num") or payload.get("rating") or payload.get("vote_average")),
            "sourceLabel": _text(payload.get("source_label") or row.get("source_key"), 120),
            "state": str(row.get("state") or "active"),
            "lastSeenAt": str(row.get("last_seen_at") or ""),
            "expiresAt": str(row.get("expires_at") or ""),
            "version": int(row.get("version") or 1),
        }

    @staticmethod
    def _subscription_identity(item):
        media_type = discover_runtime.discover_item_media_type(item)
        tmdb_id = str(discover_runtime.discover_item_tmdb_id(item, media_type) or "")
        season = discover_runtime.subscription_target_season(item) if media_type == "tv" else 0
        return media_type, tmdb_id, int(season or 0)

    def _subscriptions(self):
        payload = self.subscription_loader() or {}
        rows = payload.get("items") if isinstance(payload, dict) else payload
        return [item for item in (rows or []) if isinstance(item, dict)]

    def _duplicate(self, row):
        identity = (row.get("media_type"), str(row.get("tmdb_id") or ""), int(row.get("season_number") or 0))
        return any(self._subscription_identity(item) == identity for item in self._subscriptions())

    def _candidate(self, candidate_id, *, active=True):
        row = self.repository.get_discover_candidate(candidate_id)
        if not row:
            raise DiscoverCandidateError("DISCOVER_CANDIDATE_NOT_FOUND", "发现候选不存在", 404)
        if active:
            now = _iso(self.clock())
            if row.get("state") != "active" or str(row.get("expires_at") or "") <= now:
                raise DiscoverCandidateError("DISCOVER_CANDIDATE_STALE", "发现候选已过期或状态已变化", 409)
        return row

    def list(self, *, media_type="", query="", limit=24, offset=0):
        now = _iso(self.clock())
        payload = self.repository.list_discover_candidates(
            state="active",
            media_type=media_type,
            query=query,
            expires_after=now,
            limit=limit,
            offset=offset,
        )
        items = [self._public_candidate(row) for row in payload.get("items") or []]
        total = int(payload.get("total") or 0)
        next_offset = int(offset) + len(items)
        return {
            "ok": True,
            "items": items,
            "page": {
                "total": total,
                "limit": int(limit),
                "offset": int(offset),
                "nextOffset": next_offset if next_offset < total else None,
                "hasMore": next_offset < total,
            },
        }

    def preview(self, candidate_id):
        row = self._candidate(candidate_id)
        blockers = []
        if row.get("media_type") == "tv" and int(row.get("season_number") or 0) < 1:
            blockers.append("候选缺少明确季号")
        if not str(row.get("tmdb_id") or "").isdigit():
            blockers.append("候选缺少明确 TMDB 身份")
        duplicate = self._duplicate(row)
        if duplicate:
            blockers.append("同一媒体和范围已经在追更中")
        capability = manual_follow_snapshot(self.environment, self.config_loader() or {})
        blockers.extend(capability.get("blockers") or [])
        return {
            "ok": True,
            "candidate": self._public_candidate(row),
            "ready": not blockers,
            "duplicate": {"found": duplicate},
            "manualFollow": capability,
            "blockers": list(dict.fromkeys(_text(item, 240) for item in blockers if _text(item))),
        }

    @staticmethod
    def _follow_item(row):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        media_type = str(row.get("media_type") or "")
        item = {
            "title": _text(row.get("title") or payload.get("title") or payload.get("name"), 240),
            "media_type": media_type,
            "tmdb_id": str(row.get("tmdb_id") or ""),
            "poster_url": _image_url(payload.get("poster_url") or payload.get("poster")),
            "year": _text(row.get("year") or payload.get("year"), 20),
            "overview": _text(payload.get("overview") or payload.get("summary"), 2000),
            "original_language": _text(payload.get("original_language"), 30),
            "genre_ids": [int(value) for value in payload.get("genre_ids") or [] if str(value).isdigit()][:50],
            "origin_country": [_text(value, 10) for value in payload.get("origin_country") or []][0:20],
            "source": "manual",
            "source_label": "手动订阅",
            "origin": "manual",
            "intent_origin": "manual",
            "candidate_id": str(row.get("candidate_id") or ""),
        }
        if media_type == "tv":
            season = int(row.get("season_number") or 0)
            item.update({"target_season": season, "season_number": season, "season_name": f"第 {season} 季"})
        return item

    def follow(self, candidate_id, body):
        body = body if isinstance(body, dict) else {}
        if set(body) - {"confirm", "idempotencyKey"}:
            raise DiscoverCandidateError("DISCOVER_CANDIDATE_FOLLOW_FIELDS_INVALID", "请求包含不支持的字段", 422)
        if body.get("confirm") is not True:
            raise DiscoverCandidateError("DISCOVER_CANDIDATE_CONFIRM_REQUIRED", "需要明确确认加入追更", 422)
        idempotency_key = str(body.get("idempotencyKey") or "").strip()
        if not IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
            raise DiscoverCandidateError("DISCOVER_CANDIDATE_IDEMPOTENCY_INVALID", "幂等键必须为 12–128 位安全字符", 422)
        if str(self.environment.get("NASEMBY_CORE_WRITE_ENABLED") or "").strip().lower() not in {"1", "true", "yes", "on"}:
            raise DiscoverCandidateError("NASEMBY_CORE_WRITE_DISABLED", "追更写入尚未启用", 403)
        with self._follow_lock:
            replay = self.repository.get_candidate_follow_response(idempotency_key)
            if replay:
                if replay["candidateId"] != candidate_id:
                    raise DiscoverCandidateError("DISCOVER_CANDIDATE_IDEMPOTENCY_CONFLICT", "幂等键已用于其他候选", 409)
                return {**replay["response"], "replayed": True}
            plan = self.preview(candidate_id)
            if not plan["ready"]:
                raise DiscoverCandidateError("DISCOVER_CANDIDATE_NOT_READY", "；".join(plan["blockers"]), 409)
            row = self._candidate(candidate_id)
            data = self.save_callback({"item": self._follow_item(row)})
            response = {
                "ok": True,
                "candidate": self._public_candidate(row),
                "item": map_subscription_item(data.get("saved_item")),
                "activation": build_save_activation(data),
                "replayed": False,
            }
            try:
                stored, _ = self.repository.record_candidate_follow(candidate_id, idempotency_key, response)
            except ValueError as exc:
                raise DiscoverCandidateError("DISCOVER_CANDIDATE_STATE_CONFLICT", _text(exc, 240), 409) from exc
            self.activity_writer(
                "subscription",
                "follow_discover_candidate",
                "success",
                "发现候选已加入追更",
                candidate_id=candidate_id,
                media_type=row.get("media_type"),
                tmdb_id=row.get("tmdb_id"),
            )
            return {**stored, "replayed": False}


def register_discover_candidates(
    app: Flask,
    environment=None,
    *,
    repository=None,
    subscription_loader=None,
    config_loader=None,
    save_callback=None,
    activity_writer=None,
    clock=None,
):
    service = DiscoverCandidateService(
        repository or discover_runtime.subscription_repository(),
        environment,
        subscription_loader=subscription_loader,
        config_loader=config_loader,
        save_callback=save_callback,
        activity_writer=activity_writer,
        clock=clock,
    )
    app.extensions["mcc_discover_candidates"] = service

    @app.get("/api/v2/discover/candidates")
    def discover_candidates_list():
        try:
            limit = int(request.args.get("limit") or 24)
            offset = int(request.args.get("offset") or 0)
        except (TypeError, ValueError):
            return jsonify({"code": "DISCOVER_CANDIDATE_PAGE_INVALID", "error": "分页参数无效"}), 400
        media_type = str(request.args.get("mediaType") or "").strip()
        query = str(request.args.get("query") or "").strip()
        if limit < 1 or limit > 100 or offset < 0 or media_type not in {"", "movie", "tv"} or len(query) > 200:
            return jsonify({"code": "DISCOVER_CANDIDATE_PAGE_INVALID", "error": "候选筛选或分页参数无效"}), 400
        return jsonify(service.list(media_type=media_type, query=query, limit=limit, offset=offset))

    @app.post("/api/v2/discover/candidates/<candidate_id>/follow-previews")
    def discover_candidate_follow_preview(candidate_id):
        try:
            body = request.get_json(silent=True)
            if body not in (None, {}):
                raise DiscoverCandidateError("DISCOVER_CANDIDATE_PREVIEW_FIELDS_INVALID", "预览请求不接受业务字段", 422)
            return jsonify(service.preview(candidate_id))
        except DiscoverCandidateError as exc:
            return jsonify({"code": exc.code, "error": exc.message, "request_id": current_request_id()}), exc.status

    @app.post("/api/v2/discover/candidates/<candidate_id>/follows")
    def discover_candidate_follow(candidate_id):
        try:
            return jsonify(service.follow(candidate_id, request.get_json(silent=True) or {}))
        except DiscoverCandidateError as exc:
            return jsonify({"code": exc.code, "error": exc.message, "request_id": current_request_id()}), exc.status
        except Exception:
            return jsonify({
                "code": "DISCOVER_CANDIDATE_FOLLOW_FAILED",
                "error": "候选加入追更失败",
                "request_id": current_request_id(),
            }), 502

    return service
