from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

from flask import has_request_context, jsonify, request

from app.activity_log import write_activity
from app.http_runtime import current_request_id
from app.torra_read_runtime import subscription_matches


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _integer(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _media_type(value):
    text = str(value or "").strip().lower()
    if text in {"movie", "电影", "film"}:
        return "movie"
    if text in {"tv", "series", "电视剧", "剧集"}:
        return "tv"
    return ""


def _iso_now(clock):
    value = clock()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _fingerprint(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _remote_reference(remote_id):
    return hashlib.sha256(str(remote_id or "").encode("utf-8")).hexdigest()[:10]


def _request_id():
    return current_request_id() if has_request_context() else "background"


def _error(code, message, status):
    return jsonify({
        "ok": False,
        "success": False,
        "code": code,
        "error": message,
        "request_id": _request_id(),
    }), status


class TorraSubscriptionSyncError(RuntimeError):
    def __init__(self, code, message, status):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _classified_error(exc, fallback_code, fallback_message):
    if isinstance(exc, TorraSubscriptionSyncError):
        return exc.code, exc.message, exc.status
    text = str(exc or "")
    if "未配置" in text:
        return "TORRA_NOT_CONFIGURED", "Torra 尚未配置", 503
    if "Token 无效" in text or "登录失败" in text or "认证失败" in text:
        return "TORRA_AUTH_FAILED", "Torra 认证失败", 502
    if isinstance(exc, ValueError) and ("已关联" in text or "身份冲突" in text):
        return "TORRA_SYNC_IDENTITY_CONFLICT", "Torra 订阅存在身份冲突", 409
    return fallback_code, fallback_message, 502


def _write_failure_activity(action, code, message, status):
    write_activity(
        "torra_sync",
        action,
        "error",
        message,
        request_id=_request_id(),
        code=code,
        http_status=status,
    )


def _target_for_item(item):
    media_type = _media_type(item.get("media_type") or item.get("type"))
    return {
        "title": str(item.get("title") or item.get("name") or "").strip(),
        "mediaType": media_type,
        "tmdbId": str(item.get("tmdb_id") or item.get("tmdbId") or "").strip(),
        "seasonNumber": _integer(
            item.get("target_season", item.get("season_number", item.get("season"))),
            0,
        ),
        "year": str(item.get("year") or "").strip(),
    }


def normalize_torra_subscription(row):
    source = dict(row or {})
    remote_id = str(source.get("id") or "").strip()
    title = str(source.get("name") or source.get("keyword") or "").strip()
    media_type = _media_type(source.get("media_type") or source.get("type"))
    tmdb_id = str(source.get("tmdb_id") or source.get("tmdbid") or "").strip()
    season_number = _integer(source.get("season_number", source.get("season")), 0)
    year = str(source.get("year") or "").strip()
    mapping_status = "mapped" if tmdb_id and title and media_type else "partial" if title and media_type else "unmapped"
    remote_status = {
        "enabled": source.get("enabled") is not False,
        "completed": source.get("completed") is True,
        "running": source.get("is_running") is True,
        "downloadedEpisodes": _integer(source.get("downloaded_episode_count"), 0),
        "totalEpisodes": _integer(source.get("total_episode_count"), 0),
    }
    item = {
        "title": title,
        "media_type": media_type,
        "tmdb_id": tmdb_id,
        "year": year,
        "source": "torra",
        "source_label": "Torra 已有订阅",
        "origin": "torra",
        "read_only": True,
        "torra_remote_id": remote_id,
        "torra_mapping_status": mapping_status,
        "torra_sync_state": "current",
        "torra_remote_status": remote_status,
    }
    if media_type == "tv":
        item["target_season"] = season_number
        item["season_number"] = season_number
        item["season_name"] = "特别篇" if season_number == 0 else f"第 {season_number} 季"
    fingerprint_payload = {
        "remoteId": remote_id,
        "title": title,
        "mediaType": media_type,
        "tmdbId": tmdb_id,
        "seasonNumber": season_number,
        "year": year,
        "status": remote_status,
    }
    return {
        "remote_id": remote_id,
        "mapping_status": mapping_status,
        "remote_status": remote_status,
        "remote_fingerprint": _fingerprint(fingerprint_payload),
        "item": item,
        "raw": source,
    }


class TorraSubscriptionSyncService:
    def __init__(self, environment, repository, client, item_loader, key_resolver, clock=None):
        self.environment = os.environ if environment is None else environment
        self.repository = repository
        self.client = client
        self.item_loader = item_loader
        self.key_resolver = key_resolver
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def enabled(self):
        return _truthy(self.environment.get("MCC_TORRA_SUBSCRIPTION_SYNC_ENABLED"))

    def status(self):
        links = self.repository.list_torra_links()
        synced = [str(link.get("last_synced_at") or "") for link in links if link.get("last_synced_at")]
        return {
            "ok": True,
            "enabled": self.enabled(),
            "linked": len(links),
            "current": sum(link.get("sync_state") == "current" for link in links),
            "remoteMissing": sum(link.get("sync_state") == "remote_missing" for link in links),
            "errors": sum(link.get("sync_state") == "error" for link in links),
            "lastSyncedAt": max(synced) if synced else "",
        }

    def _local_items(self):
        payload = self.item_loader() or {}
        rows = payload.get("items") if isinstance(payload, dict) else []
        return [dict(item) for item in (rows or []) if isinstance(item, dict)]

    def _build_candidates(self):
        remote_rows = self.client.list_subscriptions()
        local_items = self._local_items()
        links = self.repository.list_torra_links()
        links_by_remote = {str(link.get("remote_id") or ""): link for link in links}
        local_by_key = {str(self.key_resolver(item) or ""): item for item in local_items}
        candidates = []
        conflicts = []
        used_local_keys = {}
        counts = {
            "total": len(remote_rows),
            "new": 0,
            "linked": 0,
            "duplicates": 0,
            "unmapped": 0,
            "conflicts": 0,
        }
        for remote_row in remote_rows:
            candidate = normalize_torra_subscription(remote_row)
            remote_id = candidate["remote_id"]
            if candidate["mapping_status"] == "unmapped" or not remote_id:
                counts["unmapped"] += 1
                continue
            linked = links_by_remote.get(remote_id)
            local_key = str((linked or {}).get("subscription_key") or "")
            local_item = local_by_key.get(local_key)
            if linked:
                counts["linked"] += 1
            if not local_key:
                target = _target_for_item(candidate["item"])
                for item in local_items:
                    if subscription_matches(remote_row, _target_for_item(item)):
                        local_item = item
                        local_key = str(self.key_resolver(item) or "")
                        break
                if local_key:
                    counts["duplicates"] += 1
                else:
                    local_key = f"torra:{remote_id}"
                    counts["new"] += 1
            previous_remote = used_local_keys.get(local_key)
            if previous_remote and previous_remote != remote_id:
                counts["conflicts"] += 1
                conflicts.append({
                    "subscriptionKey": local_key,
                    "remoteRefs": [_remote_reference(previous_remote), _remote_reference(remote_id)],
                    "title": candidate["item"]["title"],
                })
                candidates = [row for row in candidates if row.get("subscription_key") != local_key]
                continue
            used_local_keys[local_key] = remote_id
            item = {**(local_item or {}), **candidate["item"]}
            if local_item and str(local_item.get("origin") or "") != "torra":
                item["origin"] = str(local_item.get("origin") or "manual")
                item["read_only"] = bool(local_item.get("read_only"))
                item["source_label"] = str(local_item.get("source_label") or "Fluxa 订阅")
            item["subscription_key"] = local_key
            candidate.update({
                "subscription_key": local_key,
                "item": item,
                "origin": "torra_import" if not local_item else "fluxa_push",
            })
            candidates.append(candidate)
        return candidates, counts, conflicts

    def preview(self):
        candidates, counts, conflicts = self._build_candidates()
        write_activity(
            "torra_sync",
            "torra_sync_preview",
            "success",
            f"Torra 同步预览完成：{counts['total']} 条",
            request_id=_request_id(),
            total=counts["total"],
            importable=len(candidates),
            conflicts=counts["conflicts"],
        )
        return {
            "ok": True,
            "enabled": self.enabled(),
            "summary": {**counts, "importable": len(candidates)},
            "conflictItems": conflicts[:20],
            "checkedAt": _iso_now(self.clock),
        }

    def import_all(self, body):
        payload = body if isinstance(body, dict) else {}
        if payload.get("confirm") is not True:
            return None, ("TORRA_SYNC_CONFIRMATION_REQUIRED", "需要明确确认导入 Torra 订阅", 400)
        idempotency_key = str(payload.get("idempotencyKey") or "").strip()
        if not 12 <= len(idempotency_key) <= 128:
            return None, ("TORRA_SYNC_IDEMPOTENCY_INVALID", "幂等键长度必须为 12 到 128 个字符", 400)
        if not self.enabled():
            return None, ("TORRA_SUBSCRIPTION_SYNC_DISABLED", "Torra 订阅同步开关未启用", 403)
        replay = self.repository.get_torra_sync_run(idempotency_key)
        if replay:
            return {**replay, "replayed": True}, None
        candidates, counts, conflicts = self._build_candidates()
        if conflicts:
            return None, ("TORRA_SYNC_IDENTITY_CONFLICT", "Torra 订阅存在身份冲突，未执行导入", 409)
        response, replayed = self.repository.apply_torra_mirror_once(
            candidates,
            self.key_resolver,
            idempotency_key,
            lambda result: {
                "ok": True,
                "success": True,
                "replayed": False,
                "summary": {**counts, **result, "importable": len(candidates)},
                "syncedAt": _iso_now(self.clock),
                "requestId": _request_id(),
            },
        )
        if replayed:
            return {**response, "replayed": True}, None
        result = response["summary"]
        write_activity(
            "torra_sync",
            "torra_sync_import",
            "success",
            f"已导入 {result['imported']} 条 Torra 订阅",
            request_id=_request_id(),
            imported=result["imported"],
            updated=result["updated"],
            skipped=result["skipped"],
        )
        return response, None

    def sync_existing(self):
        if not self.enabled():
            return {"ok": True, "ran": False, "reason": "disabled"}
        candidates, counts, conflicts = self._build_candidates()
        if conflicts:
            raise TorraSubscriptionSyncError(
                "TORRA_SYNC_IDENTITY_CONFLICT",
                "Torra 订阅存在身份冲突",
                409,
            )
        result = self.repository.apply_torra_mirror(candidates, self.key_resolver, import_new=False, mark_missing=True)
        response = {
            "ok": True,
            "ran": True,
            "summary": {**counts, **result},
            "syncedAt": _iso_now(self.clock),
            "requestId": _request_id(),
        }
        write_activity(
            "torra_sync",
            "torra_sync_run",
            "success",
            f"Torra 状态同步完成：更新 {result['updated']} 条",
            request_id=_request_id(),
            updated=result["updated"],
            remote_missing=result["remoteMissing"],
        )
        return response

    def record_push_link(self, subscription_key, remote_id):
        subscription_key = str(subscription_key or "").strip()
        remote_id = str(remote_id or "").strip()
        if not subscription_key or not remote_id:
            return None
        self.repository.mutate_item(
            subscription_key,
            lambda item: item.update({
                "torra_remote_id": remote_id,
                "torra_sync_state": "current",
                "torra_mapping_status": "mapped",
            }),
            self.key_resolver,
        )
        return self.repository.save_torra_link({
            "subscription_key": subscription_key,
            "remote_id": remote_id,
            "origin": "fluxa_push",
            "mapping_status": "mapped",
            "remote_status": {},
            "remote_fingerprint": "",
            "sync_state": "current",
        })


def register_torra_subscription_sync(app, service):
    app.extensions["mcc_torra_subscription_sync"] = service

    @app.get("/api/v2/torra/subscription-sync/status")
    def torra_subscription_sync_status():
        return jsonify(service.status())

    @app.get("/api/v2/torra/subscription-sync/preview")
    def torra_subscription_sync_preview():
        try:
            return jsonify(service.preview())
        except Exception as exc:
            code, message, status = _classified_error(
                exc,
                "TORRA_SYNC_PREVIEW_FAILED",
                "Torra 订阅预览失败",
            )
            _write_failure_activity("torra_sync_preview", code, message, status)
            return _error(code, message, status)

    @app.post("/api/v2/torra/subscription-sync/imports")
    def torra_subscription_sync_import():
        try:
            response, denied = service.import_all(request.get_json(silent=True) or {})
        except Exception as exc:
            code, message, status = _classified_error(
                exc,
                "TORRA_SYNC_IMPORT_FAILED",
                "Torra 订阅导入失败",
            )
            _write_failure_activity("torra_sync_import", code, message, status)
            return _error(code, message, status)
        if denied:
            code, message, status = denied
            _write_failure_activity("torra_sync_import", code, message, status)
            return _error(code, message, status)
        return jsonify(response)

    @app.post("/api/v2/torra/subscription-sync/runs")
    def torra_subscription_sync_run():
        if not service.enabled():
            code = "TORRA_SUBSCRIPTION_SYNC_DISABLED"
            message = "Torra 订阅同步开关未启用"
            _write_failure_activity("torra_sync_run", code, message, 403)
            return _error(code, message, 403)
        try:
            return jsonify(service.sync_existing())
        except Exception as exc:
            code, message, status = _classified_error(
                exc,
                "TORRA_SYNC_RUN_FAILED",
                "Torra 状态同步失败",
            )
            _write_failure_activity("torra_sync_run", code, message, status)
            return _error(code, message, status)

    return service
