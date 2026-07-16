from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request

from app import discover_runtime
from app.activity_log import write_activity
from app.config import DATA_DIR, read_config
from app.services import transfer_115_share, transfer_yingchao_item


logger = logging.getLogger(__name__)
CANDIDATE_TTL_SECONDS = 15 * 60
TRANSFER_COOLDOWN_SECONDS = 60


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _error(code: str, message: str, status: int, **details):
    body = {"ok": False, "code": code, "error": message}
    body.update(details)
    return jsonify(body), status


def _subscription_item(key: str):
    target = str(key or "").strip()
    payload = discover_runtime.load_subscription_items(
        with_progress=False,
        remove_completed=False,
        persist_progress=False,
    )
    rows = payload.get("items") if isinstance(payload, dict) else []
    return next((row for row in rows or [] if str(row.get("key") or "").strip() == target), None)


def _media_type(item: dict) -> str:
    kind = discover_runtime.discover_item_media_type(item)
    return "tv" if kind == "tv" else "movie" if kind == "movie" else ""


def _source_key(item: dict) -> str:
    return str(item.get("source_key") or item.get("source") or "").strip().lower()


def _source_allowed(source: str, allowed: set[str]) -> bool:
    if source in {"hdhive", "yingchao"}:
        return "hdhive" in allowed
    if source in {"pansou", "pansearch"}:
        return "pansou" in allowed
    if source in {"telegram", "tg", "channel"}:
        return "telegram" in allowed
    return False


def _public_candidate(token: str, item: dict) -> dict:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    return {
        "id": token,
        "source": _source_key(item),
        "sourceLabel": str(item.get("source_label") or item.get("source") or "网盘来源")[:80],
        "title": str(item.get("title") or item.get("name") or "未命名资源")[:240],
        "subtitle": str(item.get("subtitle") or "")[:240],
        "quality": str(item.get("quality") or raw.get("quality") or "")[:120],
        "size": str(item.get("size") or raw.get("size") or "")[:80],
        "season": str(item.get("season") or raw.get("season") or "")[:40],
        "requiresUnlock": not bool(item.get("share_url") or raw.get("share_url")),
    }


class CloudAcquisitionService:
    def __init__(
        self,
        app: Flask,
        environment,
        searcher=None,
        transfer_share=None,
        transfer_hdhive=None,
        config_reader=None,
        state_path=None,
        clock=None,
    ):
        self.app = app
        self.environment = environment
        self.searcher = searcher or discover_runtime.search_resources
        self.transfer_share = transfer_share or transfer_115_share
        self.transfer_hdhive = transfer_hdhive or transfer_yingchao_item
        self.config_reader = config_reader or read_config
        self.state_path = Path(state_path or (DATA_DIR / "cloud_transfer_actions.json"))
        self.clock = clock or time.time
        self.lock = threading.Lock()
        self.candidates: dict[str, dict] = {}

    def policy(self) -> dict:
        config = discover_runtime.load_subscription_config()
        return discover_runtime.normalize_cloud_acquisition(config.get("cloud_acquisition"))

    def _check_preview_allowed(self):
        policy = self.policy()
        if not policy["enabled"]:
            return policy, ("CLOUD_ACQUISITION_DISABLED", "网盘通道未启用", 403)
        if not policy["manual_actions_enabled"]:
            return policy, ("CLOUD_MANUAL_ACTIONS_DISABLED", "人工网盘操作未启用", 403)
        if not _truthy(self.environment.get("MCC_CLOUD_SEARCH_ENABLED", "false")):
            return policy, ("CLOUD_SEARCH_DISABLED", "网盘候选搜索闸门未启用", 403)
        return policy, None

    def preview(self, subscription_id: str):
        policy, denied = self._check_preview_allowed()
        if denied:
            return None, denied
        item = _subscription_item(subscription_id)
        if not item:
            return None, ("SUBSCRIPTION_NOT_FOUND", "订阅不存在", 404)
        media_type = _media_type(item)
        tmdb_id = str(item.get("tmdb_id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not title or not tmdb_id or media_type not in {"movie", "tv"}:
            return None, ("CLOUD_IDENTITY_INCOMPLETE", "订阅缺少稳定的标题、TMDB ID 或媒体类型", 409)
        payload = self.searcher({"title": title, "type": media_type, "tmdb_id": tmdb_id})
        rows = payload.get("items") if isinstance(payload, dict) else []
        allowed = set(policy["sources"])
        rows = [row for row in rows or [] if isinstance(row, dict) and _source_allowed(_source_key(row), allowed)]
        now = self.clock()
        public = []
        with self.lock:
            self.candidates = {
                key: value
                for key, value in self.candidates.items()
                if now - float(value.get("createdAt") or 0) < CANDIDATE_TTL_SECONDS
            }
            for row in rows[:50]:
                token = secrets.token_urlsafe(18)
                self.candidates[token] = {
                    "subscriptionId": subscription_id,
                    "item": row,
                    "createdAt": now,
                }
                public.append(_public_candidate(token, row))
        return {
            "ok": True,
            "subscription": {
                "id": subscription_id,
                "title": title,
                "tmdbId": tmdb_id,
                "mediaType": media_type,
            },
            "policy": policy,
            "candidates": public,
            "errors": [str(value)[:160] for value in (payload.get("errors") or [])[:5]],
            "expiresInSeconds": CANDIDATE_TTL_SECONDS,
        }, None

    def _read_actions(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _write_actions(self, actions: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(actions, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    def _duplicate_reason(self, subscription_id: str) -> str:
        try:
            chain = self.app.extensions["mcc_task_chain_service"].get_chain()
        except Exception:
            return "任务链复查失败"
        target = next((
            item for item in chain.get("items") or []
            if str((item.get("sourceIds") or {}).get("subscriptionId") or "") == subscription_id
        ), None)
        if not target:
            return ""
        if target.get("embyIndexed"):
            return "Emby 已经存在该媒体"
        source_ids = target.get("sourceIds") or {}
        if source_ids.get("torraId"):
            return "Torra 已有等价订阅"
        if source_ids.get("qbHashes"):
            return "qBittorrent 已有等价任务"
        if source_ids.get("symediaIds"):
            return "Symedia 已有等价记录"
        return ""

    def transfer(self, payload: dict):
        policy, denied = self._check_preview_allowed()
        if denied:
            return None, denied
        if not _truthy(self.environment.get("MCC_CLOUD_TRANSFER_ENABLED", "false")):
            return None, ("CLOUD_TRANSFER_DISABLED", "网盘转存闸门未启用", 403)
        if payload.get("confirm") is not True:
            return None, ("CLOUD_CONFIRMATION_REQUIRED", "必须明确确认单条转存", 400)
        token = str(payload.get("candidateId") or "").strip()
        idempotency_key = str(payload.get("idempotencyKey") or "").strip()
        if not token or not (12 <= len(idempotency_key) <= 128):
            return None, ("CLOUD_TRANSFER_INPUT_INVALID", "需要候选 ID 和 12-128 字符幂等键", 400)
        now = self.clock()
        with self.lock:
            candidate = self.candidates.get(token)
            if not candidate or now - float(candidate.get("createdAt") or 0) >= CANDIDATE_TTL_SECONDS:
                return None, ("CLOUD_CANDIDATE_EXPIRED", "候选已过期，请重新预览", 409)
            subscription_id = str(candidate.get("subscriptionId") or "")
            actions = self._read_actions()
            existing = actions.get(idempotency_key)
            if isinstance(existing, dict):
                return {**existing, "replayed": True}, None
            recent = next((
                value for value in actions.values()
                if isinstance(value, dict)
                and value.get("subscriptionId") == subscription_id
                and now - float(value.get("createdAt") or 0) < TRANSFER_COOLDOWN_SECONDS
            ), None)
            if recent:
                return None, ("CLOUD_TRANSFER_COOLDOWN", "该订阅刚执行过网盘动作，请稍后再试", 409)
            duplicate = self._duplicate_reason(subscription_id)
            if duplicate:
                return None, ("CLOUD_DUPLICATE_PREVENTED", duplicate, 409)
            action = {
                "ok": False,
                "status": "pending",
                "subscriptionId": subscription_id,
                "candidateId": token,
                "createdAt": now,
                "requestId": hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16],
            }
            actions[idempotency_key] = action
            self._write_actions(actions)
        item = candidate["item"]
        try:
            source = _source_key(item)
            if source in {"hdhive", "yingchao"}:
                result = self.transfer_hdhive(item)
            else:
                raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
                share_url = str(item.get("share_url") or raw.get("share_url") or item.get("url") or "").strip()
                if not share_url:
                    raise ValueError("候选没有可转存的分享链接")
                result = self.transfer_share(share_url, notify_context=item)
            completed = {
                **action,
                "ok": bool(result.get("ok")),
                "status": "completed" if result.get("ok") else "failed",
                "completedAt": self.clock(),
                "replayed": False,
            }
            write_activity(
                "transfer",
                "cloud_transfer_v2",
                "success" if completed["ok"] else "error",
                "网盘单条转存完成" if completed["ok"] else "网盘单条转存失败",
                subscription_id=subscription_id,
                candidate_id=token,
                request_id=completed["requestId"],
            )
        except Exception:
            logger.exception("cloud transfer failed")
            completed = {
                **action,
                "ok": False,
                "status": "unknown",
                "completedAt": self.clock(),
                "replayed": False,
            }
        with self.lock:
            actions = self._read_actions()
            actions[idempotency_key] = completed
            self._write_actions(actions)
        return completed, None


def register_cloud_acquisition(
    app: Flask,
    environment=None,
    functions=None,
    state_path=None,
    clock=None,
):
    environment = os.environ if environment is None else environment
    functions = functions or {}
    service = CloudAcquisitionService(
        app,
        environment,
        searcher=functions.get("search_resources"),
        transfer_share=functions.get("transfer_115_share"),
        transfer_hdhive=functions.get("transfer_yingchao_item"),
        config_reader=functions.get("read_config"),
        state_path=state_path,
        clock=clock,
    )
    app.extensions["mcc_cloud_acquisition_service"] = service

    @app.get("/api/v2/acquisition/cloud/candidates", endpoint="mcc_v2_cloud_candidates")
    def cloud_candidates():
        subscription_id = str(request.args.get("id") or "").strip()
        if not subscription_id:
            return _error("SUBSCRIPTION_ID_REQUIRED", "需要订阅 id", 400)
        try:
            result, denied = service.preview(subscription_id)
            if denied:
                return _error(*denied)
            return jsonify(result)
        except Exception:
            logger.exception("cloud candidate preview failed")
            return _error("CLOUD_CANDIDATE_SEARCH_FAILED", "网盘候选搜索失败", 502)

    @app.post("/api/v2/acquisition/cloud/transfers", endpoint="mcc_v2_cloud_transfer")
    def cloud_transfer():
        result, denied = service.transfer(request.get_json(silent=True) or {})
        if denied:
            return _error(*denied)
        return jsonify(result), 200 if result.get("status") == "completed" else 202

    return service
