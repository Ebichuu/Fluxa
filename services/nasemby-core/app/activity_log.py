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
# 重点视图只允许折叠这些状态的后台活动；error 永不折叠
IMPORTANT_FOLDABLE_STATUSES = {"success", "info", "skip"}
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


def _important_fold_identity(row: dict[str, Any]):
    """仅当后台活动可折叠时返回折叠分组键，否则返回 None。

    只折叠 request_id=background 且状态为 success/info/skip 的后台活动；
    error 和人工请求 ID 永不折叠。
    """
    if str(row.get("status") or "") not in IMPORTANT_FOLDABLE_STATUSES:
        return None
    meta = row.get("meta")
    request_id = str(meta.get("request_id") or "") if isinstance(meta, dict) else ""
    if request_id != "background":
        return None
    return (str(row.get("category") or ""), str(row.get("action") or ""), str(row.get("status") or ""))


def read_important_activities(limit: int = 200, category: str = "") -> list[dict[str, Any]]:
    """重点视图：先按 category 过滤，倒序扫描折叠重复后台活动，最后应用 limit。

    同一 category/action/status 的后台活动合并为一条（保留最新一条的位置），
    error 和人工请求 ID 永不折叠。
    """
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
    folded: dict[tuple[str, str, str], dict[str, Any]] = {}
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if category and row.get("category") != category:
            continue
        identity = _important_fold_identity(row)
        if identity is None:
            rows.append(row)
            continue
        entry = folded.get(identity)
        if entry is not None:
            # 倒序扫描时后续命中的都是更早的记录，只需累加并回填 firstTime
            entry["repeatCount"] += 1
            entry["firstTime"] = str(row.get("time") or entry["firstTime"])
            continue
        entry = dict(row)
        entry["repeatCount"] = 1
        entry["firstTime"] = str(row.get("time") or "")
        entry["lastTime"] = str(row.get("time") or "")
        folded[identity] = entry
        rows.append(entry)
    return rows[:limit]


def clear_activities() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        LOG_PATH.write_text("", encoding="utf-8")
