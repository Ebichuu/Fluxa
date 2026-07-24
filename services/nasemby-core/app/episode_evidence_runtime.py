from __future__ import annotations

import re

from app.task_exception_runtime import protection_rule


def _text(value) -> str:
    return str(value or "").strip()


def _integer(value) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _flatten_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [item for part in value for item in _flatten_strings(part)]
    if isinstance(value, dict):
        return [item for part in value.values() for item in _flatten_strings(part)]
    return []


def _range(season: int, start: int, end: int, scheme: str) -> dict:
    return {
        "seasonNumber": season,
        "episodeStart": start,
        "episodeEnd": max(start, end),
        "numberingScheme": scheme,
    }


def parse_episode_ranges(value, default_season=0) -> list[dict]:
    text = _text(value)
    ranges = []
    for match in re.finditer(
        r"(?i)(?:^|[^A-Z0-9])S(\d{1,2})\s*E(\d{1,4})(?:\s*[-~至]\s*(?:S\d{1,2}\s*)?E?(\d{1,4}))?",
        text,
    ):
        season = int(match.group(1))
        start = int(match.group(2))
        end = int(match.group(3) or start)
        ranges.append(_range(season, start, end, "special" if season == 0 else "season_episode"))
    if ranges:
        return _dedupe_ranges(ranges)

    season = _integer(default_season)
    for match in re.finditer(r"第\s*(\d{1,4})(?:\s*[-~至]\s*(\d{1,4}))?\s*[集话]", text):
        start = int(match.group(1))
        end = int(match.group(2) or start)
        ranges.append(_range(season, start, end, "special" if season == 0 else "season_episode"))
    if ranges:
        return _dedupe_ranges(ranges)

    for match in re.finditer(r"(?i)(?:^|[^A-Z0-9])(?:EP|E)[ ._-]*0*(\d{1,4})(?:[^0-9]|$)", text):
        episode = int(match.group(1))
        ranges.append(_range(0, episode, episode, "absolute"))
    return _dedupe_ranges(ranges)


def _dedupe_ranges(values: list[dict]) -> list[dict]:
    result = {}
    for value in values:
        key = (
            value["seasonNumber"],
            value["episodeStart"],
            value["episodeEnd"],
            value["numberingScheme"],
        )
        result[key] = value
    return [result[key] for key in sorted(result)]


def _explicit_row_ranges(row: dict, default_season=0) -> list[dict]:
    season = _integer(row.get("season")) or _integer(default_season)
    episode = _integer(row.get("episode"))
    if episode or row.get("episode") == 0:
        return [_range(season, episode, episode, "special" if season == 0 else "season_episode")]
    values = [
        row.get("season_episode"),
        row.get("title"),
        row.get("src"),
        row.get("dest"),
        row.get("name"),
    ]
    return _dedupe_ranges([
        item
        for value in values
        for item in parse_episode_ranges(value, default_season=season)
    ])


def _torra_ranges(row: dict) -> list[dict]:
    season = _integer(row.get("season_number"))
    explicit_numbers = sorted({
        _integer(value)
        for value in _flatten_strings(row.get("downloaded_episode_numbers"))
        if _integer(value) or str(value).strip() == "0"
    })
    ranges = [
        _range(season, episode, episode, "special" if season == 0 else "season_episode")
        for episode in explicit_numbers
    ]
    files = [
        *_flatten_strings(row.get("downloaded_file_names")),
        *_flatten_strings(row.get("downloaded_episode_files")),
        *_flatten_strings(row.get("library_file_names")),
        *_flatten_strings(row.get("library_episode_files")),
    ]
    ranges.extend(item for value in files for item in parse_episode_ranges(value, default_season=season))
    return _dedupe_ranges(ranges)


def _evidence_items(ranges, record, stage, status, reason_code="", reason_text="") -> list[dict]:
    return [{
        **episode_range,
        "stage": stage,
        "artifactKey": _text(record.get("artifactKey")),
        "source": _text(record.get("source")),
        "observedAt": _text(record.get("observedAt")),
        "matchMethod": _text(record.get("matchMethod")) or "unresolved",
        "status": status,
        "reasonCode": reason_code,
        "reasonText": reason_text,
    } for episode_range in ranges]


def _qb_status(row: dict) -> str:
    status = _text(row.get("status")).lower()
    if status == "completed":
        return "done"
    if status in {"downloading", "queued"}:
        return "active"
    if status == "stalled":
        return "blocked"
    return "waiting"


def build_episode_evidence(torra_pairs=(), qb_pairs=(), symedia_pairs=()) -> list[dict]:
    values = []
    for row, record in torra_pairs:
        ranges = _torra_ranges(row)
        if ranges:
            # Torra 的 updated_at 是订阅更新时间，不是具体文件的获取时间。
            values.extend(_evidence_items(ranges, {**record, "observedAt": ""}, "download", "done"))
    for row, record in qb_pairs:
        ranges = parse_episode_ranges(row.get("name"))
        if ranges:
            status = _qb_status(row)
            values.extend(_evidence_items(
                ranges,
                record,
                "download",
                status,
                "DOWNLOAD_STALLED" if status == "blocked" else "",
                "qB 下载任务卡住" if status == "blocked" else "",
            ))
    for row, record in symedia_pairs:
        ranges = _explicit_row_ranges(row)
        if not ranges:
            continue
        failed = row.get("status") is False
        reason_text = _text(row.get("errmsg"))
        protected = protection_rule(row.get("reasonCode"), reason_text)
        values.extend(_evidence_items(
            ranges,
            record,
            "library",
            "blocked" if failed else "done",
            protected or (_text(row.get("reasonCode")) or "SYMEDIA_LIBRARY_FAILED" if failed else ""),
            reason_text,
        ))

    deduped = {}
    for item in values:
        key = (
            item["seasonNumber"],
            item["episodeStart"],
            item["episodeEnd"],
            item["numberingScheme"],
            item["stage"],
            item["artifactKey"],
        )
        current = deduped.get(key)
        if current is None or item["observedAt"] >= current["observedAt"]:
            deduped[key] = item
    return [
        deduped[key]
        for key in sorted(deduped, key=lambda row: (row[0], row[1], row[2], row[4], row[5]))
    ]
