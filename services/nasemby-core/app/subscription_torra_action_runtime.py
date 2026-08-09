from __future__ import annotations

import hashlib


TORRA_PUSH_COOLDOWN_SECONDS = 60


def _error(code, message, status):
    return {
        "ok": False,
        "success": False,
        "code": code,
        "error": message,
    }, status


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_tv_item(item):
    raw = str(
        item.get("media_type") or item.get("mediaType") or item.get("type") or ""
    ).strip().lower()
    return raw in {"tv", "series", "episode"} or "series" in raw or "剧" in raw


def _safe_torra_push_result(result, request_id, start_episode=0):
    source = result if isinstance(result, dict) else {}
    success = bool(source.get("success"))
    pushed = bool(source.get("pushed"))
    already_exists = bool(source.get("alreadyExists"))
    search_triggered = bool(source.get("searchTriggered"))
    if success:
        message = "已提交 Torra · 等待确认"
    else:
        message = "Torra 推送未完成"
    response = {
        "ok": success,
        "success": success,
        "pushed": pushed,
        "alreadyExists": already_exists,
        "searchTriggered": search_triggered,
        # Keep the compatibility field without exposing an upstream identifier.
        "subscriptionId": "",
        "message": (
            f"已从 E{start_episode:02d} 提交 Torra · 不回补此前集数"
            if success and start_episode > 0
            else message
        ),
        "torraPushState": "submitted" if success else "failed",
        "startEpisode": start_episode or None,
        "requestId": request_id,
        "replayed": False,
    }
    if not success:
        response["code"] = "TORRA_PUSH_REJECTED"
        response["error"] = message
    return response


class TorraSubscriptionActionService:
    def __init__(self, environment, repository, client, item_loader, preview_builder, state_recorder=None):
        self.environment = environment
        self.repository = repository
        self.client = client
        self.item_loader = item_loader
        self.preview_builder = preview_builder
        self.state_recorder = state_recorder

    def _validate(self, key, body):
        if set(body) - {"confirm", "idempotencyKey", "startEpisode"}:
            return None, _error("TORRA_PUSH_FIELDS_INVALID", "Torra 推送请求包含不支持的字段", 400)
        if body.get("confirm") is not True:
            return None, _error("TORRA_PUSH_CONFIRMATION_REQUIRED", "需要明确确认 Torra 推送", 400)
        idempotency_key = str(body.get("idempotencyKey") or "").strip()
        if not 12 <= len(idempotency_key) <= 128:
            return None, _error("TORRA_PUSH_IDEMPOTENCY_INVALID", "幂等键长度必须为 12 到 128 个字符", 400)
        if not _truthy(self.environment.get("TORRA_PUSH_ENABLED")):
            return None, _error("TORRA_PUSH_DISABLED", "Torra 安全推送开关未启用，请先核对预览", 403)
        item = self.item_loader(key)
        if not item:
            return None, _error("SUBSCRIPTION_NOT_FOUND", "订阅不存在", 404)
        start_episode = 0
        if body.get("startEpisode") not in (None, ""):
            value = body.get("startEpisode")
            if isinstance(value, bool):
                return None, _error("TORRA_PUSH_START_EPISODE_INVALID", "起始集数必须是正整数", 400)
            try:
                start_episode = int(value)
            except (TypeError, ValueError):
                return None, _error("TORRA_PUSH_START_EPISODE_INVALID", "起始集数必须是正整数", 400)
            if (
                isinstance(value, float) and not value.is_integer()
            ) or start_episode < 1 or not _is_tv_item(item):
                return None, _error(
                    "TORRA_PUSH_START_EPISODE_INVALID",
                    "起始集数只适用于剧集且必须是正整数",
                    400,
                )
        return (idempotency_key, item, start_episode), None

    def _claim_existing(self, idempotency_key, key, start_episode):
        existing = self.repository.get_action_by_idempotency(idempotency_key)
        if not existing:
            return None, None
        summary = existing.get("request_summary") or {}
        if int(summary.get("startEpisode") or 0) != int(start_episode or 0):
            return None, _error(
                "TORRA_PUSH_IDEMPOTENCY_CONFLICT",
                "幂等键已用于其他起始集数",
                409,
            )
        claim = self.repository.claim_action(
            idempotency_key,
            key,
            "torra",
            "subscription-push",
            cooldown_seconds=TORRA_PUSH_COOLDOWN_SECONDS,
        )
        disposition = claim["disposition"]
        if disposition == "conflict":
            return None, _error("TORRA_PUSH_IDEMPOTENCY_CONFLICT", "幂等键已用于其他订阅", 409)
        if disposition == "replay":
            response = dict(claim["action"]["response_summary"])
            response["replayed"] = True
            return None, (response, int(claim["action"]["http_status"] or 200))
        if disposition in {"in_progress", "resume"}:
            return None, _error("TORRA_PUSH_IN_PROGRESS", "相同 Torra 推送正在执行", 409)
        return claim, None

    def _claim_new(self, idempotency_key, key, request_id, start_episode):
        claim = self.repository.claim_action(
            idempotency_key,
            key,
            "torra",
            "subscription-push",
            request_summary={
                "requestId": request_id,
                "subscriptionId": key,
                "startEpisode": start_episode or None,
            },
            cooldown_seconds=TORRA_PUSH_COOLDOWN_SECONDS,
        )
        disposition = claim["disposition"]
        if disposition == "cooldown":
            remaining = int(claim["remaining_seconds"])
            return None, _error(
                "TORRA_PUSH_COOLDOWN",
                f"该订阅刚执行过 Torra 推送，请在 {remaining} 秒后重试",
                409,
            )
        if disposition == "conflict":
            return None, _error("TORRA_PUSH_IDEMPOTENCY_CONFLICT", "幂等键已用于其他订阅", 409)
        if disposition != "claimed":
            return None, _error("TORRA_PUSH_IN_PROGRESS", "相同 Torra 推送正在执行", 409)
        return claim, None

    def _push(self, action_id, key, payload, request_id, start_episode=0):
        try:
            result = self.client.push_subscription(payload)
            response = _safe_torra_push_result(result, request_id, start_episode)
            if callable(self.state_recorder):
                try:
                    self.state_recorder(key, response["torraPushState"])
                except Exception:
                    pass
            http_status = 200 if response["success"] else 502
        except Exception:
            response = {
                "ok": False,
                "success": False,
                "code": "TORRA_PUSH_FAILED",
                "pushed": False,
                "alreadyExists": False,
                "searchTriggered": False,
                "subscriptionId": "",
                "message": "Torra 推送失败",
                "torraPushState": "failed",
                "startEpisode": start_episode or None,
                "error": "Torra 推送失败",
                "requestId": request_id,
                "replayed": False,
            }
            if callable(self.state_recorder):
                try:
                    self.state_recorder(key, "failed")
                except Exception:
                    pass
            http_status = 502
        self.repository.complete_action(
            action_id,
            "succeeded" if response["success"] else "failed",
            response,
            http_status=http_status,
            error_code="" if response["success"] else str(response.get("code") or "TORRA_PUSH_FAILED"),
            error_message="" if response["success"] else str(response.get("error") or "Torra 推送失败"),
        )
        return response, http_status

    def execute(self, key, body):
        validated, immediate = self._validate(key, body if isinstance(body, dict) else {})
        if immediate:
            return immediate
        idempotency_key, item, start_episode = validated
        if start_episode:
            item = {**item, "torra_start_episode": start_episode}
        request_id = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
        claim, immediate = self._claim_existing(idempotency_key, key, start_episode)
        if immediate:
            return immediate
        plan = self.preview_builder(item)
        if not plan["ready"]:
            return {
                "ok": False,
                "code": "TORRA_PUSH_BLOCKED",
                "error": "；".join(plan["blockers"]),
                "preview": plan,
            }, 409
        if claim is None:
            claim, immediate = self._claim_new(idempotency_key, key, request_id, start_episode)
            if immediate:
                return immediate
        return self._push(
            claim["action"]["action_id"],
            key,
            plan["payload"],
            request_id,
            start_episode,
        )
