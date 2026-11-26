from __future__ import annotations

import re
from typing import Any

from app.adapters.http_utils import extract_season_episode

MIB = 1024 * 1024

QUALITY_POLICIES: list[dict[str, Any]] = [
    {
        "id": "donghua_hq",
        "name": "国漫高质量",
        "shortName": "国漫高质量",
        "summary": "先看正规源与可比较码率，不默认 4K/HDR/DV 优于优质 1080p；同档优先特效字幕、同编码码率、音轨与完整度。",
    },
    {
        "id": "anime_web",
        "name": "日漫追更",
        "shortName": "日漫追更",
        "summary": "面向 1080p+ WEB 追更源，不默认 4K 优于 1080p；同档优先特效字幕、同编码可比较码率、音轨与完整度。",
    },
    {
        "id": "anime_bd",
        "name": "日漫BD收藏",
        "shortName": "日漫BD收藏",
        "summary": "仅接受 BDRip / Blu-ray / BDREMUX（跳过 WEB-DL/HDTV），重视优质 BDRip、特效字幕、无损音轨与完整度，不默认 4K 优于 1080p。",
    },
    {
        "id": "general_hq",
        "name": "通用高质量（剧集/电影）",
        "shortName": "通用高质量",
        "summary": "拒绝 720p/TS/CAM/低码缩水版，不凭 4K/HDR/DV 标签压倒优质 1080p；同档按特效字幕、同编码码率、音轨与完整度择优。",
    },
    {
        "id": "none",
        "name": "仅哈希去重（不做质量筛选）",
        "shortName": "仅哈希去重",
        "summary": "不做质量过滤，仅按种子 Hash 去重后推给 qB（兼容极简模式）。",
    },
]

POLICY_MAP: dict[str, dict[str, Any]] = {p["id"]: p for p in QUALITY_POLICIES}

# MediaInfo / description parsing
BITRATE_MBPS_RE = re.compile(r"(?i)(?:video\s*bit\s*rate|bit\s*rate|码率)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:mb/s|mbps|m\b)")
BITRATE_KBPS_RE = re.compile(r"(?i)(?:video\s*bit\s*rate|bit\s*rate|码率)\s*[:：]?\s*(\d{3,6}(?:\.\d+)?)\s*(?:kb/s|kbps)")
INLINE_MBPS_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(\d+(?:\.\d+)?)\s*(?:mbps|mb/s)\b")
DURATION_HMS_RE = re.compile(r"(?i)(?:duration|时长)\s*[:：]?\s*(?:(\d+)\s*h\s*)?(\d+)\s*(?:min|mn|m|分钟)")

# Episode & batch patterns beyond standard SxxEyy
EP_CN_RE = re.compile(r"第\s*(\d{1,4})\s*[集话話]")
EP_PREFIX_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(?:EP|E)\s*(\d{1,4})(?![A-Za-z0-9])")
EP_BRACKET_RE = re.compile(r"[\[\(【]\s*(\d{2,4})(?:v\d)?\s*[\]\)】]")
EP_DASH_RE = re.compile(r"\s-\s(\d{2,4})(?:v\d)?(?=\s|\[|\(|【|$)")
BATCH_RE = re.compile(
    r"(?i)(?:\b(?:complete|bd[- .]?box|boxset|fin|all\s*episodes)\b|全\s*\d*\s*[集话話]|合集|全季|\b0?1\s*[-~～]\s*(?:0?[6-9]|[1-9]\d+)\b)"
)
SEASON_ONLY_RE = re.compile(r"(?i)\b(?:S(\d{1,2})|Season\s*(\d{1,2}))\b|第\s*(\d{1,2})\s*季")
YEAR_RE = re.compile(r"(?<!\d)(19[5-9]\d|20[0-3]\d)(?!\d)")

# Spec cut / strip patterns for clean work title extraction
SPEC_STRIP_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:2160p|1080p|1080i|720p|576p|480p|4k|uhd)\b|"
    r"\b(?:web[- .]?dl|web[- .]?rip|web|bdrip|blu[- .]?ray|bdremux|bdmv|remux|hdtv|tvrip|dvdrip|hdcam|cam|ts|tc)\b|"
    r"\b(?:hevc|h\.?265|x265|avc|h\.?264|x264|av1|vc-?1|vp9)\b|"
    r"\b(?:ma10p|hi10p|main10|10bit|8bit|10-?bit|8-?bit)\b|"
    r"\b(?:dolby[ .]?vision|dovi|hdr10\+?|hdr|dv|hlg|sdr)\b|"
    r"\b(?:flac|lpcm|pcm|truehd|dts[- .]?hd(?:\s*ma)?|dts|ddp\d*(?:\.\d)?|e-?ac-?3|ac3|atmos|aac\d*(?:\.\d)?|opus|mp3|2audio|dual[- .]?audio)\b|"
    r"\b(?:chs|cht|sc|tc|gb|big5|jpn|jap|eng|ass|srt|sup|pgs|sub|subs|multi|v\d|repack|proper|complete|fin|baha|b-?global|amzn|nf|cr|linetv|abema|iqiyi|iq|tencent|wetv|youku|bilibili|bili|mgtv|kk)\b|"
    r"特效字幕|简繁特效|繁简特效|内封特效|外挂特效|内嵌特效|简繁内封|简繁外挂|简繁内嵌|简日中字|繁日中字|中文字幕|双语字幕|多音轨|无损音轨|国语|粤语|日语|全集|合集|精简版?|压制|高码率?|低码率?|高码|低码"
    r")"
)
LEADING_GROUP_RE = re.compile(r"^\s*(?:[\[【][^\]】]{1,28}[\]】]\s*)+")
TRAILING_GROUP_RE = re.compile(r"[-@][A-Za-z0-9][A-Za-z0-9_.]{1,24}\s*$")


def list_quality_policies() -> list[dict[str, Any]]:
    return [dict(p) for p in QUALITY_POLICIES]


def get_quality_policy(policy_id: str) -> dict[str, Any]:
    pid = str(policy_id or "none").strip()
    return POLICY_MAP.get(pid, POLICY_MAP["none"])


def _detect_resolution(text: str) -> str:
    if re.search(r"(?i)(?<![A-Za-z0-9])(?:2160p|4k|uhd)(?![A-Za-z0-9])", text):
        return "2160p"
    if re.search(r"(?i)(?<![A-Za-z0-9])(?:1080p|1080i|fhd)(?![A-Za-z0-9])", text):
        return "1080p"
    if re.search(r"(?i)(?<![A-Za-z0-9])720p(?![A-Za-z0-9])", text):
        return "720p"
    if re.search(r"(?i)(?<![A-Za-z0-9])(?:576p|480p|360p)(?![A-Za-z0-9])", text):
        return "480p"
    return ""


def _detect_source(text: str) -> str:
    if re.search(r"(?i)\b(?:hdcam|camrip|cam|hdts|telesync|telecine|ts|tc)\b|枪版|录屏", text):
        # Avoid false match on ".ts" video extension or "DTS"
        if not re.search(r"(?i)\bdts\b", text) or re.search(r"(?i)\b(?:hdcam|camrip|hdts|telesync|telecine)\b|枪版|录屏", text):
            if re.search(r"(?i)(?<![.\w])(?:hdcam|camrip|hdts|telesync|telecine|\bcam\b|\btc\b)(?!\w)|枪版|录屏", text):
                return "cam_ts"
    if re.search(r"(?i)\b(?:bdmv|bd-?iso)\b|原盘", text):
        return "bdmv"
    if re.search(r"(?i)\b(?:bdremux|remux)\b", text):
        return "remux"
    if re.search(r"(?i)\b(?:bdrip|bd-?rip|bd\s*rip)\b", text):
        return "bdrip"
    if re.search(r"(?i)\b(?:blu[- .]?ray|uhd[- .]?bd|bd1080p|bd2160p)\b", text):
        return "bluray"
    if re.search(r"(?i)\b(?:web[- .]?dl|webdl|b-?global|baha|amzn|amazon|nf|netflix|linetv|crunchyroll|abema|iqiyi|wetv|youku|bilibili)\b", text):
        return "web-dl"
    if re.search(r"(?i)\b(?:web[- .]?rip|webrip)\b", text):
        return "webrip"
    if re.search(r"(?i)\b(?:hdtv|hdtvrip)\b", text):
        return "hdtv"
    if re.search(r"(?i)\b(?:tvrip|dvdrip)\b", text):
        return "tvrip"
    return ""


def _detect_codec(text: str) -> str:
    if re.search(r"(?i)(?<![A-Za-z0-9])(?:hevc|h\.?265|x265|ma10p)(?![A-Za-z0-9])", text):
        return "hevc"
    if re.search(r"(?i)(?<![A-Za-z0-9])(?:avc|h\.?264|x264|hi10p)(?![A-Za-z0-9])", text):
        return "avc"
    if re.search(r"(?i)(?<![A-Za-z0-9])av1(?![A-Za-z0-9])", text):
        return "av1"
    return ""


def _detect_hdr_tags(text: str) -> list[str]:
    tags: list[str] = []
    if re.search(r"(?i)(?<![A-Za-z0-9])(?:dolby[ .]?vision|dovi|dv)(?![A-Za-z0-9])", text):
        tags.append("DV")
    if re.search(r"(?i)(?<![A-Za-z0-9])hdr10\+(?![A-Za-z0-9])", text):
        tags.append("HDR10+")
    elif re.search(r"(?i)(?<![A-Za-z0-9])(?:hdr10|hdr)(?![A-Za-z0-9])", text):
        tags.append("HDR")
    if re.search(r"(?i)(?<![A-Za-z0-9])hlg(?![A-Za-z0-9])", text):
        tags.append("HLG")
    return tags


def _detect_audio(text: str) -> tuple[int, list[str]]:
    tags: list[str] = []
    tier = 0
    if re.search(r"(?i)(?<![A-Za-z0-9])(?:flac|lpcm|pcm|truehd|dts[- .]?hd(?:\s*ma)?)(?![A-Za-z0-9])|无损音轨", text):
        tier = 3
        for tok in ("FLAC", "LPCM", "TrueHD", "DTS-HD"):
            if re.search(rf"(?i)(?<![A-Za-z0-9]){re.escape(tok)}(?![A-Za-z0-9])", text):
                tags.append(tok)
        if not tags:
            tags.append("无损音轨")
    if re.search(r"(?i)(?<![A-Za-z0-9])(?:ddp\d*(?:\.\d)?|e-?ac-?3|eac3|ac3|dd5\.1|dts(?!-?hd)|atmos)(?![A-Za-z0-9])", text):
        tier = max(tier, 2)
        for m in re.findall(r"(?i)(?<![A-Za-z0-9])(?:ddp\d*(?:\.\d)?|e-?ac-?3|eac3|ac3|atmos|dts)(?![A-Za-z0-9])", text):
            up = m.upper()
            if up not in tags:
                tags.append(up)
    if re.search(r"(?i)(?<![A-Za-z0-9])(?:aac\d*(?:\.\d)?|opus|mp3)(?![A-Za-z0-9])", text):
        tier = max(tier, 1)
        for m in re.findall(r"(?i)(?<![A-Za-z0-9])(?:aac|opus)(?![A-Za-z0-9])", text):
            up = m.upper()
            if up not in tags:
                tags.append(up)
    return tier, tags[:4]


def _detect_completeness(text: str) -> tuple[int, list[str]]:
    score = 0
    tags: list[str] = []
    checks = [
        (r"(?i)\b(?:complete|bd[- .]?box|fin)\b|全集|合集|全季", 2, "完整合集"),
        (r"(?i)\b(?:sp|ova|oad|ncop|nced|extras?|bonus|menu|scans)\b|特典|花絮", 1, "含特典/SP"),
        (r"(?i)\b(?:chapters?|fonts?)\b|章节|字体", 1, "含章节/字体"),
        (r"(?i)\b(?:dual[- .]?audio|2audio|multi[- .]?audio)\b|双语|多音轨|国日|日英", 1, "多音轨"),
        (r"(?i)\b(?:v2|v3|repack|proper|rev)\b|修正版", 1, "修正版"),
    ]
    for pat, weight, label in checks:
        if re.search(pat, text):
            score += weight
            tags.append(label)
    return score, tags


def _extract_bitrate_and_duration(title: str, description: str, size_bytes: int) -> tuple[float | None, float | None, float | None, str]:
    blob = f"{title}\n{description}"
    bitrate_mbps: float | None = None
    m_mb = BITRATE_MBPS_RE.search(blob) or INLINE_MBPS_RE.search(blob)
    if m_mb:
        try:
            bitrate_mbps = round(float(m_mb.group(1)), 2)
        except ValueError:
            bitrate_mbps = None
    else:
        m_kb = BITRATE_KBPS_RE.search(blob)
        if m_kb:
            try:
                bitrate_mbps = round(float(m_kb.group(1)) / 1000.0, 2)
            except ValueError:
                bitrate_mbps = None

    duration_min: float | None = None
    m_dur = DURATION_HMS_RE.search(blob)
    if m_dur:
        try:
            hours = int(m_dur.group(1) or 0)
            mins = int(m_dur.group(2) or 0)
            total_m = hours * 60 + mins
            if total_m > 0:
                duration_min = float(total_m)
        except ValueError:
            duration_min = None

    if bitrate_mbps is not None:
        return bitrate_mbps, duration_min, bitrate_mbps, "measured"
    if size_bytes > 0 and duration_min and duration_min > 0:
        calc = round((size_bytes * 8.0) / (duration_min * 60.0 * 1_000_000.0), 2)
        return None, duration_min, calc, "measured"
    if size_bytes > 0:
        return None, duration_min, None, "size_only"
    return None, duration_min, None, "none"


def extract_work_episode_key(
    title: str,
    *,
    season_number: int | None = None,
    episode_number: int | None = None,
) -> dict[str, Any]:
    """Build a canonical `(work, season, episode)` grouping key so:
    - Multiple versions of the SAME work & episode compete against each other.
    - DIFFERENT works or DIFFERENT episodes NEVER eliminate each other.
    """
    raw = str(title or "").strip()
    s_num, e_num = season_number, episode_number
    if s_num is None and e_num is None:
        s_num, e_num = extract_season_episode(raw)

    is_batch = bool(BATCH_RE.search(raw))
    if e_num is None and not is_batch:
        m_cn = EP_CN_RE.search(raw)
        if m_cn:
            e_num = int(m_cn.group(1))
        else:
            m_ep = EP_PREFIX_RE.search(raw)
            if m_ep:
                e_num = int(m_ep.group(1))
            else:
                m_dash = EP_DASH_RE.search(raw)
                if m_dash:
                    e_num = int(m_dash.group(1))
                else:
                    # Bracketed pure number like [08] (avoid 4-digit years like [2026])
                    for m_br in EP_BRACKET_RE.finditer(raw):
                        val = int(m_br.group(1))
                        if 1 <= val <= 1900:
                            e_num = val
                            break

    if s_num is None:
        m_s = SEASON_ONLY_RE.search(raw)
        if m_s:
            s_val = m_s.group(1) or m_s.group(2) or m_s.group(3)
            if s_val:
                s_num = int(s_val)

    # Extract clean work title stem
    work = re.sub(r"\.(?:mkv|mp4|ts|iso|torrent)$", "", raw, flags=re.I)
    work = work.split("|")[0].strip()
    work = LEADING_GROUP_RE.sub("", work)
    work = TRAILING_GROUP_RE.sub("", work)
    # Remove bracketed metadata blocks that are pure tags/specs
    work = re.sub(r"[\[【\(][^\]】\)]*(?:1080p|2160p|720p|4k|web|bdrip|hevc|avc|h265|h264|x265|x264|aac|flac|chs|cht|ass|特效|简繁|字幕|baha|b-?global|10bit|hdr|dv)[^\]】\)]*[\]】\)]", " ", work, flags=re.I)
    # Cut at SxxEyy / EPxx / 第xx集 / resolution / source
    cut_pats = [
        r"(?i)\bS\d{1,2}E\d{1,4}\b",
        r"(?i)(?<![A-Za-z0-9])EP\d{1,4}(?![A-Za-z0-9])",
        r"第\s*\d{1,4}\s*[集话話]",
        r"\s-\s\d{2,4}(?:v\d)?(?=\s|\[|\(|【|$)",
        r"[\[\(【]\s*\d{2,4}(?:v\d)?\s*[\]\)】]",
        r"(?i)\b(?:2160p|1080p|720p|4k|web[- .]?dl|web[- .]?rip|bdrip|blu[- .]?ray|remux|hdtv)\b",
    ]
    for pat in cut_pats:
        m = re.search(pat, work)
        if m and m.start() >= 2:
            work = work[: m.start()]
    work = SPEC_STRIP_RE.sub(" ", work)
    year_m = YEAR_RE.search(raw)
    year = year_m.group(1) if year_m else ""
    work = YEAR_RE.sub(" ", work)
    # Prefer CJK core when bilingual title has "中文 / English" or "中文 English" so versions of the same anime/donghua group together!
    cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", work)
    if cjk_runs:
        slug = "".join(cjk_runs)
    else:
        slug = re.sub(r"[^a-z0-9]+", "", work.lower())

    if len(slug) < 2:
        # Fallback: cannot reliably identify work -> keep unique per title so we never merge unrelated works
        slug = f"raw_{re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '', raw.lower())[:40]}"

    if is_batch and e_num is None:
        ep_part = f"S{(s_num or 1):02d}_BATCH"
        scope_label = f"第 {s_num or 1} 季合集"
    elif e_num is not None:
        ep_part = f"S{(s_num or 1):02d}E{e_num:02d}"
        scope_label = ep_part
    elif s_num is not None:
        ep_part = f"S{s_num:02d}"
        scope_label = ep_part
    else:
        ep_part = f"MOVIE_{year or '0000'}"
        scope_label = f"电影 ({year})" if year else "单部作品"

    return {
        "workKey": slug,
        "episodePart": ep_part,
        "workEpisodeKey": f"{slug}::{ep_part}",
        "scopeLabel": scope_label,
        "seasonNumber": s_num,
        "episodeNumber": e_num,
        "isBatch": is_batch and e_num is None,
    }


def parse_release_quality_specs(
    *,
    title: str,
    description: str = "",
    size_bytes: int = 0,
    season_number: int | None = None,
    episode_number: int | None = None,
) -> dict[str, Any]:
    """Parse structured quality metadata from an RSS entry WITHOUT equating title tags to measured quality."""
    t = str(title or "").strip()
    d = str(description or "").strip()
    combined = f"{t} {d}".strip()

    scope = extract_work_episode_key(t, season_number=season_number, episode_number=episode_number)
    resolution = _detect_resolution(combined)
    source = _detect_source(combined)
    codec = _detect_codec(combined)
    hdr_tags = _detect_hdr_tags(combined)
    bit_depth = 10 if re.search(r"(?i)(?<![A-Za-z0-9])(?:ma10p|hi10p|main10|10-?bit)(?![A-Za-z0-9])", combined) else (
        8 if re.search(r"(?i)(?<![A-Za-z0-9])8-?bit(?![A-Za-z0-9])", combined) else 0
    )
    has_effects_sub = bool(
        re.search(r"特效字幕|简繁特效|繁简特效|内封特效|外挂特效|内嵌特效|特效", combined)
        or re.search(r"(?i)(?<![A-Za-z])(?:ass|styled[- .]?sub)(?![A-Za-z])", combined)
    )
    has_chinese_sub = has_effects_sub or bool(
        re.search(r"简繁|简体|繁体|中字|中文|国语|内封简|内嵌简|外挂简", combined)
        or re.search(r"(?i)(?<![A-Za-z])(?:chs|cht|gb|big5|sc|tc)(?![A-Za-z])", combined)
    )
    audio_tier, audio_tags = _detect_audio(combined)
    comp_score, comp_tags = _detect_completeness(combined)
    is_mini_rip = bool(
        re.search(r"(?i)(?<![A-Za-z0-9])(?:minibd|mini[- .]?rip|mini1080p|microhd|lowbit)(?![A-Za-z0-9])|精简版?|缩水版?|小体积版?|超小压制", combined)
    )
    bitrate_mbps, duration_min, eff_bitrate, bitrate_evidence = _extract_bitrate_and_duration(t, d, int(size_bytes or 0))

    size_mib = round(int(size_bytes or 0) / MIB, 1) if int(size_bytes or 0) > 0 else 0.0
    summary_parts = [p for p in [
        resolution or "分辨率未知",
        source.upper() if source else "来源未知",
        codec.upper() if codec else "编码未知",
        f"{bit_depth}bit" if bit_depth else "",
        "特效字幕" if has_effects_sub else ("中字" if has_chinese_sub else ""),
        "/".join(audio_tags) if audio_tags else "",
        f"{eff_bitrate} Mbps" if eff_bitrate is not None else (f"{size_mib} MiB" if size_mib > 0 else "无体积/码率"),
    ] if p]

    return {
        **scope,
        "resolution": resolution,
        "source": source,
        "codec": codec,
        "bitDepth": bit_depth,
        "hdrTags": hdr_tags,
        "hasEffectsSub": has_effects_sub,
        "hasChineseSub": has_chinese_sub,
        "audioTier": audio_tier,
        "audioTags": audio_tags,
        "completenessScore": comp_score,
        "completenessTags": comp_tags,
        "isMiniRip": is_mini_rip,
        "sizeBytes": int(size_bytes or 0),
        "sizeMiB": size_mib,
        "bitrateMbps": bitrate_mbps,
        "durationMin": duration_min,
        "effectiveBitrateMbps": eff_bitrate,
        "bitrateEvidence": bitrate_evidence,
        "specSummary": " · ".join(summary_parts),
    }


def evaluate_candidate_quality(
    specs: dict[str, Any],
    policy_id: str,
) -> dict[str, Any]:
    """Evaluate one RSS entry against a quality policy.

    Returns:
      decision: 'pass' | 'reject' | 'insufficient_evidence'
      qualityTier: int (higher is better within policy; 4K/HDR/DV NEVER blindly outranks quality 1080p)
      lowBitrate4k: bool
      reason: human-readable Chinese explanation
    """
    pid = str(policy_id or "none").strip()
    if pid not in POLICY_MAP:
        pid = "none"
    policy = POLICY_MAP[pid]

    if pid == "none":
        return {
            "policyId": "none",
            "policyName": policy["name"],
            "decision": "pass",
            "qualityTier": 2,
            "lowBitrate4k": False,
            "missingEvidence": [],
            "reason": "未绑定质量策略，仅执行官组 RSS + 种子 Hash 去重",
        }

    res = specs["resolution"]
    src = specs["source"]
    codec = specs["codec"]
    hdr_tags = specs["hdrTags"]
    eff_br = specs["effectiveBitrateMbps"]
    size_bytes = specs["sizeBytes"]
    size_mib = specs["sizeMiB"]
    is_batch = specs["isBatch"]
    is_single_ep = specs["episodeNumber"] is not None and not is_batch

    # 1. Explicit hard-reject low-quality sources / resolutions first (even if metadata is sparse)
    if src == "cam_ts":
        return {
            "policyId": pid,
            "policyName": policy["name"],
            "decision": "reject",
            "qualityTier": 0,
            "lowBitrate4k": False,
            "missingEvidence": [],
            "reason": f"[{policy['shortName']}] 拒绝低质偷拍/录屏源（CAM/TS/TC），已跳过",
        }
    if specs["isMiniRip"]:
        return {
            "policyId": pid,
            "policyName": policy["name"],
            "decision": "reject",
            "qualityTier": 0,
            "lowBitrate4k": False,
            "missingEvidence": [],
            "reason": f"[{policy['shortName']}] 命中低码精简/缩水压制特征，不符合高质量要求，已跳过",
        }
    if res in {"720p", "480p"}:
        return {
            "policyId": pid,
            "policyName": policy["name"],
            "decision": "reject",
            "qualityTier": 0,
            "lowBitrate4k": False,
            "missingEvidence": [],
            "reason": f"[{policy['shortName']}] 分辨率 {res} 低于 1080p 门槛，已跳过",
        }

    # Policy-specific source rejection BEFORE insufficient-evidence check
    if pid == "anime_bd" and src in {"web-dl", "webrip", "hdtv", "tvrip"}:
        return {
            "policyId": pid,
            "policyName": policy["name"],
            "decision": "reject",
            "qualityTier": 0,
            "lowBitrate4k": False,
            "missingEvidence": [],
            "reason": f"[{policy['shortName']}] 来源为 {src.upper()}（追更/电视源），BD 收藏策略仅接受 BDRip/Blu-ray/BDREMUX，已跳过",
        }
    if pid in {"donghua_hq", "anime_web", "general_hq"} and src in {"hdtv", "tvrip"}:
        return {
            "policyId": pid,
            "policyName": policy["name"],
            "decision": "reject",
            "qualityTier": 0,
            "lowBitrate4k": False,
            "missingEvidence": [],
            "reason": f"[{policy['shortName']}] 来源为 {src.upper()}（电视录播源），不符合高质量要求，已跳过",
        }

    # 2. Check evidence sufficiency — NEVER treat missing metadata or bare 4K/HDR/DV title tags as measured quality!
    missing: list[str] = []
    if not res:
        missing.append("分辨率")
    if not src:
        missing.append("片源类型(WEB-DL/BDRip等)")
    if not codec:
        missing.append("视频编码(HEVC/AVC/AV1)")
    if missing:
        return {
            "policyId": pid,
            "policyName": policy["name"],
            "decision": "insufficient_evidence",
            "qualityTier": 0,
            "lowBitrate4k": False,
            "missingEvidence": missing,
            "reason": f"[{policy['shortName']}] RSS 信息依据不足（缺少{'、'.join(missing)}），不凭标题盲目放行，已跳过",
        }

    # For 4K / 2160p (especially with HDR/DV tags): MUST have bitrate or file-size evidence so we never trust title labels as measured quality!
    if res == "2160p" and eff_br is None and size_bytes <= 0:
        tag_note = f"（含 {'/'.join(hdr_tags)} 标签）" if hdr_tags else ""
        return {
            "policyId": pid,
            "policyName": policy["name"],
            "decision": "insufficient_evidence",
            "qualityTier": 0,
            "lowBitrate4k": False,
            "missingEvidence": ["视频码率或文件体积"],
            "reason": (
                f"[{policy['shortName']}] 依据不足：条目仅标称 2160p/4K{tag_note}，但 RSS 未提供码率或文件体积，"
                "不将标题标签当作实测画质，已跳过"
            ),
        }

    # 3. Bitrate / single-episode size checks (respecting codec family!)
    low_bitrate_4k = False
    hevc_like = codec in {"hevc", "av1"}

    if res == "2160p":
        if eff_br is not None:
            hard_floor = 5.0 if hevc_like else 8.0
            quality_floor = 8.5 if hevc_like else 13.0
            if eff_br < hard_floor:
                return {
                    "policyId": pid,
                    "policyName": policy["name"],
                    "decision": "reject",
                    "qualityTier": 0,
                    "lowBitrate4k": True,
                    "missingEvidence": [],
                    "reason": (
                        f"[{policy['shortName']}] 标称 2160p/4K 但码率仅 {eff_br} Mbps（低于 {codec.upper()} 4K 底线 {hard_floor} Mbps），"
                        "属低码伪 4K，已跳过"
                    ),
                }
            if eff_br < quality_floor:
                low_bitrate_4k = True
        elif is_single_ep and size_bytes > 0:
            hard_mib = 500.0 if hevc_like else 750.0
            quality_mib = 950.0 if hevc_like else 1400.0
            if size_mib < hard_mib:
                return {
                    "policyId": pid,
                    "policyName": policy["name"],
                    "decision": "reject",
                    "qualityTier": 0,
                    "lowBitrate4k": True,
                    "missingEvidence": [],
                    "reason": (
                        f"[{policy['shortName']}] 标称 2160p/4K 但单集仅 {size_mib} MiB（{codec.upper()} 低码伪 4K），"
                        "不因 4K/HDR/DV 标签放行，已跳过"
                    ),
                }
            if size_mib < quality_mib:
                low_bitrate_4k = True

    elif res == "1080p":
        if eff_br is not None:
            min_br = (3.0 if pid == "anime_bd" else 2.2) if hevc_like else (4.5 if pid == "anime_bd" else 3.2)
            if eff_br < min_br:
                return {
                    "policyId": pid,
                    "policyName": policy["name"],
                    "decision": "reject",
                    "qualityTier": 0,
                    "lowBitrate4k": False,
                    "missingEvidence": [],
                    "reason": f"[{policy['shortName']}] 1080p 码率仅 {eff_br} Mbps（低于 {codec.upper()} 最低门槛 {min_br} Mbps），已跳过",
                }
        elif is_single_ep and size_bytes > 0:
            min_mib = (300.0 if pid == "anime_bd" else 200.0) if hevc_like else (450.0 if pid == "anime_bd" else 300.0)
            if size_mib < min_mib:
                return {
                    "policyId": pid,
                    "policyName": policy["name"],
                    "decision": "reject",
                    "qualityTier": 0,
                    "lowBitrate4k": False,
                    "missingEvidence": [],
                    "reason": f"[{policy['shortName']}] 1080p 单集体积仅 {size_mib} MiB（低于 {codec.upper()} 门槛 {min_mib:.0f} MiB），疑似低码版，已跳过",
                }

    # 4. Compute quality tier (4K/HDR/DV does NOT get an automatic tier boost over clean 1080p!)
    notes: list[str] = []
    if pid == "anime_bd":
        if low_bitrate_4k:
            tier = 2
            notes.append("4K 码率/体积偏低，不高于优质 1080p BDRip")
        elif src in {"bdrip", "bluray", "remux", "bdmv"} and (specs["bitDepth"] >= 10 or src in {"remux", "bdmv"} or (eff_br or 0) >= 6.0):
            tier = 3
            notes.append(f"优质 {res} {src.upper()}")
        else:
            tier = 2
            notes.append(f"标准 {res} {src.upper()}")
    else:
        if low_bitrate_4k:
            tier = 2
            notes.append("普通低码 4K（不凭 4K/HDR/DV 标签胜过优质 1080p）")
        elif src in {"web-dl", "bdrip", "bluray", "remux", "bdmv"}:
            tier = 3
            notes.append(f"主力高质档 {res} {src.upper()}")
        else:
            tier = 2
            notes.append(f"次档来源 {res} {src.upper()}")

    if specs["hasEffectsSub"]:
        notes.append("含特效字幕")
    if specs["audioTier"] >= 3:
        notes.append(f"无损音轨({'/'.join(specs['audioTags'])})")
    elif specs["audioTags"]:
        notes.append(f"音轨 {'/'.join(specs['audioTags'])}")
    if specs["completenessTags"]:
        notes.append("、".join(specs["completenessTags"]))
    if specs["bitrateEvidence"] == "measured" and eff_br is not None:
        notes.append(f"可验证码率 {eff_br} Mbps")
    elif specs["bitrateEvidence"] == "size_only":
        notes.append(f"体积参考 {size_mib} MiB（无实测时长，不同编码不按体积粗排）")
    else:
        notes.append("无体积/码率字段（按来源+规格核验，未将标签视为实测画质）")

    return {
        "policyId": pid,
        "policyName": policy["name"],
        "decision": "pass",
        "qualityTier": tier,
        "lowBitrate4k": low_bitrate_4k,
        "missingEvidence": [],
        "reason": f"[{policy['shortName']}] 符合质量要求：" + "；".join(notes),
    }


def compare_quality_candidates(
    cand_a: dict[str, Any],
    cand_b: dict[str, Any],
    policy_id: str,
) -> dict[str, Any]:
    """Compare two QUALIFIED candidates of the SAME work + season/episode.

    Strict ordering:
      1. Quality tier (e.g. quality 1080p Tier 3 > low-bitrate 4K Tier 2; 4K/HDR/DV tags NEVER override)
      2. Effects subtitles (`特效字幕` / ASS)
      3. Comparable video bitrate (ONLY when same codec family & comparable scope; NEVER rank different codecs by file size!)
      4. Audio track tier (FLAC/LPCM/TrueHD/DTS-HD > DDP/AC3 > AAC)
      5. Release completeness (chapters/fonts/SP/dual-audio/complete)
    """
    ea, eb = cand_a["eval"], cand_b["eval"]
    sa, sb = cand_a["specs"], cand_b["specs"]

    # 1. Quality tier
    if ea["qualityTier"] != eb["qualityTier"]:
        if ea["qualityTier"] > eb["qualityTier"]:
            why = "优质主力档胜过低码 4K/次档" if eb.get("lowBitrate4k") else f"档位更高（Tier {ea['qualityTier']} > Tier {eb['qualityTier']}）"
            return {"winner": "a", "significantUpgrade": True, "reason": why}
        why = "优质主力档胜过低码 4K/次档" if ea.get("lowBitrate4k") else f"档位更高（Tier {eb['qualityTier']} > Tier {ea['qualityTier']}）"
        return {"winner": "b", "significantUpgrade": True, "reason": why}

    # 2. Effects subtitles (top tie-breaker within same tier!)
    if sa["hasEffectsSub"] != sb["hasEffectsSub"]:
        if sa["hasEffectsSub"]:
            return {"winner": "a", "significantUpgrade": True, "reason": "同档位优先含特效字幕版本"}
        return {"winner": "b", "significantUpgrade": True, "reason": "同档位优先含特效字幕版本"}

    # 3. Comparable video bitrate (SAME codec family ONLY!)
    codec_note = ""
    same_codec = bool(sa["codec"] and sb["codec"] and sa["codec"] == sb["codec"])
    same_scope = bool(sa["isBatch"] == sb["isBatch"])
    if same_codec and same_scope:
        bra, brb = sa["effectiveBitrateMbps"], sb["effectiveBitrateMbps"]
        if bra is not None and brb is not None and min(bra, brb) > 0:
            ratio = max(bra, brb) / min(bra, brb)
            if ratio >= 1.15:
                winner = "a" if bra > brb else "b"
                hi, lo = (bra, brb) if winner == "a" else (brb, bra)
                return {
                    "winner": winner,
                    "significantUpgrade": ratio >= 1.25,
                    "reason": f"同编码({sa['codec'].upper()})可比较码率更高（{hi} Mbps > {lo} Mbps）",
                }
        elif not sa["isBatch"] and sa["sizeBytes"] > 0 and sb["sizeBytes"] > 0:
            sza, szb = sa["sizeBytes"], sb["sizeBytes"]
            ratio = max(sza, szb) / min(sza, szb)
            if ratio >= 1.15:
                winner = "a" if sza > szb else "b"
                hi_m, lo_m = (sa["sizeMiB"], sb["sizeMiB"]) if winner == "a" else (sb["sizeMiB"], sa["sizeMiB"])
                return {
                    "winner": winner,
                    "significantUpgrade": ratio >= 1.25,
                    "reason": f"同编码({sa['codec'].upper()})同单集可比较体积/码率更高（{hi_m} MiB > {lo_m} MiB）",
                }
    elif sa["codec"] and sb["codec"] and sa["codec"] != sb["codec"]:
        codec_note = f"不同编码（{sa['codec'].upper()} vs {sb['codec'].upper()}）不按体积粗排；"

    # 4. Audio track quality
    if sa["audioTier"] != sb["audioTier"]:
        if sa["audioTier"] > sb["audioTier"]:
            sig = (policy_id == "anime_bd") or (sa["audioTier"] >= 3 and sb["audioTier"] <= 1)
            return {
                "winner": "a",
                "significantUpgrade": sig,
                "reason": f"{codec_note}音轨规格更高（{'/'.join(sa['audioTags']) or '高规'} > {'/'.join(sb['audioTags']) or '普通'}）",
            }
        sig = (policy_id == "anime_bd") or (sb["audioTier"] >= 3 and sa["audioTier"] <= 1)
        return {
            "winner": "b",
            "significantUpgrade": sig,
            "reason": f"{codec_note}音轨规格更高（{'/'.join(sb['audioTags']) or '高规'} > {'/'.join(sa['audioTags']) or '普通'}）",
        }

    # 5. Release completeness
    if sa["completenessScore"] != sb["completenessScore"]:
        diff = abs(sa["completenessScore"] - sb["completenessScore"])
        if sa["completenessScore"] > sb["completenessScore"]:
            return {
                "winner": "a",
                "significantUpgrade": diff >= 2,
                "reason": f"{codec_note}发布完整度更高（{'、'.join(sa['completenessTags']) or '更完整'}）",
            }
        return {
            "winner": "b",
            "significantUpgrade": diff >= 2,
            "reason": f"{codec_note}发布完整度更高（{'、'.join(sb['completenessTags']) or '更完整'}）",
        }

    return {
        "winner": "tie",
        "significantUpgrade": False,
        "reason": f"{codec_note}同档质量相当，无明显实质提升",
    }
