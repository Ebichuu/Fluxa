from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify


REQUEST_TIMEOUT_SECONDS = 10
STATUS_PRIORITY = {
    "stalled": 0,
    "downloading": 1,
    "queued": 2,
    "paused": 3,
    "completed": 4,
}


@dataclass(frozen=True)
class QbittorrentConfig:
    base_url: str = ""
    username: str = ""
    password: str = ""


def resolve_qbittorrent_config(environment=None) -> QbittorrentConfig:
    environment = os.environ if environment is None else environment
    return QbittorrentConfig(
        base_url=str(environment.get("QB_BASE_URL") or "").strip().rstrip("/"),
        username=str(environment.get("QB_USERNAME") or "").strip(),
        password=str(environment.get("QB_PASSWORD") or ""),
    )


def _iso_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def _number(value) -> float:
    try:
        result = float(value or 0)
    except (TypeError, ValueError):
        return 0
    return result if math.isfinite(result) else 0


def _integer_or_number(value):
    number = _number(value)
    return int(number) if number.is_integer() else number


def task_status(task: dict) -> str:
    state = str(task.get("state") or "").lower()
    progress = _number(task.get("progress"))
    if "error" in state or "missing" in state or ("stalled" in state and progress < 0.999):
        return "stalled"
    if progress >= 0.999 or "upload" in state or "stalledup" in state:
        return "completed"
    if "pause" in state:
        return "paused"
    if (
        _number(task.get("dlspeed")) > 0
        or "downloading" in state
        or "metadl" in state
        or "forceddl" in state
    ):
        return "downloading"
    return "queued"


def _state_label(status: str, state: str) -> str:
    if status == "downloading":
        return "下载中"
    if status == "stalled":
        return "文件缺失" if "missing" in state.lower() else "卡住"
    if status == "completed":
        return "下载完成"
    if status == "paused":
        return "已暂停"
    return "排队中"


def normalize_task(task: dict) -> dict:
    status = task_status(task)
    state = str(task.get("state") or "")
    return {
        "hash": str(task.get("hash") or task.get("name") or ""),
        "name": str(task.get("name") or "未命名任务"),
        "progress": max(0, min(1, _number(task.get("progress")))),
        "state": state,
        "stateLabel": _state_label(status, state),
        "status": status,
        "dlspeed": _integer_or_number(task.get("dlspeed")),
        "upspeed": _integer_or_number(task.get("upspeed")),
        "eta": _integer_or_number(task.get("eta")),
        "size": _integer_or_number(task.get("size")),
        "downloaded": _integer_or_number(task.get("downloaded")),
        "savePath": str(task.get("save_path") or ""),
        "category": str(task.get("category") or ""),
        "tags": str(task.get("tags") or ""),
        "addedOn": _integer_or_number(task.get("added_on")),
        "completionOn": _integer_or_number(task.get("completion_on")),
    }


def _task_sort_key(task: dict):
    return (
        STATUS_PRIORITY[task["status"]],
        -task["dlspeed"],
        -task["addedOn"],
        task["name"].casefold(),
    )


class QbittorrentClient:
    def __init__(self, config: QbittorrentConfig, session=None, clock=None):
        self.config = config
        self.base_url = config.base_url.strip().rstrip("/")
        self.http = session or requests
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def reconfigure(self, config: QbittorrentConfig) -> None:
        self.config = config
        self.base_url = config.base_url.strip().rstrip("/")

    def _empty_summary(self, error=None):
        return {
            "configured": bool(self.base_url),
            "connected": False,
            "webUrl": self.base_url,
            "lastCheckedAt": _iso_timestamp(self.clock()),
            "version": "",
            "transfer": {"downloadSpeed": 0, "uploadSpeed": 0},
            "counts": {
                "total": 0,
                "active": 0,
                "downloading": 0,
                "stalled": 0,
                "completed": 0,
                "paused": 0,
            },
            "tasks": [],
            **({"error": error} if error else {}),
        }

    def _request(self, pathname: str, cookie=""):
        headers = {"Accept": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        try:
            response = self.http.request(
                "GET",
                f"{self.base_url}{pathname}",
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise RuntimeError("qBittorrent 请求失败") from exc
        if response.status_code >= 400:
            raise RuntimeError(f"qBittorrent 响应异常：{response.status_code}")
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if "application/json" in content_type:
            try:
                return response.json()
            except ValueError as exc:
                raise RuntimeError("qBittorrent 返回了无效 JSON") from exc
        return response.text

    def _login(self) -> str:
        if not self.config.username or not self.config.password:
            return ""
        try:
            response = self.http.request(
                "POST",
                f"{self.base_url}/api/v2/auth/login",
                headers={
                    "Accept": "text/plain",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "username": self.config.username,
                    "password": self.config.password,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise RuntimeError("qBittorrent 登录请求失败") from exc
        if response.status_code >= 400 or response.text.strip() != "Ok.":
            raise RuntimeError(f"qBittorrent 登录失败：{response.status_code}")
        return str(response.headers.get("Set-Cookie") or "").split(";", 1)[0]

    def set_paused(self, action: str, hashes: list[str]):
        if action not in {"pause", "resume"}:
            raise RuntimeError("不支持的 qBittorrent 动作")
        if not self.base_url:
            raise RuntimeError("未配置 QB_BASE_URL")
        cookie = self._login()
        headers = {
            "Accept": "text/plain",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if cookie:
            headers["Cookie"] = cookie
        try:
            response = self.http.request(
                "POST",
                f"{self.base_url}/api/v2/torrents/{action}",
                headers=headers,
                data={"hashes": "|".join(hashes)},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"qBittorrent {'暂停' if action == 'pause' else '恢复'}请求失败") from exc
        if response.status_code >= 400:
            raise RuntimeError(
                f"qBittorrent {'暂停' if action == 'pause' else '恢复'}失败：{response.status_code}"
            )

    def summary(self) -> dict:
        if not self.base_url:
            return self._empty_summary("未配置 QB_BASE_URL")
        try:
            cookie = self._login()
            with ThreadPoolExecutor(max_workers=3) as executor:
                version_future = executor.submit(self._request, "/api/v2/app/version", cookie)
                transfer_future = executor.submit(self._request, "/api/v2/transfer/info", cookie)
                tasks_future = executor.submit(self._request, "/api/v2/torrents/info", cookie)
                version = version_future.result()
                transfer = transfer_future.result()
                raw_tasks = tasks_future.result()
            if not isinstance(raw_tasks, list):
                raise RuntimeError("qBittorrent 返回了无效任务列表")
            transfer = transfer if isinstance(transfer, dict) else {}
            tasks = sorted(
                (normalize_task(item) for item in raw_tasks if isinstance(item, dict)),
                key=_task_sort_key,
            )
            counts = {
                "total": len(tasks),
                "active": sum(item["status"] in {"downloading", "stalled"} for item in tasks),
                "downloading": sum(item["status"] == "downloading" for item in tasks),
                "stalled": sum(item["status"] == "stalled" for item in tasks),
                "completed": sum(item["status"] == "completed" for item in tasks),
                "paused": sum(item["status"] == "paused" for item in tasks),
            }
            return {
                "configured": True,
                "connected": True,
                "webUrl": self.base_url,
                "lastCheckedAt": _iso_timestamp(self.clock()),
                "version": version,
                "transfer": {
                    "downloadSpeed": _integer_or_number(transfer.get("dl_info_speed")),
                    "uploadSpeed": _integer_or_number(transfer.get("up_info_speed")),
                },
                "counts": counts,
                "tasks": tasks,
            }
        except Exception as exc:
            return self._empty_summary(str(exc) or "qBittorrent 读取失败")


def register_qbittorrent_read(app: Flask, environment=None, client_factory=None, clock=None):
    config = resolve_qbittorrent_config(environment)
    client = client_factory(config) if client_factory else QbittorrentClient(config, clock=clock)
    app.extensions["mcc_qbittorrent_client"] = client

    @app.get("/api/qbittorrent/summary")
    def qbittorrent_summary():
        return jsonify(client.summary())

    return client
