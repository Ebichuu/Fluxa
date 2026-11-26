from __future__ import annotations

import math
from typing import Any

import requests

from app.adapters.http_utils import safe_web_url, sanitize_text

TIMEOUT = 10


def _num(val: object) -> float:
    try:
        n = float(val or 0)
        return n if math.isfinite(n) else 0.0
    except Exception:
        return 0.0


def classify_qb_state(raw_state: str, progress: float, dlspeed: float) -> tuple[str, str]:
    """Return (status_code, chinese_label) clearly surfacing stalled / paused / missing / seeding."""
    s = (raw_state or "").lower()
    if "missing" in s:
        return "missing_files", "缺少文件"
    if "error" in s:
        return "error", "任务出错"
    if "checking" in s:
        return "checking", "校验中"
    if progress >= 0.999 or "upload" in s or "stalledup" in s or "forcedup" in s:
        if "pause" in s or "stopped" in s:
            return "completed_paused", "已完成（已暂停）"
        return "seeding", "做种中（保种保护）"
    if "pause" in s or "stopped" in s:
        return "paused", "已暂停"
    if "stalleddl" in s or ("stalled" in s and progress < 0.999):
        return "stalled", "下载无速度"
    if dlspeed > 0 or "downloading" in s or "metadl" in s or "forceddl" in s:
        return "downloading", "下载中"
    return "queued", "排队中"


class QbittorrentAdapter:
    def __init__(self, cfg: dict[str, Any], session: requests.Session | None = None):
        self.base_url = str(cfg.get("baseUrl") or "").strip().rstrip("/")
        self.web_url = str(cfg.get("webUrl") or "").strip().rstrip("/")
        self.username = str(cfg.get("username") or "").strip()
        self.password = str(cfg.get("password") or "")
        self.protect_seeding = bool(cfg.get("protectSeeding", True))
        self.http = session or requests.Session()
        self._logged_in = False

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def app_url(self) -> str:
        return safe_web_url(self.web_url, self.base_url)

    def _login(self) -> None:
        if self._logged_in or not (self.username or self.password):
            return
        resp = self.http.post(
            f"{self.base_url}/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
            timeout=TIMEOUT,
        )
        if resp.status_code >= 400 or resp.text.strip().lower() == "fails.":
            raise RuntimeError("qBittorrent 认证失败，请检查用户名和密码")
        self._logged_in = True

    def _request(self, method: str, path: str, *, data: dict[str, Any] | None = None) -> requests.Response:
        if not self.configured:
            raise RuntimeError("qBittorrent 未配置")
        self._login()
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.http.request(method, url, data=data, timeout=TIMEOUT)
        if resp.status_code in {401, 403}:
            self._logged_in = False
            self._login()
            resp = self.http.request(method, url, data=data, timeout=TIMEOUT)
        return resp

    def check_health(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "service": "qbittorrent",
                "name": "qBittorrent",
                "configured": False,
                "connected": False,
                "status": "unconfigured",
                "message": "未配置",
                "webUrl": self.app_url(),
            }
        try:
            torrents = self.list_torrents()
            active = sum(1 for t in torrents if t["status"] in {"downloading", "stalled", "checking"})
            issues = sum(1 for t in torrents if t["status"] in {"missing_files", "error", "stalled"})
            msg = f"在线 · {active} 个下载中" + (f"，{issues} 个异常" if issues else "")
            return {
                "service": "qbittorrent",
                "name": "qBittorrent",
                "configured": True,
                "connected": True,
                "status": "warn" if issues else "ok",
                "message": msg,
                "count": len(torrents),
                "webUrl": self.app_url(),
            }
        except Exception as exc:
            return {
                "service": "qbittorrent",
                "name": "qBittorrent",
                "configured": True,
                "connected": False,
                "status": "error",
                "message": sanitize_text(exc),
                "webUrl": self.app_url(),
            }

    def list_torrents(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        resp = self._request("GET", "/api/v2/torrents/info")
        if resp.status_code >= 400:
            raise RuntimeError(f"qBittorrent 读取任务失败 ({resp.status_code})")
        rows = resp.json() if resp.content else []
        if not isinstance(rows, list):
            return []
        items: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            progress = max(0.0, min(1.0, _num(row.get("progress"))))
            dlspeed = int(_num(row.get("dlspeed")))
            upspeed = int(_num(row.get("upspeed")))
            raw_state = str(row.get("state") or "")
            status, label = classify_qb_state(raw_state, progress, dlspeed)
            is_seeding = status == "seeding"
            items.append({
                "hash": str(row.get("hash") or "").strip().lower(),
                "name": str(row.get("name") or "未命名种子").strip(),
                "progress": round(progress, 4),
                "progressPercent": round(progress * 100, 1),
                "status": status,
                "statusLabel": label,
                "rawState": raw_state,
                "dlspeed": dlspeed,
                "upspeed": upspeed,
                "eta": int(_num(row.get("eta"))),
                "size": int(_num(row.get("size"))),
                "category": str(row.get("category") or "").strip(),
                "tags": str(row.get("tags") or "").strip(),
                "savePath": str(row.get("save_path") or "").strip(),
                "tracker": str(row.get("tracker") or "").strip(),
                "addedOn": int(_num(row.get("added_on"))),
                "completionOn": int(_num(row.get("completion_on"))),
                "isSeeding": is_seeding,
                "seedingProtected": is_seeding and self.protect_seeding,
                "webUrl": self.app_url(),
            })
        return items

    def pause_or_resume(self, torrent_hash: str, action: str) -> dict[str, Any]:
        """Support both qBittorrent v4 (pause/resume) and v5 (stop/start) APIs."""
        h = str(torrent_hash or "").strip().lower()
        if not h or action not in {"pause", "resume"}:
            raise ValueError("无效的 qBittorrent 操作参数")
        v5_path = "/api/v2/torrents/stop" if action == "pause" else "/api/v2/torrents/start"
        v4_path = "/api/v2/torrents/pause" if action == "pause" else "/api/v2/torrents/resume"
        resp = self._request("POST", v5_path, data={"hashes": h})
        if resp.status_code == 404:
            resp = self._request("POST", v4_path, data={"hashes": h})
        if resp.status_code >= 400:
            raise RuntimeError(f"qBittorrent 操作失败 ({resp.status_code})")
        return {"ok": True, "message": "已暂停下载任务" if action == "pause" else "已恢复下载任务"}
