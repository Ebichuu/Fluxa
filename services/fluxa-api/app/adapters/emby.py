from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlencode, urljoin

import requests
from requests.adapters import HTTPAdapter

from app.adapters.http_utils import safe_web_url, sanitize_text

TIMEOUT = 12
BEIJING_TZ = timezone(timedelta(hours=8))
AUTH_HEADER = 'MediaBrowser Client="Fluxa", Device="fluxa-v2", DeviceId="fluxa-v2", Version="2.0"'
SERIES_MAP_CACHE_TTL_SECONDS = 86400.0  # 24 hours for positive SeriesId -> TMDB hits
_EXPLICIT_TMDB_TAG_RE = re.compile(
    r"(?i)(?:\{|\[)\s*(?:tmdb(?:id)?)\s*[-=:]\s*(\d{1,10})\s*(?:\}|\])"
)
_MULTI_ARC_OR_VERSION_RE = re.compile(
    r"(?i)(?:4k|2160p|篇章|特别篇|版本|重制|重置|剧场版|第[二三四五六七八九十2-9]季|season\s*[2-9]|part\s*[2-9]|\bS0[2-9]\b|\b(?:ova|oad|sp\d*)\b)"
)


def _tmdb_id(item: dict[str, Any]) -> str:
    pids = item.get("ProviderIds")
    if not isinstance(pids, dict):
        return ""
    raw = str(pids.get("Tmdb") or pids.get("tmdb") or "").strip()
    return raw if raw.isdigit() and int(raw) > 0 else ""


def extract_explicit_tmdb_diagnostic(title: str, path_texts: list[str]) -> dict[str, Any]:
    """Extract explicit `{tmdb-xxx}` / `[tmdbid=xxx]` hints strictly for diagnosis (NEVER auto-apply!)."""
    found_ids: list[str] = []
    for text in path_texts:
        for m in _EXPLICIT_TMDB_TAG_RE.finditer(str(text or "")):
            tid = m.group(1).strip()
            if tid.isdigit() and int(tid) > 0 and tid not in found_ids:
                found_ids.append(tid)
    combined = " ".join([str(title or ""), *[str(p or "") for p in path_texts]])
    multi_arc_flag = bool(_MULTI_ARC_OR_VERSION_RE.search(combined))

    if len(found_ids) > 1:
        return {
            "explicitTmdbHint": "",
            "explicitTmdbCandidates": found_ids,
            "hasConflictOrMultiArc": True,
            "requiresConfirm": True,
            "diagnosticHint": (
                f"目录/文件路径含多个冲突的显式 TMDB 标识（{', '.join('#' + x for x in found_ids)}），"
                "未自动套用，请在 Emby 人工核对后修正元数据"
            ),
        }
    if len(found_ids) == 1:
        tid = found_ids[0]
        extra = "；路径含多版本/多篇章或季号特征，需人工核对季集映射" if multi_arc_flag else ""
        return {
            "explicitTmdbHint": tid,
            "explicitTmdbCandidates": found_ids,
            "hasConflictOrMultiArc": multi_arc_flag,
            "requiresConfirm": True,
            "diagnosticHint": f"目录含显式 TMDB #{tid}（仅供辅助诊断，未自动套用{extra}；请在 Emby 修正元数据）",
        }
    extra = "（路径含多版本/多篇章或季号特征）" if multi_arc_flag else ""
    return {
        "explicitTmdbHint": "",
        "explicitTmdbCandidates": [],
        "hasConflictOrMultiArc": multi_arc_flag,
        "requiresConfirm": True,
        "diagnosticHint": f"Emby 剧集缺少 TMDB ProviderId{extra}；不能仅按标题自动套 ID，请在 Emby 确认后修正元数据",
    }


def is_emby_identity_incomplete(emby: dict[str, Any] | None) -> bool:
    """Return True if Emby snapshot has unresolved SeriesId->TMDB mappings or orphan episodes.

    Intentionally does NOT trust `complete=True` alone: even if pagination completed (`complete=True`
    from legacy/external snapshots), any orphan episodes, unresolved/failed SeriesIds, or
    `identityComplete=False` means absence in Emby CANNOT be asserted.
    """
    if not isinstance(emby, dict):
        return False
    return bool(
        emby.get("identityComplete") is False
        or int(emby.get("orphanEpisodeCount") or 0) > 0
        or (emby.get("unresolvedSeriesIds") or [])
        or (emby.get("failedSeriesLookupIds") or [])
        or (emby.get("unmappedSeriesIds") or [])
        or (emby.get("unmappedSeries") or [])
    )


class EmbyAdapter:
    """Emby read adapter: checks actual library existence (Movie / Series / Episode) & today's additions."""

    def __init__(self, cfg: dict[str, Any], session: requests.Session | None = None, store: Any | None = None):
        self.base_url = str(cfg.get("baseUrl") or "").strip().rstrip("/")
        self.web_url = str(cfg.get("webUrl") or "").strip().rstrip("/")
        self.api_key = str(cfg.get("apiKey") or "").strip()
        self.user_id = str(cfg.get("userId") or "").strip()
        self.username = str(cfg.get("username") or "").strip()
        self.password = str(cfg.get("password") or "")
        if session is not None:
            self.http = session
        else:
            sess = requests.Session()
            adapter = HTTPAdapter(pool_connections=8, pool_maxsize=8)
            sess.mount("http://", adapter)
            sess.mount("https://", adapter)
            self.http = sess
        self.store = store
        self._token = self.api_key
        self._resolved_user_id = self.user_id
        self._series_id_cache: dict[str, dict[str, Any]] = {}

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
        concurrency: int = 4,
    ) -> tuple[list[dict[str, Any]], int, bool]:
        """Paginate Emby Users/{userId}/Items cleanly via StartIndex + Limit without truncating.

        Fetches page 0 first to learn `TotalRecordCount`, then fetches remaining pages with bounded
        concurrency (default 4) while preserving exact page order and 100% completeness.
        """
        first_limit = min(page_size, max_items)
        if first_limit <= 0:
            return [], 0, False

        def _fetch_page(start_idx: int, limit: int) -> tuple[list[dict[str, Any]], int | None]:
            data = self._get(
                "Users/{userId}/Items",
                {
                    "Recursive": "true",
                    "IncludeItemTypes": include_types,
                    "Fields": fields,
                    "SortBy": "DateCreated",
                    "SortOrder": "Descending",
                    "StartIndex": start_idx,
                    "Limit": limit,
                },
            )
            batch = data.get("Items") if isinstance(data, dict) and isinstance(data.get("Items"), list) else []
            raw_total = data.get("TotalRecordCount") if isinstance(data, dict) else None
            total_val = raw_total if isinstance(raw_total, int) and raw_total >= 0 else None
            return [r for r in batch if isinstance(r, dict)], total_val

        first_batch, raw_total = _fetch_page(0, first_limit)
        total_records = raw_total if raw_total is not None else len(first_batch)
        collected: list[dict[str, Any]] = list(first_batch)

        if not first_batch or len(first_batch) < first_limit:
            total_records = max(total_records, len(collected))
            return collected, total_records, total_records > len(collected)

        target_cap = min(max_items, total_records) if raw_total is not None and total_records > 0 else max_items
        if len(collected) < target_cap:
            if raw_total is not None and total_records > len(collected) and concurrency > 1:
                offsets: list[tuple[int, int]] = []
                idx = len(collected)
                while idx < target_cap:
                    lim = min(page_size, target_cap - idx)
                    offsets.append((idx, lim))
                    idx += lim
                workers = min(max(1, int(concurrency)), len(offsets))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(_fetch_page, s, lim) for s, lim in offsets]
                    for fut in futures:
                        batch, t_val = fut.result()
                        if t_val is not None:
                            total_records = max(total_records, t_val)
                        if batch:
                            collected.extend(batch)
            else:
                start_index = len(collected)
                while len(collected) < target_cap:
                    lim = min(page_size, target_cap - len(collected))
                    batch, t_val = _fetch_page(start_index, lim)
                    if t_val is not None:
                        total_records = max(total_records, t_val)
                    if not batch:
                        break
                    collected.extend(batch)
                    start_index += len(batch)
                    if total_records > 0 and start_index >= total_records:
                        break
                    if len(batch) < lim:
                        break

        if total_records == 0:
            total_records = len(collected)
        truncated = total_records > len(collected)
        return collected, total_records, truncated

    def _series_cache_key(self) -> str:
        return f"emby:series_map:{self.base_url.casefold()}"

    def _load_series_map_cache(self) -> dict[str, dict[str, Any]]:
        now_ts = time.time()
        merged: dict[str, dict[str, Any]] = {}
        if self.store is not None:
            try:
                cached = self.store.get_cache(self._series_cache_key())
                payload = cached.get("payload") if isinstance(cached, dict) else None
                if isinstance(payload, dict):
                    for sid, entry in payload.items():
                        if not isinstance(entry, dict):
                            continue
                        tmdb = str(entry.get("tmdbId") or "").strip()
                        cached_at = float(entry.get("cachedAt") or 0)
                        if tmdb.isdigit() and (now_ts - cached_at) <= SERIES_MAP_CACHE_TTL_SECONDS:
                            merged[str(sid)] = entry
            except Exception:
                pass
        for sid, entry in self._series_id_cache.items():
            tmdb = str(entry.get("tmdbId") or "").strip()
            cached_at = float(entry.get("cachedAt") or 0)
            if tmdb.isdigit() and (now_ts - cached_at) <= SERIES_MAP_CACHE_TTL_SECONDS:
                merged[str(sid)] = entry
        return merged

    def _persist_series_map_cache(self, updates: dict[str, dict[str, Any]]) -> None:
        if not updates:
            return
        now_ts = time.time()
        current = self._load_series_map_cache()
        for sid, entry in updates.items():
            tmdb = str(entry.get("tmdbId") or "").strip()
            if not sid or not tmdb.isdigit():
                continue
            record = {
                "tmdbId": tmdb,
                "title": str(entry.get("title") or ""),
                "dateCreated": str(entry.get("dateCreated") or ""),
                "cachedAt": float(entry.get("cachedAt") or now_ts),
            }
            self._series_id_cache[str(sid)] = record
            current[str(sid)] = record
        if self.store is not None:
            try:
                self.store.set_cache(self._series_cache_key(), current)
            except Exception:
                pass

    def _lookup_missing_series_providers(
        self,
        missing_series_ids: list[str],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
        """Resolve SeriesIds absent from the Series listing via cache + targeted Emby ProviderIds queries.

        Returns `(resolved_by_sid, unmapped_meta_by_sid, failed_sids)`.
        - Positive SeriesId -> TMDB mappings are cached in memory & SQLite (24h TTL).
        - Failures are NEVER cached as permanent misses so transient errors recover on next refresh.
        """
        ordered_ids = sorted({str(s).strip() for s in missing_series_ids if str(s or "").strip()})
        if not ordered_ids:
            return {}, {}, []

        cached_map = self._load_series_map_cache()
        resolved: dict[str, dict[str, Any]] = {}
        to_fetch: list[str] = []
        for sid in ordered_ids:
            hit = cached_map.get(sid)
            if hit and str(hit.get("tmdbId") or "").isdigit():
                resolved[sid] = hit
            else:
                to_fetch.append(sid)

        unmapped_meta: dict[str, dict[str, Any]] = {}
        failed_sids: list[str] = []
        newly_resolved: dict[str, dict[str, Any]] = {}
        now_ts = time.time()

        def _ingest_item(expected_sid: str, item: dict[str, Any]) -> bool:
            if not isinstance(item, dict):
                return False
            sid = str(item.get("Id") or expected_sid).strip()
            if not sid:
                return False
            tmdb = _tmdb_id(item)
            if tmdb:
                entry = {
                    "tmdbId": tmdb,
                    "title": str(item.get("Name") or ""),
                    "dateCreated": str(item.get("DateCreated") or ""),
                    "cachedAt": now_ts,
                }
                resolved[sid] = entry
                newly_resolved[sid] = entry
                return True
            unmapped_meta[sid] = {
                "seriesId": sid,
                "title": str(item.get("Name") or f"Emby 剧集 #{sid}"),
                "path": str(item.get("Path") or ""),
                "dateCreated": str(item.get("DateCreated") or ""),
            }
            return True

        # 1. Batch targeted lookup via Users/{userId}/Items?Ids=...&Fields=ProviderIds,Path,DateCreated
        batch_size = 40
        still_missing: list[str] = []
        for i in range(0, len(to_fetch), batch_size):
            chunk = to_fetch[i : i + batch_size]
            seen_in_batch: set[str] = set()
            try:
                data = self._get(
                    "Users/{userId}/Items",
                    {
                        "Ids": ",".join(chunk),
                        "Fields": "ProviderIds,Path,DateCreated",
                    },
                )
                rows = data.get("Items") if isinstance(data, dict) and isinstance(data.get("Items"), list) else []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    rid = str(row.get("Id") or "").strip()
                    if rid in chunk:
                        seen_in_batch.add(rid)
                        _ingest_item(rid, row)
            except Exception:
                pass
            for sid in chunk:
                if sid not in seen_in_batch and sid not in resolved and sid not in unmapped_meta:
                    still_missing.append(sid)

        # 2. Single-item fallback Users/{userId}/Items/{seriesId} for any SeriesId not returned in batch
        for sid in still_missing:
            try:
                item = self._get(
                    f"Users/{{userId}}/Items/{quote(sid, safe='')}",
                    {"Fields": "ProviderIds,Path,DateCreated"},
                )
                if isinstance(item, dict) and (item.get("Id") or item.get("ProviderIds") is not None or item.get("Name")):
                    _ingest_item(sid, item)
                else:
                    failed_sids.append(sid)
            except Exception:
                failed_sids.append(sid)

        if newly_resolved:
            self._persist_series_map_cache(newly_resolved)

        return resolved, unmapped_meta, sorted(set(failed_sids))

    def fetch_library_snapshot(self, *, page_size: int = 2000, max_movies_series: int = 100000, max_episodes: int = 200000) -> dict[str, Any]:
        """Build a complete, paginated TMDB-indexed snapshot of movies, series, episodes, and today's additions.

        Distinguishes `dataReadComplete` (pagination read all items) from `identityComplete`
        (every episode's SeriesId mapped to a TMDB ProviderId), builds a concrete `unmappedSeries`
        checklist with Emby deep links and explicit-path-tag diagnostic hints (never auto-applying
        by title), and never treats mapping gaps as "episode absent from library".
        """
        empty = {
            "available": False,
            "complete": False,
            "dataReadComplete": False,
            "paginationComplete": False,
            "identityComplete": False,
            "identityStatus": "unavailable",
            "truncated": False,
            "truncationWarning": "",
            "identityWarning": "",
            "totalMoviesAndSeries": 0,
            "totalEpisodes": 0,
            "fetchedEpisodes": 0,
            "orphanEpisodeCount": 0,
            "resolvedMissingSeriesCount": 0,
            "unresolvedSeriesIds": [],
            "failedSeriesLookupIds": [],
            "unmappedSeriesIds": [],
            "unmappedSeries": [],
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
            unmapped_parent_series: dict[str, dict[str, Any]] = {}
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
                elif itype == "Series":
                    if tmdb:
                        series_id_to_tmdb[item_id] = tmdb
                        series[tmdb] = {
                            "itemId": item_id,
                            "title": str(item.get("Name") or ""),
                            "dateCreated": created,
                            "webUrl": self.app_url(item_id),
                        }
                    elif item_id:
                        unmapped_parent_series[item_id] = {
                            "seriesId": item_id,
                            "title": str(item.get("Name") or f"Emby 剧集 #{item_id}"),
                            "path": path,
                            "dateCreated": created,
                        }

            # Targeted lookup for any Episode SeriesId missing from the Series listing (e.g. 454815 -> 350389 -> TMDB 295790)
            missing_series_ids = sorted({
                str(item.get("SeriesId") or "").strip()
                for item in ep_rows
                if item.get("Type") == "Episode"
                and str(item.get("SeriesId") or "").strip()
                and str(item.get("SeriesId") or "").strip() not in series_id_to_tmdb
                and str(item.get("SeriesId") or "").strip() not in unmapped_parent_series
            })
            resolved_missing, unmapped_lookup_meta, failed_lookup_sids = self._lookup_missing_series_providers(
                missing_series_ids
            )
            for sid, info in resolved_missing.items():
                tmdb = str(info.get("tmdbId") or "").strip()
                if not tmdb:
                    continue
                series_id_to_tmdb[sid] = tmdb
                series.setdefault(
                    tmdb,
                    {
                        "itemId": sid,
                        "title": str(info.get("title") or ""),
                        "dateCreated": str(info.get("dateCreated") or ""),
                        "webUrl": self.app_url(sid),
                    },
                )

            orphan_eps = 0
            orphan_series_ids: set[str] = set()
            orphan_ep_counts: dict[str, int] = {}
            orphan_ep_samples: dict[str, list[str]] = {}
            orphan_series_names: dict[str, str] = {}

            for item in ep_rows:
                if item.get("Type") != "Episode":
                    continue
                sid = str(item.get("SeriesId") or "").strip()
                tmdb = series_id_to_tmdb.get(sid, "")
                s_num = item.get("ParentIndexNumber")
                e_num = item.get("IndexNumber")
                if not tmdb:
                    orphan_eps += 1
                    if sid:
                        orphan_series_ids.add(sid)
                        orphan_ep_counts[sid] = orphan_ep_counts.get(sid, 0) + 1
                        p = str(item.get("Path") or "").strip()
                        if p and len(orphan_ep_samples.setdefault(sid, [])) < 3:
                            orphan_ep_samples[sid].append(p)
                        sn = str(item.get("SeriesName") or "").strip()
                        if sn and sid not in orphan_series_names:
                            orphan_series_names[sid] = sn
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
            pagination_complete = not truncated
            unmapped_meta_all: dict[str, dict[str, Any]] = {**unmapped_parent_series, **unmapped_lookup_meta}
            unmapped_sids_all = sorted(
                set(unmapped_lookup_meta.keys()) | (set(unmapped_parent_series.keys()) & orphan_series_ids)
            )
            unresolved_series_ids = sorted(set(unmapped_sids_all) | set(failed_lookup_sids) | orphan_series_ids)
            identity_complete = bool(orphan_eps == 0 and not unresolved_series_ids and not failed_lookup_sids)
            complete = bool(pagination_complete and identity_complete)

            # Build rich, actionable unmappedSeries checklist (with Emby deep links + explicit TMDB diagnostic hints)
            unmapped_series_list: list[dict[str, Any]] = []
            for sid in unresolved_series_ids:
                meta = unmapped_meta_all.get(sid) or {}
                title = str(meta.get("title") or orphan_series_names.get(sid) or f"Emby 剧集 #{sid}")
                path = str(meta.get("path") or "")
                samples = orphan_ep_samples.get(sid) or []
                is_lookup_fail = sid in failed_lookup_sids
                diag = extract_explicit_tmdb_diagnostic(title, [path, *samples])
                unmapped_series_list.append({
                    "seriesId": sid,
                    "title": title,
                    "path": path or (samples[0].rsplit("/", 1)[0] if samples and "/" in samples[0] else ""),
                    "episodeCount": orphan_ep_counts.get(sid, 0),
                    "embyUrl": self.app_url(sid),
                    "reason": "lookup_failed" if is_lookup_fail else "missing_tmdb_provider_id",
                    "reasonLabel": "SeriesId 定向查询失败" if is_lookup_fail else "缺少 TMDB ProviderId",
                    "explicitTmdbHint": diag["explicitTmdbHint"],
                    "explicitTmdbCandidates": diag["explicitTmdbCandidates"],
                    "hasConflictOrMultiArc": diag["hasConflictOrMultiArc"],
                    "requiresConfirm": True,
                    "diagnosticHint": (
                        "定向查询该 SeriesId 失败，暂无法读取元数据，请在 Emby 检查条目状态"
                        if is_lookup_fail
                        else diag["diagnosticHint"]
                    ),
                })

            # Flag multiple unmapped directories pointing to the same explicit TMDB hint (e.g. multi-version/multi-arc)
            hint_counts: dict[str, int] = {}
            for row in unmapped_series_list:
                h = row.get("explicitTmdbHint") or ""
                if h:
                    hint_counts[h] = hint_counts.get(h, 0) + 1
            for row in unmapped_series_list:
                h = row.get("explicitTmdbHint") or ""
                if h and hint_counts.get(h, 0) > 1:
                    row["hasConflictOrMultiArc"] = True
                    row["diagnosticHint"] = (
                        f"多个未关联目录均含显式 TMDB #{h}（可能为多版本或多篇章目录），未自动套用，请人工确认后在 Emby 修正元数据"
                    )
            unmapped_series_list.sort(key=lambda r: (-int(r.get("episodeCount") or 0), r.get("title") or ""))

            if identity_complete:
                identity_status = "complete"
            elif failed_lookup_sids:
                identity_status = "lookup_failed"
            else:
                identity_status = "partial_unmapped"

            warn_parts = []
            if parents_truncated:
                warn_parts.append(f"电影/剧集仅拉取 {len(parent_rows)}/{total_parents} 项")
            if eps_truncated:
                warn_parts.append(f"分集仅拉取 {len(ep_rows)}/{total_eps} 项")
            trunc_warn = f"Emby 库存快照未完整覆盖（{'，'.join(warn_parts)}），未查到的作品不能断言未入库" if truncated else ""

            if identity_complete:
                identity_warn = ""
            elif failed_lookup_sids:
                identity_warn = (
                    f"数据读取{'完整' if pagination_complete else '存在截断'}，但有 {len(failed_lookup_sids)} 个 SeriesId 定向查询失败、"
                    f"{orphan_eps} 条分集未关联到 TMDB；不能把映射失败当作库内不存在。"
                )
            else:
                identity_warn = (
                    f"数据读取{'完整' if pagination_complete else '存在截断'}（{len(ep_rows)} 条分集），但有 {len(unmapped_series_list)} 个 Emby 剧集缺少 TMDB ProviderId"
                    f"（涉及 {orphan_eps} 条分集未关联）；这不是缺库也不是 Fluxa 故障，未关联作品将显示「库存未确认」，可点下方清单去 Emby 修正元数据。"
                )

            return {
                "available": True,
                "complete": complete,
                "dataReadComplete": pagination_complete,
                "paginationComplete": pagination_complete,
                "identityComplete": identity_complete,
                "identityStatus": identity_status,
                "truncated": truncated,
                "truncationWarning": trunc_warn,
                "identityWarning": identity_warn,
                "totalMoviesAndSeries": total_parents,
                "totalEpisodes": total_eps,
                "fetchedEpisodes": len(ep_rows),
                "orphanEpisodeCount": orphan_eps,
                "resolvedMissingSeriesCount": len(resolved_missing),
                "unresolvedSeriesIds": unresolved_series_ids,
                "failedSeriesLookupIds": failed_lookup_sids,
                "unmappedSeriesIds": unmapped_sids_all,
                "unmappedSeries": unmapped_series_list,
                "movies": movies,
                "series": series,
                "episodes": {k: sorted(v) for k, v in episodes.items()},
                "episodeDetails": episode_details,
                "recentToday": recent_today[:40],
                "error": "",
            }
        except Exception as exc:
            return {**empty, "error": sanitize_text(exc)}
