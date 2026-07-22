from __future__ import annotations

import hashlib
import re


def _clean(value) -> str:
    return str(value or "").strip()


def _title_key(value) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", _clean(value).casefold())


def media_key(media_type, tmdb_id="", title="") -> str:
    kind = _clean(media_type).lower() or "unknown"
    identity = _clean(tmdb_id)
    return f"{kind}:tmdb:{identity}" if identity else f"{kind}:title:{_title_key(title) or 'unknown'}"


def target_key(media_type, tmdb_id="", title="", season_number=0, episode_number=None) -> str:
    base = media_key(media_type, tmdb_id, title)
    season = int(season_number or 0)
    if episode_number is None:
        return f"{base}:season:{season}"
    return f"{base}:season:{season}:episode:{int(episode_number)}"


def artifact_key(*, qb_hash="", file_fingerprint="", remote_file_id="", fallback="") -> str:
    value = _clean(qb_hash) or _clean(file_fingerprint) or _clean(remote_file_id)
    if value:
        return f"artifact:{value}"
    digest = hashlib.sha256(_clean(fallback).encode("utf-8")).hexdigest()[:24]
    return f"artifact:anonymous:{digest}"


def chain_id(media, target, artifact_keys=()) -> str:
    # Artifact identity can change as a file moves through qB/115/Symedia;
    # the chain must remain stable for the same media target.
    source = "|".join([_clean(media), _clean(target)])
    return f"chain:{hashlib.sha256(source.encode('utf-8')).hexdigest()[:24]}"
