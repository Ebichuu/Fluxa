from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from flask import Flask, jsonify

from app.symedia_evidence_runtime import (
    is_cancelled_override,
    is_low_score_protection,
    is_successful_replacement,
    normalize_symedia_status,
    symedia_outcome,
    symedia_protection_rule,
)
from app.task_public_runtime import safe_public_text


REQUEST_TIMEOUT_SECONDS = 15
RECENT_PAGE_SIZE = 50
MAX_TODAY_PAGES = 20
BEIJING_TZ = timezone(timedelta(hours=8))
SYMEDIA_CAPABILITY_NAMES = (
    "transferHistory",
    "archiveMonitor",
    "cloudDriveListener",
    "webhook",
    "strmGenerator",
    "archiveScheduler",
    "fileObserver",
)


@dataclass(frozen=True)
class SymediaReadConfig:
    base_url: str = ""
    token: str = ""
    username: str = ""
    password: str = ""


def resolve_symedia_read_config(environment=None) -> SymediaReadConfig:
    environment = os.environ if environment is None else environment
    return SymediaReadConfig(
        base_url=str(environment.get("SYMEDIA_BASE_URL") or "").strip().rstrip("/"),
        token=str(environment.get("SYMEDIA_TOKEN") or "").strip(),
        username=str(environment.get("SYMEDIA_USERNAME") or "").strip(),
        password=str(environment.get("SYMEDIA_PASSWORD") or ""),
    )


def _iso_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


class SymediaReadClient:
    def __init__(self, config: SymediaReadConfig, session=None, clock=None):
        self.config = config
        self.base_url = config.base_url.strip().rstrip("/")
        self.http = session or requests
        self.clock = clock or (lambda: datetime.now(BEIJING_TZ))
        self.access_token = ""

    def reconfigure(self, config: SymediaReadConfig) -> None:
        self.config = config
        self.base_url = config.base_url.strip().rstrip("/")
        self.access_token = ""

    def is_configured(self) -> bool:
        return bool(
            self.base_url
            and (self.config.token or (self.config.username and self.config.password))
        )

    def _api_url(self, pathname: str) -> str:
        return f"{self.base_url}/api/v1/{pathname.lstrip('/')}"

    def _login(self) -> str:
        if self.config.token:
            self.access_token = self.config.token
            return self.access_token
        if self.access_token:
            return self.access_token
        try:
            response = self.http.request(
                "POST",
                self._api_url("login/access-token"),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"username": self.config.username, "password": self.config.password},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise RuntimeError("Symedia 登录请求失败") from exc
        if response.status_code >= 400:
            raise RuntimeError(f"Symedia 登录失败：{response.status_code}")
        try:
            data = response.json()
        except ValueError:
            data = {}
        token = str(
            (data.get("access_token") or data.get("token") or "")
            if isinstance(data, dict)
            else ""
        ).strip()
        if not token:
            raise RuntimeError("Symedia 登录成功但没有返回 access_token")
        self.access_token = token
        return token

    def _attempt(self, pathname: str, *, method="GET", json_payload=None):
        try:
            response = self.http.request(
                method,
                self._api_url(pathname),
                headers={
                    "Accept": "application/json",
                    **({"Content-Type": "application/json"} if json_payload is not None else {}),
                    "Authorization": f"Bearer {self.access_token}",
                },
                **({"json": json_payload} if json_payload is not None else {}),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise RuntimeError("Symedia 请求失败") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        return response.status_code, data

    def _authed_request(self, pathname: str, *, method="GET", json_payload=None):
        if not self.access_token:
            self._login()
        status, data = self._attempt(pathname, method=method, json_payload=json_payload)
        if status in {401, 403} and not self.config.token:
            self.access_token = ""
            self._login()
            status, data = self._attempt(pathname, method=method, json_payload=json_payload)
        return status, data

    def _authed_get(self, pathname: str):
        return self._authed_request(pathname)

    def list_transfer_tasks(self) -> list[dict]:
        if not self.is_configured():
            raise RuntimeError("Symedia 未配置")
        status, data = self._authed_get("transfer/transfer_list")
        if status in {401, 403}:
            raise RuntimeError("Symedia 认证失败：Token 无效或账号密码错误")
        if status >= 400:
            raise RuntimeError(f"Symedia 归档任务读取失败：{status}")
        body = data.get("data") if isinstance(data, dict) and "data" in data else data
        rows = body
        if isinstance(body, dict):
            rows = next(
                (
                    body.get(key)
                    for key in ("items", "list", "transfer_list", "tasks")
                    if isinstance(body.get(key), list)
                ),
                None,
            )
        if not isinstance(rows, list):
            raise RuntimeError("Symedia 归档任务响应结构无效")
        return [dict(row) for row in rows if isinstance(row, dict)]

    def manual_transfer_file(self, file_path: str, transfer_task_id: str) -> dict:
        if not self.is_configured():
            raise RuntimeError("Symedia 未配置")
        file_path = str(file_path or "").strip()
        transfer_task_id = str(transfer_task_id or "").strip()
        if not file_path.startswith("/") or not transfer_task_id:
            raise ValueError("Symedia 单文件归档目标无效")
        status, data = self._authed_request(
            "transfer/manual",
            method="POST",
            json_payload={
                "items": [{"type": "file", "path": file_path}],
                "transfer_task_id": transfer_task_id,
            },
        )
        if status in {401, 403}:
            raise RuntimeError("Symedia 认证失败：Token 无效或账号密码错误")
        if status >= 400:
            raise RuntimeError(f"Symedia 单文件归档提交失败：{status}")
        if not isinstance(data, dict) or data.get("success") is False:
            message = str(data.get("message") or "") if isinstance(data, dict) else ""
            raise RuntimeError(message or "Symedia 未接受单文件归档任务")
        return data

    def list_transfer_history(self, count=200, page=1) -> dict:
        if not self.is_configured():
            raise RuntimeError("Symedia 未配置")
        query = urlencode({
            "page": page,
            "count": max(1, min(500, count)),
            "sort_by": "date",
            "sort_order": "desc",
        })
        status, data = self._authed_get(f"history/transfer?{query}")
        if status in {401, 403}:
            raise RuntimeError("Symedia 认证失败：Token 无效或账号密码错误")
        if status >= 400:
            raise RuntimeError(f"Symedia 响应异常：{status}")
        body = data.get("data") if isinstance(data, dict) and "data" in data else data
        body = body if isinstance(body, dict) else {}
        raw_rows = body.get("list") if isinstance(body.get("list"), list) else []
        rows = [row for row in raw_rows if isinstance(row, dict)]
        try:
            total = int(body.get("total", len(rows)) or 0)
        except (TypeError, ValueError):
            total = 0
        return {"rows": rows, "total": total}

    def _capabilities(self, transfer_state, transfer_reason, observed_at=""):
        capabilities = {
            name: {
                "state": "unknown",
                "reasonCode": "NOT_INTEGRATED",
                "observedAt": "",
            }
            for name in SYMEDIA_CAPABILITY_NAMES
        }
        capabilities["transferHistory"] = {
            "state": transfer_state,
            "reasonCode": transfer_reason,
            "observedAt": observed_at,
        }
        return capabilities

    @staticmethod
    def _empty_wash_summary():
        return {
            "scope": "today",
            "evidenceState": "insufficient",
            "successfulReplacements": None,
            "lowScoreProtected": None,
            "cancelledOverrides": None,
            "realFailures": None,
            "latestTarget": None,
        }

    def _empty_summary(self, error=None, transfer_reason="SYMEDIA_NOT_CONFIGURED"):
        checked_at = _iso_timestamp(self.clock())
        return {
            "configured": self.is_configured(),
            "connected": False,
            "webUrl": self.base_url,
            "lastCheckedAt": checked_at,
            "totals": {
                "records": 0,
                "today": 0,
                "processedToday": 0,
                "archivedToday": 0,
                "protectedToday": 0,
                "failedToday": 0,
                "unknownToday": 0,
                "failedRecent": 0,
                "protectedRecent": 0,
            },
            "capabilities": self._capabilities(
                "unknown" if transfer_reason == "SYMEDIA_NOT_CONFIGURED" else "unavailable",
                transfer_reason,
                "" if transfer_reason == "SYMEDIA_NOT_CONFIGURED" else checked_at,
            ),
            "washSummary": self._empty_wash_summary(),
            "latest": [],
            **({"error": error} if error else {}),
        }

    def _history_window(self):
        today = self.clock().strftime("%Y-%m-%d")
        first_page = self.list_transfer_history(RECENT_PAGE_SIZE, 1)
        recent_rows = first_page["rows"]
        today_rows = [row for row in recent_rows if str(row.get("date") or "").startswith(today)]
        current_rows = recent_rows
        page = 2
        while (
            page <= MAX_TODAY_PAGES
            and len(current_rows) == RECENT_PAGE_SIZE
            and str(current_rows[-1].get("date") or "").startswith(today)
        ):
            current_rows = self.list_transfer_history(RECENT_PAGE_SIZE, page)["rows"]
            today_rows.extend(
                row for row in current_rows
                if str(row.get("date") or "").startswith(today)
            )
            page += 1
        truncated = (
            page > MAX_TODAY_PAGES
            and len(current_rows) == RECENT_PAGE_SIZE
            and bool(current_rows)
            and str(current_rows[-1].get("date") or "").startswith(today)
        )
        return first_page["total"], recent_rows, today_rows, truncated

    @staticmethod
    def _dedupe_rows(rows):
        result = []
        seen = set()
        for index, row in enumerate(rows):
            path = next((
                str(row.get(key) or "").strip().replace("\\", "/").casefold()
                for key in ("src", "dest")
                if str(row.get(key) or "").strip()
            ), "")
            identity = f"path:{path}" if path else f"id:{str(row.get('id') or '').strip()}"
            if identity == "id:":
                identity = f"row:{index}"
            if identity in seen:
                continue
            seen.add(identity)
            result.append(row)
        return result

    @staticmethod
    def _result_counts(rows):
        counts = {"archived": 0, "protected": 0, "failed": 0, "unknown": 0}
        for row in rows:
            status = normalize_symedia_status(row.get("status"))
            if status is True:
                counts["archived"] += 1
            elif status is False and symedia_protection_rule(row):
                counts["protected"] += 1
            elif status is False:
                counts["failed"] += 1
            else:
                counts["unknown"] += 1
        return counts

    @staticmethod
    def _latest_target(rows):
        if not rows:
            return None
        row = rows[0]
        return {
            "title": str(row.get("title") or "未识别条目"),
            "seasonEpisode": str(row.get("season_episode") or ""),
            "mediaType": str(row.get("type") or ""),
            "date": str(row.get("date") or ""),
            "outcome": symedia_outcome(row),
        }

    @classmethod
    def _wash_summary(cls, today_rows, recent_rows, truncated, real_failures):
        statuses = [normalize_symedia_status(row.get("status")) for row in today_rows]
        evidence_state = "partial" if truncated or any(status is None for status in statuses) else "verified"
        failed_rows = [row for row, status in zip(today_rows, statuses) if status is False]
        return {
            "scope": "today",
            "evidenceState": evidence_state,
            "successfulReplacements": sum(is_successful_replacement(row) for row in today_rows),
            "lowScoreProtected": sum(is_low_score_protection(row) for row in failed_rows),
            "cancelledOverrides": sum(is_cancelled_override(row) for row in failed_rows),
            "realFailures": real_failures,
            "latestTarget": cls._latest_target(recent_rows),
        }

    @staticmethod
    def _latest_items(rows):
        return [{
            "title": str(row.get("title") or "未识别条目"),
            "year": str(row.get("year") or ""),
            "mediaType": str(row.get("type") or ""),
            "seasonEpisode": str(row.get("season_episode") or ""),
            "mode": str(row.get("mode") or ""),
            "status": normalize_symedia_status(row.get("status")),
            "outcome": symedia_outcome(row),
            "errmsg": safe_public_text(row.get("errmsg")),
            "date": str(row.get("date") or ""),
        } for row in rows[:5]]

    def get_summary(self) -> dict:
        if not self.base_url:
            return self._empty_summary("未配置 SYMEDIA_BASE_URL")
        if not self.is_configured():
            return self._empty_summary(
                "未配置 SYMEDIA_TOKEN 或 SYMEDIA_USERNAME/SYMEDIA_PASSWORD"
            )
        try:
            total, recent_rows, today_rows, truncated = self._history_window()
            recent_rows = self._dedupe_rows(recent_rows)
            today_rows = self._dedupe_rows(today_rows)
            checked_at = _iso_timestamp(self.clock())
            today_counts = self._result_counts(today_rows)
            recent_counts = self._result_counts(recent_rows)
            return {
                "configured": True,
                "connected": True,
                "webUrl": self.base_url,
                "lastCheckedAt": checked_at,
                "totals": {
                    "records": total,
                    "today": len(today_rows),
                    "processedToday": len(today_rows),
                    "archivedToday": today_counts["archived"],
                    "protectedToday": today_counts["protected"],
                    "failedToday": today_counts["failed"],
                    "unknownToday": today_counts["unknown"],
                    "failedRecent": recent_counts["failed"],
                    "protectedRecent": recent_counts["protected"],
                },
                "capabilities": self._capabilities(
                    "available", "TRANSFER_HISTORY_READABLE", checked_at,
                ),
                "washSummary": self._wash_summary(
                    today_rows, recent_rows, truncated, today_counts["failed"],
                ),
                "latest": self._latest_items(recent_rows),
            }
        except Exception as exc:
            return self._empty_summary(
                str(exc) or "Symedia 读取失败",
                "TRANSFER_HISTORY_UNAVAILABLE",
            )


def register_symedia_read(app: Flask, environment=None, client_factory=None, clock=None):
    config = resolve_symedia_read_config(environment)
    client = client_factory(config) if client_factory else SymediaReadClient(config, clock=clock)
    app.extensions["mcc_symedia_client"] = client

    @app.get("/api/symedia/summary")
    def symedia_summary():
        return jsonify(client.get_summary())

    return client
