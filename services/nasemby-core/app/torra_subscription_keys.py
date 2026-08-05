from __future__ import annotations

import hashlib
import re


PUBLIC_KEY_SUFFIX = re.compile(r"^[0-9a-f]{10}$")


def _remote_ref(remote_id) -> str:
    return hashlib.sha256(str(remote_id or "").encode("utf-8")).hexdigest()[:10]


def torra_public_subscription_key(remote_id) -> str:
    remote_id = str(remote_id or "").strip()
    return f"torra:{_remote_ref(remote_id)}" if remote_id else ""


def torra_canonical_subscription_key(remote_id) -> str:
    remote_id = str(remote_id or "").strip()
    return f"torra:{remote_id}" if remote_id else ""


def is_torra_public_subscription_key(value) -> bool:
    key = str(value or "").strip()
    return bool(key.startswith("torra:") and PUBLIC_KEY_SUFFIX.fullmatch(key.removeprefix("torra:")))


def torra_public_storage_key(value, remote_id="") -> str:
    key = str(value or "").strip()
    if not key.startswith("torra:"):
        return key
    explicit_remote_id = str(remote_id or "").strip()
    if explicit_remote_id:
        return torra_public_subscription_key(explicit_remote_id)
    suffix = key.removeprefix("torra:")
    if not suffix or PUBLIC_KEY_SUFFIX.fullmatch(suffix):
        return key
    return torra_public_subscription_key(suffix)


def resolve_torra_subscription_key(value, rows) -> dict:
    requested = str(value or "").strip()
    if not requested.startswith("torra:") or not requested.removeprefix("torra:"):
        return {"status": "missing"}

    matches = {}
    for source in rows if isinstance(rows, list) else []:
        if not isinstance(source, dict):
            continue
        item = source.get("item") if isinstance(source.get("item"), dict) else source
        remote_id = str(
            source.get("remote_id")
            or source.get("remoteId")
            or item.get("id")
            or item.get("remote_id")
            or item.get("remoteId")
            or ""
        ).strip()
        if not remote_id:
            continue
        canonical_key = torra_canonical_subscription_key(remote_id)
        public_key = torra_public_subscription_key(remote_id)
        if requested not in {canonical_key, public_key}:
            continue
        matches[remote_id] = {
            "status": "resolved",
            "canonicalKey": canonical_key,
            "publicKey": public_key,
            "remoteId": remote_id,
            "item": dict(item),
        }
    if len(matches) > 1:
        return {"status": "conflict"}
    return next(iter(matches.values()), {"status": "missing"})


def torra_public_unit_key(value, canonical_key, public_key) -> str:
    unit_key = str(value or "").strip()
    canonical = str(canonical_key or "").strip()
    public = str(public_key or "").strip()
    if unit_key == canonical:
        return public
    if canonical and unit_key.startswith(f"{canonical}:"):
        return f"{public}{unit_key[len(canonical):]}"
    return unit_key


def torra_public_match_keys(subscription_key, unit_key) -> tuple[str, str]:
    internal_key = str(subscription_key or "").strip()
    public_key = torra_public_storage_key(internal_key)
    return public_key, torra_public_unit_key(unit_key, internal_key, public_key)


def torra_internal_unit_key(value, canonical_key, public_key) -> str:
    unit_key = str(value or "").strip()
    canonical = str(canonical_key or "").strip()
    public = str(public_key or "").strip()
    if unit_key == public:
        return canonical
    if public and unit_key.startswith(f"{public}:"):
        return f"{canonical}{unit_key[len(public):]}"
    return unit_key
