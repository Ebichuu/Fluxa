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


class MoviePilotAdapter:
    """First-class MoviePilot Neo / v2 REST API adapter."""

    def __init__(self, cfg: dict[str, Any], session: requests.Session | None = None):
        self.base_url = str(cfg.get("baseUrl") or "").strip().rstrip("/")
        self.web_url = str(cfg.get("webUrl") or "").strip().rstrip("/")
        self.api_token = str(cfg.get("apiToken") or "").strip()
        self.username = str(cfg.get("username") or "").strip()
        self.password = str(cfg.get("password") or "")
        self.http = session or requests.Session()
        self._bearer = ""

    @property
    def configured(self) -> bool:
        return bool(self.base_url and (self.api_token or (self.username and self.password)))

    def app_url(self, path: str = "") -> str:
        return safe_web_url(self.web_url, self.base_url, path)

    def _login(self) -> tuple[dict[str, str], dict[str, str]]:
        if self.api_token:
            return (
                {"Accept": "application/json", "X-API-KEY": self.api_token},
                {"token": self.api_token},
            )
        if not self._bearer and self.username and self.password:
            resp = self.http.post(
                f"{self.base_url}/api/v1/login/access-token",
                data={"username": self.username, "password": self.password},
                headers={"Accept": "application/json"},
                timeout=TIMEOUT,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"MoviePilot 登录失败 ({resp.status_code})")
            body = resp.json() if resp.content else {}
            token = str(body.get("access_token") or body.get("token") or "").strip()
            if not token:
                raise RuntimeError("MoviePilot 登录未返回 access_token")
            self._bearer = token
        return (
            {"Accept": "application/json", "Authorization": f"Bearer {self._bearer}"},
            {},
        )

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json_body: Any = None) -> Any:
        if not self.configured:
            raise RuntimeError("MoviePilot Neo 未配置")
        headers, auth_params = self._login()
        merged_params = {**auth_params, **(params or {})}
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.http.request(
            method,
            url,
            headers=headers,
            params=merged_params,
            json=json_body,
            timeout=TIMEOUT,
        )
        if resp.status_code in {401, 403} and not self.api_token and self.username:
            self._bearer = ""
            headers, auth_params = self._login()
            resp = self.http.request(
                method,
                url,
                headers=headers,
                params={**auth_params, **(params or {})},
                json=json_body,
                timeout=TIMEOUT,
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"MoviePilot 响应异常 ({resp.status_code})")
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
        data = self._request("GET", "/api/v1/subscribe/list")
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
            # Extract rule / version preference hints already configured in MP Neo
            rule_parts = [
                str(row.get(k) or "").strip()
                for k in ("quality", "resolution", "effect", "filter_groups", "include")
                if row.get(k)
            ]
            rule_summary = " · ".join(p for p in rule_parts if p) or "沿用 MoviePilot Neo 默认规则"
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
                "originReason": str(row.get("description") or "").strip() or "MoviePilot Neo 订阅",
                "updatedAt": str(row.get("last_update") or row.get("date") or "").strip(),
                "webUrl": self.app_url("subscribe/tv" if mtype == "tv" else "subscribe/movie"),
            })
        return normalized

    def list_download_history(self, limit: int = 80) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        try:
            data = self._request("GET", "/api/v1/history/download", params={"page": 1, "count": limit})
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
            raise ValueError("缺少 MoviePilot 订阅 ID")
        data = self._request("GET", f"/api/v1/subscribe/search/{sub_id}")
        ok = bool(data.get("success", True)) if isinstance(data, dict) else True
        msg = str(data.get("message") or ("已触发 MoviePilot 单条搜索" if ok else "触发失败")) if isinstance(data, dict) else "已触发搜索"
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
        data = self._request("POST", "/api/v1/subscribe/", json_body=payload)
        ok = bool(data.get("success", True)) if isinstance(data, dict) else True
        msg = str(data.get("message") or ("已添加到 MoviePilot Neo 订阅" if ok else "添加订阅失败")) if isinstance(data, dict) else "已添加订阅"
        return {"ok": ok, "message": sanitize_text(msg), "raw": data}
