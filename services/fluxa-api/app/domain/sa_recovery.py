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
) -> str:
    """Build a standardized filename keeping original specs, Disc/Cut markers, release group, and extension intact."""
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
    if marker_str:
        parts.append(marker_str)
    if tail:
        parts.append(tail)
    elif parsed.get("year"):
        parts.append(str(parsed["year"]))
    return ".".join(p for p in parts if p) + ext


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

    def _add_cand(mt: str, cid: str, *, query: str = "", trusted: bool = False):
        c = str(cid or "").strip()
        if mt not in {"movie", "tv"} or not c.isdigit():
            return
        entry = candidate_meta.setdefault((mt, c), {"queries": [], "trusted": False})
        if query and query not in entry["queries"]:
            entry["queries"].append(query)
        if trusted:
            entry["trusted"] = True

    if manual_tmdb_id and str(manual_tmdb_id).isdigit():
        _add_cand(target_type, manual_tmdb_id, trusted=True)
    if saved_mapping:
        _add_cand(saved_mapping["mediaType"], saved_mapping["tmdbId"], trusted=True)

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
    emby_eps = emby.get("episodes") or {}
    emby_movies = emby.get("movies") or {}
    for prop in proposals:
        if prop["mediaType"] == "movie":
            prop["alreadyInEmby"] = prop["tmdbId"] in emby_movies
        else:
            eps_set = set(tuple(pair) for pair in emby_eps.get(prop["tmdbId"], []))
            prop["alreadyInEmby"] = (prop["targetSeason"], prop["targetEpisode"]) in eps_set

    top = proposals[0] if proposals else None
    has_conflict = bool(top and (top.get("sequelConflict") or {}).get("hasConflict"))
    can_auto_apply = bool(
        saved_mapping
        and saved_mapping.get("autoApply")
        and parsed["sourceSeason"] == saved_mapping["sourceSeason"]
        and parsed["sourceEpisode"] is not None
        and not parsed["hasSpecialMarkers"]
        and not has_conflict
    )
    requires_confirm = True
    if can_auto_apply and (not top or not top.get("alreadyInEmby")):
        requires_confirm = False

    latest_job = store.get_latest_sa_recovery_for_record(str(record.get("id") or ""))
    recognition_status = "matched" if proposals else "unreliable"
    status_message = (
        ""
        if proposals
        else "无法可靠识别：未找到与源文件作品身份可靠匹配的 TMDB 候选（已过滤无关作品），请手动输入 TMDB ID 核对季集"
    )

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
    if media_type == "movie" and tmdb_id in (emby.get("movies") or {}):
        raise ValueError(f"Emby 中已存在电影 TMDB {tmdb_id}，按安全边界拒绝覆盖已有媒体")
    if media_type == "tv" and target_season and target_episode:
        existing_eps = set(tuple(pair) for pair in (emby.get("episodes") or {}).get(tmdb_id, []))
        if (target_season, target_episode) in existing_eps:
            raise ValueError(f"Emby 中已存在 TMDB {tmdb_id} S{target_season:02d}E{target_episode:02d}，按安全边界拒绝覆盖已有媒体")

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
    )
    renamed_from = ""
    renamed_to = ""
    rollback: dict[str, Any] = {}
    submit_path = src_path

    if strategy == "rename_and_manual":
        if not src_path or not Path(src_path).is_file():
            raise ValueError(f"本地未挂载源文件 {src_path}，无法执行本地改名；建议优先使用 SA 单文件指定归档，或在 115 改名为 {suggested_name}")
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

    try:
        if not adapters["symedia"].configured:
            raise RuntimeError("Symedia (SA) 未配置，无法提交单文件归档")
        if not transfer_task_id:
            raise RuntimeError("请选择对应的 SA 归档任务 ID 后再提交单文件归档")
        if not submit_path.startswith("/"):
            raise RuntimeError("缺少以 / 开头的完整源文件路径，无法提交 SA 单文件归档")

        res = adapters["symedia"].submit_manual_transfer(
            file_path=submit_path,
            transfer_task_id=transfer_task_id,
            tmdb_id=tmdb_id,
            media_type=media_type,
            season=target_season if media_type == "tv" else None,
            episode=target_episode if media_type == "tv" else None,
        )
        # Explicit honest state: 'submitted' (待处理), NEVER 'resolved'!
        status = "submitted"
        message = f"{res['message']}（状态：已提交/待处理，需核验新 SA 成功记录、TMDB/季集、目标视频与 STRM 后才会标记验证成功）"
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
    """Strictly verify all 4 criteria + detect 'not_processed' timeout (Point 4) & 'failed' new SA error."""
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

    matched_success = None
    matched_new_failure = None
    for rec in sa_records:
        if rec["id"] == job["saRecordId"]:
            continue
        if job["failedDate"] and rec.get("date") and rec["date"] < job["failedDate"]:
            continue
        file_ok = bool(
            (job["fileName"] and job["fileName"] in (rec.get("src") or ""))
            or (job["renamedTo"] and Path(job["renamedTo"]).name in (rec.get("src") or ""))
        )
        tmdb_ok = rec.get("tmdbId") == target_tmdb
        se_blob = f"{rec.get('seasonEpisode', '')} {rec.get('seasons', '')}{rec.get('episodes', '')} {rec.get('dest', '')}".upper()
        se_ok = (not expected_se) or (expected_se in se_blob)

        if rec["outcome"] in {"archived", "replaced"} and ((tmdb_ok and se_ok) or (file_ok and tmdb_ok)):
            matched_success = rec
            break
        if rec["outcome"] in {"identify_failed", "failed", "auth_or_connection_error"} and file_ok and not matched_new_failure:
            matched_new_failure = rec

    sa_ok = matched_success is not None
    tmdb_se_ok = bool(
        matched_success
        and matched_success.get("tmdbId") == target_tmdb
        and (
            not expected_se
            or expected_se in f"{matched_success.get('seasonEpisode', '')} {matched_success.get('seasons', '')}{matched_success.get('episodes', '')} {matched_success.get('dest', '')}".upper()
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
    if sa_ok and tmdb_se_ok and dest_ok and strm_ok:
        new_status = "resolved"
        detail = f"验证成功：SA 新记录 #{matched_success['id']} 已归档至 {dest_path}，已核对 TMDB {target_tmdb} {expected_se} 与 STRM/Emby（实际播放以 Emby 客户端为准）"
        resolved_at = _now_iso()
    elif sa_ok and tmdb_se_ok and dest_ok and not strm_ok:
        new_status = "processing"
        detail = f"处理中：SA 新记录 #{matched_success['id']} 已归档至 {dest_path}，但尚未核验到 STRM / Emby 条目（仍不可标记为已解决）"
    elif matched_new_failure:
        new_status = "failed"
        detail = f"再次失败：SA 产生新失败记录 #{matched_new_failure['id']}（{matched_new_failure['reasonText']}），已停止自动重试"
    else:
        submitted_dt = _parse_iso_dt(job.get("submittedAt") or "")
        elapsed_min = ((current_dt - submitted_dt).total_seconds() / 60.0) if submitted_dt else 0
        if elapsed_min >= NOT_PROCESSED_TIMEOUT_MINUTES:
            new_status = "not_processed"
            detail = f"尚未处理：已超过 {NOT_PROCESSED_TIMEOUT_MINUTES} 分钟仍未在 SA 历史中发现新记录，文件可能未真正入队；不自动重启 SA 或全库扫描，请检查 SA 队列"
        else:
            new_status = "processing"
            detail = "处理中：尚未在 SA 历史中看到新记录，请稍后再次点击核验"

    updated = store.create_or_update_sa_recovery_job({
        **job,
        "status": new_status,
        "resolvedAt": resolved_at,
        "lastError": matched_new_failure["reasonText"] if matched_new_failure else job.get("lastError", ""),
        "verification": {
            "saSuccessConfirmed": sa_ok,
            "saSuccessRecordId": (matched_success or {}).get("id") or "",
            "tmdbAndEpisodeMatched": tmdb_se_ok,
            "destVideoConfirmed": dest_ok,
            "destPath": dest_path,
            "strmOrEmbyConfirmed": strm_ok,
            "embyEntryConfirmed": emby_ok,
            "detail": detail,
            "checkedAt": _now_iso(),
        },
    })
    return {"ok": True, "resolved": new_status == "resolved", "status": new_status, "job": updated, "message": detail}
