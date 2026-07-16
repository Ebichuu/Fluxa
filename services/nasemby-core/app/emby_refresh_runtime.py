from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify

from app.activity_log import write_activity
from app.config import DATA_DIR


COOLDOWN = timedelta(minutes=10)
BEIJING_TZ = timezone(timedelta(hours=8))
NAIVE_DATE_TIME = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2}(?:\.\d+)?)$"
)
EMPTY_STATE = {"lastTriggeredAt": "", "evidenceAt": ""}


class EmbyRefreshError(RuntimeError):
    def __init__(self, message: str, status: int, code: str):
        super().__init__(message)
        self.status = status
        self.code = code


def _iso_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def _parse_datetime(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    naive_match = NAIVE_DATE_TIME.fullmatch(text)
    if naive_match:
        text = f"{naive_match.group(1)}T{naive_match.group(2)}+08:00"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_service_timestamp(value) -> str:
    parsed = _parse_datetime(value)
    return _iso_timestamp(parsed) if parsed else ""


def _newest_timestamp(values) -> str:
    normalized = [parse_service_timestamp(value) for value in values]
    normalized = [value for value in normalized if value]
    return sorted(normalized)[-1] if normalized else ""


class EmbyRefreshStateStore:
    def __init__(self, path=None):
        self.path = Path(path or (DATA_DIR / "emby-refresh-state.json"))
        self.lock = threading.Lock()

    def read(self) -> dict:
        with self.lock:
            try:
                parsed = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return dict(EMPTY_STATE)
        if not isinstance(parsed, dict):
            return dict(EMPTY_STATE)
        return {
            "lastTriggeredAt": parsed.get("lastTriggeredAt", "")
            if isinstance(parsed.get("lastTriggeredAt"), str) else "",
            "evidenceAt": parsed.get("evidenceAt", "")
            if isinstance(parsed.get("evidenceAt"), str) else "",
        }

    def write(self, state: dict):
        payload = {
            "lastTriggeredAt": str(state.get("lastTriggeredAt") or ""),
            "evidenceAt": str(state.get("evidenceAt") or ""),
        }
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f"{self.path.name}.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)


def evaluate_refresh_status(input_data: dict) -> dict:
    now = input_data["now"]
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stored = input_data.get("stored") or EMPTY_STATE
    last_triggered_at = parse_service_timestamp(stored.get("lastTriggeredAt"))
    evidence_at = parse_service_timestamp(stored.get("evidenceAt"))
    latest_symedia_at = parse_service_timestamp(input_data.get("latestSymediaAt"))
    latest_emby_at = parse_service_timestamp(input_data.get("latestEmbyAt"))
    last_triggered = _parse_datetime(last_triggered_at)
    cooldown_end = last_triggered + COOLDOWN if last_triggered else None
    cooldown_until = _iso_timestamp(cooldown_end) if cooldown_end and cooldown_end > now else ""
    base = {
        "configured": bool(input_data.get("configured")),
        "connected": bool(input_data.get("connected")),
        "latestSymediaAt": latest_symedia_at,
        "latestEmbyAt": latest_emby_at,
        "lastTriggeredAt": last_triggered_at,
        "cooldownUntil": cooldown_until,
    }
    if not base["configured"] or not base["connected"]:
        return {
            **base,
            "state": "service_unavailable",
            "canRefresh": False,
            "reason": "Emby 或 Symedia 未配置或当前离线",
        }
    if not latest_symedia_at or not latest_emby_at:
        return {
            **base,
            "state": "insufficient_evidence",
            "canRefresh": False,
            "reason": "缺少可比较的 Symedia 或 Emby 时间证据",
        }
    if _parse_datetime(latest_symedia_at) <= _parse_datetime(latest_emby_at):
        return {
            **base,
            "state": "up_to_date",
            "canRefresh": False,
            "reason": "Emby 已跟上最新 Symedia 入库",
        }
    if evidence_at and _parse_datetime(latest_symedia_at) <= _parse_datetime(evidence_at):
        return {
            **base,
            "state": "up_to_date",
            "canRefresh": False,
            "reason": "这批 Symedia 入库证据已经触发过刷新",
        }
    if cooldown_until:
        return {
            **base,
            "state": "cooldown",
            "canRefresh": False,
            "reason": "Emby 刷新处于 10 分钟冷却期",
        }
    return {
        **base,
        "state": "ready",
        "canRefresh": True,
        "reason": "检测到 Symedia 有较新的入库证据",
    }


class EmbyRefreshService:
    def __init__(
        self,
        emby,
        symedia,
        store=None,
        activity_writer=None,
        clock=None,
    ):
        self.emby = emby
        self.symedia = symedia
        self.store = store or EmbyRefreshStateStore()
        self.activity_writer = activity_writer or write_activity
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.trigger_lock = threading.Lock()

    def _activity(self, status: str, message: str):
        try:
            self.activity_writer(
                "emby",
                "refresh_library",
                status,
                message,
            )
        except Exception:
            pass

    def get_status(self, now=None) -> dict:
        now = now or self.clock()
        configured = self.emby.is_configured() and self.symedia.is_configured()
        if not configured:
            return evaluate_refresh_status({
                "configured": False,
                "connected": False,
                "latestSymediaAt": "",
                "latestEmbyAt": "",
                "stored": self.store.read(),
                "now": now,
            })
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                symedia_future = executor.submit(
                    self.symedia.list_transfer_history,
                    50,
                    1,
                )
                emby_future = executor.submit(self.emby.get_recent_items, 20)
                symedia_page = symedia_future.result()
                emby_rows = emby_future.result()
        except Exception:
            return evaluate_refresh_status({
                "configured": True,
                "connected": False,
                "latestSymediaAt": "",
                "latestEmbyAt": "",
                "stored": self.store.read(),
                "now": now,
            })
        latest_symedia_at = _newest_timestamp(
            row.get("date")
            for row in symedia_page.get("rows", [])
            if isinstance(row, dict) and row.get("status") is not False
        )
        latest_emby_at = _newest_timestamp(
            row.get("dateCreated")
            for row in emby_rows
            if isinstance(row, dict)
        )
        return evaluate_refresh_status({
            "configured": True,
            "connected": True,
            "latestSymediaAt": latest_symedia_at,
            "latestEmbyAt": latest_emby_at,
            "stored": self.store.read(),
            "now": now,
        })

    def trigger(self, now=None) -> dict:
        now = now or self.clock()
        if not self.trigger_lock.acquire(blocking=False):
            raise EmbyRefreshError(
                "Emby 刷新请求正在提交",
                409,
                "EMBY_REFRESH_IN_PROGRESS",
            )
        try:
            status = self.get_status(now)
            if not status["canRefresh"]:
                unavailable = status["state"] == "service_unavailable"
                self._activity("skip", status["reason"])
                raise EmbyRefreshError(
                    status["reason"],
                    503 if unavailable else 409,
                    "EMBY_REFRESH_UNAVAILABLE" if unavailable else "EMBY_REFRESH_NOT_READY",
                )
            self._activity(
                "start",
                f"根据 Symedia 入库证据 {status['latestSymediaAt']} 触发媒体库刷新",
            )
            self.emby.trigger_library_refresh()
            triggered_at = _iso_timestamp(now)
            cooldown_until = _iso_timestamp(now + COOLDOWN)
            self.store.write({
                "lastTriggeredAt": triggered_at,
                "evidenceAt": status["latestSymediaAt"],
            })
            self._activity(
                "success",
                f"Emby 已接受媒体库刷新请求，证据时间 {status['latestSymediaAt']}",
            )
            return {
                "triggered": True,
                "message": "Emby 媒体库扫描已触发",
                "triggeredAt": triggered_at,
                "cooldownUntil": cooldown_until,
            }
        except EmbyRefreshError:
            raise
        except Exception as exc:
            message = str(exc) or "Emby 刷新接口调用失败"
            self._activity("error", message)
            raise EmbyRefreshError(
                message,
                502,
                "EMBY_REFRESH_FAILED",
            ) from exc
        finally:
            self.trigger_lock.release()


def register_emby_refresh(app: Flask, service=None):
    refresh_service = service or EmbyRefreshService(
        app.extensions["mcc_emby_client"],
        app.extensions["mcc_symedia_client"],
    )
    app.extensions["mcc_emby_refresh_service"] = refresh_service

    @app.get("/api/media/emby/refresh-status")
    def emby_refresh_status():
        return jsonify(refresh_service.get_status())

    @app.post("/api/media/emby/refresh")
    def emby_refresh():
        try:
            return jsonify(refresh_service.trigger()), 202
        except EmbyRefreshError as exc:
            return jsonify({"code": exc.code, "error": str(exc)}), exc.status

    return refresh_service
