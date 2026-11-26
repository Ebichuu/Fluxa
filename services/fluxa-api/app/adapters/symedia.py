from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import requests

from app.adapters.http_utils import safe_web_url, sanitize_text

TIMEOUT = 15

RULE_PROTECT_KEYWORDS = (
    "低分",
    "权重",
    "得分",
    "score",
    "weight",
    "已有更高",
    "更高版本",
    "规则跳过",
    "版本规则",
    "跳过覆盖",
    "取消覆盖",
    "quality_score_lower",
    "quality_weight",
    "quality_higher_version",
    "quality_version_rule",
    "quality_overwrite",
)

IDENTIFY_FAIL_KEYWORDS = (
    "识别失败",
    "未识别",
    "未匹配",
    "tmdb",
    "刮削失败",
    "找不到媒体",
    "not found",
)


def classify_symedia_record(row: dict[str, Any]) -> tuple[str, str, str]:
    """Return (outcome, label, reason_text).

    Explicitly separates:
      - archived / replaced
      - rule_protected (rejected by SA weight/version rules -> NOT an error!)
      - identify_failed
      - failed
      - unconfirmed
    """
    raw_status = row.get("status")
    err_text = sanitize_text(row.get("errmsg") or row.get("message") or row.get("reason") or "")
    reason_code = str(row.get("reasonCode") or row.get("reason_code") or "").strip()
    haystack = f"{reason_code} {err_text}".casefold()

    if raw_status is True or raw_status == 1:
        src_score = (row.get("src_detail") or {}).get("totalScore") if isinstance(row.get("src_detail"), dict) else None
        dst_score = (row.get("dst_detail") or {}).get("totalScore") if isinstance(row.get("dst_detail"), dict) else None
        if isinstance(src_score, (int, float)) and isinstance(dst_score, (int, float)) and src_score > dst_score:
            return "replaced", "归档并完成版本替换", f"已按 SA 规则替换旧版（{dst_score} → {src_score}）"
        return "archived", "归档成功", "已完成整理归档"

    if raw_status is False or raw_status == 0:
        if any(k in haystack for k in RULE_PROTECT_KEYWORDS):
            return "rule_protected", "被 SA 权重规则拒绝（正常保护）", err_text or "未高于现有版本权重，SA 已保留原版本"
        if any(k in haystack for k in IDENTIFY_FAIL_KEYWORDS):
            return "identify_failed", "SA 识别失败", err_text or "未能识别媒体信息"
        return "failed", "SA 归档失败", err_text or "整理归档失败"

    return "unconfirmed", "未确认", err_text or "SA 未返回明确状态"


class SymediaAdapter:
    """Read-only Symedia (SA) adapter — never modifies SA's rules."""

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

    def _get(self, pathname: str) -> Any:
        if not self.configured:
            raise RuntimeError("Symedia 未配置")
        tok = self._login()
        url = f"{self.base_url}/api/v1/{pathname.lstrip('/')}"
        resp = self.http.get(url, headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"}, timeout=TIMEOUT)
        if resp.status_code in {401, 403} and not self.token and self.username:
            self._access_token = ""
            tok = self._login()
            resp = self.http.get(url, headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"}, timeout=TIMEOUT)
        if resp.status_code >= 400:
            raise RuntimeError(f"Symedia 响应异常 ({resp.status_code})")
        return resp.json() if resp.content else {}

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
            records.append({
                "id": str(row.get("id") or "").strip(),
                "mediaKey": f"{mtype}:{tmdb_id}" if tmdb_id else "",
                "mediaType": mtype,
                "tmdbId": tmdb_id,
                "title": str(row.get("title") or "").strip() or (src.rsplit("/", 1)[-1] if src else "未识别文件"),
                "seasons": str(row.get("seasons") or "").strip(),
                "episodes": str(row.get("episodes") or "").strip(),
                "src": src,
                "dest": dest,
                "fileName": src.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if src else "",
                "outcome": outcome,
                "outcomeLabel": label,
                "reasonText": reason,
                "date": str(row.get("date") or "").strip(),
                "webUrl": self.app_url("history"),
            })
        return records
