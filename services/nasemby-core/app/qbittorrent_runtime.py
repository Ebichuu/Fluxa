from __future__ import annotations

import math
import os
import threading
import time
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.parse import urlsplit

import requests
from flask import Flask, jsonify

from app.qbittorrent_assessment_runtime import assess_qb_task, summarize_qb_assessments


REQUEST_TIMEOUT_SECONDS = 10
SUMMARY_CACHE_SECONDS = 5
TORRENT_FILES_CACHE_SECONDS = 3600
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
        "lastActivity": _integer_or_number(task.get("last_activity")),
    }


def _task_sort_key(task: dict):
    return (
        STATUS_PRIORITY[task["status"]],
        -task["dlspeed"],
        -task["addedOn"],
        task["name"].casefold(),
    )


class QbittorrentClient:
    def __init__(self, config: QbittorrentConfig, session=None, clock=None, monotonic=None):
        self.config = config
        self.base_url = config.base_url.strip().rstrip("/")
        self.http = session or requests
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic or time.monotonic
        self._summary_condition = threading.Condition(threading.RLock())
        self._summary_cache = None
        self._summary_cache_at = 0.0
        self._summary_refreshing = False
        self._summary_generation = 0
        self._torrent_files_condition = threading.Condition(threading.RLock())
        self._torrent_files_cache = {}
        self._torrent_files_refreshing = set()

    def _invalidate_summary_cache(self) -> None:
        with self._summary_condition:
            self._summary_generation += 1
            self._summary_cache = None
            self._summary_cache_at = 0.0
            self._summary_condition.notify_all()

    def reconfigure(self, config: QbittorrentConfig) -> None:
        with self._summary_condition:
            self.config = config
            self.base_url = config.base_url.strip().rstrip("/")
        with self._torrent_files_condition:
            self._torrent_files_cache.clear()
        self._invalidate_summary_cache()

    def torrent_files(self, torrent_hash: str) -> list[dict]:
        torrent_hash = str(torrent_hash or "").strip()
        if not torrent_hash:
            raise RuntimeError("qBittorrent 任务 Hash 为空")
        while True:
            with self._torrent_files_condition:
                cached = self._torrent_files_cache.get(torrent_hash)
                if cached and self.monotonic() - cached[0] < TORRENT_FILES_CACHE_SECONDS:
                    return deepcopy(cached[1])
                if torrent_hash in self._torrent_files_refreshing:
                    self._torrent_files_condition.wait()
                    continue
                self._torrent_files_refreshing.add(torrent_hash)
                break

        try:
            if not self.base_url:
                raise RuntimeError("未配置 QB_BASE_URL")
            cookie = self._login()
            payload = self._request(
                f"/api/v2/torrents/files?hash={quote(torrent_hash, safe='')}",
                cookie,
            )
            if not isinstance(payload, list):
                raise RuntimeError("qBittorrent 返回了无效文件列表")
            files = [
                {
                    "name": str(row.get("name") or ""),
                    "size": _integer_or_number(row.get("size")),
                    "progress": max(0, min(1, _number(row.get("progress")))),
                    "priority": _integer_or_number(row.get("priority")),
                }
                for row in payload
                if isinstance(row, dict) and str(row.get("name") or "").strip()
            ]
            with self._torrent_files_condition:
                self._torrent_files_cache[torrent_hash] = (self.monotonic(), deepcopy(files))
            return files
        finally:
            with self._torrent_files_condition:
                self._torrent_files_refreshing.discard(torrent_hash)
                self._torrent_files_condition.notify_all()

    def _empty_summary(self, error=None, observed_at=None):
        return {
            "configured": bool(self.base_url),
            "connected": False,
            "webUrl": self.base_url,
            "lastCheckedAt": _iso_timestamp(observed_at or self.clock()),
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
        self._invalidate_summary_cache()

    def add_torrent(self, download_url: str, save_path: str, category: str, tags: list[str]):
        download_url = str(download_url or "").strip()
        save_path = str(save_path or "").strip()
        category = str(category or "").strip()
        normalized_tags = sorted({
            str(value or "").strip()
            for value in tags or []
            if str(value or "").strip()
        })
        parsed = urlsplit(download_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or len(download_url) > 4096:
            raise RuntimeError("RSS 下载资源地址无效")
        if not save_path or len(save_path) > 1024 or any(char in save_path for char in "\r\n\0"):
            raise RuntimeError("qBittorrent 保存路径无效")
        if category and (len(category) > 200 or any(char in category for char in "\r\n\0")):
            raise RuntimeError("qBittorrent 分类无效")
        if not normalized_tags or any(
            len(value) > 100 or any(char in value for char in ",\r\n\0")
            for value in normalized_tags
        ):
            raise RuntimeError("qBittorrent 标签无效")
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
            data = {
                "urls": download_url,
                "savepath": save_path,
                "tags": ",".join(normalized_tags),
            }
            if category:
                data["category"] = category
            response = self.http.request(
                "POST",
                f"{self.base_url}/api/v2/torrents/add",
                headers=headers,
                data=data,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise RuntimeError("qBittorrent 添加任务请求失败") from exc
        if response.status_code >= 400 or str(response.text or "").strip() not in {"", "Ok."}:
            raise RuntimeError(f"qBittorrent 添加任务失败：{response.status_code}")
        self._invalidate_summary_cache()
        return {"accepted": True, "category": category, "tags": normalized_tags}

    def _read_summary(self) -> dict:
        checked_at = self.clock()
        if not self.base_url:
            return self._empty_summary("未配置 QB_BASE_URL", checked_at)
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
            assessment = summarize_qb_assessments(
                [assess_qb_task(task, checked_at) for task in tasks],
                checked_at,
            )
            return {
                "configured": True,
                "connected": True,
                "webUrl": self.base_url,
                "lastCheckedAt": _iso_timestamp(checked_at),
                "version": version,
                "transfer": {
                    "downloadSpeed": _integer_or_number(transfer.get("dl_info_speed")),
                    "uploadSpeed": _integer_or_number(transfer.get("up_info_speed")),
                },
                "counts": counts,
                "tasks": tasks,
                "assessment": assessment,
            }
        except Exception as exc:
            return self._empty_summary(str(exc) or "qBittorrent 读取失败", checked_at)

    def summary(self) -> dict:
        while True:
            with self._summary_condition:
                cache_age = self.monotonic() - self._summary_cache_at
                if self._summary_cache is not None and cache_age < SUMMARY_CACHE_SECONDS:
                    return deepcopy(self._summary_cache)
                if self._summary_refreshing:
                    self._summary_condition.wait()
                    continue
                self._summary_refreshing = True
                generation = self._summary_generation
                break

        try:
            snapshot = self._read_summary()
        finally:
            with self._summary_condition:
                if 'snapshot' in locals() and generation == self._summary_generation:
                    self._summary_cache = deepcopy(snapshot)
                    self._summary_cache_at = self.monotonic()
                self._summary_refreshing = False
                self._summary_condition.notify_all()
        return deepcopy(snapshot)


def register_qbittorrent_read(app: Flask, environment=None, client_factory=None, clock=None):
    config = resolve_qbittorrent_config(environment)
    client = client_factory(config) if client_factory else QbittorrentClient(config, clock=clock)
    app.extensions["mcc_qbittorrent_client"] = client

    @app.get("/api/qbittorrent/summary")
    def qbittorrent_summary():
        return jsonify(client.summary())

    return client
