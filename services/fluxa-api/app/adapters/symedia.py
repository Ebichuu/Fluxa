from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import requests

from app.adapters.http_utils import safe_web_url, sanitize_text

TIMEOUT = 15

RULE_PROTECT_KEYWORDS = (
    "小于等于",
    "低分",
    "权重",
    "得分",
    "score",
    "weight",
    "已有更高",
    "更高版本",
    "跳过覆盖",
    "取消覆盖",
    "quality_score_lower",
    "quality_weight",
    "quality_higher_version",
    "quality_overwrite",
)

VERSION_REJECT_KEYWORDS = (
    "版本规则",
    "准入",
    "不满足规则",
    "规则跳过",
    "规则过滤",
    "quality_version_rule",
    "filtered",
)

AUTH_CONN_KEYWORDS = (
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "认证失败",
    "登录失败",
    "token",
    "cookie",
    "connection",
    "timeout",
    "timed out",
    "连接失败",
    "连接超时",
    "网络异常",
)

IDENTIFY_FAIL_KEYWORDS = (
    "没有查询到媒体信息",
    "未查询到媒体信息",
    "查询不到媒体",
    "识别失败",
    "未识别",
    "未匹配",
    "tmdb",
    "刮削失败",
    "找不到媒体",
    "not found",
)


def classify_symedia_record(row: dict[str, Any]) -> tuple[str, str, str]:
    """Return (outcome, label, reason_text) purely from real error reason — NEVER by directory name or generic prefix.

    5 explicit failure/rejection categories:
      - weight_rejected (even if message starts with '文件转移错误', '小于等于/取消覆盖' => normal weight protection!)
      - version_rejected (version admission filter)
      - identify_failed (missing media info / TMDB match)
      - auth_or_connection_error (auth/network outage)
      - transfer_failed (real IO/file transfer failure)
    Plus: archived / replaced / unconfirmed.
    """
    raw_status = row.get("status")
    err_text = sanitize_text(row.get("errmsg") or row.get("message") or row.get("reason") or "")
    reason_code = str(row.get("reasonCode") or row.get("reason_code") or "").strip()
    haystack = f"{reason_code} {err_text}".casefold()

    src_score = (row.get("src_detail") or {}).get("totalScore") if isinstance(row.get("src_detail"), dict) else None
    dst_score = (row.get("dst_detail") or {}).get("totalScore") if isinstance(row.get("dst_detail"), dict) else None
    score_note = (
        f"（新文件权重 {src_score} vs 库内权重 {dst_score}）"
        if isinstance(src_score, (int, float)) and isinstance(dst_score, (int, float))
        else ""
    )

    if raw_status is True or raw_status == 1:
        if isinstance(src_score, (int, float)) and isinstance(dst_score, (int, float)) and src_score > dst_score:
            return "replaced", "归档并完成版本替换", f"已按 SA 规则替换旧版（{dst_score} → {src_score}）"
        return "archived", "归档成功", "已完成整理归档"

    if raw_status is False or raw_status == 0:
        # Check weight & version rejection BEFORE transfer_failed, because SA often prefixes them with '文件转移错误'!
        if any(k in haystack for k in RULE_PROTECT_KEYWORDS):
            return "rule_protected", "正常权重拒绝（保留库内版本）", (err_text or "未高于库内版本权重，已保留原版本") + score_note
        if any(k in haystack for k in VERSION_REJECT_KEYWORDS):
            return "version_rejected", "版本准入拒绝（规则过滤）", err_text or "不满足 SA 版本准入规则，已跳过"
        if any(k in haystack for k in IDENTIFY_FAIL_KEYWORDS):
            return "identify_failed", "SA 识别失败", err_text or "没有查询到媒体信息"
        if any(k in haystack for k in AUTH_CONN_KEYWORDS):
            return "auth_or_connection_error", "连接 / 认证故障", err_text or "SA 上游连接或认证异常"
        return "failed", "文件转移故障", err_text or "SA 文件转移失败"

    return "unconfirmed", "未确认", err_text or "SA 未返回明确状态"


class SymediaAdapter:
    """Symedia (SA) adapter — never modifies SA global rules or weights.

    Supports:
      - Read-only transfer history & transfer task list
      - Read-only OpenAPI capability probe (discovers whether manual identify/override params are supported)
      - Targeted single-file manual transfer (strictly 1 file, never full-library rescan)
    """

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
            raise RuntimeError(f"Symedia 登录失败 ({resp.status_code})")
        body = resp.json() if resp.content else {}
        tok = str(body.get("access_token") or body.get("token") or "").strip()
        if not tok:
            raise RuntimeError("Symedia 登录未返回 access_token")
        self._access_token = tok
        return tok

    def _request(self, method: str, pathname: str, *, json_body: Any = None) -> Any:
        if not self.configured:
            raise RuntimeError("Symedia 未配置")
        tok = self._login()
        url = f"{self.base_url}/api/v1/{pathname.lstrip('/')}"
        headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
        resp = self.http.request(method, url, headers=headers, json=json_body, timeout=TIMEOUT)
        if resp.status_code in {401, 403} and not self.token and self.username:
            self._access_token = ""
            tok = self._login()
            headers["Authorization"] = f"Bearer {tok}"
            resp = self.http.request(method, url, headers=headers, json=json_body, timeout=TIMEOUT)
        if resp.status_code >= 400:
            raise RuntimeError(f"Symedia 响应异常 ({resp.status_code})")
        return resp.json() if resp.content else {}

    def _get(self, pathname: str) -> Any:
        return self._request("GET", pathname)

    def check_health(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "service": "symedia",
                "name": "Symedia (SA)",
                "configured": False,
                "connected": False,
                "status": "unconfigured",
                "message": "未配置",
                "webUrl": self.app_url(),
            }
        try:
            records = self.list_transfer_history(count=50)
            fails = sum(1 for r in records if r["outcome"] in {"failed", "identify_failed"})
            msg = f"在线 · 最近 {len(records)} 条记录" + (f"，{fails} 条异常" if fails else "")
            return {
                "service": "symedia",
                "name": "Symedia (SA)",
                "configured": True,
                "connected": True,
                "status": "warn" if fails else "ok",
                "message": msg,
                "count": len(records),
                "webUrl": self.app_url(),
            }
        except Exception as exc:
            return {
                "service": "symedia",
                "name": "Symedia (SA)",
                "configured": True,
                "connected": False,
                "status": "error",
                "message": sanitize_text(exc),
                "webUrl": self.app_url(),
            }

    def list_transfer_tasks(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        try:
            data = self._get("transfer/transfer_list")
        except Exception:
            return []
        body = data.get("data") if isinstance(data, dict) and "data" in data else data
        rows = body
        if isinstance(body, dict):
            rows = next(
                (body.get(k) for k in ("items", "list", "transfer_list", "tasks") if isinstance(body.get(k), list)),
                [],
            )
        if not isinstance(rows, list):
            return []
        tasks: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            tid = str(row.get("id") or row.get("transfer_task_id") or "").strip()
            if not tid:
                continue
            tasks.append({
                "id": tid,
                "name": str(row.get("name") or row.get("title") or f"归档任务 #{tid}").strip(),
                "sourcePath": str(row.get("source_path") or row.get("src") or "").strip(),
                "targetPath": str(row.get("target_path") or row.get("dest") or "").strip(),
                "failPath": str(row.get("fail_path") or row.get("failed_path") or "").strip(),
            })
        return tasks

    def probe_recovery_capabilities(self) -> dict[str, Any]:
        """Inspect SA OpenAPI spec (read-only) to verify supported single-file manual identify endpoints."""
        caps = {
            "transferManual": False,
            "manualIdentifyParams": [],
            "historyRedo": False,
            "openapiChecked": False,
        }
        if not self.configured:
            return caps
        for spec_path in ("/openapi.json", "/api/v1/openapi.json"):
            try:
                resp = self.http.get(f"{self.base_url}{spec_path}", timeout=6)
                if resp.status_code != 200:
                    continue
                spec = resp.json() if resp.content else {}
                paths = spec.get("paths") if isinstance(spec, dict) else {}
                if not isinstance(paths, dict):
                    continue
                caps["openapiChecked"] = True
                for p, ops in paths.items():
                    if p.endswith("/transfer/manual") and isinstance(ops, dict) and "post" in ops:
                        caps["transferManual"] = True
                        body_str = str(ops["post"]).lower()
                        for key in ("tmdbid", "tmdb_id", "season", "episode", "type", "media_type", "transfer_task_id"):
                            if key in body_str and key not in caps["manualIdentifyParams"]:
                                caps["manualIdentifyParams"].append(key)
                    if "history/transfer" in p and ("redo" in p or "retry" in p or "manual" in p):
                        caps["historyRedo"] = True
                break
            except Exception:
                continue
        return caps

    def submit_manual_transfer(
        self,
        *,
        file_path: str,
        transfer_task_id: str,
        tmdb_id: str = "",
        media_type: str = "",
        season: int | None = None,
        episode: int | None = None,
    ) -> dict[str, Any]:
        """Submit strictly ONE file to SA's verified /api/v1/transfer/manual endpoint."""
        clean_path = str(file_path or "").strip()
        tid = str(transfer_task_id or "").strip()
        if not clean_path.startswith("/") or not tid:
            raise ValueError("单文件归档需要完整的云盘/源文件路径与 SA 归档任务 ID")
        payload: dict[str, Any] = {
            "items": [{"type": "file", "path": clean_path}],
            "transfer_task_id": tid,
        }
        # Pass explicit TMDB / season / episode hints when provided
        if tmdb_id and str(tmdb_id).isdigit():
            payload["tmdbid"] = int(tmdb_id)
        if media_type in {"movie", "tv"}:
            payload["type"] = "电视剧" if media_type == "tv" else "电影"
        if season is not None and int(season) > 0:
            payload["season"] = int(season)
        if episode is not None and int(episode) > 0:
            payload["episode"] = int(episode)

        data = self._request("POST", "transfer/manual", json_body=payload)
        if isinstance(data, dict) and data.get("success") is False:
            raise RuntimeError(sanitize_text(data.get("message") or "Symedia 拒绝了单文件归档请求"))
        return {
            "ok": True,
            "message": sanitize_text((data.get("message") if isinstance(data, dict) else "") or "已向 SA 提交单文件归档请求（待核验结果）"),
            "raw": data,
        }

    def list_transfer_history(self, count: int = 120) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        qs = urlencode({"page": 1, "count": max(1, min(300, count)), "sort_by": "date", "sort_order": "desc"})
        payload = self._get(f"history/transfer?{qs}")
        body = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
        rows = body.get("list") if isinstance(body, dict) and isinstance(body.get("list"), list) else []
        records: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            outcome, label, reason = classify_symedia_record(row)
            raw_type = str(row.get("type") or row.get("media_type") or "").strip().lower()
            mtype = "tv" if raw_type in {"tv", "series", "电视剧", "剧集"} or row.get("seasons") or row.get("episodes") else "movie"
            tmdb_raw = str(row.get("tmdbid") or row.get("tmdb_id") or "").strip()
            tmdb_id = tmdb_raw if tmdb_raw.isdigit() and int(tmdb_raw) > 0 else ""
            src = str(row.get("src") or "").strip()
            dest = str(row.get("dest") or "").strip()
            season_ep = str(row.get("season_episode") or "").strip()
            seasons = str(row.get("seasons") or "").strip()
            episodes = str(row.get("episodes") or "").strip()
            if not seasons and not episodes and season_ep:
                seasons = season_ep
            records.append({
                "id": str(row.get("id") or "").strip(),
                "mediaKey": f"{mtype}:{tmdb_id}" if tmdb_id else "",
                "mediaType": mtype,
                "tmdbId": tmdb_id,
                "title": str(row.get("title") or "").strip() or (src.rsplit("/", 1)[-1] if src else "未识别文件"),
                "seasons": seasons,
                "episodes": episodes,
                "seasonEpisode": season_ep or f"{seasons}{episodes}",
                "src": src,
                "dest": dest,
                "fileName": src.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if src else "",
                "fileSize": row.get("file_size") or row.get("size") or None,
                "outcome": outcome,
                "outcomeLabel": label,
                "reasonText": reason,
                "date": str(row.get("date") or "").strip(),
                "webUrl": self.app_url("history"),
            })
        return records
