from __future__ import annotations

import re
from typing import Any

import requests

from app.adapters.http_utils import safe_web_url, sanitize_text

TIMEOUT = 12
SECUPLOAD_PLUGIN_KEY = "secupload_115"


def _media_type(raw: object) -> str:
    text = str(raw or "").strip().lower()
    if text in {"tv", "series", "show", "电视剧", "剧集"}:
        return "tv"
    return "movie"


def _int_or_none(val: object) -> int | None:
    try:
        n = int(str(val).strip())
        return n if n > 0 else None
    except Exception:
        return None


def _parse_secupload_files(result: Any, *, key: str) -> list[dict[str, Any]]:
    """Extract per-file success/failure evidence from Torra secupload_115 run result."""
    if not isinstance(result, dict):
        return []
    candidates = result.get(key)
    if not isinstance(candidates, list):
        candidates = []
        for nested_key in ("items", "files", "details", "records"):
            nested = result.get(nested_key)
            if isinstance(nested, list):
                for row in nested:
                    if not isinstance(row, dict):
                        continue
                    ok_val = row.get("success")
                    is_fail = (ok_val is False) or str(row.get("status") or "").lower() in {"failed", "error"}
                    if (key == "failed_files" and is_fail) or (key == "success_files" and ok_val is True):
                        candidates.append(row)
    parsed: list[dict[str, Any]] = []
    for item in candidates:
        if isinstance(item, str):
            name = item.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            parsed.append({"fileName": name, "path": item, "error": ""})
        elif isinstance(item, dict):
            path = str(item.get("path") or item.get("file_path") or item.get("source_path") or item.get("name") or "").strip()
            name = str(item.get("name") or item.get("file_name") or (path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else "")).strip()
            err = sanitize_text(item.get("error") or item.get("message") or item.get("reason") or "")
            if name or path:
                parsed.append({
                    "fileName": name or path,
                    "path": path,
                    "destPath": str(item.get("dest_path") or item.get("target_path") or "").strip(),
                    "error": err,
                })
    return parsed


class TorraAdapter:
    """Clean Torra adapter for subscriptions + 115 secupload evidence + targeted actions."""

    def __init__(self, cfg: dict[str, Any], session: requests.Session | None = None):
        self.base_url = str(cfg.get("baseUrl") or "").strip().rstrip("/")
        self.web_url = str(cfg.get("webUrl") or "").strip().rstrip("/")
        self.token = str(cfg.get("token") or "").strip()
        self.username = str(cfg.get("username") or "").strip()
        self.password = str(cfg.get("password") or "")
        self.http = session or requests.Session()
        self._access_token = self.token

    @property
    def configured(self) -> bool:
        return bool(self.base_url and (self.token or (self.username and self.password)))

    def app_url(self, path: str = "") -> str:
        return safe_web_url(self.web_url, self.base_url, path)

    def _login(self) -> str:
        if self._access_token:
            return self._access_token
        resp = self.http.post(
            f"{self.base_url}/api/v1/login/access-token",
            data={"username": self.username, "password": self.password},
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Torra 登录失败 ({resp.status_code})")
        body = resp.json() if resp.content else {}
        tok = str(body.get("access_token") or body.get("token") or "").strip()
        if not tok:
            raise RuntimeError("Torra 登录未返回 access_token")
        self._access_token = tok
        return tok

    def _request(self, method: str, pathname: str, *, json_body: Any = None) -> Any:
        if not self.configured:
            raise RuntimeError("Torra 未配置")
        tok = self._login()
        url = f"{self.base_url}/{pathname.lstrip('/')}"
        headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
        resp = self.http.request(method, url, headers=headers, json=json_body, timeout=TIMEOUT)
        if resp.status_code in {401, 403} and not self.token and self.username:
            self._access_token = ""
            tok = self._login()
            headers["Authorization"] = f"Bearer {tok}"
            resp = self.http.request(method, url, headers=headers, json=json_body, timeout=TIMEOUT)
        if resp.status_code >= 400:
            raise RuntimeError(f"Torra 响应异常 ({resp.status_code})")
        return resp.json() if resp.content else {}

    def check_health(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "service": "torra",
                "name": "Torra",
                "configured": False,
                "connected": False,
                "status": "unconfigured",
                "message": "未配置",
                "webUrl": self.app_url(),
            }
        try:
            subs = self.list_subscriptions()
            return {
                "service": "torra",
                "name": "Torra",
                "configured": True,
                "connected": True,
                "status": "ok",
                "message": f"在线 · {len(subs)} 条订阅",
                "count": len(subs),
                "webUrl": self.app_url(),
            }
        except Exception as exc:
            return {
                "service": "torra",
                "name": "Torra",
                "configured": True,
                "connected": False,
                "status": "error",
                "message": sanitize_text(exc),
                "webUrl": self.app_url(),
            }

    def list_subscriptions(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        payload = self._request("GET", "/api/v1/subscriptions")
        raw = payload if isinstance(payload, list) else (
            payload.get("data", {}).get("subscriptions")
            if isinstance(payload.get("data"), dict)
            else payload.get("subscriptions", [])
        )
        if not isinstance(raw, list):
            return []
        items: list[dict[str, Any]] = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            mtype = _media_type(row.get("media_type") or row.get("type"))
            tmdb_id = _int_or_none(row.get("tmdb_id") or row.get("tmdbid"))
            season = _int_or_none(row.get("season_number") or row.get("season"))
            year_m = re.search(r"(?:19|20)\d{2}", str(row.get("year") or ""))
            sub_id = str(row.get("id") or "").strip()
            rule_name = str(row.get("rule_name") or row.get("weight_rule_name") or row.get("category") or "").strip()
            items.append({
                "source": "torra",
                "sourceLabel": "Torra",
                "subscriptionId": sub_id,
                "mediaKey": f"{mtype}:{tmdb_id}" if tmdb_id else "",
                "mediaType": mtype,
                "tmdbId": str(tmdb_id) if tmdb_id else "",
                "title": str(row.get("name") or row.get("keyword") or "未命名订阅").strip(),
                "year": year_m.group(0) if year_m else "",
                "seasonNumber": season if mtype == "tv" else None,
                "posterUrl": str(row.get("poster_url") or row.get("poster") or "").strip(),
                "enabled": row.get("enabled") is not False,
                "completed": row.get("completed") is True,
                "isRunning": row.get("is_running") is True,
                "ruleSummary": rule_name or "沿用 Torra 权重规则",
                "originReason": "Torra 订阅",
                "updatedAt": str(row.get("updated_at") or "").strip(),
                "webUrl": self.app_url("subscriptions"),
            })
        return items

    def get_secupload_evidence(self) -> dict[str, Any]:
        """Read 115 secupload plugin runs & per-file success/failure evidence."""
        empty = {
            "available": False,
            "activeRuns": [],
            "successFiles": [],
            "failedFiles": [],
            "recentRuns": [],
            "webUrl": self.app_url(f"plugins/{SECUPLOAD_PLUGIN_KEY}"),
            "error": "",
        }
        if not self.configured:
            return empty
        try:
            payload = self._request("GET", f"/api/v1/plugins/{SECUPLOAD_PLUGIN_KEY}")
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                return {**empty, "error": "Torra 未返回 secupload_115 插件数据"}

            recent_runs = []
            active_runs = []
            success_files = []
            failed_files = []
            for run in data.get("recent_runs") or []:
                if not isinstance(run, dict):
                    continue
                status = str(run.get("status") or "").lower()
                run_id = str(run.get("run_id") or "").strip()
                target_item_id = str(run.get("target_item_id") or "").strip()
                task_key = str(run.get("task_key") or "").strip()
                message = sanitize_text(run.get("message") or "")
                observed_at = str(run.get("finished_at") or run.get("started_at") or run.get("created_at") or "")
                result = run.get("result")

                run_entry = {
                    "runId": run_id,
                    "taskKey": task_key,
                    "targetItemId": target_item_id,
                    "status": status,
                    "message": message,
                    "startedAt": str(run.get("started_at") or ""),
                    "finishedAt": str(run.get("finished_at") or ""),
                }
                recent_runs.append(run_entry)
                if status in {"queued", "pending", "running"}:
                    active_runs.append(run_entry)

                for sf in _parse_secupload_files(result, key="success_files"):
                    success_files.append({**sf, "runId": run_id, "targetItemId": target_item_id, "observedAt": observed_at})
                for ff in _parse_secupload_files(result, key="failed_files"):
                    failed_files.append({
                        **ff,
                        "runId": run_id,
                        "taskKey": task_key,
                        "targetItemId": target_item_id,
                        "error": ff.get("error") or message or "115 上传/秒传失败",
                        "observedAt": observed_at,
                    })

            return {
                "available": True,
                "activeRuns": active_runs,
                "successFiles": success_files,
                "failedFiles": failed_files,
                "recentRuns": recent_runs[:20],
                "webUrl": self.app_url(f"plugins/{SECUPLOAD_PLUGIN_KEY}"),
                "error": "",
            }
        except Exception as exc:
            return {**empty, "error": sanitize_text(exc)}

    def retry_secupload_task(self, *, task_key: str, target_item_id: str = "") -> dict[str, Any]:
        """Trigger a single secupload plugin task run (never global restart)."""
        if not task_key:
            raise ValueError("缺少 Torra 秒传任务 task_key")
        body: dict[str, Any] = {"task_key": task_key}
        if target_item_id:
            body["target_item_id"] = target_item_id
        data = self._request("POST", f"/api/v1/plugins/{SECUPLOAD_PLUGIN_KEY}/tasks/{task_key}/run", json_body=body)
        ok = bool(data.get("success", True)) if isinstance(data, dict) else True
        msg = str(data.get("message") or ("已提交单个 115 上传任务重试" if ok else "提交重试失败")) if isinstance(data, dict) else "已提交重试"
        return {"ok": ok, "message": sanitize_text(msg)}

    def add_subscription(
        self,
        *,
        title: str,
        media_type: str,
        tmdb_id: int,
        year: str = "",
        season: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": title,
            "media_type": media_type,
            "tmdb_id": int(tmdb_id),
            "year": str(year or ""),
            "enabled": True,
        }
        if media_type == "tv" and season:
            payload["season_number"] = int(season)
        data = self._request("POST", "/api/v1/subscriptions", json_body=payload)
        ok = bool(data.get("success", True)) if isinstance(data, dict) else True
        msg = str(data.get("message") or ("已添加到 Torra 订阅" if ok else "Torra 添加订阅失败")) if isinstance(data, dict) else "已添加订阅"
        return {"ok": ok, "message": sanitize_text(msg), "raw": data}
