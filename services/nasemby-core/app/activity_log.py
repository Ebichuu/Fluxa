from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from app.config import DATA_DIR


LOG_PATH = DATA_DIR / "activity_log.jsonl"
_LOCK = threading.Lock()
BEIJING_TZ = timezone(timedelta(hours=8))
SENSITIVE_KEYS = {
    "password", "passwd", "token", "api_key", "api_hash", "cookie", "cookies",
    "secret", "authorization", "passkey", "sign",
}
SENSITIVE_QUERY_PATTERN = re.compile(
    r"([?&][^=&#\s]+)=([^&#\s]+)",
    re.I,
)
CREDENTIAL_ASSIGNMENT_PATTERN = re.compile(
    r"\b(password|passwd|token|api[_-]?key|api[_-]?hash|cookie|secret|authorization|passkey|sign)=([^\s&]+)",
    re.I,
)
BEARER_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+\-/]+=*", re.I)


def _now_text() -> str:
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _safe_text(value: Any, limit=500) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = SENSITIVE_QUERY_PATTERN.sub(r"\1=***", text)
    text = CREDENTIAL_ASSIGNMENT_PATTERN.sub(r"\1=***", text)
    text = BEARER_PATTERN.sub("Bearer ***", text)
    return text[:limit]


def _safe_value(value: Any, depth=0) -> Any:
    if depth >= 4:
        return "[truncated]"
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:50]:
            key_text = str(key)
            if any(hint in key_text.lower() for hint in SENSITIVE_KEYS):
                result[key_text] = "***"
            else:
                result[key_text] = _safe_value(item, depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item, depth + 1) for item in list(value)[:50]]
    if isinstance(value, str):
        return _safe_text(value)
    return value


def write_activity(category: str, action: str, status: str = "info", message: str = "", **meta: Any) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    row = {
        "time": _now_text(),
        "ts": int(time.time()),
        "category": _safe_text(category or "system", 80),
        "action": _safe_text(action or "", 120),
        "status": _safe_text(status or "info", 30),
        "message": _safe_text(message),
        "meta": _safe_value({k: v for k, v in meta.items() if v not in (None, "")}),
    }
    line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
    with _LOCK:
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return row


def read_activities(limit: int = 200, category: str = "") -> list[dict[str, Any]]:
    try:
        limit = max(1, min(int(limit or 200), 1000))
    except Exception:
        limit = 200
    category = str(category or "").strip()
    if not LOG_PATH.exists():
        return []
    with _LOCK:
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    rows: list[dict[str, Any]] = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if category and row.get("category") != category:
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def clear_activities() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        LOG_PATH.write_text("", encoding="utf-8")
