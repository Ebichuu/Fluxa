from __future__ import annotations

import calendar
import hashlib
import re
from datetime import datetime, timezone
from html import unescape

import feedparser


MAX_FEED_BYTES = 2 * 1024 * 1024


def _text(value):
    text = unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _published(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return ""


def _number(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _season_episode(title):
    text = str(title or "")
    season = episode_start = episode_end = None
    match = re.search(r"(?i)\bS(\d{1,2})\s*E(\d{1,4})(?:\s*[-~]\s*E?(\d{1,4}))?", text)
    if match:
        season = int(match.group(1))
        episode_start = int(match.group(2))
        episode_end = int(match.group(3) or match.group(2))
    else:
        season_match = re.search(r"(?i)\bS(\d{1,2})\b", text)
        if not season_match:
            season_match = re.search(r"(?i)(?:Season\s*|第\s*)(\d{1,2})(?:\s*季)", text)
        episode_match = re.search(r"第\s*(\d{1,4})(?:\s*[-~至]\s*(\d{1,4}))?\s*[集话]", text)
        if season_match:
            season = int(season_match.group(1))
        if episode_match:
            episode_start = int(episode_match.group(1))
            episode_end = int(episode_match.group(2) or episode_match.group(1))
    media_type = "tv" if episode_start is not None or season is not None else "movie"
    return media_type, season, episode_start, episode_end


def _version_summary(title):
    text = str(title or "")
    patterns = (
        r"(?i)\b(?:2160p|1080p|1080i|720p|4k|8k)\b",
        r"(?i)\b(?:web[- .]?dl|web[- .]?rip|bluray|blu[- .]?ray|remux|hdtv)\b",
        r"(?i)\b(?:hdr10\+?|dolby[ .]?vision|dv|hlg|hdr)\b",
        r"(?i)\b(?:x265|h\.265|hevc|av1|x264|h\.264)\b",
        r"(?i)\b(?:atmos|truehd|dts[- .]?hd|ddp?5\.1|aac)\b",
    )
    values = []
    for pattern in patterns:
        for match in re.findall(pattern, text):
            value = str(match).upper().replace("  ", " ")
            if value not in values:
                values.append(value)
    return " · ".join(values[:8])


def parse_private_feed(content):
    if not isinstance(content, (bytes, bytearray)):
        content = str(content or "").encode("utf-8")
    if len(content) > MAX_FEED_BYTES:
        raise ValueError("RSS 响应超过 2 MiB")
    parsed = feedparser.parse(bytes(content), resolve_relative_uris=False, sanitize_html=True)
    if parsed.bozo and not parsed.entries:
        raise ValueError("RSS/Atom 解析失败")
    items = []
    for entry in parsed.entries:
        title = _text(entry.get("title"))
        if not title:
            continue
        enclosures = entry.get("enclosures") or []
        enclosure = next((value for value in enclosures if value.get("href")), {})
        detail_url = str(entry.get("link") or "").strip()
        download_url = str(enclosure.get("href") or "").strip()
        guid = str(entry.get("id") or entry.get("guid") or download_url or detail_url).strip()
        categories = [str(value.get("term") or "").strip() for value in (entry.get("tags") or []) if value.get("term")]
        media_type, season, episode_start, episode_end = _season_episode(title)
        fingerprint_source = guid or "|".join((title, _published(entry), download_url, detail_url))
        items.append({
            "fingerprint": hashlib.sha256(fingerprint_source.encode("utf-8", errors="ignore")).hexdigest(),
            "guid": guid,
            "title": title,
            "description": _text(entry.get("summary") or entry.get("description"))[:2000],
            "published_at": _published(entry),
            "category": " / ".join(categories[:8]),
            "size_bytes": _number(enclosure.get("length") or entry.get("size")),
            "detail_url": detail_url,
            "download_url": download_url,
            "media_type": media_type,
            "season_number": season,
            "episode_start": episode_start,
            "episode_end": episode_end,
            "version_summary": _version_summary(title),
        })
    return {
        "title": _text(parsed.feed.get("title")),
        "items": items,
    }
