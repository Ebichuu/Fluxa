from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.adapters.http_utils import compact_title, extract_season_episode, sanitize_text
from app.domain.pipeline import build_adapters, collect_upstream_snapshot, merge_subscriptions
from app.storage import FluxaStore

BEIJING_TZ = timezone(timedelta(hours=8))
MEDIA_EXT_RE = re.compile(r"\.(mkv|mp4|ts|m2ts|avi|mov|flv|wmv|webm|iso)$", re.I)
SXXEXX_SPLIT_RE = re.compile(r"(?i)(?:[.\s_\-\[]+)(S(\d{1,2})\s*E(\d{1,4}))(?=$|[.\s_\-\]])")
YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")

# Promotional / collection noise tokens to strip ONLY when building TMDB search queries
PROMO_NOISE_RE = re.compile(
    r"(?i)(?:不撸酷客|英SR版|原盘正片全花絮|正片全花絮|全花絮|原盘DIY|中字DIY|DIY中字|国粤双语|国语中字|简繁中字|特效字幕|合集\d*|^\s*\d{1,3}[.\-_、\s]+)"
)
MULTI_DISC_OR_EXTRAS_RE = re.compile(
    r"(?i)\b(?:disc\s*\d+|cd\s*\d+|bd\s*\d+|bonus|extras?|featurettes?|sp\d*|ova\d*|director'?s?\s*cut|extended|unrated)\b|花絮|特典|多碟"
)
ROMAN_MAP = {"ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8}
SEQUEL_RE = re.compile(r"(?i)(?:\bpart\s*(ii|iii|iv|v|vi|[2-9])\b|\b(ii|iii|iv|v|vi)\b|(?<![a-z0-9\-])([2-9])(?=$|[\s.\(\[])|第\s*([2-9])\s*部)")
NOT_PROCESSED_TIMEOUT_MINUTES = 15


def _now_dt() -> datetime:
    return datetime.now(BEIJING_TZ)


def _now_iso() -> str:
    return _now_dt().isoformat(timespec="seconds")


def _parse_date(val: str) -> date | None:
    raw = str(val or "").strip()[:10]
    if len(raw) != 10:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _parse_iso_dt(val: str) -> datetime | None:
    raw = str(val or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=BEIJING_TZ)
    except ValueError:
        return None


def extract_sequel_number(text: str) -> int | None:
    """Extract sequel/part number (e.g. 'Hostel Part II' -> 2, '人皮客栈2' -> 2) while ignoring years & resolutions."""
    cleaned = re.sub(r"(?i)\b(?:2160p|1080p|720p|4k|h\.?264|h\.?265|x264|x265|5\.1|7\.1|u[\-\s]?17|s\d+e?\d*|(?:19|20)\d{2})\b", " ", str(text or ""))
    m = SEQUEL_RE.search(cleaned)
    if not m:
        return None
    token = next((g for g in m.groups() if g), "").strip().lower()
    if token in ROMAN_MAP:
        return ROMAN_MAP[token]
    return int(token) if token.isdigit() else None


def check_sequel_identity_conflict(
    *,
    source_name: str,
    source_year: str,
    target_title: str,
    target_original_title: str = "",
    target_year: str = "",
) -> dict[str, Any]:
    """Detect sequel/prequel or remake identity collisions (e.g. Hostel Part II 2007 vs Hostel 2005 / tmdb-1690)."""
    src_seq = extract_sequel_number(source_name)
    tgt_seq = extract_sequel_number(f"{target_title} {target_original_title}")
    reasons: list[str] = []

    if src_seq and not tgt_seq:
        reasons.append(f"源文件包含续集标识（第 {src_seq} 部），但目标作品「{target_title}」未包含对应续集编号")
    elif not src_seq and tgt_seq:
        reasons.append(f"源文件未包含续集标识，但目标作品「{target_title}」为第 {tgt_seq} 部")
    elif src_seq and tgt_seq and src_seq != tgt_seq:
        reasons.append(f"续集编号冲突：源文件为第 {src_seq} 部，目标作品为第 {tgt_seq} 部")

    if source_year and target_year and source_year.isdigit() and target_year.isdigit():
        diff = abs(int(source_year) - int(target_year))
        if diff >= 2:
            reasons.append(f"年份冲突：源文件年份 {source_year}，目标作品年份 {target_year}（相差 {diff} 年）")

    return {
        "hasConflict": bool(reasons),
        "sourceSequel": src_seq,
        "targetSequel": tgt_seq,
        "reasons": reasons,
        "warningText": "；".join(reasons),
    }


def parse_media_filename(file_name: str) -> dict[str, Any]:
    """Parse release filename while cleaning search noise AND preserving full specs, Disc/Cut markers, and arc scope."""
    raw = str(file_name or "").strip().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    ext_m = MEDIA_EXT_RE.search(raw)
    ext = ext_m.group(0) if ext_m else ""
    stem = raw[: -len(ext)] if ext else raw

    m = SXXEXX_SPLIT_RE.search(stem)
    inferred_type = "tv" if m else "movie"
    if m:
        title_part = stem[: m.start()].strip(" ._-[]")
        source_season = int(m.group(2))
        source_episode = int(m.group(3))
        specs_tail = stem[m.end() :].strip(" ._-")
    else:
        s_num, e_num = extract_season_episode(stem)
        source_season, source_episode = s_num, e_num
        if s_num or e_num:
            inferred_type = "tv"
        # For movie filenames, split before year or resolution token to preserve specs tail
        split_m = re.search(r"(?i)[.\s_\-\(]+((?:19|20)\d{2}|2160p|1080p|720p|4k|bluray|remux|web[\-\.]?dl)\b", stem)
        if split_m:
            title_part = stem[: split_m.start()].strip(" ._-[]")
            specs_tail = stem[split_m.start() :].strip(" ._-")
        else:
            title_part = stem
            specs_tail = ""

    raw_title = re.sub(r"[._]+", " ", title_part).strip()
    raw_title = re.sub(r"\s+", " ", raw_title)

    # Detect multi-disc / extras / special cuts (must NOT strip these or blindly treat as single movie!)
    special_markers = sorted({m.group(0).strip() for m in MULTI_DISC_OR_EXTRAS_RE.finditer(raw)})
    has_special_markers = bool(special_markers)

    # Clean promotional/collection noise for search queries ONLY (original raw_title stays preserved for arc_key!)
    cleaned_search_title = PROMO_NOISE_RE.sub(" ", raw_title)
    cleaned_search_title = re.sub(r"\s+", " ", cleaned_search_title).strip(" -_/|")
    arc_key = compact_title(raw_title)

    year_m = YEAR_RE.search(specs_tail or stem)
    year = year_m.group(1) if year_m else ""
    sequel_num = extract_sequel_number(raw_title)

    # Build progressive search queries (handles full Romaji, multi-alias titles, and promo-polluted names)
    # Strictly rejects pure-digit / meaningless fragments like "17" extracted from "U 17"!
    queries: list[str] = []

    def _is_meaningful_query(text: str) -> bool:
        c = re.sub(r"\s+", " ", str(text or "")).strip(" -_/|")
        if not c or re.fullmatch(r"[\d\s\-_./:]+", c):
            return False
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", c))
        if has_cjk:
            return len(re.findall(r"[\u4e00-\u9fff]", c)) >= 2
        latin_words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9'\-]*", c) if len(w) >= 2]
        return len(latin_words) >= 2 or (len(latin_words) == 1 and len(latin_words[0]) >= 4)

    def _add_q(q: str):
        c = re.sub(r"\s+", " ", str(q or "")).strip(" -_/|")
        if _is_meaningful_query(c) and c not in queries:
            queries.append(c)

    _add_q(cleaned_search_title or raw_title)
    u_norm = re.sub(r"\bU\s+(\d+)\b", r"U-\1", cleaned_search_title or raw_title, flags=re.I)
    _add_q(u_norm)

    # Split multi-alias titles separated by / 、 |
    for seg in re.split(r"[/、|]+", cleaned_search_title):
        _add_q(seg)

    # CJK extraction MUST require actual Han characters — never extract bare digits like "17"!
    cjk_segments = [s for s in re.findall(r"[\u4e00-\u9fff0-9]+", cleaned_search_title) if re.search(r"[\u4e00-\u9fff]", s)]
    cjk_part = " ".join(cjk_segments).strip()
    latin_part = " ".join(re.findall(r"[A-Za-z0-9'\-]+", u_norm)).strip()
    if cjk_part:
        _add_q(cjk_part)
    if latin_part:
        _add_q(latin_part)

    # Progressively trim trailing arc subtitle words (e.g. drop "Kesshou Member Ketteisen" -> "Shin Tennis no Oujisama U-17 World Cup")
    base_words = (latin_part or u_norm).split(" ")
    for drop in range(1, max(1, len(base_words) - 1)):
        prefix = " ".join(base_words[:-drop])
        if len(prefix.split(" ")) >= 2:
            _add_q(prefix)

    return {
        "fileName": raw,
        "inferredMediaType": inferred_type,
        "arcTitle": raw_title,
        "cleanedTitle": cleaned_search_title or raw_title,
        "arcKey": arc_key,
        "sourceSeason": source_season,
        "sourceEpisode": source_episode,
        "year": year,
        "sequelNumber": sequel_num,
        "specialMarkers": special_markers,
        "hasSpecialMarkers": has_special_markers,
        "specsTail": specs_tail,
        "ext": ext,
        "searchQueries": queries[:12],
    }


def build_spec_preserving_filename(
    parsed: dict[str, Any],
    *,
    canonical_title: str,
    target_season: int | None = None,
    target_episode: int | None = None,
    media_type: str = "tv",
    tmdb_id: str = "",
) -> str:
    """Build a standardized filename keeping original specs, Disc/Cut markers, release group, and extension intact.

    When `tmdb_id` is supplied, embeds `{tmdbid=...}` which Symedia's recognizer natively honors
    even when `/api/v1/transfer/manual` does not expose TMDB/season override request fields.
    """
    safe_title = re.sub(r'[\\/:*?"<>|]+', " ", str(canonical_title or "").strip())
    safe_title = re.sub(r"\s+", ".", safe_title).strip(".")
    tail = str(parsed.get("specsTail") or "").strip(" .")
    ext = str(parsed.get("ext") or ".mkv")

    # Ensure Disc / Cut markers from original filename are NEVER dropped if they were in title_part
    preserved_markers = [
        mk for mk in (parsed.get("specialMarkers") or [])
        if mk.lower() not in tail.lower()
    ]
    marker_str = ".".join(re.sub(r"\s+", ".", mk) for mk in preserved_markers)

    parts = [safe_title]
    if media_type == "tv" and target_season and target_episode:
        parts.append(f"S{int(target_season):02d}E{int(target_episode):02d}")
    if tmdb_id and str(tmdb_id).isdigit():
        parts.append(f"{{tmdbid={tmdb_id}}}")
    if marker_str:
        parts.append(marker_str)
    if tail:
        parts.append(tail)
    elif parsed.get("year"):
        parts.append(str(parsed["year"]))
    return ".".join(p for p in parts if p) + ext


SOURCE_PREFERENCE_PATTERNS: list[tuple[str, int, str]] = [
    (r"(?i)(?<![A-Za-z0-9])(?:bdmv|bdremux|remux|bdrip|blu[- .]?ray)(?![A-Za-z0-9])", 5, "BD/BDRip"),
    (r"(?i)(?<![A-Za-z0-9])(?:crunchyroll|cr)(?![A-Za-z0-9])", 4, "CR"),
    (r"(?i)(?<![A-Za-z0-9])(?:b-?global|bglobal)(?![A-Za-z0-9])", 4, "B-Global"),
    (r"(?i)(?<![A-Za-z0-9])(?:amzn|amazon)(?![A-Za-z0-9])", 4, "AMZN"),
    (r"(?i)(?<![A-Za-z0-9])(?:netflix|nf)(?![A-Za-z0-9])", 4, "NF"),
    (r"(?i)(?<![A-Za-z0-9])(?:abema|at-?x)(?![A-Za-z0-9])", 4, "ABEMA/AT-X"),
    (r"(?i)(?<![A-Za-z0-9])(?:baha|bahamut)(?![A-Za-z0-9])", 3, "Baha"),
    (r"(?i)(?<![A-Za-z0-9])(?:linetv|line[- .]?tv)(?![A-Za-z0-9])", 2, "LINETV"),
    (r"(?i)(?<![A-Za-z0-9])(?:kktv|myvideo|friday|viu)(?![A-Za-z0-9])", 2, "台/港流媒体"),
    (r"(?i)(?<![A-Za-z0-9])(?:web[- .]?dl|web[- .]?rip)(?![A-Za-z0-9])", 2, "WEB-DL"),
    (r"(?i)(?<![A-Za-z0-9])(?:hdtv|tvrip)(?![A-Za-z0-9])", 1, "HDTV/TVRip"),
]


def detect_release_source_preference(text: str) -> tuple[str, int]:
    """Identify release/streaming source tag & preference rank (e.g. CR=4 > LINETV=2)."""
    s = str(text or "")
    for pat, rank, label in SOURCE_PREFERENCE_PATTERNS:
        if re.search(pat, s):
            return label, rank
    return "", 0


def _basename(path_or_name: str) -> str:
    return str(path_or_name or "").strip().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def _sa_record_matches_target_episode(
    rec: dict[str, Any],
    *,
    media_type: str,
    tmdb_id: str,
    target_season: int | None,
    target_episode: int | None,
) -> bool:
    if str(rec.get("tmdbId") or "") != str(tmdb_id):
        return False
    if media_type == "movie":
        return rec.get("mediaType") == "movie"
    if not target_season or not target_episode:
        return False
    expected = f"S{int(target_season):02d}E{int(target_episode):02d}"
    blob = f"{rec.get('seasonEpisode', '')} {rec.get('seasons', '')}{rec.get('episodes', '')} {rec.get('dest', '')}".upper()
    if expected in blob:
        return True
    s_str = str(rec.get("seasons") or "").strip().upper()
    e_str = str(rec.get("episodes") or "").strip().upper()
    return (
        s_str in {f"S{int(target_season):02d}", f"S{int(target_season)}", str(int(target_season))}
        and e_str in {f"E{int(target_episode):02d}", f"E{int(target_episode)}", str(int(target_episode))}
    )


def _parse_sa_dt(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw[:19], fmt).replace(tzinfo=BEIJING_TZ)
        except ValueError:
            continue
    return _parse_iso_dt(raw)


def _parent_dir(path_str: str) -> str:
    clean = str(path_str or "").strip().replace("\\", "/")
    return clean.rsplit("/", 1)[0] if "/" in clean else ""


def _release_group_tail(file_name: str) -> str:
    stem = re.sub(r"\.(?:mkv|mp4|ts|iso)$", "", _basename(file_name), flags=re.I)
    m = re.search(r"[-@]([A-Za-z0-9][A-Za-z0-9_]{1,20})$", stem)
    return m.group(1).casefold() if m else ""


def _record_season_episode(rec: dict[str, Any]) -> tuple[int | None, int | None]:
    blob = f"{rec.get('seasonEpisode', '')} {rec.get('seasons', '')}{rec.get('episodes', '')} {rec.get('dest', '')} {rec.get('fileName', '')}"
    m = re.search(r"(?i)S(\d{1,2})\s*E(\d{1,4})", blob)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def find_corroborated_sa_sibling_archive(
    *,
    record: dict[str, Any],
    parsed: dict[str, Any],
    sa_history: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find a corroborated SA archived sibling of the SAME episode under an alternate Romaji/alias title.

    Example:
      Failed:  Kuroka.de.Seijo.ni.Hyou.S02E09...Baha...-ADWeb.mp4 (00:30:18)
      Archived: Kizu.darake.Seijo.yori.Houfuku.wo.Komete.S02E09...LINETV...-ADWeb.mp4 (00:31:37, TMDB #293124 S02E09)
    Requires exact SxxEyy match + valid TMDB ID + concrete corroborating evidence (shared distinctive Romaji/CJK token
    OR same release group + same source folder within 30 minutes). Never matches unrelated shows!
    """
    src_s = parsed.get("sourceSeason")
    src_e = parsed.get("sourceEpisode")
    if not src_s or not src_e:
        return None

    fail_base = _basename(record.get("fileName") or record.get("src") or "").casefold()
    fail_dt = _parse_sa_dt(str(record.get("date") or ""))
    fail_group = _release_group_tail(fail_base)
    fail_dir = _parent_dir(str(record.get("src") or "")).casefold()
    fail_lat, fail_cjk = _distinctive_tokens(parsed.get("cleanedTitle") or parsed.get("arcTitle") or "")
    fail_distinct_lat = {t for t in fail_lat if len(t) >= 4}

    best: dict[str, Any] | None = None
    best_score = 0

    for rec in sa_history or []:
        if rec.get("outcome") not in {"archived", "replaced"}:
            continue
        tmdb_id = str(rec.get("tmdbId") or "").strip()
        if not tmdb_id.isdigit():
            continue
        sib_base = _basename(rec.get("fileName") or rec.get("src") or "").casefold()
        if fail_base and sib_base == fail_base:
            continue

        rec_s, rec_e = _record_season_episode(rec)
        if rec_s != int(src_s) or rec_e != int(src_e):
            continue

        sib_parsed = parse_media_filename(rec.get("fileName") or _basename(rec.get("src") or ""))
        sib_lat, sib_cjk = _distinctive_tokens(
            f"{sib_parsed.get('cleanedTitle', '')} {rec.get('title', '')}"
        )
        shared_lat = sorted(fail_distinct_lat & {t for t in sib_lat if len(t) >= 4})
        shared_cjk = [sc for sc in fail_cjk for cc in sib_cjk if sc in cc or cc in sc]

        sib_group = _release_group_tail(sib_base)
        same_group = bool(fail_group and sib_group and fail_group == sib_group)
        sib_dir = _parent_dir(str(rec.get("src") or "")).casefold()
        same_dir = bool(fail_dir and sib_dir and fail_dir == sib_dir)

        sib_dt = _parse_sa_dt(str(rec.get("date") or ""))
        delta_min: float | None = None
        if fail_dt and sib_dt:
            delta_min = abs((sib_dt - fail_dt).total_seconds()) / 60.0

        # Strict corroboration gate:
        # 1) Shared distinctive title token (e.g. 'seijo') within 48h (or undated), OR
        # 2) Same release group + same folder + exact SxxEyy archived within 30 minutes!
        corroborated = False
        reasons: list[str] = []
        score = 0
        if (shared_lat or shared_cjk) and (delta_min is None or delta_min <= 48 * 60):
            corroborated = True
            score += 45 + len(shared_lat) * 10
            toks = shared_cjk[:2] or shared_lat[:3]
            reasons.append(f"同集 S{src_s:02d}E{src_e:02d} 别名包含共同特征词「{'/'.join(toks)}」")
        if same_group and delta_min is not None and delta_min <= 30:
            if same_dir or shared_lat or shared_cjk:
                corroborated = True
                score += 35
                mins_text = f"{max(1, round(delta_min))} 分钟" if delta_min >= 0.5 else "1 分钟内"
                reasons.append(f"相隔 {mins_text} 的同发布组（{fail_group.upper()}）同目录归档记录")
        elif same_group and corroborated:
            score += 15
            reasons.append(f"同发布组（{fail_group.upper()}）")
        if delta_min is not None and delta_min <= 60 and corroborated and not any("相隔" in r for r in reasons):
            score += 15
            reasons.append(f"归档时间仅差 {max(1, round(delta_min))} 分钟")

        if corroborated and score > best_score:
            best_score = score
            best = {
                "record": rec,
                "mediaType": rec.get("mediaType") or "tv",
                "tmdbId": tmdb_id,
                "targetSeason": rec_s,
                "targetEpisode": rec_e,
                "score": score,
                "reason": f"同集 SA 归档关联命中（TMDB #{tmdb_id} S{rec_s:02d}E{rec_e:02d}）：{'，'.join(reasons)}",
            }

    return best


def detect_existing_version_for_target(
    *,
    media_type: str,
    tmdb_id: str,
    target_season: int | None,
    target_episode: int | None,
    failed_file_name: str,
    emby: dict[str, Any],
    sa_history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Check BOTH Emby AND SA successful archive history for an existing version of the target movie/episode.

    Distinguishes:
      - `covered_preferred` (`noRecoveryNeeded=True`): existing is BD/BDRip over WEB-DL, OR existing CR over fallback LINETV.
      - `existing_needs_compare` (`exists=True, noRecoveryNeeded=False, needsComparison=True`): same episode IS already
        in library (e.g. failed Baha vs archived LINETV), so it is NOT missing — but Fluxa refuses to declare which
        streaming platform is better by platform name alone!
    """
    failed_base = _basename(failed_file_name).casefold()
    failed_src_tag, failed_rank = detect_release_source_preference(failed_file_name)
    found_items: list[dict[str, Any]] = []
    sources_seen: set[str] = set()

    # 1. Check Emby library details (including path/fileName!)
    if media_type == "movie":
        m_info = (emby.get("movies") or {}).get(str(tmdb_id))
        if m_info:
            sources_seen.add("emby")
            fname = m_info.get("fileName") or _basename(m_info.get("path") or "") or m_info.get("title") or ""
            tag, rank = detect_release_source_preference(f"{fname} {m_info.get('path') or ''}")
            found_items.append({
                "origin": "emby",
                "fileName": fname,
                "path": m_info.get("path") or "",
                "sourceTag": tag,
                "rank": rank,
            })
    elif target_season and target_episode:
        ep_label = f"S{int(target_season):02d}E{int(target_episode):02d}"
        ep_details = ((emby.get("episodeDetails") or {}).get(str(tmdb_id)) or {}).get(ep_label) or []
        if ep_details:
            sources_seen.add("emby")
            for ed in ep_details:
                fname = ed.get("fileName") or _basename(ed.get("path") or "") or ""
                if failed_base and fname.casefold() == failed_base:
                    continue
                tag, rank = detect_release_source_preference(f"{fname} {ed.get('path') or ''}")
                found_items.append({
                    "origin": "emby",
                    "fileName": fname,
                    "path": ed.get("path") or "",
                    "sourceTag": tag,
                    "rank": rank,
                })
        else:
            eps_set = set(tuple(pair) for pair in (emby.get("episodes") or {}).get(str(tmdb_id), []))
            if (int(target_season), int(target_episode)) in eps_set:
                sources_seen.add("emby")
                found_items.append({
                    "origin": "emby",
                    "fileName": f"Emby {ep_label}",
                    "path": "",
                    "sourceTag": "",
                    "rank": 0,
                })

    # 2. Check SA successful archive history for the SAME TMDB + target season/episode (excluding this failed file!)
    for rec in sa_history or []:
        if rec.get("outcome") not in {"archived", "replaced"}:
            continue
        if not _sa_record_matches_target_episode(
            rec,
            media_type=media_type,
            tmdb_id=tmdb_id,
            target_season=target_season,
            target_episode=target_episode,
        ):
            continue
        rec_src_base = _basename(rec.get("src") or rec.get("fileName") or "")
        if failed_base and rec_src_base.casefold() == failed_base:
            continue
        sources_seen.add("symedia")
        combined_name = f"{rec_src_base} {rec.get('dest') or ''}"
        tag, rank = detect_release_source_preference(combined_name)
        found_items.append({
            "origin": "symedia",
            "recordId": rec.get("id") or "",
            "fileName": rec_src_base or _basename(rec.get("dest") or ""),
            "path": rec.get("dest") or rec.get("src") or "",
            "sourceTag": tag,
            "rank": rank,
        })

    if not found_items:
        return {
            "exists": False,
            "noRecoveryNeeded": False,
            "needsComparison": False,
            "badgeLabel": "",
            "shortBanner": "",
            "source": "",
            "existingSourceTag": "",
            "failedSourceTag": failed_src_tag,
            "existingFileName": "",
            "existingPath": "",
            "summary": "",
        }

    # Prefer the item with richest source metadata / highest clear tier
    best = max(found_items, key=lambda x: (x["rank"], bool(x["sourceTag"]), bool(x["fileName"])))
    existing_tag = best["sourceTag"]
    src_origin = "+".join(sorted(sources_seen))
    scope_text = f"TMDB #{tmdb_id} S{int(target_season):02d}E{int(target_episode):02d}" if (media_type == "tv" and target_season and target_episode) else f"TMDB #{tmdb_id}"
    failed_desc = f"{failed_src_tag}版" if failed_src_tag else "当前版本"
    existing_desc = f"{existing_tag}版" if existing_tag else "其他版本"
    file_hint = f"（{best['fileName']}）" if best["fileName"] else ""

    # Only auto-declare "已有版本，无需补库" when existing clearly covers preference:
    #   - BD/BDRip already covers WEB-DL/streaming/HDTV, OR
    #   - Explicit user-preferred CR already covers fallback LINETV!
    # For other streaming platform pairs (e.g. failed Baha vs existing LINETV), DO NOT judge better/worse by platform name alone!
    clearly_covered = (
        (existing_tag == "BD/BDRip" and failed_src_tag != "BD/BDRip")
        or (existing_tag == "CR" and failed_src_tag == "LINETV")
    )

    if clearly_covered:
        short_banner = f"已有版本，无需补库（同集已有{existing_desc}入库）"
        summary = f"已有版本，无需补库：{scope_text} 已存在符合偏好的 {existing_desc}{file_hint}，无需继续恢复 {failed_desc}。"
        badge_label = "已有版本，无需补库"
    else:
        short_banner = f"{failed_desc}识别失败；同集已有{existing_desc}入库"
        summary = (
            f"{failed_desc}识别失败；同集（{scope_text}）已有{existing_desc}入库{file_hint}，"
            "该集未缺库；两版谁更好需比较实际码率/字幕/音轨，不能仅凭平台名判断。"
        )
        badge_label = f"同集已有{existing_desc}入库（非缺库）"

    return {
        "exists": True,
        "noRecoveryNeeded": clearly_covered,
        "needsComparison": not clearly_covered,
        "badgeLabel": badge_label,
        "shortBanner": short_banner,
        "source": src_origin,
        "existingSourceTag": existing_tag,
        "failedSourceTag": failed_src_tag,
        "existingFileName": best["fileName"],
        "existingPath": best["path"],
        "summary": summary,
    }


def check_local_file_status(src_path: str) -> dict[str, Any]:
    clean = str(src_path or "").strip()
    if not clean:
        return {"checked": False, "exists": None, "sizeBytes": None, "detail": "SA 记录未提供源文件路径，无法核对文件存在"}
    p = Path(clean)
    if p.is_file():
        try:
            size = p.stat().st_size
            return {"checked": True, "exists": True, "sizeBytes": size, "detail": f"本地挂载路径已确认存在（{size} 字节）"}
        except OSError:
            pass
    return {
        "checked": False,
        "exists": None,
        "sizeBytes": None,
        "detail": f"容器内未挂载该云盘路径（{clean}），实物存在以 115 / SA 为准（未确认）",
    }


IDENTITY_STOPWORDS = {
    "the", "a", "an", "of", "no", "in", "on", "to", "for", "and", "or", "part", "season", "episode", "movie", "film",
}


def _distinctive_tokens(text: str) -> tuple[set[str], set[str]]:
    """Return (latin_tokens, cjk_chunks) excluding bare numbers and stopwords."""
    norm_u = re.sub(r"\bU\s+(\d+)\b", r"U-\1", str(text or ""), flags=re.I)
    latin = {
        tok.lower()
        for tok in re.findall(r"[A-Za-z][A-Za-z0-9'\-]*", norm_u)
        if len(tok) >= 2 and tok.lower() not in IDENTITY_STOPWORDS
    }
    cjk = {seg for seg in re.findall(r"[\u4e00-\u9fff]{2,}", str(text or ""))}
    return latin, cjk


def evaluate_candidate_identity(
    detail: dict[str, Any],
    *,
    parsed: dict[str, Any],
    matched_queries: list[str],
    trusted_source: bool = False,
) -> dict[str, Any]:
    """Verify work-identity relevance BEFORE year/season/episode scoring.

    Prevents unrelated shows (e.g. 'Daha 17' / '代号17' matching only a bare digit '17' or year '2026')
    from ever entering executable recovery proposals.
    """
    if trusted_source:
        return {"relevant": True, "score": 50, "reason": "由人工指定的 TMDB ID 或已确认篇章映射直接锁定"}

    src_title = parsed.get("cleanedTitle") or parsed.get("arcTitle") or ""
    src_latin, src_cjk = _distinctive_tokens(src_title)
    src_compact = compact_title(src_title)

    cand_names = [
        str(detail.get("title") or ""),
        str(detail.get("originalTitle") or ""),
        *[str(a) for a in (detail.get("alternativeTitles") or [])],
    ]
    cand_names = [n.strip() for n in cand_names if n.strip()]

    # 1. Direct CJK overlap check
    best_overlap_tokens: set[str] = set()
    matched_name = ""
    for name in cand_names:
        c_compact = compact_title(name)
        if len(c_compact) >= 2 and len(src_compact) >= 2 and (c_compact in src_compact or src_compact in c_compact):
            return {"relevant": True, "score": 60, "reason": f"作品名称与「{name}」直接命中"}
        c_latin, c_cjk = _distinctive_tokens(name)
        if src_cjk and c_cjk:
            for sc in src_cjk:
                for cc in c_cjk:
                    if sc in cc or cc in sc:
                        return {"relevant": True, "score": 55, "reason": f"中文片名片段「{cc}」与源文件匹配"}
        overlap = src_latin & c_latin
        if len(overlap) > len(best_overlap_tokens):
            best_overlap_tokens = overlap
            matched_name = name

    # 2. Meaningful non-numeric Latin/Romaji token overlap (e.g. 'u-17', 'world', 'cup' in '新テニスの王子様 U-17 WORLD CUP')
    if len(best_overlap_tokens) >= 2:
        tokens_text = " / ".join(sorted(best_overlap_tokens)[:5])
        return {
            "relevant": True,
            "score": min(55, 25 + len(best_overlap_tokens) * 10),
            "reason": f"作品名称「{matched_name}」命中关键标识词：{tokens_text}",
        }

    # Single long distinctive token match ONLY if candidate title itself is short (1-2 Latin words) & matches exact distinctive word
    if len(best_overlap_tokens) == 1:
        tok = next(iter(best_overlap_tokens))
        cand_lat, _ = _distinctive_tokens(matched_name)
        if len(tok) >= 5 and len(cand_lat) <= 2:
            return {
                "relevant": True,
                "score": 30,
                "reason": f"作品名称「{matched_name}」命中特征词「{tok}」",
            }

    return {"relevant": False, "score": 0, "reason": "作品标题与源文件无可靠身份关联"}


def _evaluate_tv_candidate(
    detail: dict[str, Any],
    tmdb_adapter: Any,
    *,
    parsed: dict[str, Any],
    failed_date: date | None,
    identity: dict[str, Any],
) -> list[dict[str, Any]]:
    if not identity.get("relevant"):
        return []
    proposals: list[dict[str, Any]] = []
    source_s = parsed.get("sourceSeason") or 1
    source_e = parsed.get("sourceEpisode") or 1
    file_year = parsed.get("year") or ""
    tmdb_id = str(detail["tmdbId"])
    id_score = int(identity.get("score") or 0)
    id_reason = str(identity.get("reason") or "")

    seasons = [s for s in (detail.get("seasons") or []) if int(s.get("seasonNumber") or 0) > 0]
    for s in seasons:
        s_num = int(s["seasonNumber"])
        s_name = str(s.get("name") or f"第 {s_num} 季")
        s_air = str(s.get("airDate") or "")
        ep_count = int(s.get("episodeCount") or 0)
        if ep_count > 0 and source_e > ep_count:
            continue

        aux_score = 0
        reasons: list[str] = [id_reason] if id_reason else []
        if file_year and s_air.startswith(file_year):
            aux_score += 35
            reasons.append(f"第 {s_num} 季首播 {s_air} 吻合文件名年份 {file_year}")
        elif file_year and s_air:
            try:
                if abs(int(s_air[:4]) - int(file_year)) <= 1:
                    aux_score += 10
            except ValueError:
                pass

        ep_match = None
        if len(seasons) <= 6 or aux_score > 0 or s_num == source_s:
            eps = tmdb_adapter.get_season_episodes(tmdb_id, s_num)
            ep_match = next((ep for ep in eps if ep["episodeNumber"] == source_e), None)
            if ep_match:
                ep_air_str = ep_match.get("airDate") or ""
                ep_air = _parse_date(ep_air_str)
                if ep_air and failed_date:
                    delta = abs((failed_date - ep_air).days)
                    if delta == 0:
                        aux_score += 60
                        reasons.append(f"S{s_num:02d}E{source_e:02d}《{ep_match['name']}》播出日 {ep_air_str} 与失败日期完全一致")
                    elif delta <= 3:
                        aux_score += 45
                        reasons.append(f"S{s_num:02d}E{source_e:02d}《{ep_match['name']}》播出日 {ep_air_str} 与失败日期仅差 {delta} 天")
                    elif delta <= 14:
                        aux_score += 25
                        reasons.append(f"S{s_num:02d}E{source_e:02d}《{ep_match['name']}》播出日 {ep_air_str} 接近失败日期")
                elif ep_air_str and file_year and ep_air_str.startswith(file_year):
                    aux_score += 20
                    reasons.append(f"S{s_num:02d}E{source_e:02d}《{ep_match['name']}》播出于 {ep_air_str}")

        if aux_score == 0 and s_num == source_s:
            aux_score = 5
            reasons.append(f"同季号候选 S{s_num:02d}E{source_e:02d}")

        if aux_score > 0:
            total_score = id_score + aux_score
            proposals.append({
                "mediaType": "tv",
                "tmdbId": tmdb_id,
                "canonicalTitle": detail["title"],
                "originalTitle": detail.get("originalTitle") or "",
                "year": detail.get("year") or "",
                "posterUrl": detail.get("posterUrl") or "",
                "sourceSeason": source_s,
                "sourceEpisode": source_e,
                "targetSeason": s_num,
                "targetEpisode": source_e,
                "seasonName": s_name,
                "seasonAirDate": s_air,
                "episodeName": (ep_match or {}).get("name") or "",
                "episodeAirDate": (ep_match or {}).get("airDate") or "",
                "seasonChanged": s_num != source_s,
                "sequelConflict": {"hasConflict": False, "reasons": [], "warningText": ""},
                "identityScore": id_score,
                "score": total_score,
                "confidence": "high" if total_score >= 90 else "medium" if total_score >= 55 else "low",
                "reasons": reasons,
                "suggestedFileName": build_spec_preserving_filename(
                    parsed,
                    canonical_title=detail["title"],
                    target_season=s_num,
                    target_episode=source_e,
                    media_type="tv",
                    tmdb_id=tmdb_id,
                ),
            })

    proposals.sort(key=lambda x: x["score"], reverse=True)
    return proposals


def _evaluate_movie_candidate(detail: dict[str, Any], *, parsed: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any] | None:
    if not identity.get("relevant"):
        return None
    conflict = check_sequel_identity_conflict(
        source_name=parsed["arcTitle"],
        source_year=parsed.get("year") or "",
        target_title=detail["title"],
        target_original_title=detail.get("originalTitle") or "",
        target_year=detail.get("year") or "",
    )
    id_score = int(identity.get("score") or 0)
    score = id_score + 20
    reasons: list[str] = [identity["reason"]] if identity.get("reason") else []
    if parsed.get("year") and detail.get("year") == parsed["year"]:
        score += 35
        reasons.append(f"电影年份 {detail['year']} 与源文件一致")
    if conflict["hasConflict"]:
        score = max(5, score - 50)
        reasons.append(f"⚠️ {conflict['warningText']}")
    else:
        reasons.append("续集编号与年份核对一致")

    return {
        "mediaType": "movie",
        "tmdbId": str(detail["tmdbId"]),
        "canonicalTitle": detail["title"],
        "originalTitle": detail.get("originalTitle") or "",
        "year": detail.get("year") or "",
        "posterUrl": detail.get("posterUrl") or "",
        "sourceSeason": None,
        "sourceEpisode": None,
        "targetSeason": None,
        "targetEpisode": None,
        "seasonName": "",
        "seasonAirDate": "",
        "episodeName": "",
        "episodeAirDate": "",
        "seasonChanged": False,
        "sequelConflict": conflict,
        "identityScore": id_score,
        "score": score,
        "confidence": "low" if conflict["hasConflict"] else ("high" if score >= 85 else "medium"),
        "reasons": reasons,
        "suggestedFileName": build_spec_preserving_filename(
            parsed,
            canonical_title=detail["title"],
            media_type="movie",
            tmdb_id=str(detail["tmdbId"]),
        ),
    }


def diagnose_sa_failure(
    *,
    record: dict[str, Any],
    settings: dict[str, Any],
    store: FluxaStore,
    manual_tmdb_id: str = "",
    manual_media_type: str = "",
    adapters: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure read-only diagnosis: never moves files, never submits SA archive jobs."""
    adapters = adapters or build_adapters(settings)
    snapshot = snapshot or collect_upstream_snapshot(adapters)

    file_name = record.get("fileName") or record.get("title") or ""
    src_path = record.get("src") or ""
    failed_date = _parse_date(str(record.get("date") or ""))
    parsed = parse_media_filename(file_name)
    file_check = check_local_file_status(src_path)

    target_type = manual_media_type if manual_media_type in {"movie", "tv"} else parsed["inferredMediaType"]
    saved_mapping = (
        store.find_sa_mapping(parsed["arcKey"], parsed["sourceSeason"])
        if parsed["arcKey"] and parsed["sourceSeason"]
        else None
    )

    sa_tasks = adapters["symedia"].list_transfer_tasks() if adapters["symedia"].configured else []
    sa_caps = adapters["symedia"].probe_recovery_capabilities() if adapters["symedia"].configured else {}

    tmdb = adapters["tmdb"]
    candidate_meta: dict[tuple[str, str], dict[str, Any]] = {}

    def _add_cand(mt: str, cid: str, *, query: str = "", trusted: bool = False, corroborated_reason: str = ""):
        c = str(cid or "").strip()
        if mt not in {"movie", "tv"} or not c.isdigit():
            return
        entry = candidate_meta.setdefault((mt, c), {"queries": [], "trusted": False, "corroboratedReason": ""})
        if query and query not in entry["queries"]:
            entry["queries"].append(query)
        if trusted:
            entry["trusted"] = True
        if corroborated_reason and not entry["corroboratedReason"]:
            entry["corroboratedReason"] = corroborated_reason

    if manual_tmdb_id and str(manual_tmdb_id).isdigit():
        _add_cand(target_type, manual_tmdb_id, trusted=True)
    if saved_mapping:
        _add_cand(saved_mapping["mediaType"], saved_mapping["tmdbId"], trusted=True)

    # Check corroborated SA sibling archived record (e.g. same S02E09 archived 1 min later under alternate Romaji alias!)
    sa_history = snapshot.get("saHistory") or (
        adapters["symedia"].list_transfer_history(count=150) if adapters["symedia"].configured else []
    )
    sibling_match = find_corroborated_sa_sibling_archive(record=record, parsed=parsed, sa_history=sa_history)
    if sibling_match and not manual_tmdb_id:
        _add_cand(
            sibling_match["mediaType"],
            sibling_match["tmdbId"],
            query=f"同集SA归档:{sibling_match['record'].get('fileName') or sibling_match['tmdbId']}",
            corroborated_reason=sibling_match["reason"],
        )

    # Run ALL meaningful progressive title queries (never break prematurely on raw unverified hit count!)
    searched_queries: list[dict[str, Any]] = []
    if tmdb.configured and not manual_tmdb_id:
        for q in parsed["searchQueries"]:
            hits = tmdb.search(q)
            typed_hits = [h for h in hits if h["mediaType"] == target_type]
            searched_queries.append({"query": q, "hitCount": len(typed_hits)})
            for h in typed_hits[:4]:
                _add_cand(h["mediaType"], h["tmdbId"], query=q)

    # Also cross-check user's existing MP Neo / Torra subscriptions
    prefs = store.list_preferences()
    src_latin, src_cjk = _distinctive_tokens(parsed["cleanedTitle"])
    for sub in merge_subscriptions(snapshot, prefs):
        if not sub["tmdbId"]:
            continue
        sub_lat, sub_cjk = _distinctive_tokens(sub["title"])
        if (src_cjk and any(sc in cc or cc in sc for sc in src_cjk for cc in sub_cjk)) or len(src_latin & sub_lat) >= 2:
            _add_cand(sub["mediaType"], sub["tmdbId"], query=f"已有订阅:{sub['title']}")

    proposals: list[dict[str, Any]] = []
    filtered_out_count = 0
    for (mt, cid), meta in list(candidate_meta.items())[:12]:
        detail = tmdb.get_details(mt, cid) if tmdb.configured else None
        if not detail:
            continue
        identity = evaluate_candidate_identity(
            detail,
            parsed=parsed,
            matched_queries=meta["queries"],
            trusted_source=meta["trusted"],
        )
        if not identity["relevant"] and meta.get("corroboratedReason"):
            identity = {"relevant": True, "score": 45, "reason": meta["corroboratedReason"]}
        if not identity["relevant"]:
            filtered_out_count += 1
            continue
        if mt == "tv":
            proposals.extend(_evaluate_tv_candidate(detail, tmdb, parsed=parsed, failed_date=failed_date, identity=identity))
        else:
            m_prop = _evaluate_movie_candidate(detail, parsed=parsed, identity=identity)
            if m_prop:
                proposals.append(m_prop)

    proposals.sort(key=lambda x: x["score"], reverse=True)

    emby = snapshot.get("emby") or {}
    for prop in proposals:
        existing_ver = detect_existing_version_for_target(
            media_type=prop["mediaType"],
            tmdb_id=prop["tmdbId"],
            target_season=prop.get("targetSeason"),
            target_episode=prop.get("targetEpisode"),
            failed_file_name=file_name,
            emby=emby,
            sa_history=sa_history,
        )
        prop["existingVersion"] = existing_ver
        prop["alreadyInEmby"] = bool(existing_ver["exists"])
        if existing_ver["exists"] and existing_ver["summary"]:
            prop["reasons"] = [existing_ver["summary"], *prop["reasons"]]

    top = proposals[0] if proposals else None
    top_existing = (top or {}).get("existingVersion") or {"exists": False, "noRecoveryNeeded": False, "summary": ""}
    no_recovery_needed = bool(top_existing.get("noRecoveryNeeded"))
    has_conflict = bool(top and (top.get("sequelConflict") or {}).get("hasConflict"))
    can_auto_apply = bool(
        saved_mapping
        and saved_mapping.get("autoApply")
        and parsed["sourceSeason"] == saved_mapping["sourceSeason"]
        and parsed["sourceEpisode"] is not None
        and not parsed["hasSpecialMarkers"]
        and not has_conflict
        and not top_existing.get("exists")
    )
    requires_confirm = True
    if can_auto_apply:
        requires_confirm = False

    latest_job = store.get_latest_sa_recovery_for_record(str(record.get("id") or ""))
    recognition_status = "matched" if proposals else "unreliable"
    if not proposals:
        status_message = "无法可靠识别：未找到与源文件作品身份可靠匹配的 TMDB 候选（已过滤无关作品），请手动输入 TMDB ID 核对季集"
    elif no_recovery_needed:
        status_message = str(top_existing.get("summary") or "已有版本，无需补库")
        # Auto-mark this stale failure record as covered by existing preferred version so Home 「异常待办」 ignores it by default
        rec_id_str = str(record.get("id") or "").strip()
        if rec_id_str:
            store.set_cache(f"sa_covered:{rec_id_str}", {
                "noRecoveryNeeded": True,
                "existingSourceTag": top_existing.get("existingSourceTag") or "",
                "existingFileName": top_existing.get("existingFileName") or "",
                "summary": status_message,
            })
    elif top_existing.get("needsComparison"):
        status_message = str(top_existing.get("summary") or "")
    else:
        status_message = ""

    return {
        "record": {
            "id": str(record.get("id") or ""),
            "fileName": file_name,
            "src": src_path,
            "date": str(record.get("date") or ""),
            "outcome": record.get("outcome") or "identify_failed",
            "reasonText": record.get("reasonText") or "没有查询到媒体信息",
            "fileSize": record.get("fileSize"),
        },
        "recognitionStatus": recognition_status,
        "statusMessage": status_message,
        "noRecoveryNeeded": no_recovery_needed,
        "existingVersion": top_existing if top_existing.get("exists") else None,
        "filteredOutCount": filtered_out_count,
        "parsed": parsed,
        "fileCheck": file_check,
        "searchedQueries": searched_queries,
        "savedMapping": saved_mapping,
        "canAutoApply": can_auto_apply,
        "requiresConfirm": requires_confirm,
        "proposals": proposals[:8],
        "saTransferTasks": sa_tasks,
        "saCapabilities": sa_caps,
        "latestJob": latest_job,
    }


def execute_sa_recovery(
    *,
    payload: dict[str, Any],
    settings: dict[str, Any],
    store: FluxaStore,
    adapters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute single-file SA recovery with explicit lifecycle states:
       confirmed -> file_prepared -> submitted -> processing / not_processed -> resolved / failed.
    """
    adapters = adapters or build_adapters(settings)
    sa_record_id = str(payload.get("saRecordId") or "").strip()
    file_name = str(payload.get("fileName") or "").strip()
    src_path = str(payload.get("srcPath") or "").strip()
    failed_date = str(payload.get("failedDate") or "").strip()
    failure_reason = str(payload.get("failureReason") or "没有查询到媒体信息").strip()
    tmdb_id = str(payload.get("tmdbId") or "").strip()
    canonical_title = str(payload.get("canonicalTitle") or "").strip()
    target_original_title = str(payload.get("originalTitle") or "").strip()
    target_year = str(payload.get("targetYear") or "").strip()
    media_type = "movie" if payload.get("mediaType") == "movie" else "tv"
    source_season = int(payload["sourceSeason"]) if payload.get("sourceSeason") else (1 if media_type == "tv" else None)
    source_episode = int(payload["sourceEpisode"]) if payload.get("sourceEpisode") else (1 if media_type == "tv" else None)
    target_season = int(payload["targetSeason"]) if payload.get("targetSeason") else source_season
    target_episode = int(payload["targetEpisode"]) if payload.get("targetEpisode") else source_episode
    season_name = str(payload.get("seasonName") or "").strip()
    transfer_task_id = str(payload.get("transferTaskId") or "").strip()
    strategy = str(payload.get("strategy") or "sa_manual").strip()
    save_mapping_flag = bool(payload.get("saveMapping", True))
    confirmed = bool(payload.get("confirmed", False))

    if not sa_record_id or not file_name or not tmdb_id.isdigit() or not canonical_title:
        raise ValueError("缺少 SA 失败记录 ID、文件名、目标 TMDB ID 或规范片名")

    parsed = parse_media_filename(file_name)
    saved_mapping = store.find_sa_mapping(parsed["arcKey"], source_season) if source_season else None
    season_changed = bool(media_type == "tv" and source_season and target_season and target_season != source_season)

    # Sequel / remake identity safety guard (Point 3: e.g. Hostel Part II vs Hostel 2005!)
    if media_type == "movie":
        conflict = check_sequel_identity_conflict(
            source_name=parsed["arcTitle"],
            source_year=parsed.get("year") or "",
            target_title=canonical_title,
            target_original_title=target_original_title,
            target_year=target_year,
        )
        if conflict["hasConflict"] and not confirmed:
            raise ValueError(f"检测到续集/身份冲突，已阻止自动覆盖：{conflict['warningText']}")

    if (season_changed or not saved_mapping or parsed["hasSpecialMarkers"]) and not confirmed:
        raise ValueError("首次执行季号转换、特殊多碟/花絮文件或新映射必须经过人工确认")

    existing_job = store.get_latest_sa_recovery_for_record(sa_record_id)
    if existing_job and existing_job["status"] == "resolved":
        return {"ok": True, "alreadyResolved": True, "job": existing_job, "message": "该记录已核验解决，无需重复执行"}
    attempt_count = int(existing_job["attemptCount"]) if existing_job else 0
    max_attempts = int(existing_job["maxAttempts"]) if existing_job else 2
    if attempt_count >= max_attempts:
        raise ValueError(f"已达到单文件有限重试上限（{max_attempts} 次），已停止重试；请人工检查源文件与 SA 日志")

    emby = adapters["emby"].fetch_library_snapshot() if adapters["emby"].configured else {"available": False, "episodes": {}, "movies": {}}
    sa_history = adapters["symedia"].list_transfer_history(count=150) if adapters["symedia"].configured else []
    existing_ver = detect_existing_version_for_target(
        media_type=media_type,
        tmdb_id=tmdb_id,
        target_season=target_season,
        target_episode=target_episode,
        failed_file_name=file_name,
        emby=emby,
        sa_history=sa_history,
    )
    if existing_ver["exists"]:
        raise ValueError(existing_ver["summary"] or f"已有版本，无需补库：目标 TMDB {tmdb_id} 已存在库内版本，拒绝覆盖")

    sa_caps = adapters["symedia"].probe_recovery_capabilities() if adapters["symedia"].configured else {}

    mapping = saved_mapping
    if save_mapping_flag and media_type == "tv" and parsed["arcKey"] and source_season and target_season:
        mapping = store.save_sa_mapping(
            arc_key=parsed["arcKey"],
            arc_title=parsed["arcTitle"],
            source_season=source_season,
            media_type=media_type,
            tmdb_id=tmdb_id,
            canonical_title=canonical_title,
            target_season=target_season,
            season_name=season_name,
            episode_offset=(target_episode - source_episode) if (target_episode and source_episode) else 0,
            transfer_task_id=transfer_task_id,
            auto_apply=not parsed["hasSpecialMarkers"],
            confirmed_by_sample=file_name,
            evidence={"saRecordId": sa_record_id, "failedDate": failed_date, "seasonName": season_name},
        )

    suggested_name = build_spec_preserving_filename(
        parsed,
        canonical_title=canonical_title,
        target_season=target_season,
        target_episode=target_episode,
        media_type=media_type,
        tmdb_id=tmdb_id,
    )
    renamed_from = ""
    renamed_to = ""
    rollback: dict[str, Any] = {}
    submit_path = src_path

    if strategy == "rename_and_manual":
        if not src_path or not Path(src_path).is_file():
            raise ValueError(f"本地未挂载源文件 {src_path}，无法执行本地改名；建议在 115 改名为 {suggested_name} 后再触发单文件归档")
        src_file = Path(src_path)
        dst_file = src_file.with_name(suggested_name)
        if dst_file.exists():
            raise ValueError(f"目标文件名 {suggested_name} 已存在，拒绝覆盖")
        os.rename(src_file, dst_file)
        renamed_from = str(src_file)
        renamed_to = str(dst_file)
        submit_path = str(dst_file)
        rollback = {
            "originalFileName": file_name,
            "originalPath": str(src_file),
            "renamedPath": str(dst_file),
            "preservedSpecs": parsed.get("specsTail") or "",
            "preservedMarkers": parsed.get("specialMarkers") or [],
        }

    if not transfer_task_id and adapters["symedia"].configured:
        tasks = adapters["symedia"].list_transfer_tasks()
        if len(tasks) == 1:
            transfer_task_id = tasks[0]["id"]

    submitted_at = _now_iso()
    next_attempt = attempt_count + 1
    status = "submitted"
    last_error = ""
    message = ""
    override_mode = ""

    try:
        if not adapters["symedia"].configured:
            raise RuntimeError("Symedia (SA) 未配置，无法提交单文件归档")
        if not transfer_task_id:
            raise RuntimeError("请选择对应的 SA 归档任务 ID 后再提交单文件归档")
        if not submit_path.startswith("/"):
            raise RuntimeError("缺少以 / 开头的完整源文件路径，无法提交 SA 单文件归档")

        submit_kwargs: dict[str, Any] = {
            "file_path": submit_path,
            "transfer_task_id": transfer_task_id,
            "tmdb_id": tmdb_id,
            "media_type": media_type,
            "season": target_season if media_type == "tv" else None,
            "episode": target_episode if media_type == "tv" else None,
        }
        # Only enforce OpenAPI explicit-param check when caller relies on `sa_manual` without renaming
        if strategy == "sa_manual" and sa_caps.get("openapiChecked") and not sa_caps.get("supportsExplicitIdentify"):
            # Backward-compatible check: if fake/custom adapter reports explicit params in manualIdentifyParams, allow it
            declared_legacy = {str(x).lower() for x in (sa_caps.get("manualIdentifyParams") or [])}
            has_legacy_override = ("tmdbid" in declared_legacy or "tmdb_id" in declared_legacy) and (
                media_type == "movie" or ("season" in declared_legacy and "episode" in declared_legacy)
            )
            if not has_legacy_override:
                accepted = ", ".join(sa_caps.get("manualBodyFields") or ["items", "transfer_task_id"])
                raise RuntimeError(
                    f"核对 SA 真实接口 {sa_caps.get('manualEndpointPath') or '/api/v1/transfer/manual'} 后发现仅支持参数 [{accepted}]，"
                    "不支持直接传入 TMDB ID / 季集覆盖；原样提交不会让指定的 TMDB 和季集生效。"
                    f"请改用「保留规格改名（含 {{tmdbid={tmdb_id}}} 与目标季集）+ 单文件提交」，或在 SA 网页端手动指定识别。"
                )

        res = adapters["symedia"].submit_manual_transfer(**submit_kwargs)
        override_mode = str(res.get("overrideMode") or "")
        # Explicit honest state: 'submitted' (待处理), NEVER 'resolved'!
        status = "submitted"
        unverified_note = (
            "（注意：SA 未暴露 OpenAPI 参数定义，暂无法保证指定 TMDB/季集已被 SA 采纳，务必点击核验检查 SA 实际处理结果）"
            if override_mode == "unverified_no_openapi"
            else "（状态：已提交/待处理，需核验 SA 实际处理结果中的 TMDB/季集、目标视频与 STRM 后才会标记验证成功）"
        )
        message = f"{res['message']}{unverified_note}"
    except Exception as exc:
        status = "file_prepared" if renamed_to else "failed"
        last_error = sanitize_text(exc)
        message = (
            f"文件已按规范改名准备完成，但 SA 提交失败（第 {next_attempt}/{max_attempts} 次）：{last_error}"
            if renamed_to
            else f"提交失败（第 {next_attempt}/{max_attempts} 次）：{last_error}"
        )

    job = store.create_or_update_sa_recovery_job({
        "id": existing_job["id"] if existing_job else None,
        "saRecordId": sa_record_id,
        "fileName": file_name,
        "srcPath": src_path,
        "failedDate": failed_date,
        "failureReason": failure_reason,
        "arcKey": parsed["arcKey"],
        "mappingId": mapping["id"] if mapping else None,
        "mediaType": media_type,
        "tmdbId": tmdb_id,
        "canonicalTitle": canonical_title,
        "sourceSeason": source_season,
        "sourceEpisode": source_episode,
        "targetSeason": target_season,
        "targetEpisode": target_episode,
        "strategy": strategy,
        "transferTaskId": transfer_task_id,
        "renamedFrom": renamed_from,
        "renamedTo": renamed_to,
        "rollback": rollback,
        "status": status,
        "attemptCount": next_attempt,
        "maxAttempts": max_attempts,
        "lastError": last_error,
        "verification": {
            "saSuccessConfirmed": False,
            "tmdbAndEpisodeMatched": False,
            "destVideoConfirmed": False,
            "strmOrEmbyConfirmed": False,
            "detail": "已提交单文件归档，等待 SA 产生新记录（不可当作已完成）",
        },
        "submittedAt": submitted_at,
        "resolvedAt": "",
    })

    se_label = f" S{target_season:02d}E{target_episode:02d}" if (media_type == "tv" and target_season and target_episode) else ""
    store.add_audit(
        action_type="sa_recovery_submit",
        target_service="symedia",
        target_id=sa_record_id,
        media_key=f"{media_type}:{tmdb_id}",
        summary=f"SA 单文件恢复提交：{file_name} → TMDB {tmdb_id}{se_label}",
        status="success" if status == "submitted" else "error",
        error_text=last_error,
    )
    return {"ok": status == "submitted", "message": message, "job": job, "mapping": mapping}


def verify_sa_recovery(
    *,
    job_id: int,
    settings: dict[str, Any],
    store: FluxaStore,
    adapters: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Strictly verify THIS file's new SA result (tracking moved paths, new failures, TMDB/season mismatch,
    and existing preferred versions like CR vs LINETV) — NEVER falsely matching another release!
    """
    job = store.get_sa_recovery_job(job_id)
    if not job:
        raise ValueError("恢复任务不存在")
    adapters = adapters or build_adapters(settings)
    current_dt = now or _now_dt()

    sa_records = adapters["symedia"].list_transfer_history(count=150) if adapters["symedia"].configured else []
    emby = adapters["emby"].fetch_library_snapshot() if adapters["emby"].configured else {"available": False, "episodes": {}, "movies": {}}

    target_tmdb = str(job["tmdbId"])
    target_s = job["targetSeason"]
    target_e = job["targetEpisode"]
    expected_se = f"S{int(target_s):02d}E{int(target_e):02d}" if target_s and target_e else ""

    tracked_names = {
        n.casefold()
        for n in (_basename(job.get("fileName") or ""), _basename(job.get("renamedTo") or ""))
        if n
    }

    def _same_file(rec: dict[str, Any]) -> bool:
        rec_src = str(rec.get("src") or "")
        rec_base = _basename(rec.get("fileName") or rec_src).casefold()
        if rec_base and rec_base in tracked_names:
            return True
        return any(name and name in rec_src.casefold() for name in tracked_names)

    def _rec_sort_key(rec: dict[str, Any]) -> tuple[str, int]:
        rid = str(rec.get("id") or "")
        return (str(rec.get("date") or ""), int(rid) if rid.isdigit() else 0)

    # Track ALL newer SA records for THIS EXACT file (even if moved between /00-待整理 and /09-归档失败!)
    orig_id = str(job.get("saRecordId") or "")
    orig_id_num = int(orig_id) if orig_id.isdigit() else None
    file_records_after: list[dict[str, Any]] = []
    for rec in sa_records:
        rec_id = str(rec.get("id") or "")
        if rec_id == orig_id:
            continue
        if orig_id_num is not None and rec_id.isdigit() and int(rec_id) <= orig_id_num:
            continue
        if job.get("failedDate") and rec.get("date") and rec["date"] < job["failedDate"]:
            continue
        if _same_file(rec):
            file_records_after.append(rec)

    file_records_after.sort(key=_rec_sort_key, reverse=True)
    newest_file_rec = file_records_after[0] if file_records_after else None

    # Check if an EXISTING preferred version (e.g. CR版) already exists in Emby / SA archive for this episode
    existing_ver = detect_existing_version_for_target(
        media_type=job["mediaType"],
        tmdb_id=target_tmdb,
        target_season=target_s,
        target_episode=target_e,
        failed_file_name=job.get("fileName") or "",
        emby=emby,
        sa_history=sa_records,
    )
    existing_note = f"（另：{existing_ver['summary']}）" if existing_ver["exists"] else ""

    matched_success = None
    matched_new_failure = None
    matched_protected = None
    if newest_file_rec:
        out = newest_file_rec.get("outcome")
        if out in {"archived", "replaced"}:
            matched_success = newest_file_rec
        elif out in {"rule_protected", "version_rejected"}:
            matched_protected = newest_file_rec
        else:
            matched_new_failure = newest_file_rec

    sa_ok = matched_success is not None
    tmdb_se_ok = bool(
        matched_success
        and _sa_record_matches_target_episode(
            matched_success,
            media_type=job["mediaType"],
            tmdb_id=target_tmdb,
            target_season=target_s,
            target_episode=target_e,
        )
    )
    dest_path = str((matched_success or {}).get("dest") or "").strip()
    dest_ok = bool(dest_path)

    emby_ok = False
    if emby.get("available"):
        if job["mediaType"] == "movie":
            emby_ok = target_tmdb in (emby.get("movies") or {})
        elif target_s and target_e:
            eps_set = set(tuple(p) for p in (emby.get("episodes") or {}).get(target_tmdb, []))
            emby_ok = (int(target_s), int(target_e)) in eps_set
    strm_ok = dest_path.casefold().endswith(".strm") or emby_ok

    resolved_at = ""
    new_failure_id = ""
    moved_src = ""

    if sa_ok and not tmdb_se_ok:
        # SA processed this file, but specified TMDB or season/episode DID NOT TAKE EFFECT!
        actual_tmdb = matched_success.get("tmdbId") or "未知"
        actual_se = matched_success.get("seasonEpisode") or f"{matched_success.get('seasons', '')}{matched_success.get('episodes', '')}" or "未知"
        new_status = "failed"
        detail = (
            f"指定映射未生效：SA 新记录 #{matched_success['id']} 实际识别为 TMDB {actual_tmdb} {actual_se}"
            f"（预期 TMDB {target_tmdb} {expected_se}），不可标记为已解决，请核对目标目录 {dest_path or '-'}"
        )
    elif sa_ok and tmdb_se_ok and dest_ok and strm_ok:
        new_status = "resolved"
        detail = f"验证成功：SA 新记录 #{matched_success['id']} 已归档至 {dest_path}，已核对 TMDB {target_tmdb} {expected_se} 与 STRM/Emby（实际播放以 Emby 客户端为准）"
        resolved_at = _now_iso()
    elif sa_ok and tmdb_se_ok and dest_ok and not strm_ok:
        new_status = "processing"
        detail = f"处理中：SA 新记录 #{matched_success['id']} 已归档至 {dest_path}，但尚未核验到 STRM / Emby 条目（仍不可标记为已解决）"
    elif matched_protected:
        new_status = "skipped_existing"
        detail = f"已有版本，无需补库：SA 新记录 #{matched_protected['id']} 按规则保护跳过覆盖（{matched_protected['reasonText']}）{existing_note}"
    elif matched_new_failure:
        new_status = "failed"
        new_failure_id = str(matched_new_failure.get("id") or "")
        moved_src = str(matched_new_failure.get("src") or "")
        moved_note = f"，文件移动后路径：{moved_src}" if (moved_src and moved_src != job.get("srcPath")) else ""
        override_hint = (
            f"；指定的 TMDB {target_tmdb} {expected_se} 未在 SA 实际处理结果中生效"
            if matched_new_failure.get("outcome") == "identify_failed"
            else ""
        )
        detail = (
            f"再次失败：追踪到文件移动后的 SA 新失败记录 #{new_failure_id}（{matched_new_failure['reasonText']}{moved_note}）"
            f"{override_hint}，已停止自动重试{existing_note}"
        )
    else:
        submitted_dt = _parse_iso_dt(job.get("submittedAt") or "")
        elapsed_min = ((current_dt - submitted_dt).total_seconds() / 60.0) if submitted_dt else 0
        if existing_ver.get("noRecoveryNeeded"):
            new_status = "skipped_existing"
            detail = f"{existing_ver['summary']}（SA 历史中暂未看到本失败文件的新处理记录）"
        elif elapsed_min >= NOT_PROCESSED_TIMEOUT_MINUTES:
            new_status = "not_processed"
            detail = f"尚未处理：已超过 {NOT_PROCESSED_TIMEOUT_MINUTES} 分钟仍未在 SA 历史中发现本文件新记录，文件可能未真正入队；不自动重启 SA 或全库扫描，请检查 SA 队列{existing_note}"
        else:
            new_status = "processing"
            detail = f"处理中：尚未在 SA 历史中看到本文件新记录，请稍后再次点击核验{existing_note}"

    updated = store.create_or_update_sa_recovery_job({
        **job,
        "status": new_status,
        "resolvedAt": resolved_at,
        "lastError": matched_new_failure["reasonText"] if matched_new_failure else (detail if new_status == "failed" else job.get("lastError", "")),
        "verification": {
            "saSuccessConfirmed": sa_ok and tmdb_se_ok,
            "saSuccessRecordId": (matched_success or {}).get("id") or "",
            "newFailureRecordId": new_failure_id,
            "movedSrcPath": moved_src,
            "tmdbAndEpisodeMatched": tmdb_se_ok,
            "destVideoConfirmed": dest_ok,
            "destPath": dest_path,
            "strmOrEmbyConfirmed": strm_ok,
            "embyEntryConfirmed": emby_ok,
            "existingVersion": existing_ver if existing_ver["exists"] else None,
            "detail": detail,
            "checkedAt": _now_iso(),
        },
    })
    return {
        "ok": True,
        "resolved": new_status == "resolved",
        "status": new_status,
        "existingVersion": existing_ver if existing_ver["exists"] else None,
        "job": updated,
        "message": detail,
    }
