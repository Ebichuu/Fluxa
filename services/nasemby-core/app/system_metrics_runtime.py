from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify

from app.services import dashboard_system_metrics


SYSTEM_METRICS_CACHE_SECONDS = 30


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _integer(value):
    return max(0, int(_number(value)))


def _percent(value):
    return round(max(0.0, min(100.0, _number(value))), 1)


def _iso_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_metrics(payload, sampled_at):
    source = payload if isinstance(payload, dict) else {}
    cpu = source.get("cpu") if isinstance(source.get("cpu"), dict) else {}
    memory = source.get("memory") if isinstance(source.get("memory"), dict) else {}
    disk = source.get("disk") if isinstance(source.get("disk"), dict) else {}
    network = source.get("network") if isinstance(source.get("network"), dict) else {}
    return {
        "ok": True,
        "checkedAt": _iso_timestamp(sampled_at),
        "cpu": {"percent": _percent(cpu.get("percent"))},
        "memory": {
            "total": _integer(memory.get("total")),
            "used": _integer(memory.get("used")),
            "available": _integer(memory.get("available")),
            "percent": _percent(memory.get("percent")),
        },
        "disk": {
            "total": _integer(disk.get("total")),
            "used": _integer(disk.get("used")),
            "free": _integer(disk.get("free")),
            "percent": _percent(disk.get("percent")),
        },
        "network": {
            "downBps": _integer(network.get("down_bps")),
            "upBps": _integer(network.get("up_bps")),
            "received": _integer(network.get("rx_total")),
            "sent": _integer(network.get("tx_total")),
        },
    }


class SystemMetricsService:
    def __init__(self, sampler=None, clock=None, cache_seconds=SYSTEM_METRICS_CACHE_SECONDS):
        self.sampler = sampler or dashboard_system_metrics
        self.clock = clock or time.time
        self.cache_seconds = cache_seconds
        self.lock = threading.Lock()
        self.cached_at = 0.0
        self.cached = None

    def get(self):
        now = self.clock()
        with self.lock:
            if self.cached is not None and now - self.cached_at < self.cache_seconds:
                return {**self.cached, "cached": True}
            payload = self.sampler()
            self.cached = _safe_metrics(payload, now)
            self.cached_at = now
            return {**self.cached, "cached": False}


def register_system_metrics(app: Flask, sampler=None, clock=None):
    service = SystemMetricsService(sampler=sampler, clock=clock)
    app.extensions["mcc_system_metrics"] = service

    @app.get("/api/v2/system/metrics", endpoint="mcc_v2_system_metrics")
    def system_metrics():
        try:
            return jsonify(service.get())
        except Exception:
            return jsonify({
                "ok": False,
                "code": "SYSTEM_METRICS_UNAVAILABLE",
                "error": "系统指标暂不可用",
            }), 502

    return service
