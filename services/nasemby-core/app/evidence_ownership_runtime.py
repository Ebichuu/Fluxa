from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone

from app.resource_identity_runtime import artifact_key, target_key


def _text(value) -> str:
    return str(value or "").strip()


def _integer(value) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _title_key(value) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", _text(value).casefold())


def _identity_title_key(value) -> str:
    text = _text(value)
    without_year = re.sub(r"(?<!\d)(?:19|20)\d{2}(?!\d)", " ", text)
    return _title_key(without_year) or _title_key(text)


def _year(*values) -> int:
    for value in values:
        match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", _text(value))
        if match:
            return int(match.group(1))
    return 0


def _flatten_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [item for part in value for item in _flatten_strings(part)]
    if isinstance(value, dict):
        return [item for part in value.values() for item in _flatten_strings(part)]
    return []


def _file_key(value) -> str:
    basename = os.path.basename(_text(value).replace("\\", "/"))
    return _title_key(re.sub(r"\.[A-Za-z0-9]{2,5}$", "", basename, flags=re.IGNORECASE))


def _reliable_file_key(value: str) -> bool:
    return len(value) >= 8 or len(re.findall(r"[\u4e00-\u9fff]", value)) >= 4


def _season_from_text(value) -> int:
    match = re.search(r"(?i)(?:^|[.\s_-])S(?:eason)?[.\s_-]*0*(\d{1,2})(?:E\d{1,4}|[.\s_-]|$)", _text(value))
    return int(match.group(1)) if match else 0


def _qb_title_keys(name: str) -> list[str]:
    text = _text(name)
    prefix = re.split(
        r"(?i)(?:^|[.\s_-])S(?:eason)?[.\s_-]*\d{1,2}",
        text,
        maxsplit=1,
    )[0]
    values = [prefix]
    values.extend(match.group(1) for match in re.finditer(r"[\[【（(]([^\]】）)]+)[\]】）)]", text))
    leading = re.match(r"^[\s.\-_[\]【】（）()]*([\u4e00-\u9fff][\u4e00-\u9fff·、，。！？：:]*)", text)
    if leading:
        values.append(leading.group(1))
    result = []
    for value in values:
        key = _identity_title_key(value)
        if key and key not in result:
            result.append(key)
    return result


def _media_type(value, season=0) -> str:
    text = _text(value).lower()
    if text in {"tv", "series"} or "剧" in text:
        return "tv"
    if text in {"movie", "film"} or "电影" in text:
        return "movie"
    return "tv" if _integer(season) else "unknown"


def _target(subscription: dict) -> dict:
    media_type = _text(subscription.get("mediaType")).lower()
    season = _integer(subscription.get("seasonNumber"))
    return {
        "targetKey": target_key(
            media_type,
            subscription.get("tmdbId"),
            subscription.get("title"),
            season,
        ),
        "mediaType": media_type,
        "tmdbId": _text(subscription.get("tmdbId")),
        "titleKey": _identity_title_key(subscription.get("title")),
        "titleKeys": sorted({key for key in (
            _identity_title_key(subscription.get("title")),
            *(_identity_title_key(value) for value in subscription.get("aliases") or []),
        ) if key}),
        "seasonNumber": season,
        "year": _year(subscription.get("year")),
    }


def _torra_evidence(row: dict, index: int) -> dict:
    identity = _text(row.get("id")) or f"row:{index}"
    season = _integer(row.get("season_number"))
    aliases = []
    names_json = row.get("names_json")
    if names_json:
        try:
            parsed = json.loads(names_json) if isinstance(names_json, str) else names_json
        except (TypeError, ValueError):
            parsed = []
        aliases.extend(_flatten_strings(parsed))
    aliases.extend(_flatten_strings(row.get("aliases")))
    return {
        "artifactKey": artifact_key(remote_file_id=f"torra:{identity}"),
        "source": "Torra",
        "sourceIndex": index,
        "mediaType": _media_type(row.get("media_type"), season),
        "tmdbId": _text(row.get("tmdb_id")),
        "titleKey": _identity_title_key(row.get("name") or row.get("keyword")),
        "titleKeys": sorted({key for key in (
            _identity_title_key(row.get("name") or row.get("keyword")),
            *(_identity_title_key(value) for value in aliases),
        ) if key}),
        "seasonNumber": season,
        "year": _year(row.get("year"), row.get("release_year"), row.get("name"), row.get("keyword")),
        "observedAt": _text(row.get("updated_at") or row.get("created_at")),
        "fileKeys": [
            key
            for key in (
                _file_key(value)
                for value in [
                    *_flatten_strings(row.get("downloaded_file_names")),
                    *_flatten_strings(row.get("downloaded_episode_files")),
                    *_flatten_strings(row.get("library_file_names")),
                    *_flatten_strings(row.get("library_episode_files")),
                ]
            )
            if _reliable_file_key(key)
        ],
    }


def _symedia_evidence(row: dict, index: int) -> dict:
    identity = _text(row.get("id"))
    fallback = "|".join((_text(row.get("date")), _text(row.get("src")), str(index)))
    season = _integer(row.get("season")) or _season_from_text(row.get("season_episode"))
    file_values = [row.get("src"), row.get("dest")]
    files = row.get("files")
    if isinstance(files, list):
        file_values.extend(files)
    return {
        "artifactKey": artifact_key(
            remote_file_id=f"symedia:{identity}" if identity else "",
            fallback=f"symedia:{fallback}",
        ),
        "source": "Symedia",
        "sourceIndex": index,
        "mediaType": _media_type(row.get("type"), season),
        "tmdbId": _text(row.get("tmdbid")),
        "titleKey": _identity_title_key(row.get("title")),
        "seasonNumber": season,
        "year": _year(row.get("year"), row.get("release_year"), row.get("title")),
        "observedAt": _text(row.get("date")),
        "fileKeys": [
            key
            for key in (_file_key(value) for value in file_values)
            if _reliable_file_key(key)
        ],
    }


def _qb_evidence(row: dict, index: int) -> dict:
    name = _text(row.get("name"))
    season = _season_from_text(name)
    title_keys = _qb_title_keys(name)
    observed_at = ""
    seconds = 0
    for value in (row.get("completionOn"), row.get("addedOn")):
        try:
            if float(value or 0) > 0:
                seconds = value
                break
        except (TypeError, ValueError):
            continue
    try:
        observed_at = datetime.fromtimestamp(float(seconds), timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        pass
    return {
        "artifactKey": artifact_key(qb_hash=_text(row.get("hash")), fallback=f"qb:{index}:{name}"),
        "source": "qBittorrent",
        "sourceIndex": index,
        "mediaType": "tv" if season else "movie",
        "tmdbId": "",
        "titleKey": title_keys[0] if title_keys else "",
        "titleKeys": title_keys,
        "seasonNumber": season,
        "year": _year(name),
        "observedAt": observed_at,
        "fileKeys": [_file_key(name)],
    }


def _candidate(evidence: dict, target: dict) -> tuple[str, str] | None:
    evidence_type = evidence["mediaType"]
    if evidence_type != "unknown" and evidence_type != target["mediaType"]:
        return None
    evidence_tmdb = evidence["tmdbId"]
    if evidence_tmdb:
        if evidence_tmdb != target["tmdbId"]:
            return None
        if target["mediaType"] == "tv":
            evidence_season = evidence["seasonNumber"]
            target_season = target["seasonNumber"]
            if evidence_season and target_season and evidence_season != target_season:
                return None
        return "tmdb_exact", "strong"
    evidence_titles = set(evidence.get("titleKeys") or [evidence.get("titleKey")])
    target_titles = set(target.get("titleKeys") or [target.get("titleKey")])
    if not evidence_titles.intersection(target_titles):
        return None
    if target["mediaType"] == "tv":
        if not evidence["seasonNumber"] or not target["seasonNumber"]:
            return None
        if evidence["seasonNumber"] != target["seasonNumber"]:
            return None
        return "title_season_unique", "fallback"
    if target["mediaType"] == "movie":
        if not evidence["year"] or not target["year"] or evidence["year"] != target["year"]:
            return None
        return "title_year_unique", "fallback"
    return None


def _decide(evidence: dict, targets: list[dict], artifact_candidates=()) -> dict:
    candidates = {}
    for target_value in targets:
        result = _candidate(evidence, target_value)
        if result:
            candidates[target_value["targetKey"]] = result
    for target_value in artifact_candidates:
        candidates[target_value] = ("artifact_exact", "strong")
    ordered = sorted(candidates)
    if len(ordered) == 1:
        method, confidence = candidates[ordered[0]]
        owner = ordered[0]
    else:
        owner = ""
        method = "unresolved"
        confidence = "conflict" if ordered else "unlinked"
    return {
        "artifactKey": evidence["artifactKey"],
        "ownerTargetKey": owner,
        "matchMethod": method,
        "confidence": confidence,
        "conflictCandidates": ordered if len(ordered) > 1 else [],
        "observedAt": evidence["observedAt"],
        "source": evidence["source"],
    }


def _owned_torra_file_targets(evidence: dict, torra_rows: list[tuple[dict, dict]], *, exact_only=False) -> set[str]:
    file_keys = [key for key in evidence["fileKeys"] if _reliable_file_key(key)]
    owners = set()
    for torra_evidence, decision in torra_rows:
        owner = decision["ownerTargetKey"]
        if not owner:
            continue
        if any(
            file_key == torra_key if exact_only else file_key in torra_key or torra_key in file_key
            for file_key in file_keys
            for torra_key in torra_evidence["fileKeys"]
        ):
            owners.add(owner)
    return owners


def adjudicate_task_evidence(
    subscriptions: list[dict],
    torra_rows: list[dict],
    qb_tasks: list[dict],
    symedia_rows: list[dict],
) -> dict:
    targets_by_key = {}
    subscription_targets = {}
    for subscription in subscriptions:
        target_value = _target(subscription)
        targets_by_key[target_value["targetKey"]] = target_value
        subscription_targets[str(subscription.get("id") or "")] = target_value["targetKey"]
    targets = list(targets_by_key.values())
    owned = {
        key: {"torra": [], "qb": [], "symedia": [], "records": []}
        for key in targets_by_key
    }
    unowned = {"torra": [], "qb": [], "symedia": []}
    records = []

    torra_decisions = []
    for index, row in enumerate(torra_rows):
        evidence = _torra_evidence(row, index)
        decision = _decide(evidence, targets)
        torra_decisions.append((evidence, decision))
        records.append(decision)
        bucket = owned.get(decision["ownerTargetKey"])
        (bucket["torra"] if bucket else unowned["torra"]).append((row, decision))
        if bucket:
            bucket["records"].append(decision)

    for index, row in enumerate(symedia_rows):
        evidence = _symedia_evidence(row, index)
        artifact_candidates = _owned_torra_file_targets(
            evidence,
            torra_decisions,
            exact_only=True,
        )
        decision = _decide(evidence, targets, artifact_candidates)
        records.append(decision)
        bucket = owned.get(decision["ownerTargetKey"])
        (bucket["symedia"] if bucket else unowned["symedia"]).append((row, decision))
        if bucket:
            bucket["records"].append(decision)

    for index, row in enumerate(qb_tasks):
        evidence = _qb_evidence(row, index)
        artifact_candidates = _owned_torra_file_targets(evidence, torra_decisions)
        decision = _decide(evidence, targets, artifact_candidates)
        records.append(decision)
        bucket = owned.get(decision["ownerTargetKey"])
        (bucket["qb"] if bucket else unowned["qb"]).append((row, decision))
        if bucket:
            bucket["records"].append(decision)

    return {
        "subscriptionTargets": subscription_targets,
        "owned": owned,
        "unowned": unowned,
        "records": records,
        "summary": {
            "owned": sum(bool(record["ownerTargetKey"]) for record in records),
            "conflicts": sum(record["confidence"] == "conflict" for record in records),
            "unlinked": sum(record["confidence"] == "unlinked" for record in records),
        },
    }


def _legacy_same_season(expected, actual) -> bool:
    expected_number = _integer(expected)
    actual_number = _integer(actual)
    return expected is None or expected_number == 0 or actual_number == 0 or expected_number == actual_number


def _legacy_torra_index(subscription: dict, rows: list[dict]) -> int | None:
    for index, row in enumerate(rows):
        if (
            _text(row.get("tmdb_id")) == _text(subscription.get("tmdbId"))
            and _media_type(row.get("media_type")) == _text(subscription.get("mediaType"))
            and _legacy_same_season(subscription.get("seasonNumber"), row.get("season_number"))
        ):
            return index
    wanted = _title_key(subscription.get("title"))
    for index, row in enumerate(rows):
        candidate = _title_key(row.get("name") or row.get("keyword"))
        if (
            _media_type(row.get("media_type")) == _text(subscription.get("mediaType"))
            and _legacy_same_season(subscription.get("seasonNumber"), row.get("season_number"))
            and wanted
            and candidate
            and (candidate in wanted or wanted in candidate)
        ):
            return index
    return None


def _legacy_symedia_indices(subscription: dict, rows: list[dict]) -> list[int]:
    exact = [
        index
        for index, row in enumerate(rows)
        if _text(row.get("tmdbid")) == _text(subscription.get("tmdbId"))
        and _legacy_same_season(
            subscription.get("seasonNumber"),
            _integer(row.get("season")) or _season_from_text(row.get("season_episode")),
        )
    ]
    if exact:
        return exact
    wanted = _title_key(subscription.get("title"))
    return [
        index
        for index, row in enumerate(rows)
        if _legacy_same_season(
            subscription.get("seasonNumber"),
            _integer(row.get("season")) or _season_from_text(row.get("season_episode")),
        )
        and wanted
        and (candidate := _title_key(row.get("title")))
        and (candidate in wanted or wanted in candidate)
    ]


def _legacy_qb_indices(subscription: dict, torra_row: dict | None, tasks: list[dict]) -> list[int]:
    torra_keys = [
        _file_key(value)
        for value in [
            *_flatten_strings((torra_row or {}).get("downloaded_file_names")),
            *_flatten_strings((torra_row or {}).get("downloaded_episode_files")),
            *_flatten_strings((torra_row or {}).get("library_file_names")),
            *_flatten_strings((torra_row or {}).get("library_episode_files")),
        ]
        if _reliable_file_key(_file_key(value))
    ]
    by_file = [
        index
        for index, task in enumerate(tasks)
        if (task_key := _file_key(task.get("name")))
        and any(task_key in key or key in task_key for key in torra_keys)
    ]
    if by_file:
        return by_file
    wanted = _title_key(subscription.get("title"))
    return [
        index
        for index, task in enumerate(tasks)
        if wanted
        and wanted in _title_key(task.get("name"))
        and _legacy_same_season(subscription.get("seasonNumber"), _season_from_text(task.get("name")))
    ]


def compare_legacy_ownership(
    subscriptions: list[dict],
    torra_rows: list[dict],
    qb_tasks: list[dict],
    symedia_rows: list[dict],
    adjudicated: dict,
) -> dict:
    legacy_claims: dict[str, set[str]] = {}
    for subscription in subscriptions:
        target_value = _target(subscription)["targetKey"]
        torra_index = _legacy_torra_index(subscription, torra_rows)
        torra_row = torra_rows[torra_index] if torra_index is not None else None
        if torra_index is not None:
            key = _torra_evidence(torra_row, torra_index)["artifactKey"]
            legacy_claims.setdefault(key, set()).add(target_value)
        for index in _legacy_symedia_indices(subscription, symedia_rows):
            key = _symedia_evidence(symedia_rows[index], index)["artifactKey"]
            legacy_claims.setdefault(key, set()).add(target_value)
        for index in _legacy_qb_indices(subscription, torra_row, qb_tasks):
            key = _qb_evidence(qb_tasks[index], index)["artifactKey"]
            legacy_claims.setdefault(key, set()).add(target_value)

    new_owners = {
        record["artifactKey"]: record["ownerTargetKey"]
        for record in adjudicated.get("records") or []
    }
    legacy_bound = {key: owners for key, owners in legacy_claims.items() if owners}
    return {
        "legacyClaimedEvidence": len(legacy_bound),
        "legacySharedEvidence": sum(len(owners) > 1 for owners in legacy_bound.values()),
        "newOwnedEvidence": sum(bool(owner) for owner in new_owners.values()),
        "newConflictEvidence": sum(
            record.get("confidence") == "conflict"
            for record in adjudicated.get("records") or []
        ),
        "releasedEvidence": sum(
            key in legacy_bound and not new_owners.get(key)
            for key in new_owners
        ),
        "changedOwnerEvidence": sum(
            bool(new_owners.get(key))
            and key in legacy_bound
            and new_owners[key] not in legacy_bound[key]
            for key in new_owners
        ),
    }
