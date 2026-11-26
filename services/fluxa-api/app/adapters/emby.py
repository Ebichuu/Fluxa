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

    def _fetch_paged_items(
        self,
        *,
        include_types: str,
        fields: str,
        page_size: int = 2000,
        max_items: int = 200000,
    ) -> tuple[list[dict[str, Any]], int, bool]:
        """Paginate Emby Users/{userId}/Items cleanly via StartIndex + Limit so large libraries never truncate."""
        collected: list[dict[str, Any]] = []
        start_index = 0
        total_records = 0
        while len(collected) < max_items:
            limit = min(page_size, max_items - len(collected))
            data = self._get(
                "Users/{userId}/Items",
                {
                    "Recursive": "true",
                    "IncludeItemTypes": include_types,
                    "Fields": fields,
                    "SortBy": "DateCreated",
                    "SortOrder": "Descending",
                    "StartIndex": start_index,
                    "Limit": limit,
                },
            )
            batch = data.get("Items") if isinstance(data, dict) and isinstance(data.get("Items"), list) else []
            raw_total = data.get("TotalRecordCount") if isinstance(data, dict) else None
            if isinstance(raw_total, int) and raw_total >= 0:
                total_records = max(total_records, raw_total)
            if not batch:
                break
            collected.extend(r for r in batch if isinstance(r, dict))
            start_index += len(batch)
            if total_records > 0 and start_index >= total_records:
                break
            if len(batch) < limit:
                break
        if total_records == 0:
            total_records = len(collected)
        truncated = total_records > len(collected)
        return collected, total_records, truncated

    def fetch_library_snapshot(self, *, page_size: int = 2000, max_movies_series: int = 100000, max_episodes: int = 200000) -> dict[str, Any]:
        """Build a complete, paginated TMDB-indexed snapshot of movies, series, episodes, and today's additions.

        Loads ALL Movies & Series FIRST (paginated), then ALL Episodes (paginated), so parent Series IDs
        are never missed and large libraries (>8000 items) never silently lose episodes.
        """
        empty = {
            "available": False,
            "complete": False,
            "truncated": False,
            "truncationWarning": "",
            "totalMoviesAndSeries": 0,
            "totalEpisodes": 0,
            "fetchedEpisodes": 0,
            "orphanEpisodeCount": 0,
            "movies": {},
            "series": {},
            "episodes": {},
            "episodeDetails": {},
            "recentToday": [],
            "error": "",
        }
        if not self.configured:
            return empty
        try:
            parent_rows, total_parents, parents_truncated = self._fetch_paged_items(
                include_types="Movie,Series",
                fields="ProviderIds,Path,DateCreated,ProductionYear",
                page_size=page_size,
                max_items=max_movies_series,
            )
            ep_rows, total_eps, eps_truncated = self._fetch_paged_items(
                include_types="Episode",
                fields="Path,DateCreated",
                page_size=page_size,
                max_items=max_episodes,
            )
            movies: dict[str, dict[str, Any]] = {}
            series: dict[str, dict[str, Any]] = {}
            series_id_to_tmdb: dict[str, str] = {}
            episodes: dict[str, set[tuple[int, int]]] = {}
            episode_details: dict[str, dict[str, list[dict[str, Any]]]] = {}
            recent_today: list[dict[str, Any]] = []
            today_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

            for item in parent_rows:
                itype = item.get("Type")
                item_id = str(item.get("Id") or "")
                path = str(item.get("Path") or "")
                file_name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else ""
                is_strm = path.casefold().endswith(".strm")
                created = str(item.get("DateCreated") or "")
                tmdb = _tmdb_id(item)

                if itype == "Movie" and tmdb:
                    movies[tmdb] = {
                        "itemId": item_id,
                        "title": str(item.get("Name") or ""),
                        "path": path,
                        "fileName": file_name,
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

            orphan_eps = 0
            for item in ep_rows:
                if item.get("Type") != "Episode":
                    continue
                sid = str(item.get("SeriesId") or "")
                tmdb = series_id_to_tmdb.get(sid, "")
                s_num = item.get("ParentIndexNumber")
                e_num = item.get("IndexNumber")
                if not tmdb:
                    orphan_eps += 1
                    continue
                if not isinstance(s_num, int) or not isinstance(e_num, int):
                    continue
                episodes.setdefault(tmdb, set()).add((s_num, e_num))
                item_id = str(item.get("Id") or "")
                path = str(item.get("Path") or "")
                file_name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else ""
                created = str(item.get("DateCreated") or "")
                ep_label = f"S{s_num:02d}E{e_num:02d}"
                episode_details.setdefault(tmdb, {}).setdefault(ep_label, []).append({
                    "itemId": item_id,
                    "title": str(item.get("Name") or ""),
                    "path": path,
                    "fileName": file_name,
                    "isStrm": path.casefold().endswith(".strm"),
                    "dateCreated": created,
                    "webUrl": self.app_url(item_id),
                })
                if created.startswith(today_str):
                    recent_today.append({
                        "mediaKey": f"tv:{tmdb}",
                        "mediaType": "tv",
                        "tmdbId": tmdb,
                        "title": str(item.get("SeriesName") or series.get(tmdb, {}).get("title") or ""),
                        "episodeLabel": ep_label,
                        "seasonNumber": s_num,
                        "episodeNumber": e_num,
                        "isStrm": path.casefold().endswith(".strm"),
                        "dateCreated": created,
                        "webUrl": self.app_url(item_id),
                    })

            truncated = parents_truncated or eps_truncated
            warn_parts = []
            if parents_truncated:
                warn_parts.append(f"电影/剧集仅拉取 {len(parent_rows)}/{total_parents} 项")
            if eps_truncated:
                warn_parts.append(f"分集仅拉取 {len(ep_rows)}/{total_eps} 项")
            trunc_warn = f"Emby 库存快照未完整覆盖（{'，'.join(warn_parts)}），未查到的作品不能断言未入库" if truncated else ""

            return {
                "available": True,
                "complete": not truncated,
                "truncated": truncated,
                "truncationWarning": trunc_warn,
                "totalMoviesAndSeries": total_parents,
                "totalEpisodes": total_eps,
                "fetchedEpisodes": len(ep_rows),
                "orphanEpisodeCount": orphan_eps,
                "movies": movies,
                "series": series,
                "episodes": {k: sorted(v) for k, v in episodes.items()},
                "episodeDetails": episode_details,
                "recentToday": recent_today[:40],
                "error": "",
            }
        except Exception as exc:
            return {**empty, "error": sanitize_text(exc)}
