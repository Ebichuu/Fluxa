from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from flask import Flask, jsonify


REQUEST_TIMEOUT_SECONDS = 15
SECUPLOAD_PLUGIN_KEY = "secupload_115"


@dataclass(frozen=True)
class TorraReadConfig:
    base_url: str = ""
    token: str = ""
    username: str = ""
    password: str = ""


def resolve_torra_read_config(environment=None) -> TorraReadConfig:
    environment = os.environ if environment is None else environment
    return TorraReadConfig(
        base_url=str(environment.get("TORRA_BASE_URL") or "").strip().rstrip("/"),
        token=str(environment.get("TORRA_TOKEN") or "").strip(),
        username=str(environment.get("TORRA_USERNAME") or "").strip(),
        password=str(environment.get("TORRA_PASSWORD") or ""),
    )


def _iso_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def extract_subscription_rows(data) -> list[dict]:
    rows = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        body = data.get("data")
        if isinstance(body, dict) and isinstance(body.get("subscriptions"), list):
            rows = body["subscriptions"]
        elif isinstance(data.get("subscriptions"), list):
            rows = data["subscriptions"]
    return [row for row in rows if isinstance(row, dict)]


def _integer(value, fallback=0) -> int:
    match = re.match(r"^[+-]?\d+", str(value if value is not None else "").strip())
    return int(match.group(0)) if match else fallback


def _media_type(value) -> str:
    normalized = str(value if value is not None else "").strip().lower()
    if normalized in {"movie", "电影"}:
        return "movie"
    if normalized in {"tv", "series", "电视剧", "剧集"}:
        return "tv"
    return ""


def _compact_title(value) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum())


def _run_counts(message: str) -> dict:
    text = str(message or "")
    success = re.search(r"成功\s*(\d+)\s*个", text)
    failed = re.search(r"失败\s*(\d+)\s*个", text)
    return {
        "success": int(success.group(1)) if success else None,
        "failed": int(failed.group(1)) if failed else None,
    }


def _safe_run_message(status: str, counts: dict) -> str:
    if counts.get("success") is not None or counts.get("failed") is not None:
        return f"任务完成，成功 {counts.get('success') or 0} 个，失败 {counts.get('failed') or 0} 个"
    labels = {
        "queued": "任务正在排队",
        "pending": "任务正在等待",
        "running": "任务正在运行",
        "stopping": "任务正在停止",
        "success": "任务运行成功",
        "failed": "任务运行失败",
        "cancelled": "任务已取消",
    }
    return labels.get(str(status or "").lower(), "任务状态已更新")


def subscription_matches(row: dict, target: dict) -> bool:
    row_tmdb_id = _integer(row.get("tmdb_id", row.get("tmdbid")))
    target_tmdb_id = _integer(target.get("tmdbId"))
    row_type = _media_type(row.get("media_type", row.get("type")))
    target_type = str(target.get("mediaType") or "")
    target_season = _integer(target.get("seasonNumber"))
    if row_tmdb_id and target_tmdb_id:
        if row_tmdb_id != target_tmdb_id or row_type != target_type:
            return False
        if target_type == "movie":
            return True
        row_season = _integer(row.get("season_number", row.get("season")), -1)
        return row_season <= 0 or target_season <= 0 or row_season == target_season
    row_title = _compact_title(row.get("name", row.get("keyword")))
    target_title = _compact_title(target.get("title"))
    if not row_title or row_title != target_title or row_type != target_type:
        return False
    row_year = str(row.get("year") or "").strip()
    target_year = str(target.get("year") or "")
    if row_year and target_year and row_year != target_year:
        return False
    if target_type == "tv":
        row_season = _integer(row.get("season_number", row.get("season")), -1)
        if row_season > 0 and target_season > 0 and row_season != target_season:
            return False
    return True


def find_subscription(rows: list[dict], target: dict):
    return next((row for row in rows if subscription_matches(row, target)), None)


class TorraReadClient:
    def __init__(self, config: TorraReadConfig, session=None, clock=None):
        self.config = config
        self.base_url = config.base_url.strip().rstrip("/")
        self.http = session or requests
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.access_token = ""

    def reconfigure(self, config: TorraReadConfig) -> None:
        self.config = config
        self.base_url = config.base_url.strip().rstrip("/")
        self.access_token = ""

    def _use_password(self) -> bool:
        return bool(self.config.username and self.config.password)

    def is_configured(self) -> bool:
        return bool(self.base_url and (self.config.token or self._use_password()))

    def _login(self) -> str:
        if self.config.token:
            self.access_token = self.config.token
            return self.access_token
        if self.access_token:
            return self.access_token
        if not self._use_password():
            raise RuntimeError("未配置 TORRA_TOKEN 或 TORRA_USERNAME/TORRA_PASSWORD")
        try:
            response = self.http.request(
                "POST",
                f"{self.base_url}/api/v1/login/access-token",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"username": self.config.username, "password": self.config.password},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise RuntimeError("Torra 登录请求失败") from exc
        if response.status_code >= 400:
            raise RuntimeError(f"Torra 登录失败：{response.status_code}")
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
            raise RuntimeError("Torra 登录成功但没有返回 access_token")
        self.access_token = token
        return token

    def _attempt(self, pathname: str, token: str):
        try:
            response = self.http.request(
                "GET",
                f"{self.base_url}{pathname}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise RuntimeError("Torra 请求失败") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        return response.status_code, data

    def _fetch_json(self, pathname: str):
        status, data = self._attempt(pathname, self._login())
        if status in {401, 403} and not self.config.token and self._use_password():
            self.access_token = ""
            status, data = self._attempt(pathname, self._login())
        return status, data

    def _write_attempt(self, method: str, pathname: str, token: str, payload=None):
        try:
            response = self.http.request(
                method,
                f"{self.base_url}{pathname}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
                timeout=REQUEST_TIMEOUT_SECONDS if method == "POST" else 30,
            )
        except requests.RequestException as exc:
            raise RuntimeError("Torra 写入请求失败") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        return response.status_code, data

    def _write_json(self, pathname: str, payload=None):
        status, data = self._write_attempt("POST", pathname, self._login(), payload)
        if status in {401, 403} and not self.config.token and self._use_password():
            self.access_token = ""
            status, data = self._write_attempt("POST", pathname, self._login(), payload)
        if status in {401, 403}:
            raise RuntimeError("Torra Token 无效或已过期")
        if status >= 400:
            raise RuntimeError(f"Torra 响应异常：{status}")
        return data if isinstance(data, dict) else {}

    def list_subscriptions(self) -> list[dict]:
        if not self.is_configured():
            raise RuntimeError("未配置 Torra 地址或认证信息")
        status, data = self._fetch_json("/api/v1/subscriptions")
        if status in {401, 403}:
            raise RuntimeError("Torra Token 无效或已过期")
        if status >= 400:
            raise RuntimeError(f"Torra 响应异常：{status}")
        return extract_subscription_rows(data)

    def inspect_duplicate(self, target: dict) -> dict:
        if not self.is_configured():
            return {
                "checked": False,
                "found": False,
                "subscriptionId": "",
                "name": "",
                "error": "Torra 未配置，未执行在线查重",
            }
        try:
            existing = find_subscription(self.list_subscriptions(), target)
            return {
                "checked": True,
                "found": existing is not None,
                "subscriptionId": str((existing or {}).get("id") or ""),
                "name": str((existing or {}).get("name") or (existing or {}).get("keyword") or ""),
            }
        except Exception as exc:
            return {
                "checked": False,
                "found": False,
                "subscriptionId": "",
                "name": "",
                "error": str(exc),
            }

    def get_summary(self) -> dict:
        base = {
            "configured": self.is_configured(),
            "connected": False,
            "webUrl": self.base_url,
            "lastCheckedAt": _iso_timestamp(self.clock()),
            "counts": {"total": 0, "active": 0, "completed": 0, "running": 0},
        }
        if not self.is_configured():
            return {**base, "error": "未配置 Torra 地址或认证信息"}
        try:
            rows = self.list_subscriptions()
            return {
                **base,
                "connected": True,
                "lastCheckedAt": _iso_timestamp(self.clock()),
                "counts": {
                    "total": len(rows),
                    "active": sum(row.get("enabled") is not False and row.get("completed") is not True for row in rows),
                    "completed": sum(row.get("completed") is True for row in rows),
                    "running": sum(row.get("is_running") is True for row in rows),
                },
            }
        except Exception as exc:
            return {**base, "error": str(exc) or "Torra 读取失败"}

    def get_secupload_summary(self) -> dict:
        """读取 Torra 秒传插件的可验证摘要，不访问任何写接口。"""
        base = {
            "configured": self.is_configured(),
            "connected": False,
            "pluginKey": SECUPLOAD_PLUGIN_KEY,
            "pluginEnabled": False,
            "readable": False,
            "perFileEvidence": False,
            "configItems": [],
            "tasks": [],
            "schedules": [],
            "recentRuns": [],
            "lastCheckedAt": _iso_timestamp(self.clock()),
            "error": "",
        }
        if not self.is_configured():
            return {**base, "error": "未配置 Torra 地址或认证信息"}
        try:
            status, payload = self._fetch_json(f"/api/v1/plugins/{SECUPLOAD_PLUGIN_KEY}")
            if status in {401, 403}:
                raise RuntimeError("Torra Token 无效或已过期")
            if status >= 400:
                raise RuntimeError(f"Torra 插件读取失败：{status}")
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                raise RuntimeError("Torra 秒传插件响应结构无效")

            manifest = data.get("manifest") if isinstance(data.get("manifest"), dict) else {}
            config_items = []
            for item in data.get("config_items") or []:
                if not isinstance(item, dict):
                    continue
                config_items.append({
                    "itemId": str(item.get("item_id") or ""),
                    "name": str(item.get("name") or ""),
                    "enabled": item.get("enabled") is not False,
                    "updatedAt": str(item.get("updated_at") or ""),
                })

            tasks = []
            for task in data.get("tasks") or []:
                if not isinstance(task, dict):
                    continue
                tasks.append({
                    "key": str(task.get("key") or ""),
                    "name": str(task.get("name") or ""),
                    "allowSchedule": bool(task.get("allow_schedule")),
                    "allowManualRun": bool(task.get("allow_manual_run")),
                })

            schedules = []
            for schedule in data.get("schedules") or []:
                if not isinstance(schedule, dict):
                    continue
                schedules.append({
                    "taskKey": str(schedule.get("task_key") or ""),
                    "targetItemId": str(schedule.get("target_item_id") or ""),
                    "enabled": schedule.get("enabled") is True,
                    "cron": str(schedule.get("cron") or ""),
                    "nextRunAt": str(schedule.get("next_run_at") or ""),
                    "lastRunAt": str(schedule.get("last_run_at") or ""),
                })

            recent_runs = []
            for run in data.get("recent_runs") or []:
                if not isinstance(run, dict):
                    continue
                raw_message = str(run.get("message") or "")
                counts = _run_counts(raw_message)
                status_text = str(run.get("status") or "")
                recent_runs.append({
                    "runId": str(run.get("run_id") or ""),
                    "taskKey": str(run.get("task_key") or ""),
                    "targetItemId": str(run.get("target_item_id") or ""),
                    "trigger": str(run.get("trigger") or ""),
                    "status": status_text,
                    "message": _safe_run_message(status_text, counts),
                    "counts": counts,
                    "startedAt": str(run.get("started_at") or ""),
                    "finishedAt": str(run.get("finished_at") or ""),
                    "createdAt": str(run.get("created_at") or ""),
                })

            active_runs = sum(run["status"] in {"queued", "pending", "running", "stopping"} for run in recent_runs)
            latest_run = recent_runs[0] if recent_runs else None
            latest_schedule = max(
                schedules,
                key=lambda row: str(row.get("lastRunAt") or ""),
                default=None,
            )
            return {
                **base,
                "connected": True,
                "readable": True,
                "pluginEnabled": manifest.get("enabled") is not False,
                "configItems": config_items,
                "tasks": tasks,
                "schedules": schedules,
                "recentRuns": recent_runs,
                "activeRuns": active_runs,
                "latestRun": latest_run,
                "lastRunAt": str((latest_run or {}).get("finishedAt") or ""),
                "nextRunAt": str((latest_schedule or {}).get("nextRunAt") or ""),
                "lastCheckedAt": _iso_timestamp(self.clock()),
            }
        except Exception as exc:
            return {**base, "error": str(exc) or "Torra 秒传插件读取失败"}

    def push_subscription(self, subscription: dict) -> dict:
        if not self.is_configured():
            raise RuntimeError("未配置 Torra 地址或认证信息")
        if not str(subscription.get("save_path") or "").strip():
            raise RuntimeError("分类保存路径为空，已停止推送")
        if not str(subscription.get("downloader_id") or "").strip():
            raise RuntimeError("Torra 下载器 ID 未核对，已停止推送")
        target = {
            "title": subscription.get("name") or subscription.get("keyword") or "",
            "mediaType": subscription.get("media_type") or "",
            "tmdbId": str(subscription.get("tmdb_id") or ""),
            "seasonNumber": subscription.get("season_number") or 0,
            "year": subscription.get("year") or "",
        }
        existing = find_subscription(self.list_subscriptions(), target)
        if existing:
            subscription_id = str(existing.get("id") or "").strip()
            if not subscription_id:
                raise RuntimeError("Torra 已有订阅缺少 ID，已停止推送")
            run = self._write_json(f"/api/v1/subscriptions/run/{quote(subscription_id, safe='')}?mode=auto")
            success = run.get("success") is not False
            return {
                "success": success,
                "pushed": False,
                "alreadyExists": True,
                "searchTriggered": success,
                "subscriptionId": subscription_id,
                "message": str(run.get("message") or "Torra 已有订阅，未重复创建；已触发搜索"),
            }
        saved = self._write_json("/api/v1/subscriptions/save", {"subscription": subscription})
        if saved.get("success") is False:
            return {
                "success": False,
                "pushed": False,
                "alreadyExists": False,
                "searchTriggered": False,
                "subscriptionId": str(subscription.get("id") or ""),
                "message": str(saved.get("message") or "Torra 返回失败"),
            }
        subscription_id = str(subscription.get("id") or "")
        run = self._write_json(f"/api/v1/subscriptions/run/{quote(subscription_id, safe='')}?mode=auto")
        triggered = run.get("success") is not False
        return {
            "success": triggered,
            "pushed": True,
            "alreadyExists": False,
            "searchTriggered": triggered,
            "subscriptionId": subscription_id,
            "message": f"{saved.get('message') or '已推送到 Torra'}；{run.get('message') or '已触发搜索'}",
        }


def register_torra_read(app: Flask, environment=None, client_factory=None, clock=None):
    config = resolve_torra_read_config(environment)
    client = client_factory(config) if client_factory else TorraReadClient(config, clock=clock)
    app.extensions["mcc_torra_client"] = client

    @app.get("/api/torra/summary")
    def torra_summary():
        return jsonify(client.get_summary())

    return client
