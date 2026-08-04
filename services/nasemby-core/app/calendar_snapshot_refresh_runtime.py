from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone


UTC = timezone.utc


def _utc(value):
    value = value or datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class CalendarSnapshotRefreshRuntime:
    def __init__(self, repository, builder, clock=None, lease_seconds=120):
        self.repository = repository
        self.builder = builder
        self.clock = clock or (lambda: datetime.now(UTC))
        self.lease_seconds = max(5, int(lease_seconds))
        self._run_lock = threading.Lock()

    def request_default_scope(self, *, now=None):
        current = _utc(now or self.clock())
        shanghai = current.astimezone(timezone(timedelta(hours=8)))
        return self.repository.request_refresh(
            shanghai.year,
            shanghai.month,
            "all",
            False,
            now=current,
            idempotency_key=f"calendar-default:{shanghai.year:04d}-{shanghai.month:02d}",
        )

    def run_once(self):
        if not self._run_lock.acquire(blocking=False):
            return {"status": "already_running", "ran": False}
        current = _utc(self.clock())
        claim = None
        try:
            self.repository.enqueue_due(now=current)
            claim = self.repository.claim_next(now=current, lease_seconds=self.lease_seconds)
            if not claim:
                return {"status": "idle", "ran": False}
            payload = self.builder(
                claim["year"],
                claim["month"],
                claim["mediaType"],
                include_unlinked=claim["includeUnlinked"],
            )
            if not isinstance(payload, dict):
                raise ValueError("日历快照构建结果无效")
            calendar = payload.get("calendar") if isinstance(payload.get("calendar"), dict) else {}
            confirmation = str(payload.get("confirmation") or "confirmed")
            if confirmation not in {"confirmed", "partial", "unknown"}:
                confirmation = "partial" if calendar.get("errors") else "confirmed"
            observed_at = current
            fresh_until = current + timedelta(seconds=300)
            self.repository.complete_success(
                claim,
                payload,
                observed_at=observed_at,
                fresh_until=fresh_until,
                confirmation=confirmation,
                now=current,
            )
            return {"status": "partial" if confirmation != "confirmed" else "success", "ran": True, "scopeKey": claim["scopeKey"]}
        except Exception as exc:
            if claim:
                try:
                    self.repository.complete_failure(
                        claim,
                        type(exc).__name__,
                        "日历刷新暂时失败",
                        now=current,
                    )
                except Exception:
                    pass
            return {"status": "failed", "ran": bool(claim), "errorCode": type(exc).__name__}
        finally:
            self._run_lock.release()


def register_calendar_snapshot_refresh(app, runtime):
    app.extensions["mcc_calendar_snapshot_refresh"] = runtime
    return runtime
