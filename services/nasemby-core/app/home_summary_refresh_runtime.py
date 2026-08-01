from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from app.home_summary_repository import MODULE_KEYS


DATE_MODULES = {"archive_today", "rss_resource_center"}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _today_key(value: datetime) -> str:
    return _as_utc(value).astimezone(timezone(timedelta(hours=8))).date().isoformat()


class HomeSummaryRefreshRuntime:
    def __init__(self, repository, collector, clock=None, lease_seconds=120):
        self.repository = repository
        self.collector = collector
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lease_seconds = max(5, int(lease_seconds))
        self._run_lock = threading.Lock()

    def _scope(self, module_key: str, today_key: str) -> str:
        return f"date:{today_key}" if module_key in DATE_MODULES else "global"

    def run_once(self) -> dict:
        if not self._run_lock.acquire(blocking=False):
            return {"status": "already_running", "ran": False}
        now = _as_utc(self.clock())
        token = None
        try:
            token = self.repository.claim_refresh(now=now, lease_seconds=self.lease_seconds)
            if not token:
                return {"status": "already_running", "ran": False}
            modules = self.collector.snapshot_modules()
            today_key = _today_key(now)
            written = []
            failed = []
            for module_key, payload in modules.items():
                scope_key = self._scope(module_key, today_key)
                try:
                    self.repository.write_success(
                        module_key,
                        scope_key,
                        payload,
                        observed_at=now,
                        fresh_until=now + timedelta(minutes=5),
                        confirmation="confirmed",
                        now=now,
                    )
                    written.append(module_key)
                except Exception as exc:
                    failed.append(module_key)
                    try:
                        self.repository.write_failure(
                            module_key, scope_key, type(exc).__name__, "模块刷新暂时失败", now=now
                        )
                    except Exception:
                        pass
            self.repository.finish_refresh(token, now=now, error_code="PARTIAL_FAILURE" if failed else "")
            token = None
            return {
                "status": "partial" if failed else "success",
                "ran": True,
                "modules": written,
                "failedModules": failed,
            }
        except Exception as exc:
            today_key = _today_key(now)
            for module_key in MODULE_KEYS:
                try:
                    self.repository.write_failure(
                        module_key,
                        self._scope(module_key, today_key),
                        type(exc).__name__,
                        "首页摘要刷新暂时失败",
                        now=now,
                    )
                except Exception:
                    pass
            if token:
                try:
                    self.repository.finish_refresh(token, now=now, error_code=type(exc).__name__)
                except Exception:
                    pass
            return {"status": "failed", "ran": True, "errorCode": type(exc).__name__}
        finally:
            self._run_lock.release()


def register_home_summary_refresh(app, runtime):
    app.extensions["mcc_home_summary_refresh"] = runtime
    return runtime
