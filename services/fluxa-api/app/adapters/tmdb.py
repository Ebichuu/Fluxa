from __future__ import annotations

from typing import Any

import requests

from app.adapters.http_utils import sanitize_text

TIMEOUT = 10
TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/w342"


class TmdbAdapter:
    def __init__(self, cfg: dict[str, Any], session: requests.Session | None = None):
        self.api_key = str(cfg.get("apiKey") or "").strip()
        self.bearer = str(cfg.get("bearerToken") or "").strip()
        self.http = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.api_key or self.bearer)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError("TMDB 未配置")
        headers = {"Accept": "application/json"}
        query = {"language": "zh-CN", **(params or {})}
        if self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"
        else:
            query["api_key"] = self.api_key
        resp = self.http.get(f"{TMDB_API}/{path.lstrip('/')}", headers=headers, params=query, timeout=TIMEOUT)
        if resp.status_code >= 400:
            raise RuntimeError(f"TMDB 响应异常 ({resp.status_code})")
        return resp.json() if resp.content else {}

    def check_health(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "service": "tmdb",
                "name": "TMDB",
                "configured": False,
                "connected": False,
                "status": "unconfigured",
                "message": "未配置",
                "webUrl": "https://www.themoviedb.org",
            }
        try:
            self._get("configuration")
            return {
                "service": "tmdb",
                "name": "TMDB",
                "configured": True,
                "connected": True,
                "status": "ok",
                "message": "在线",
                "webUrl": "https://www.themoviedb.org",
            }
        except Exception as exc:
            return {
                "service": "tmdb",
                "name": "TMDB",
                "configured": True,
                "connected": False,
                "status": "error",
                "message": sanitize_text(exc),
                "webUrl": "https://www.themoviedb.org",
            }

    @staticmethod
    def _normalize_item(item: dict[str, Any], forced_type: str = "") -> dict[str, Any] | None:
        mtype = forced_type or str(item.get("media_type") or "").lower()
        if mtype not in {"movie", "tv"}:
            return None
        tmdb_id = item.get("id")
        if not tmdb_id:
            return None
        title = str(item.get("title") or item.get("name") or item.get("original_title") or item.get("original_name") or "").strip()
        date_str = str(item.get("release_date") or item.get("first_air_date") or "").strip()
        poster = str(item.get("poster_path") or "").strip()
        return {
            "mediaKey": f"{mtype}:{tmdb_id}",
            "mediaType": mtype,
            "tmdbId": str(tmdb_id),
            "title": title,
            "originalTitle": str(item.get("original_title") or item.get("original_name") or "").strip(),
            "releaseDate": date_str,
            "year": date_str[:4] if len(date_str) >= 4 else "",
            "rating": round(float(item.get("vote_average") or 0), 1),
            "overview": str(item.get("overview") or "").strip(),
            "posterUrl": f"{TMDB_IMG}{poster}" if poster else "",
        }

    def search(self, query: str) -> list[dict[str, Any]]:
        if not self.configured or not query.strip():
            return []
        data = self._get("search/multi", {"query": query.strip(), "include_adult": "false"})
        items = []
        for row in data.get("results") or []:
            normalized = self._normalize_item(row)
            if normalized:
                items.append(normalized)
        return items[:24]

    def discover(self, category: str = "trending_tv") -> list[dict[str, Any]]:
        if not self.configured:
            return []
        if category == "trending_movie":
            data = self._get("trending/movie/week")
            forced = "movie"
        elif category == "on_the_air":
            data = self._get("tv/on_the_air")
            forced = "tv"
        elif category == "now_playing":
            data = self._get("movie/now_playing")
            forced = "movie"
        else:
            data = self._get("trending/tv/week")
            forced = "tv"
        items = []
        for row in data.get("results") or []:
            normalized = self._normalize_item(row, forced)
            if normalized:
                items.append(normalized)
        return items[:24]

    def get_details(self, media_type: str, tmdb_id: str) -> dict[str, Any] | None:
        if not self.configured or media_type not in {"movie", "tv"} or not str(tmdb_id).isdigit():
            return None
        try:
            raw = self._get(f"{media_type}/{tmdb_id}", {"append_to_response": "alternative_titles,translations"})
        except Exception:
            return None
        base = self._normalize_item(raw, media_type)
        if not base:
            return None
        alt_block = raw.get("alternative_titles") if isinstance(raw.get("alternative_titles"), dict) else {}
        alt_rows = alt_block.get("results") or alt_block.get("titles") or []
        alt_titles: list[str] = []
        for row in alt_rows if isinstance(alt_rows, list) else []:
            if isinstance(row, dict):
                t = str(row.get("title") or "").strip()
                if t and t not in alt_titles:
                    alt_titles.append(t)
        trans_block = raw.get("translations") if isinstance(raw.get("translations"), dict) else {}
        for tr in (trans_block.get("translations") or []) if isinstance(trans_block.get("translations"), list) else []:
            if isinstance(tr, dict):
                tr_data = tr.get("data") if isinstance(tr.get("data"), dict) else {}
                t = str(tr_data.get("title") or tr_data.get("name") or "").strip()
                if t and t not in alt_titles:
                    alt_titles.append(t)
        base["alternativeTitles"] = alt_titles[:60]
        if media_type == "tv":
            seasons = []
            for s in raw.get("seasons") or []:
                snum = s.get("season_number")
                if isinstance(snum, int) and snum > 0:
                    seasons.append({
                        "seasonNumber": snum,
                        "name": str(s.get("name") or f"第 {snum} 季"),
                        "airDate": str(s.get("air_date") or ""),
                        "episodeCount": int(s.get("episode_count") or 0),
                    })
            base["seasons"] = seasons
            base["status"] = str(raw.get("status") or "")
            base["lastAirDate"] = str(raw.get("last_air_date") or "")
            base["nextEpisodeToAir"] = raw.get("next_episode_to_air")
        else:
            base["status"] = str(raw.get("status") or "")
        return base

    def get_season_episodes(self, tmdb_id: str, season_number: int) -> list[dict[str, Any]]:
        if not self.configured or not str(tmdb_id).isdigit() or season_number <= 0:
            return []
        try:
            raw = self._get(f"tv/{tmdb_id}/season/{season_number}")
        except Exception:
            return []
        episodes = []
        for ep in raw.get("episodes") or []:
            ep_num = ep.get("episode_number")
            if not isinstance(ep_num, int) or ep_num <= 0:
                continue
            episodes.append({
                "seasonNumber": season_number,
                "episodeNumber": ep_num,
                "episodeLabel": f"S{season_number:02d}E{ep_num:02d}",
                "name": str(ep.get("name") or f"第 {ep_num} 集"),
                "airDate": str(ep.get("air_date") or ""),
            })
        return episodes
