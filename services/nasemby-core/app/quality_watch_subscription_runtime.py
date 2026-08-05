from __future__ import annotations

from app.torra_subscription_keys import (
    torra_canonical_subscription_key,
    torra_public_storage_key,
    torra_public_subscription_key,
)


def _text(value) -> str:
    return str(value or "").strip()


def _integer(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _media_type(value) -> str:
    normalized = _text(value).lower()
    if normalized in {"tv", "series", "电视剧", "剧集"}:
        return "tv"
    if normalized in {"movie", "film", "电影"}:
        return "movie"
    return ""


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _is_torra_read_only_mirror(row: dict) -> bool:
    origin = _text(row.get("origin") or row.get("source")).lower()
    remote_id = _first(row, ("torra_remote_id", "torraRemoteId"))
    return bool(remote_id and origin == "torra" and _truthy(
        row.get("read_only", row.get("readOnly"))
    ))


def _rows(value) -> list[dict]:
    rows = value.get("items") if isinstance(value, dict) else value
    return [dict(row) for row in rows or [] if isinstance(row, dict)]


def _first(row: dict, keys: tuple[str, ...]) -> str:
    return next((_text(row.get(key)) for key in keys if _text(row.get(key))), "")


def _local_key(row: dict) -> str:
    return _first(row, ("key", "subscription_key", "dedupe_key", "id"))


def _tmdb_id(row: dict) -> str:
    return _first(row, ("tmdb_id", "tmdbId", "tmdbid"))


def _season_number(row: dict) -> int:
    return next((
        number for key in ("target_season", "season_number", "seasonNumber", "season")
        if (number := _integer(row.get(key))) > 0
    ), 0)


def _identity(row: dict) -> tuple[str, str, int]:
    media_type = _media_type(row.get("media_type") or row.get("mediaType") or row.get("type"))
    return media_type, _tmdb_id(row), _season_number(row) if media_type == "tv" else 0


def _fact_identity(fact: dict) -> tuple[str, str, int]:
    media_type = _media_type(fact.get("media_type"))
    return media_type, _text(fact.get("tmdb_id")), _integer(fact.get("season_number"))


def _resolved(subscription: dict, subscription_key: str, canonical_key: str, public_key: str) -> dict:
    return {
        "status": "resolved",
        "reason": "",
        "subscription": subscription,
        "subscriptionKey": subscription_key,
        "canonicalKey": canonical_key,
        "publicKey": public_key,
    }


class QualityWatchSubscriptionResolver:
    """Resolve bridge ownership from structured local and Torra subscription facts only."""

    def __init__(self, local_subscriptions=(), torra_subscriptions=()):
        self.local_by_key: dict[str, list[dict]] = {}
        self.local_by_remote_id: dict[str, list[dict]] = {}
        for row in _rows(local_subscriptions):
            if _is_torra_read_only_mirror(row):
                continue
            key = _local_key(row)
            if key:
                self.local_by_key.setdefault(key, []).append(row)
            remote_id = _first(row, ("torra_remote_id", "torraRemoteId"))
            if remote_id:
                self.local_by_remote_id.setdefault(remote_id, []).append(row)

        self.torra_by_remote_id: dict[str, list[dict]] = {}
        self.torra_by_public_key: dict[str, set[str]] = {}
        for row in _rows(torra_subscriptions):
            remote_id = _first(row, ("id", "remote_id", "remoteId"))
            if remote_id:
                self.torra_by_remote_id.setdefault(remote_id, []).append(row)
                self.torra_by_public_key.setdefault(
                    torra_public_subscription_key(remote_id), set()
                ).add(remote_id)

    @staticmethod
    def _failure(reason: str) -> dict:
        return {"status": "needs_review", "reason": reason, "subscription": None, "subscriptionKey": ""}

    def subscription_map(self) -> dict[str, dict]:
        result = {
            key: dict(rows[0])
            for key, rows in self.local_by_key.items()
            if rows and all(row == rows[0] for row in rows)
        }
        for remote_id, rows in self.torra_by_remote_id.items():
            public_key = torra_public_subscription_key(remote_id)
            if (
                len(rows) != 1
                or len(self.torra_by_public_key.get(public_key, set())) != 1
                or len(self.local_by_remote_id.get(remote_id, [])) > 1
            ):
                continue
            if len(self.local_by_remote_id.get(remote_id, [])) == 1:
                continue
            remote = dict(rows[0])
            identity = _identity(remote)
            if (
                identity[0] not in {"movie", "tv"}
                or not identity[1].isdigit()
                or (identity[0] == "tv" and identity[2] <= 0)
            ):
                continue
            canonical_key = torra_canonical_subscription_key(remote_id)
            result[canonical_key] = {
                **remote,
                "key": canonical_key,
                "subscription_key": canonical_key,
                "media_type": identity[0],
                "tmdb_id": identity[1],
                "target_season": identity[2],
                "torra_remote_id": remote_id,
                "origin": "torra",
                "source": "torra",
                "read_only": True,
            }
        return result

    def resolve(self, fact: dict) -> dict:
        remote_id = _text(fact.get("torra_subscription_id"))
        expected_identity = _fact_identity(fact)
        if (
            not remote_id
            or expected_identity[0] not in {"movie", "tv"}
            or not expected_identity[1].isdigit()
            or (expected_identity[0] == "tv" and expected_identity[2] <= 0)
        ):
            return self._failure("identity_incomplete")

        requested_key = _text(fact.get("subscription_key"))
        canonical_key = torra_canonical_subscription_key(remote_id)
        public_key = torra_public_subscription_key(remote_id)
        if len(self.torra_by_public_key.get(public_key, set())) > 1:
            return self._failure("torra_subscription_key_conflict")
        local_rows = self.local_by_key.get(requested_key, []) if requested_key else []
        if len(local_rows) > 1:
            return self._failure("subscription_identity_conflict")
        remote_local_rows = self.local_by_remote_id.get(remote_id, [])
        if len(remote_local_rows) > 1:
            return self._failure("subscription_identity_conflict")
        if local_rows:
            subscription = dict(local_rows[0])
            local_remote_id = _first(subscription, ("torra_remote_id", "torraRemoteId"))
            if local_remote_id != remote_id:
                return self._failure("torra_binding_unconfirmed")
            if _identity(subscription) != expected_identity:
                return self._failure("subscription_identity_conflict")
            subscription["key"] = requested_key
            subscription["subscription_key"] = requested_key
            return _resolved(subscription, requested_key, canonical_key, public_key)

        if remote_local_rows:
            subscription = dict(remote_local_rows[0])
            local_key = _local_key(subscription)
            if not local_key or _identity(subscription) != expected_identity:
                return self._failure("subscription_identity_conflict")
            if requested_key and torra_public_storage_key(requested_key, remote_id) != public_key:
                return self._failure("subscription_identity_missing")
            subscription["key"] = local_key
            subscription["subscription_key"] = local_key
            return _resolved(subscription, local_key, canonical_key, public_key)
        if requested_key and torra_public_storage_key(requested_key, remote_id) != public_key:
            return self._failure("subscription_identity_missing")

        remote_rows = self.torra_by_remote_id.get(remote_id, [])
        if len(remote_rows) != 1:
            return self._failure(
                "torra_subscription_identity_conflict" if remote_rows else "torra_subscription_missing"
            )
        remote = dict(remote_rows[0])
        if _identity(remote) != expected_identity:
            return self._failure("torra_subscription_identity_conflict")
        subscription = {
            "key": canonical_key,
            "subscription_key": canonical_key,
            "media_type": expected_identity[0],
            "tmdb_id": expected_identity[1],
            "target_season": expected_identity[2],
            "torra_remote_id": remote_id,
            "origin": "torra",
            "source": "torra",
            "read_only": True,
        }
        return _resolved(subscription, canonical_key, canonical_key, public_key)
