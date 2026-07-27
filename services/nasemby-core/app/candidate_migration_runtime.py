from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from flask import Flask, jsonify, request

from app import discover_runtime
from app.http_runtime import current_request_id
from app.subscription_repository import CandidateMigrationConflict


IDEMPOTENCY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{12,128}")
FINGERPRINT_PATTERN = re.compile(r"[a-f0-9]{64}")
URL_PATTERN = re.compile(r"(?i)https?://[^\s]+")
SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)(password|passwd|token|api[_-]?key|cookie|secret|authorization|passkey|sign)\s*[=:]\s*[^\s&]+"
)
CATEGORIES = ("manual", "downstream-owned", "candidate-eligible", "migration-review")
MANUAL_ORIGINS = {"manual", "user", "user_added", "user-added"}
AUTO_ORIGINS = {"auto", "source", "discover", "ranking", "scheduler"}


class CandidateMigrationError(RuntimeError):
    def __init__(self, code, message, status):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.status = int(status)


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _text(value, limit=240):
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = URL_PATTERN.sub("", text)
    text = SENSITIVE_TEXT_PATTERN.sub(r"\1=***", text)
    return " ".join(text.split())[:limit]


def _iso(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _integer(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _public_id(subscription_key):
    digest = hashlib.sha256(f"candidate-migration\0{subscription_key}".encode("utf-8")).hexdigest()[:24]
    return f"migration-item:{digest}"


class CandidateMigrationService:
    def __init__(
        self,
        repository,
        environment=None,
        *,
        backup_callback=None,
        activity_writer=None,
        clock=None,
    ):
        self.repository = repository
        self.environment = environment or {}
        self.backup_callback = backup_callback or repository.ensure_candidate_migration_backup
        self.activity_writer = activity_writer or discover_runtime.write_activity
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _classify_row(row):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        origin = str(payload.get("origin") or payload.get("subscription_origin") or "").strip().lower()
        intent_origin = str(payload.get("intent_origin") or "").strip().lower()
        if origin in MANUAL_ORIGINS or intent_origin in MANUAL_ORIGINS:
            return "manual", "MANUAL_INTENT_PRESENT", "明确由用户加入，保留在追更台账"
        if row.get("torraOwned"):
            return "downstream-owned", "TORRA_LINK_PRESENT", "已有 Torra 归属证据，保留在追更台账"
        if row.get("resourceOwned"):
            return "downstream-owned", "RESOURCE_CHAIN_PRESENT", "已有任务或产物链证据，保留在追更台账"
        if payload.get("read_only") or origin == "torra":
            return "migration-review", "DOWNSTREAM_ORIGIN_UNVERIFIED", "标记为下游来源但缺少当前关联证据，保留待审"

        media_type = str(row.get("mediaType") or "")
        tmdb_id = str(row.get("tmdbId") or "")
        season_number = _integer(row.get("seasonNumber"))
        if media_type not in {"movie", "tv"} or not tmdb_id.isdigit():
            return "migration-review", "IDENTITY_INCOMPLETE", "媒体类型或 TMDB 身份不完整，保留待审"
        if media_type == "tv" and season_number < 1:
            return "migration-review", "TV_SEASON_UNCONFIRMED", "电视剧缺少明确季号，保留待审"
        if origin in AUTO_ORIGINS or intent_origin in AUTO_ORIGINS:
            return "candidate-eligible", "AUTO_SOURCE_CONFIRMED", "明确来自自动来源且没有下游归属，可迁入候选池"
        return "migration-review", "ORIGIN_UNCLEAR", "缺少明确人工或自动来源证据，保留待审"

    @classmethod
    def classify_rows(cls, rows):
        result = []
        for row in rows or []:
            category, reason_code, reason_text = cls._classify_row(row)
            result.append({
                "row": row,
                "category": category,
                "reasonCode": reason_code,
                "reasonText": reason_text,
            })
        return result

    @staticmethod
    def _public_item(item):
        row = item["row"]
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        return {
            "id": _public_id(row.get("subscriptionKey")),
            "title": _text(row.get("title") or payload.get("title") or payload.get("name")),
            "mediaType": str(row.get("mediaType") or "unknown"),
            "tmdbId": str(row.get("tmdbId") or ""),
            "seasonNumber": _integer(row.get("seasonNumber")),
            "sourceLabel": _text(payload.get("source_label") or payload.get("source") or "来源未确认", 120),
            "category": item["category"],
            "reasonCode": item["reasonCode"],
            "reasonText": item["reasonText"],
            "version": int(row.get("version") or 1),
        }

    def preview(self, *, limit=100, offset=0):
        snapshot = self.repository.candidate_migration_snapshot()
        classified = self.classify_rows(snapshot["rows"])
        counts = {category: 0 for category in CATEGORIES}
        for item in classified:
            counts[item["category"]] += 1
        return {
            "ok": True,
            "previewFingerprint": snapshot["fingerprint"],
            "generatedAt": _iso(self.clock()),
            "total": len(classified),
            "counts": counts,
            "canExecute": counts["candidate-eligible"] > 0 and _truthy(
                self.environment.get("NASEMBY_CORE_WRITE_ENABLED")
            ),
            "requiresConfirmation": True,
            "items": [self._public_item(item) for item in classified[offset:offset + limit]],
            "page": {
                "total": len(classified),
                "limit": int(limit),
                "offset": int(offset),
                "nextOffset": offset + limit if offset + limit < len(classified) else None,
                "hasMore": offset + limit < len(classified),
            },
        }

    @staticmethod
    def _validate_execute_body(body):
        body = body if isinstance(body, dict) else {}
        if set(body) - {"confirm", "idempotencyKey", "previewFingerprint"}:
            raise CandidateMigrationError("CANDIDATE_MIGRATION_FIELDS_INVALID", "请求包含不支持的字段", 422)
        if body.get("confirm") is not True:
            raise CandidateMigrationError("CANDIDATE_MIGRATION_CONFIRM_REQUIRED", "需要明确确认迁移历史追更", 422)
        idempotency_key = str(body.get("idempotencyKey") or "").strip()
        if not IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
            raise CandidateMigrationError("CANDIDATE_MIGRATION_IDEMPOTENCY_INVALID", "幂等键必须为 12–128 位安全字符", 422)
        fingerprint = str(body.get("previewFingerprint") or "").strip().lower()
        if not FINGERPRINT_PATTERN.fullmatch(fingerprint):
            raise CandidateMigrationError("CANDIDATE_MIGRATION_FINGERPRINT_INVALID", "迁移预览指纹无效", 422)
        return idempotency_key, fingerprint

    def execute(self, body):
        idempotency_key, fingerprint = self._validate_execute_body(body)
        if not _truthy(self.environment.get("NASEMBY_CORE_WRITE_ENABLED")):
            raise CandidateMigrationError("NASEMBY_CORE_WRITE_DISABLED", "本地追更写入尚未启用", 403)

        replay = self.repository.get_candidate_migration_run(idempotency_key=idempotency_key)
        if replay:
            if replay["previewFingerprint"] != fingerprint:
                raise CandidateMigrationError("CANDIDATE_MIGRATION_IDEMPOTENCY_CONFLICT", "幂等键已用于其他迁移预览", 409)
            return {**replay["response"], "replayed": True}

        current = self.repository.candidate_migration_snapshot()
        if current["fingerprint"] != fingerprint:
            raise CandidateMigrationError("CANDIDATE_MIGRATION_PREVIEW_STALE", "追更台账已变化，请重新预览", 409)
        if not any(
            item["category"] == "candidate-eligible"
            for item in self.classify_rows(current["rows"])
        ):
            raise CandidateMigrationError("CANDIDATE_MIGRATION_NOT_NEEDED", "当前没有可迁入候选池的历史追更", 409)

        try:
            backup_ref = self.backup_callback(fingerprint)
        except Exception as exc:
            raise CandidateMigrationError("CANDIDATE_MIGRATION_BACKUP_FAILED", "迁移前备份失败，未修改追更台账", 500) from exc
        try:
            response = self.repository.execute_candidate_migration(
                preview_fingerprint=fingerprint,
                idempotency_key=idempotency_key,
                backup_ref=backup_ref,
                classify=self.classify_rows,
            )
        except CandidateMigrationConflict as exc:
            code = {
                "PREVIEW_STALE": "CANDIDATE_MIGRATION_PREVIEW_STALE",
                "IDEMPOTENCY_KEY_CONFLICT": "CANDIDATE_MIGRATION_IDEMPOTENCY_CONFLICT",
                "NO_ELIGIBLE_CANDIDATES": "CANDIDATE_MIGRATION_NOT_NEEDED",
                "SUBSCRIPTION_CHANGED_CONCURRENTLY": "CANDIDATE_MIGRATION_PREVIEW_STALE",
            }.get(exc.reason_code, "CANDIDATE_MIGRATION_CONFLICT")
            raise CandidateMigrationError(code, "迁移条件已变化，未修改追更台账", 409) from exc
        try:
            self.activity_writer(
                "subscription",
                "migrate_discover_candidates",
                "success",
                "历史自动来源追更已迁入候选池",
                run_id=response.get("runId"),
                migrated_count=response.get("migratedCount"),
                review_count=response.get("reviewCount"),
            )
        except Exception:
            pass
        return response

    def get_run(self, run_id):
        run = self.repository.get_candidate_migration_run(run_id=run_id)
        if not run:
            raise CandidateMigrationError("CANDIDATE_MIGRATION_RUN_NOT_FOUND", "候选迁移记录不存在", 404)
        return run["response"]


def register_candidate_migrations(
    app: Flask,
    environment=None,
    *,
    repository=None,
    backup_callback=None,
    activity_writer=None,
    clock=None,
):
    service = CandidateMigrationService(
        repository or discover_runtime.subscription_repository(),
        environment,
        backup_callback=backup_callback,
        activity_writer=activity_writer,
        clock=clock,
    )
    app.extensions["mcc_candidate_migrations"] = service

    @app.get("/api/v2/subscriptions/candidate-migrations/preview")
    def candidate_migration_preview():
        try:
            limit = int(request.args.get("limit") or 100)
            offset = int(request.args.get("offset") or 0)
        except (TypeError, ValueError):
            return jsonify({
                "code": "CANDIDATE_MIGRATION_PAGE_INVALID",
                "error": "迁移预览分页参数无效",
                "request_id": current_request_id(),
            }), 400
        if limit < 1 or limit > 200 or offset < 0:
            return jsonify({
                "code": "CANDIDATE_MIGRATION_PAGE_INVALID",
                "error": "迁移预览分页参数无效",
                "request_id": current_request_id(),
            }), 400
        return jsonify(service.preview(limit=limit, offset=offset))

    @app.post("/api/v2/subscriptions/candidate-migrations")
    def candidate_migration_execute():
        try:
            result = service.execute(request.get_json(silent=True) or {})
            response = jsonify(result)
            if result.get("replayed"):
                return response
            response.status_code = 201
            response.headers["Location"] = f"/api/v2/subscriptions/candidate-migrations/{result['runId']}"
            return response
        except CandidateMigrationError as exc:
            return jsonify({"code": exc.code, "error": exc.message, "request_id": current_request_id()}), exc.status
        except Exception:
            return jsonify({
                "code": "CANDIDATE_MIGRATION_FAILED",
                "error": "候选迁移失败，未确认任何数据变更",
                "request_id": current_request_id(),
            }), 500

    @app.get("/api/v2/subscriptions/candidate-migrations/<run_id>")
    def candidate_migration_run(run_id):
        try:
            return jsonify(service.get_run(run_id))
        except CandidateMigrationError as exc:
            return jsonify({"code": exc.code, "error": exc.message, "request_id": current_request_id()}), exc.status

    return service
