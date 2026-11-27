from __future__ import annotations

import re
from urllib.parse import urlsplit

SENSITIVE_RE = re.compile(
    r"(?i)(token|api[_-]?key|passkey|password|passwd|cookie|secret|authorization)=[^&\s]+"
)


def redact_text(value: object) -> str:
    """Redact sensitive query/header tokens WITHOUT truncating or dropping text."""
    text = str(value or "").strip()
    return SENSITIVE_RE.sub(r"\1=***", text)


def sanitize_text(value: object, max_len: int | None = 300) -> str:
    """Redact secrets and optionally truncate for UI display ONLY (never use truncated text for classification)."""
    text = redact_text(value)
    if max_len is None or max_len <= 0 or len(text) <= max_len:
        return text
    return text[:max_len]


def truncate_display_preserving_tail(text: str, max_len: int = 300) -> str:
    """Keep UI display text within `max_len` while preserving the meaningful error tail after long paths."""
    clean = redact_text(text)
    if len(clean) <= max_len:
        return clean
    if max_len < 40:
        return clean[:max_len]
    tail_len = max(120, max_len // 2)
    head_len = max_len - tail_len - 3
    return f"{clean[:head_len]} … {clean[-tail_len:]}"


def safe_web_url(web_url: str, base_url: str, path: str = "") -> str:
    root = (web_url or base_url or "").strip().rstrip("/")
    if not root:
        return ""
    parsed = urlsplit(root)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if not path:
        return root
    return f"{root}/{path.lstrip('/')}"


def compact_title(value: object) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").casefold())


def extract_season_episode(text: str) -> tuple[int | None, int | None]:
    raw = str(text or "")
    m = re.search(r"(?i)\bS(\d{1,2})\s*E(\d{1,4})\b", raw)
    if m:
        return int(m.group(1)), int(m.group(2))
    m_cn = re.search(r"第\s*(\d{1,2})\s*季.*?第\s*(\d{1,4})\s*[集话]", raw)
    if m_cn:
        return int(m_cn.group(1)), int(m_cn.group(2))
    m_ep = re.search(r"(?i)(?:第\s*(\d{1,4})\s*[集话]|\bEP?(\d{1,4})\b)", raw)
    m_s = re.search(r"(?i)(?:\bS(\d{1,2})\b|Season\s*(\d{1,2})|第\s*(\d{1,2})\s*季)", raw)
    season = int(next(g for g in m_s.groups() if g)) if m_s else None
    episode = int(next(g for g in m_ep.groups() if g)) if m_ep else None
    return season, episode
