from __future__ import annotations

import calendar
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.parse import urlsplit

import feedparser
import requests

from app.adapters.http_utils import extract_season_episode

MAX_FEED_BYTES = 2 * 1024 * 1024
TMDB_RE = re.compile(r"(?i)(?:themoviedb\.org/(?:movie|tv)/|\btmdb(?:[_\s-]*id)?\b[^0-9]{0,12})(\d{1,10})")
IMDB_RE = re.compile(r"(?i)\b(tt\d{5,12})\b")

# Common PT torrent release-group suffix pattern: "...-GroupName" or "...@GroupName"
RELEASE_GROUP_RE = re.compile(
    r"(?:[-@])([A-Za-z0-9][A-Za-z0-9_.]{1,24})(?:\s*(?:\[[^\]]*\]|\([^)]*\)))?\s*$"
)
BRACKET_GROUP_RE = re.compile(r"\[([A-Za-z0-9][A-Za-z0-9_.]{1,24})\]\s*$")
NON_GROUP_TOKENS = {
    "2160P", "1080P", "1080I", "720P", "4K", "WEB", "DL", "WEBRIP", "BLURAY", "REMUX",
    "HDR", "HDR10", "DV", "HEVC", "H265", "H264", "X265", "X264", "DDP", "ATMOS", "AAC",
    "DTS", "FLAC", "TRUEHD", "CHS", "CHT", "ENG", "JPN", "10BIT", "8BIT", "COMPLETE",
}


def extract_release_group(title: str) -> str:
    """Extract release group tag from a PT torrent title, avoiding codec/resolution false positives."""
    cleaned = re.sub(r"\.(?:mkv|mp4|ts|iso|torrent)$", "", str(title or "").strip(), flags=re.I)
    # Strip trailing Chinese subtitle notes like " | xxx" or trailing spaces
    primary = cleaned.split("|")[0].strip()
    for regex in (RELEASE_GROUP_RE, BRACKET_GROUP_RE):
        m = regex.search(primary)
        if m:
            candidate = m.group(1).strip("._- ")
            if candidate and candidate.upper() not in NON_GROUP_TOKENS and not candidate.isdigit():
                return candidate
    return ""


def extract_version_tags(title: str) -> list[str]:
    text = str(title or "")
    patterns = (
        r"(?i)\b(?:2160p|1080p|1080i|720p|4k)\b",
        r"(?i)\b(?:web[- .]?dl|web[- .]?rip|bluray|blu[- .]?ray|remux|hdtv)\b",
        r"(?i)\b(?:dolby[ .]?vision|hdr10\+?|hdr|dv|hlg)\b",
        r"(?i)\b(?:hevc|h\.?265|x265|h\.?264|x264|av1)\b",
    )
    tags: list[str] = []
    for pat in patterns:
        for m in re.findall(pat, text):
            val = re.sub(r"\s+", " ", str(m)).upper()
            if val not in tags:
                tags.append(val)
    return tags[:6]


def fetch_and_parse_rss(url: str, *, site_id: str = "", site_name: str = "") -> list[dict[str, Any]]:
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Fluxa/2.0"})
    resp.raise_for_status()
    content = resp.content
    if len(content) > MAX_FEED_BYTES:
        raise ValueError("RSS 内容超过 2 MiB 限制")
    parsed = feedparser.parse(content, resolve_relative_uris=False, sanitize_html=True)
    host = (urlsplit(url).hostname or "").lower()
    items: list[dict[str, Any]] = []
    for entry in parsed.entries[:80]:
        title = re.sub(r"\s+", " ", unescape(str(entry.get("title") or ""))).strip()
        if not title:
            continue
        desc = re.sub(r"<[^>]+>", " ", unescape(str(entry.get("summary") or entry.get("description") or ""))).strip()
        link = str(entry.get("link") or "").strip()
        enclosures = entry.get("enclosures") or []
        dl = str(enclosures[0].get("href") or "") if enclosures and isinstance(enclosures[0], dict) else ""
        size = int(enclosures[0].get("length") or 0) if enclosures and isinstance(enclosures[0], dict) and str(enclosures[0].get("length") or "").isdigit() else 0
        blob = f"{title} {desc} {link}"
        tmdb_m = TMDB_RE.search(blob)
        imdb_m = IMDB_RE.search(blob)
        season, episode = extract_season_episode(title)
        published_ts = entry.get("published_parsed") or entry.get("updated_parsed")
        published_at = (
            datetime.fromtimestamp(calendar.timegm(published_ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")
            if published_ts
            else ""
        )
        items.append({
            "title": title,
            "siteId": site_id,
            "siteName": site_name or host,
            "siteDomain": host,
            "releaseGroup": extract_release_group(title),
            "versionTags": extract_version_tags(title),
            "tmdbId": tmdb_m.group(1) if tmdb_m else "",
            "imdbId": imdb_m.group(1).lower() if imdb_m else "",
            "seasonNumber": season,
            "episodeNumber": episode,
            "sizeBytes": size,
            "detailUrl": link,
            "downloadUrl": dl,
            "publishedAt": published_at,
        })
    return items
