from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlencode, urljoin

import requests

from app.adapters.http_utils import safe_web_url, sanitize_text

TIMEOUT = 12
BEIJING_TZ = timezone(timedelta(hours=8))
AUTH_HEADER = 'MediaBrowser Client="Fluxa", Device="fluxa-v2", DeviceId="fluxa-v2", Version="2.0"'


def _tmdb_id(item: dict[str, Any]) -> str:
    pids = item.get("ProviderIds")
    if not isinstance(pids, dict):
        return ""
    raw = str(pids.get("Tmdb") or pids.get("tmdb") or "").strip()
    return raw if raw.isdigit() and int(raw) > 0 else ""


class EmbyAdapter:
    """Emby read adapter: checks actual library existence (Movie / Series / Episode) & today's additions."""

    def __init__(self, cfg: dict[str, Any], session: requests.Session | None = None):
        self.base_url = str(cfg.get("baseUrl") or "").strip().rstrip("/")
        self.web_url = str(cfg.get("webUrl") or "").strip().rstrip("/")
        self.api_key = str(cfg.get("apiKey") or "").strip()
        self.user_id = str(cfg.get("userId") or "").strip()
        self.username = str(cfg.get("username") or "").strip()
        self.password = str(cfg.get("password") or "")
        self.http = session or requests.Session()
        self._token = self.api_key
        self._resolved_user_id = self.user_id

    @property
    def configured(self) -> bool:
        return bool(self.base_url and (self.api_key or (self.username and self.password)))

    def app_url(self, item_id: str = "") -> str:
        if item_id:
            return safe_web_url(self.web_url, self.base_url, f"web/index.html#!/item?id={quote(item_id)}")
        return safe_web_url(self.web_url, self.base_url)

    def _ensure_auth(self) -> tuple[str, str]:
        if not self.configured:
            raise RuntimeError("Emby 未配置")
        if self._token and self._resolved_user_id:
            return self._token, self._resolved_user_id
        if self.api_key and not self._resolved_user_id:
            resp = self.http.get(
                urljoin(f"{self.base_url}/", "Users"),
                params={"api_key": self.api_key},
                headers={"Accept": "application/json"},
                timeout=TIMEOUT,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"Emby 读取用户列表失败 ({resp.status_code})")
            users = resp.json() if resp.content else []
            if isinstance(users, list) and users:
                self._resolved_user_id = str(users[0].get("Id") or "").strip()
            self._token = self.api_key
            if not self._resolved_user_id:
                raise RuntimeError("无法自动解析 Emby 用户 ID，请在设置中填写")
            return self._token, self._resolved_user_id

        resp = self.http.post(
            urljoin(f"{self.base_url}/", "Users/AuthenticateByName"),
            headers={"Accept": "application/json", "X-Emby-Authorization": AUTH_HEADER},
            json={"Username": self.username, "Pw": self.password},
            timeout=TIMEOUT,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Emby 登录失败 ({resp.status_code})")
        body = resp.json() if resp.content else {}
        self._token = str(body.get("AccessToken") or "").strip()
        self._resolved_user_id = str((body.get("User") or {}).get("Id") or "").strip()
        if not self._token or not self._resolved_user_id:
            raise RuntimeError("Emby 登录未返回有效凭据")
        return self._token, self._resolved_user_id

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        token, user_id = self._ensure_auth()
        url = urljoin(f"{self.base_url}/", path.replace("{userId}", quote(user_id, safe="")))
        qs = urlencode({"api_key": token, **(params or {})})
        resp = self.http.get(f"{url}?{qs}", headers={"Accept": "application/json"}, timeout=TIMEOUT)
        if resp.status_code >= 400:
            raise RuntimeError(f"Emby 响应异常 ({resp.status_code})")
        return resp.json() if resp.content else {}

    def check_health(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "service": "emby",
                "name": "Emby",
                "configured": False,
                "connected": False,
                "status": "unconfigured",
                "message": "未配置",
                "webUrl": self.app_url(),
            }
        try:
            data = self._get("Users/{userId}/Items", {"Recursive": "true", "IncludeItemTypes": "Movie,Series", "Limit": 1})
            total = int(data.get("TotalRecordCount") or 0) if isinstance(data, dict) else 0
            return {
                "service": "emby",
                "name": "Emby",
                "configured": True,
                "connected": True,
                "status": "ok",
                "message": f"在线 · {total} 部影视",
                "count": total,
                "webUrl": self.app_url(),
            }
        except Exception as exc:
            return {
                "service": "emby",
                "name": "Emby",
                "configured": True,
                "connected": False,
                "status": "error",
                "message": sanitize_text(exc),
                "webUrl": self.app_url(),
            }

    def fetch_library_snapshot(self) -> dict[str, Any]:
        """Build an exact TMDB-indexed snapshot of movies, series, episodes, and today's additions."""
        empty = {
            "available": False,
            "movies": {},
            "series": {},
            "episodes": {},
            "recentToday": [],
            "error": "",
        }
        if not self.configured:
            return empty
        try:
            data = self._get(
                "Users/{userId}/Items",
                {
                    "Recursive": "true",
                    "IncludeItemTypes": "Movie,Series,Episode",
                    "Fields": "ProviderIds,Path,DateCreated,ProductionYear",
                    "SortBy": "DateCreated",
                    "SortOrder": "Descending",
                    "Limit": 8000,
                },
            )
            rows = data.get("Items") if isinstance(data, dict) and isinstance(data.get("Items"), list) else []
            movies: dict[str, dict[str, Any]] = {}
            series: dict[str, dict[str, Any]] = {}
            series_id_to_tmdb: dict[str, str] = {}
            episodes: dict[str, set[tuple[int, int]]] = {}
            recent_today: list[dict[str, Any]] = []
            today_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

            for item in rows:
                if not isinstance(item, dict):
                    continue
                itype = item.get("Type")
                item_id = str(item.get("Id") or "")
                path = str(item.get("Path") or "")
                is_strm = path.casefold().endswith(".strm")
                created = str(item.get("DateCreated") or "")
                tmdb = _tmdb_id(item)

                if itype == "Movie" and tmdb:
                    movies[tmdb] = {
                        "itemId": item_id,
                        "title": str(item.get("Name") or ""),
                        "isStrm": is_strm,
                        "dateCreated": created,
                        "webUrl": self.app_url(item_id),
                    }
                    if created.startswith(today_str):
                        recent_today.append({
                            "mediaKey": f"movie:{tmdb}",
                            "mediaType": "movie",
                            "tmdbId": tmdb,
                            "title": str(item.get("Name") or ""),
                            "episodeLabel": "整部电影",
                            "isStrm": is_strm,
                            "dateCreated": created,
                            "webUrl": self.app_url(item_id),
                        })
                elif itype == "Series" and tmdb:
                    series_id_to_tmdb[item_id] = tmdb
                    series[tmdb] = {
                        "itemId": item_id,
                        "title": str(item.get("Name") or ""),
                        "dateCreated": created,
                        "webUrl": self.app_url(item_id),
                    }

            for item in rows:
                if not isinstance(item, dict) or item.get("Type") != "Episode":
                    continue
                sid = str(item.get("SeriesId") or "")
                tmdb = series_id_to_tmdb.get(sid, "")
                s_num = item.get("ParentIndexNumber")
                e_num = item.get("IndexNumber")
                if not tmdb or not isinstance(s_num, int) or not isinstance(e_num, int):
                    continue
                episodes.setdefault(tmdb, set()).add((s_num, e_num))
                created = str(item.get("DateCreated") or "")
                if created.startswith(today_str):
                    item_id = str(item.get("Id") or "")
                    path = str(item.get("Path") or "")
                    recent_today.append({
                        "mediaKey": f"tv:{tmdb}",
                        "mediaType": "tv",
                        "tmdbId": tmdb,
                        "title": str(item.get("SeriesName") or series.get(tmdb, {}).get("title") or ""),
                        "episodeLabel": f"S{s_num:02d}E{e_num:02d}",
                        "seasonNumber": s_num,
                        "episodeNumber": e_num,
                        "isStrm": path.casefold().endswith(".strm"),
                        "dateCreated": created,
                        "webUrl": self.app_url(item_id),
                    })

            return {
                "available": True,
                "movies": movies,
                "series": series,
                "episodes": {k: sorted(v) for k, v in episodes.items()},
                "recentToday": recent_today[:40],
                "error": "",
            }
        except Exception as exc:
            return {**empty, "error": sanitize_text(exc)}
