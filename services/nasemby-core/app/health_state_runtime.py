from __future__ import annotations

import threading
from datetime import datetime, timezone


HEALTH_PRIORITY = {
    "normal": 0,
    "protected": 1,
    "waiting": 2,
    "evidence_insufficient": 3,
    "action_required": 4,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def combine_health(*states: str) -> str:
    values = [str(state or "evidence_insufficient") for state in states]
    return max(values, key=lambda state: HEALTH_PRIORITY.get(state, HEALTH_PRIORITY["evidence_insufficient"])) if values else "evidence_insufficient"


def evidence(*, state: str, source: str, reason_code: str = "", reason_text: str = "", observed_at: str = "", fresh_until: str = "") -> dict:
    return {
        "healthState": state,
        "observedAt": observed_at or _now(),
        "freshUntil": fresh_until,
        "source": source,
        "reasonCode": reason_code,
        "reasonText": reason_text,
    }


class SchedulerStatusRegistry:
    """Small in-process heartbeat registry used to distinguish config from a live scheduler."""

    def __init__(self, clock=None):
        self.clock = clock or _now
        self._lock = threading.Lock()
        self._states: dict[str, dict] = {}

    def register(self, name: str, *, enabled: bool, configured: bool = True) -> None:
        with self._lock:
            current = self._states.get(name) or {}
            self._states[name] = {
                "name": name,
                "enabled": bool(enabled),
                "configured": bool(configured),
                "started": bool(current.get("started")),
                "lastRunAt": str(current.get("lastRunAt") or ""),
                "lastError": str(current.get("lastError") or ""),
                "checkedAt": self.clock(),
            }

    def mark_started(self, name: str) -> None:
        with self._lock:
            state = self._states.setdefault(name, {"name": name, "enabled": True, "configured": True})
            state.update({"started": True, "checkedAt": self.clock()})

    def mark_run(self, name: str, *, error: str = "") -> None:
        with self._lock:
            state = self._states.setdefault(name, {"name": name, "enabled": True, "configured": True})
            now = self.clock()
            state.update({"started": True, "lastRunAt": now, "checkedAt": now, "lastError": str(error or "")})

    def snapshot(self, name: str) -> dict:
        with self._lock:
            state = dict(self._states.get(name) or {
                "name": name,
                "enabled": False,
                "configured": False,
                "started": False,
                "lastRunAt": "",
                "lastError": "",
                "checkedAt": self.clock(),
            })
        return state
