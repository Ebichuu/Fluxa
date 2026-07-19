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


def _safe_torra_push_result(result, request_id):
    source = result if isinstance(result, dict) else {}
    success = bool(source.get("success"))
    pushed = bool(source.get("pushed"))
    already_exists = bool(source.get("alreadyExists"))
    search_triggered = bool(source.get("searchTriggered"))
    if success and pushed:
        message = "已创建 Torra 订阅并触发搜索"
    elif success and already_exists:
        message = "Torra 已有订阅，未重复创建；已触发搜索"
    elif success:
        message = "Torra 已接受订阅动作"
    else:
        message = "Torra 推送未完成"
    response = {
        "ok": success,
        "success": success,
        "pushed": pushed,
        "alreadyExists": already_exists,
        "searchTriggered": search_triggered,
        "subscriptionId": str(source.get("subscriptionId") or "")[:200],
        "message": message,
        "requestId": request_id,
        "replayed": False,
    }
    if not success:
        response["code"] = "TORRA_PUSH_REJECTED"
        response["error"] = message
    return response


class TorraSubscriptionActionService:
    def __init__(self, environment, repository, client, item_loader, preview_builder):
        self.environment = environment
        self.repository = repository
        self.client = client
        self.item_loader = item_loader
        self.preview_builder = preview_builder

    def _validate(self, key, body):
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
        return (idempotency_key, item), None

    def _claim_existing(self, idempotency_key, key):
        if not self.repository.get_action_by_idempotency(idempotency_key):
            return None, None
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

    def _claim_new(self, idempotency_key, key, request_id):
        claim = self.repository.claim_action(
            idempotency_key,
            key,
            "torra",
            "subscription-push",
            request_summary={"requestId": request_id, "subscriptionId": key},
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

    def _push(self, action_id, payload, request_id):
        try:
            result = self.client.push_subscription(payload)
            response = _safe_torra_push_result(result, request_id)
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
                "error": "Torra 推送失败",
                "requestId": request_id,
                "replayed": False,
            }
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
        idempotency_key, item = validated
        request_id = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
        claim, immediate = self._claim_existing(idempotency_key, key)
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
            claim, immediate = self._claim_new(idempotency_key, key, request_id)
            if immediate:
                return immediate
        return self._push(claim["action"]["action_id"], plan["payload"], request_id)
