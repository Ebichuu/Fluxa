from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from flask import Flask, jsonify

from app.task_exception_runtime import protection_rule


REQUEST_TIMEOUT_SECONDS = 15
RECENT_PAGE_SIZE = 50
MAX_TODAY_PAGES = 20
BEIJING_TZ = timezone(timedelta(hours=8))


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

    def _attempt(self, pathname: str):
        try:
            response = self.http.request(
                "GET",
                self._api_url(pathname),
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.access_token}",
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise RuntimeError("Symedia 请求失败") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        return response.status_code, data

    def _authed_get(self, pathname: str):
        if not self.access_token:
            self._login()
        status, data = self._attempt(pathname)
        if status in {401, 403} and not self.config.token:
            self.access_token = ""
            self._login()
            status, data = self._attempt(pathname)
        return status, data

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

    def _empty_summary(self, error=None):
        return {
            "configured": self.is_configured(),
            "connected": False,
            "webUrl": self.base_url,
            "lastCheckedAt": _iso_timestamp(self.clock()),
            "totals": {
                "records": 0,
                "today": 0,
                "processedToday": 0,
                "archivedToday": 0,
                "protectedToday": 0,
                "failedToday": 0,
                "failedRecent": 0,
                "protectedRecent": 0,
            },
            "latest": [],
            **({"error": error} if error else {}),
        }

    def get_summary(self) -> dict:
        if not self.base_url:
            return self._empty_summary("未配置 SYMEDIA_BASE_URL")
        if not self.is_configured():
            return self._empty_summary(
                "未配置 SYMEDIA_TOKEN 或 SYMEDIA_USERNAME/SYMEDIA_PASSWORD"
            )
        try:
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
            protected_today = sum(
                row.get("status") is False
                and bool(protection_rule(row.get("reasonCode"), row.get("errmsg")))
                for row in today_rows
            )
            failed_today = sum(
                row.get("status") is False
                and not protection_rule(row.get("reasonCode"), row.get("errmsg"))
                for row in today_rows
            )
            protected_recent = sum(
                row.get("status") is False
                and bool(protection_rule(row.get("reasonCode"), row.get("errmsg")))
                for row in recent_rows
            )
            failed_recent = sum(
                row.get("status") is False
                and not protection_rule(row.get("reasonCode"), row.get("errmsg"))
                for row in recent_rows
            )
            return {
                "configured": True,
                "connected": True,
                "webUrl": self.base_url,
                "lastCheckedAt": _iso_timestamp(self.clock()),
                "totals": {
                    "records": first_page["total"],
                    "today": len(today_rows),
                    "processedToday": len(today_rows),
                    "archivedToday": sum(row.get("status") is not False for row in today_rows),
                    "protectedToday": protected_today,
                    "failedToday": failed_today,
                    "failedRecent": failed_recent,
                    "protectedRecent": protected_recent,
                },
                "latest": [
                    {
                        "title": str(row.get("title") or "未识别条目"),
                        "year": str(row.get("year") or ""),
                        "mediaType": str(row.get("type") or ""),
                        "seasonEpisode": str(row.get("season_episode") or ""),
                        "mode": str(row.get("mode") or ""),
                        "status": row.get("status") is not False,
                        "errmsg": str(row.get("errmsg") or ""),
                        "date": str(row.get("date") or ""),
                    }
                    for row in recent_rows[:5]
                ],
            }
        except Exception as exc:
            return self._empty_summary(str(exc) or "Symedia 读取失败")


def register_symedia_read(app: Flask, environment=None, client_factory=None, clock=None):
    config = resolve_symedia_read_config(environment)
    client = client_factory(config) if client_factory else SymediaReadClient(config, clock=clock)
    app.extensions["mcc_symedia_client"] = client

    @app.get("/api/symedia/summary")
    def symedia_summary():
        return jsonify(client.get_summary())

    return client
