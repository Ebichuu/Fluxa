from __future__ import annotations

import re
from typing import Any

import requests

from app.adapters.http_utils import safe_web_url, sanitize_text

TIMEOUT = 12


def _media_type(raw: object) -> str:
    text = str(raw or "").strip().lower()
    if text in {"tv", "series", "show", "电视剧", "剧集"} or "剧" in text:
        return "tv"
    return "movie"


def _int_or_none(val: object) -> int | None:
    try:
        n = int(str(val).strip())
        return n if n > 0 else None
    except Exception:
        return None


def normalize_api_root(base_url: str) -> str:
    """Accept either http://host:3001 or http://host:3001/api/v1 without doubling /api/v1."""
    cleaned = str(base_url or "").strip().rstrip("/")
    if not cleaned:
        return ""
    if cleaned.lower().endswith("/api/v1"):
        return cleaned
    return f"{cleaned}/api/v1"


class MoviePilotAdapter:
    """MoviePilot Neo v2 REST adapter verified against v2.16.12.

    Uses strictly `X-API-KEY` header authentication (never query string token,
    never unverified username/password endpoints).
    """

    def __init__(self, cfg: dict[str, Any], session: requests.Session | None = None):
        self.raw_base_url = str(cfg.get("baseUrl") or "").strip().rstrip("/")
        self.api_root = normalize_api_root(self.raw_base_url)
        self.web_url = str(cfg.get("webUrl") or "").strip().rstrip("/")
        self.api_token = str(cfg.get("apiToken") or "").strip()
        self.http = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.api_root and self.api_token)

    def app_url(self, path: str = "") -> str:
        fallback = re.sub(r"/api/v1$", "", self.raw_base_url, flags=re.I)
        return safe_web_url(self.web_url, fallback, path)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "X-API-KEY": self.api_token,
        }

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        if not self.configured:
            raise RuntimeError("MoviePilot Neo 未配置 API 地址或 API Token")
        url = f"{self.api_root}/{endpoint.lstrip('/')}"
        resp = self.http.request(
            method,
            url,
            headers=self._headers(),
            params=params,
            json=json_body,
            timeout=TIMEOUT,
        )
        if resp.status_code in {401, 403}:
            raise RuntimeError("MoviePilot Neo 认证失败，请检查 X-API-KEY Token")
        if resp.status_code >= 400:
            raise RuntimeError(f"MoviePilot Neo 响应异常 ({resp.status_code})")
        return resp.json() if resp.content else {}

    def check_health(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "service": "moviepilot",
                "name": "MoviePilot Neo",
                "configured": False,
                "connected": False,
                "status": "unconfigured",
                "message": "未配置",
                "webUrl": self.app_url(),
            }
        try:
            subs = self.list_subscriptions()
            return {
                "service": "moviepilot",
                "name": "MoviePilot Neo",
                "configured": True,
                "connected": True,
                "status": "ok",
                "message": f"在线 · {len(subs)} 条订阅",
                "count": len(subs),
                "webUrl": self.app_url(),
            }
        except Exception as exc:
            return {
                "service": "moviepilot",
                "name": "MoviePilot Neo",
                "configured": True,
                "connected": False,
                "status": "error",
                "message": sanitize_text(exc),
                "webUrl": self.app_url(),
            }

    def list_subscriptions(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        # Verified endpoint on MoviePilot Neo v2.16.12: GET /api/v1/subscribe/
        data = self._request("GET", "subscribe/")
        rows = data if isinstance(data, list) else (data.get("data") if isinstance(data, dict) else [])
        if not isinstance(rows, list):
            return []
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            mtype = _media_type(row.get("type") or row.get("media_type") or row.get("mtype"))
            tmdb_id = _int_or_none(row.get("tmdbid") or row.get("tmdb_id"))
            season = _int_or_none(row.get("season"))
            sub_id = str(row.get("id") or "").strip()
            title = str(row.get("name") or row.get("title") or "").strip()
            year_match = re.search(r"(?:19|20)\d{2}", str(row.get("year") or row.get("date") or ""))
            year = year_match.group(0) if year_match else ""

            # Surface Neo's own bound filter groups / rules directly without duplicating rules in Fluxa
            rule_parts: list[str] = []
            fg = row.get("filter_groups")
            if isinstance(fg, list) and fg:
                rule_parts.append(f"规则组：{', '.join(str(x) for x in fg)}")
            elif isinstance(fg, str) and fg.strip():
                rule_parts.append(f"规则组：{fg.strip()}")
            for k, label in (("quality", "质量"), ("resolution", "分辨率"), ("effect", "特效"), ("include", "包含")):
                val = str(row.get(k) or "").strip()
                if val:
                    rule_parts.append(f"{label}：{val}")
            rule_summary = " · ".join(rule_parts) or "沿用 MoviePilot Neo 现有默认规则"

            normalized.append({
                "source": "moviepilot",
                "sourceLabel": "MoviePilot Neo",
                "subscriptionId": sub_id,
                "mediaKey": f"{mtype}:{tmdb_id}" if tmdb_id else "",
                "mediaType": mtype,
                "tmdbId": str(tmdb_id) if tmdb_id else "",
                "title": title or "未命名订阅",
                "year": year,
                "seasonNumber": season if mtype == "tv" else None,
                "posterUrl": str(row.get("poster") or "").strip(),
                "state": str(row.get("state") or "R"),
                "totalEpisode": _int_or_none(row.get("total_episode")),
                "lackEpisode": int(row.get("lack_episode") or 0) if row.get("lack_episode") is not None else None,
                "ruleSummary": rule_summary,
                "sites": row.get("sites") if isinstance(row.get("sites"), list) else [],
                "include": str(row.get("include") or "").strip(),
                "exclude": str(row.get("exclude") or "").strip(),
                "originReason": str(row.get("description") or "").strip() or "MoviePilot Neo 订阅（由 Neo 执行选种过滤）",
                "updatedAt": str(row.get("last_update") or row.get("date") or "").strip(),
                "webUrl": self.app_url("subscribe/tv" if mtype == "tv" else "subscribe/movie"),
            })
        return normalized

    def list_download_history(self, limit: int = 80) -> list[dict[str, Any]]:
        """Optional read: if Neo does not expose this path, return [] gracefully."""
        if not self.configured:
            return []
        try:
            data = self._request("GET", "history/download", params={"page": 1, "count": limit})
        except Exception:
            return []
        rows = data if isinstance(data, list) else (data.get("data") if isinstance(data, dict) else [])
        if not isinstance(rows, list):
            return []
        items: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            mtype = _media_type(row.get("type") or row.get("media_type"))
            tmdb_id = _int_or_none(row.get("tmdbid") or row.get("tmdb_id"))
            items.append({
                "mediaKey": f"{mtype}:{tmdb_id}" if tmdb_id else "",
                "title": str(row.get("title") or "").strip(),
                "torrentName": str(row.get("torrent_name") or row.get("name") or "").strip(),
                "torrentSite": str(row.get("torrent_site") or row.get("site") or "").strip(),
                "downloadHash": str(row.get("download_hash") or "").strip().lower(),
                "seasons": str(row.get("seasons") or "").strip(),
                "episodes": str(row.get("episodes") or "").strip(),
                "date": str(row.get("date") or "").strip(),
            })
        return items

    def trigger_subscribe_search(self, subscription_id: str) -> dict[str, Any]:
        sub_id = str(subscription_id or "").strip()
        if not sub_id:
            raise ValueError("缺少 MoviePilot Neo 订阅 ID")
        data = self._request("GET", f"subscribe/search/{sub_id}")
        ok = bool(data.get("success", True)) if isinstance(data, dict) else True
        msg = str(data.get("message") or ("已触发 MoviePilot Neo 单条订阅搜索" if ok else "触发失败")) if isinstance(data, dict) else "已触发搜索"
        return {"ok": ok, "message": sanitize_text(msg)}

    def add_subscription(
        self,
        *,
        title: str,
        media_type: str,
        tmdb_id: int,
        year: str = "",
        season: int | None = None,
        best_version: bool = False,
        include: str = "",
        exclude: str = "",
    ) -> dict[str, Any]:
        """Delegate subscription to MoviePilot Neo so Neo applies its existing filtering rules."""
        payload: dict[str, Any] = {
            "name": title,
            "type": "电视剧" if media_type == "tv" else "电影",
            "tmdbid": int(tmdb_id),
            "year": str(year or ""),
            "best_version": 1 if best_version else 0,
        }
        if media_type == "tv" and season:
            payload["season"] = int(season)
        if include:
            payload["include"] = include
        if exclude:
            payload["exclude"] = exclude
        data = self._request("POST", "subscribe/", json_body=payload)
        ok = bool(data.get("success", True)) if isinstance(data, dict) else True
        msg = str(data.get("message") or ("已交由 MoviePilot Neo 订阅并按 Neo 规则选种" if ok else "添加订阅失败")) if isinstance(data, dict) else "已添加订阅"
        return {"ok": ok, "message": sanitize_text(msg), "raw": data}
