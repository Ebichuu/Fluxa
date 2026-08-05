from __future__ import annotations

from app.torra_subscription_keys import torra_public_storage_key, torra_public_subscription_key


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


def _resolved(subscription: dict, subscription_key: str) -> dict:
    return {
        "status": "resolved",
        "reason": "",
        "subscription": subscription,
        "subscriptionKey": subscription_key,
    }


class QualityWatchSubscriptionResolver:
    """Resolve bridge ownership from structured local and Torra subscription facts only."""

    def __init__(self, local_subscriptions=(), torra_subscriptions=()):
        self.local_by_key: dict[str, list[dict]] = {}
        self.local_by_remote_id: dict[str, list[dict]] = {}
        for row in _rows(local_subscriptions):
            key = _local_key(row)
            if key:
                self.local_by_key.setdefault(key, []).append(row)
            remote_id = _first(row, ("torra_remote_id", "torraRemoteId"))
            if remote_id:
                self.local_by_remote_id.setdefault(remote_id, []).append(row)

        self.torra_by_remote_id: dict[str, list[dict]] = {}
        for row in _rows(torra_subscriptions):
            remote_id = _first(row, ("id", "remote_id", "remoteId"))
            if remote_id:
                self.torra_by_remote_id.setdefault(remote_id, []).append(row)

    @staticmethod
    def _failure(reason: str) -> dict:
        return {"status": "needs_review", "reason": reason, "subscription": None, "subscriptionKey": ""}

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
            return _resolved(subscription, requested_key)

        public_key = torra_public_subscription_key(remote_id)
        if remote_local_rows:
            subscription = dict(remote_local_rows[0])
            local_key = _local_key(subscription)
            if not local_key or _identity(subscription) != expected_identity:
                return self._failure("subscription_identity_conflict")
            if requested_key and torra_public_storage_key(requested_key, remote_id) != public_key:
                return self._failure("subscription_identity_missing")
            subscription["key"] = local_key
            subscription["subscription_key"] = local_key
            return _resolved(subscription, local_key)
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
            "key": public_key,
            "subscription_key": public_key,
            "media_type": expected_identity[0],
            "tmdb_id": expected_identity[1],
            "target_season": expected_identity[2],
            "torra_remote_id": remote_id,
            "origin": "torra",
            "read_only": True,
        }
        return _resolved(subscription, public_key)
