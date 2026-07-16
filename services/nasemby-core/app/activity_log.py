from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from app.config import DATA_DIR


LOG_PATH = DATA_DIR / "activity_log.jsonl"
_LOCK = threading.Lock()
BEIJING_TZ = timezone(timedelta(hours=8))


def _now_text() -> str:
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


def write_activity(category: str, action: str, status: str = "info", message: str = "", **meta: Any) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    row = {
        "time": _now_text(),
        "ts": int(time.time()),
        "category": str(category or "system"),
        "action": str(action or ""),
        "status": str(status or "info"),
        "message": str(message or ""),
        "meta": {k: v for k, v in meta.items() if v not in (None, "")},
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
