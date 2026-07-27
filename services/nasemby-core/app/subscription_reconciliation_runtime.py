from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify

from app.health_state_runtime import evidence
from app.http_runtime import current_request_id
from app.pipeline_fact_runtime import normalize_pipeline_fact, target_scope_for_item
from app.pipeline_outcome_runtime import PIPELINE_OUTCOMES, derive_pipeline_outcome
from app.pipeline_source_fact_runtime import build_torra_source_fact
from app.task_public_runtime import present_pipeline_fact, present_pipeline_outcome
from app.torra_subscription_sync_runtime import normalize_torra_subscription
from app.torra_subscription_keys import (
    resolve_torra_subscription_key,
    torra_internal_unit_key,
    torra_public_storage_key,
    torra_public_subscription_key,
    torra_public_unit_key,
)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _integer(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _media_type(value) -> str:
    text = str(value or "").strip().lower()
    if text in {"movie", "film", "电影"}:
        return "movie"
    if text in {"tv", "series", "电视剧", "剧集"}:
        return "tv"
    return "unknown"


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _title_key(value) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").casefold())


def _local_record(item: dict, key_resolver) -> dict:
    row = dict(item or {})
    media_type = _media_type(row.get("media_type") or row.get("mediaType") or row.get("type"))
    season = _integer(
        row.get("target_season", row.get("season_number", row.get("seasonNumber", row.get("season")))),
        0,
    )
    return {
        "key": str(key_resolver(row) or row.get("subscription_key") or row.get("dedupe_key") or "").strip(),
        "title": str(row.get("title") or row.get("name") or "").strip(),
        "mediaType": media_type,
        "tmdbId": str(row.get("tmdb_id") or row.get("tmdbId") or "").strip(),
        "seasonNumber": season if media_type == "tv" else 0,
        "remoteId": str(row.get("torra_remote_id") or "").strip(),
        "origin": str(row.get("origin") or "").strip().lower(),
        "inLibrary": _truthy(row.get("in_library") or row.get("inLibrary")),
        "paused": _truthy(row.get("paused")),
        "blocked": bool(str(row.get("blocking_reason") or row.get("blockingReason") or "").strip()),
        "readOnly": _truthy(row.get("read_only") or row.get("readOnly")),
        "sourceLabel": str(row.get("source_label") or row.get("sourceLabel") or "Fluxa").strip(),
        "raw": row,
    }


def _identity(row: dict):
    tmdb_id = str(row.get("tmdbId") or "").strip()
    media_type = str(row.get("mediaType") or "unknown")
    if not tmdb_id or media_type not in {"movie", "tv"}:
        return None
    return media_type, tmdb_id, _integer(row.get("seasonNumber"), 0) if media_type == "tv" else 0


def _title_identity(row: dict):
    return (
        str(row.get("mediaType") or "unknown"),
        _title_key(row.get("title")),
        _integer(row.get("seasonNumber"), 0) if row.get("mediaType") == "tv" else 0,
    )


def _pipeline_projection(
    local: dict | None,
    remote: dict | None,
    reconciliation_state: str,
    observed_at: str,
    fresh_until: str,
    now_value: datetime,
) -> tuple[dict, dict]:
    remote_item = (remote or {}).get("item") or {}
    primary = local or {
        "mediaType": remote_item.get("media_type"),
        "tmdbId": remote_item.get("tmdb_id"),
        "seasonNumber": remote_item.get("season_number", remote_item.get("target_season", 0)),
    }
    context = {
        "mediaType": primary.get("mediaType"),
        "tmdbId": primary.get("tmdbId"),
        "seasonNumber": primary.get("seasonNumber"),
    }
    if (local or {}).get("blocked") or reconciliation_state == "conflict":
        state = "failed"
        evidence_kind = "verified"
        reason_code = "SUBSCRIPTION_BLOCKED" if (local or {}).get("blocked") else "SUBSCRIPTION_IDENTITY_CONFLICT"
        reason_text = "追更当前被阻塞" if (local or {}).get("blocked") else "本地与 Torra 身份冲突"
        source = "Fluxa/Torra 对账"
    elif remote:
        internal_fact = build_torra_source_fact(
            {**context, "torra": (remote or {}).get("raw") or {}},
            observed_at=observed_at,
        )
        outcome = derive_pipeline_outcome(
            [internal_fact],
            target_scope=target_scope_for_item(context),
            now=now_value,
        )
        return present_pipeline_fact(internal_fact), present_pipeline_outcome(outcome)
    elif reconciliation_state == "only_fluxa" and (local or {}).get("origin") == "manual":
        state = "waiting"
        evidence_kind = "verified"
        reason_code = "PENDING_TORRA_SYNC"
        reason_text = "已保存追更意图，尚未同步到 Torra"
        source = "Fluxa"
    else:
        state = "unknown"
        evidence_kind = "missing"
        reason_code = "TORRA_EVIDENCE_MISSING"
        reason_text = "缺少可验证的 Torra 订阅证据"
        source = ""
    internal_fact = normalize_pipeline_fact({
        "stage": "torra",
        "state": state,
        "scope": target_scope_for_item(context),
        "evidence": evidence_kind,
        "observedAt": observed_at,
        "freshUntil": fresh_until,
        "source": source,
        "sourceRef": str((remote or {}).get("remote_id") or ""),
        "reasonCode": reason_code,
        "reasonText": reason_text,
    })
    outcome = derive_pipeline_outcome(
        [internal_fact],
        target_scope=target_scope_for_item(context),
        now=now_value,
    )
    return present_pipeline_fact(internal_fact), present_pipeline_outcome(outcome)


def _fulfillment(
    local: dict | None,
    remote: dict | None,
    reconciliation_state: str,
    torra_fact: dict,
) -> str:
    if (local or {}).get("blocked") or reconciliation_state in {"conflict", "remote_missing"}:
        return "blocked"
    if (local or {}).get("paused") or (
        remote and ((remote or {}).get("remote_status") or {}).get("enabled") is False
    ):
        return "paused"
    if reconciliation_state == "only_fluxa":
        return "pending_sync"
    if torra_fact.get("state") == "succeeded":
        return "completed"
    return "following"


def _health(reconciliation_state: str, local: dict | None, remote: dict | None, observed_at: str, fresh_until: str) -> dict:
    source = "Fluxa/Torra 对账"
    if reconciliation_state == "conflict":
        return evidence(
            state="action_required",
            source=source,
            reason_code="SUBSCRIPTION_IDENTITY_CONFLICT",
            reason_text="标题相近但身份不唯一，需要人工确认",
            observed_at=observed_at,
            fresh_until=fresh_until,
        )
    if reconciliation_state == "remote_missing":
        return evidence(
            state="evidence_insufficient",
            source=source,
            reason_code="TORRA_REMOTE_MISSING",
            reason_text="已关联的 Torra 远端订阅不存在，本地意图已保留",
            observed_at=observed_at,
            fresh_until=fresh_until,
        )
    if (local or {}).get("blocked"):
        return evidence(
            state="action_required",
            source=source,
            reason_code="SUBSCRIPTION_BLOCKED",
            reason_text="追更当前被阻塞",
            observed_at=observed_at,
            fresh_until=fresh_until,
        )
    if reconciliation_state == "only_fluxa":
        state = "waiting" if _identity(local or {}) else "evidence_insufficient"
        return evidence(
            state=state,
            source=source,
            reason_code="PENDING_TORRA_SYNC" if state == "waiting" else "LOCAL_IDENTITY_INCOMPLETE",
            reason_text="已保存追更意图，尚未同步到 Torra" if state == "waiting" else "本地追更缺少可验证的 TMDB 身份",
            observed_at=observed_at,
            fresh_until=fresh_until,
        )
    if (remote or {}).get("mapping_status") != "mapped":
        return evidence(
            state="evidence_insufficient",
            source=source,
            reason_code="TORRA_IDENTITY_INCOMPLETE",
            reason_text="Torra 订阅缺少完整媒体身份",
            observed_at=observed_at,
            fresh_until=fresh_until,
        )
    return evidence(
        state="normal",
        source=source,
        observed_at=observed_at,
        fresh_until=fresh_until,
    )


def _public_item(
    local: dict | None,
    remote: dict | None,
    state: str,
    observed_at: str,
    fresh_until: str,
    now_value: datetime,
) -> dict:
    remote_item = (remote or {}).get("item") or {}
    primary = local or {
        "title": remote_item.get("title"),
        "mediaType": remote_item.get("media_type"),
        "tmdbId": remote_item.get("tmdb_id"),
        "seasonNumber": remote_item.get("season_number", remote_item.get("target_season", 0)),
    }
    remote_id = str((remote or {}).get("remote_id") or "")
    local_key = str((local or {}).get("key") or "")
    public_local_key = local_key
    if local and local_key.startswith("torra:") and (
        local.get("readOnly") or local.get("origin") == "torra"
    ):
        public_local_key = torra_public_storage_key(
            local_key,
            remote_id or local.get("remoteId"),
        )
    health = _health(state, local, remote, observed_at, fresh_until)
    torra_fact, pipeline_outcome = _pipeline_projection(
        local,
        remote,
        state,
        observed_at,
        fresh_until,
        now_value,
    )
    return {
        "id": public_local_key or torra_public_subscription_key(remote_id),
        "localId": public_local_key,
        "remoteRef": torra_public_subscription_key(remote_id).removeprefix("torra:") if remote_id else "",
        "title": str(primary.get("title") or remote_item.get("title") or "未命名订阅"),
        "mediaType": str(primary.get("mediaType") or remote_item.get("media_type") or "unknown"),
        "tmdbId": str(primary.get("tmdbId") or remote_item.get("tmdb_id") or ""),
        "seasonNumber": _integer(primary.get("seasonNumber", remote_item.get("season_number", 0)), 0),
        "reconciliationState": state,
        "fulfillmentState": _fulfillment(local, remote, state, torra_fact),
        "torraFact": torra_fact,
        "pipelineOutcome": pipeline_outcome,
        **health,
        "local": {
            "present": local is not None,
            "readOnly": bool((local or {}).get("readOnly")),
            "sourceLabel": str((local or {}).get("sourceLabel") or ""),
            "origin": str((local or {}).get("origin") or ""),
        },
        "torra": {
            "present": remote is not None,
            "enabled": bool(((remote or {}).get("remote_status") or {}).get("enabled", False)),
            "completed": torra_fact.get("state") == "succeeded",
            "mappingStatus": str((remote or {}).get("mapping_status") or ""),
        },
    }


class SubscriptionReconciliationService:
    def __init__(self, repository, client, item_loader, key_resolver, clock=None):
        self.repository = repository
        self.client = client
        self.item_loader = item_loader
        self.key_resolver = key_resolver
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def snapshot(self) -> dict:
        now_value = self.clock()
        if now_value.tzinfo is None:
            now_value = now_value.replace(tzinfo=timezone.utc)
        observed_at = _iso(now_value)
        fresh_until = _iso(now_value + timedelta(minutes=10))
        payload = self.item_loader() or {}
        local_rows = payload.get("items") if isinstance(payload, dict) else []
        locals_ = [
            _local_record(item, self.key_resolver)
            for item in (local_rows or [])
            if isinstance(item, dict)
        ]
        links = self.repository.list_torra_links()
        links_by_local = {str(link.get("subscription_key") or ""): link for link in links}

        configured = not hasattr(self.client, "is_configured") or bool(self.client.is_configured())
        source_error = "" if configured else "Torra 尚未配置"
        try:
            remote_rows = self.client.list_subscriptions() if configured else []
        except Exception:
            remote_rows = []
            source_error = "Torra 订阅读取失败"
        source_available = configured and not source_error
        remotes = [normalize_torra_subscription(row) for row in remote_rows if isinstance(row, dict)]
        remote_by_id = {str(row.get("remote_id") or ""): index for index, row in enumerate(remotes) if row.get("remote_id")}
        used_local: set[int] = set()
        used_remote: set[int] = set()
        rows = []

        for local_index, local in enumerate(locals_):
            link = links_by_local.get(local["key"]) or {}
            remote_id = str(link.get("remote_id") or local.get("remoteId") or "")
            if not remote_id:
                continue
            remote_index = remote_by_id.get(remote_id)
            if remote_index is None:
                state = "remote_missing" if source_available else "linked"
                rows.append(_public_item(local, None, state, observed_at, fresh_until, now_value))
                used_local.add(local_index)
                continue
            rows.append(_public_item(local, remotes[remote_index], "linked", observed_at, fresh_until, now_value))
            used_local.add(local_index)
            used_remote.add(remote_index)

        identity_to_local: dict[tuple, list[int]] = {}
        identity_to_remote: dict[tuple, list[int]] = {}
        for index, local in enumerate(locals_):
            if index not in used_local and (identity := _identity(local)):
                identity_to_local.setdefault(identity, []).append(index)
        for index, remote in enumerate(remotes):
            remote_item = remote.get("item") or {}
            public_remote = {
                "mediaType": remote_item.get("media_type"),
                "tmdbId": remote_item.get("tmdb_id"),
                "seasonNumber": remote_item.get("season_number", remote_item.get("target_season", 0)),
            }
            if index not in used_remote and (identity := _identity(public_remote)):
                identity_to_remote.setdefault(identity, []).append(index)

        for identity in sorted(set(identity_to_local) & set(identity_to_remote)):
            local_indexes = identity_to_local[identity]
            remote_indexes = identity_to_remote[identity]
            if len(local_indexes) == 1 and len(remote_indexes) == 1:
                local_index, remote_index = local_indexes[0], remote_indexes[0]
                rows.append(_public_item(
                    locals_[local_index], remotes[remote_index], "linked", observed_at, fresh_until, now_value,
                ))
                used_local.add(local_index)
                used_remote.add(remote_index)
                continue
            for local_index in local_indexes:
                if local_index not in used_local:
                    rows.append(_public_item(
                        locals_[local_index], remotes[remote_indexes[0]], "conflict",
                        observed_at, fresh_until, now_value,
                    ))
                    used_local.add(local_index)
            used_remote.update(remote_indexes)

        title_to_local: dict[tuple, list[int]] = {}
        title_to_remote: dict[tuple, list[int]] = {}
        for index, local in enumerate(locals_):
            if index not in used_local and _title_key(local.get("title")):
                title_to_local.setdefault(_title_identity(local), []).append(index)
        for index, remote in enumerate(remotes):
            if index in used_remote:
                continue
            item = remote.get("item") or {}
            public_remote = {
                "title": item.get("title"),
                "mediaType": item.get("media_type"),
                "seasonNumber": item.get("season_number", item.get("target_season", 0)),
            }
            if _title_key(public_remote["title"]):
                title_to_remote.setdefault(_title_identity(public_remote), []).append(index)

        for identity in sorted(set(title_to_local) & set(title_to_remote)):
            local_indexes = title_to_local[identity]
            remote_indexes = title_to_remote[identity]
            for local_index in local_indexes:
                rows.append(_public_item(
                    locals_[local_index], remotes[remote_indexes[0]], "conflict",
                    observed_at, fresh_until, now_value,
                ))
                used_local.add(local_index)
            used_remote.update(remote_indexes)

        for index, local in enumerate(locals_):
            if index not in used_local:
                rows.append(_public_item(local, None, "only_fluxa", observed_at, fresh_until, now_value))
        for index, remote in enumerate(remotes):
            if index not in used_remote:
                rows.append(_public_item(None, remote, "only_torra", observed_at, fresh_until, now_value))

        if not source_available:
            for row in rows:
                row.update(evidence(
                    state="evidence_insufficient",
                    source="Torra",
                    reason_code="TORRA_READ_FAILED" if configured else "TORRA_NOT_CONFIGURED",
                    reason_text=source_error,
                    observed_at=observed_at,
                    fresh_until=fresh_until,
                ))

        reconciliation_counts = {
            state: sum(row["reconciliationState"] == state for row in rows)
            for state in ("linked", "only_fluxa", "only_torra", "conflict", "remote_missing")
        }
        fulfillment_counts = {
            state: sum(row["fulfillmentState"] == state for row in rows)
            for state in ("pending_sync", "following", "completed", "paused", "blocked")
        }
        health_counts = {
            state: sum(row["healthState"] == state for row in rows)
            for state in ("normal", "waiting", "protected", "action_required", "evidence_insufficient")
        }
        outcome_counts = {
            state: sum((row.get("pipelineOutcome") or {}).get("state") == state for row in rows)
            for state in PIPELINE_OUTCOMES
        }
        return {
            "ok": True,
            "configured": configured,
            "sourceError": source_error,
            "observedAt": observed_at,
            "freshUntil": fresh_until,
            "summary": {
                "localTotal": len(locals_),
                "remoteTotal": len(remotes),
                "reconciliation": reconciliation_counts,
                "fulfillment": fulfillment_counts,
                "health": health_counts,
                "outcome": outcome_counts,
            },
            "items": rows,
        }


def register_subscription_reconciliation(app: Flask, service: SubscriptionReconciliationService):
    app.extensions["mcc_subscription_reconciliation"] = service

    @app.get("/api/v2/subscriptions/reconciliation")
    def subscription_reconciliation():
        try:
            return jsonify(service.snapshot())
        except Exception:
            return jsonify({
                "code": "SUBSCRIPTION_RECONCILIATION_READ_FAILED",
                "error": "追更对账读取失败",
                "request_id": current_request_id(),
            }), 502

    return service
