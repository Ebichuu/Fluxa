from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from app.adapters.emby import EmbyAdapter
from app.adapters.http_utils import compact_title, extract_season_episode, sanitize_text
from app.adapters.moviepilot import MoviePilotAdapter
from app.adapters.qbittorrent import QbittorrentAdapter
from app.adapters.symedia import SymediaAdapter
from app.adapters.tmdb import TmdbAdapter
from app.adapters.torra import TorraAdapter
from app.storage import FluxaStore

BEIJING_TZ = timezone(timedelta(hours=8))
SNAPSHOT_FRESH_TTL_SECONDS = 60.0
EMBY_CACHE_TTL_SECONDS = 300.0
TMDB_HEALTH_CACHE_TTL_SECONDS = 600.0
TMDB_DATA_CACHE_TTL_SECONDS = 21600.0  # 6 hours for season episodes & media details


def _now_iso() -> str:
    return datetime.now(BEIJING_TZ).isoformat(timespec="seconds")


class CachedTmdbProxy:
    """SQLite-backed cache wrapper around TmdbAdapter for season episodes & details (6h TTL)."""

    def __init__(self, inner: Any, store: FluxaStore, ttl_seconds: float = TMDB_DATA_CACHE_TTL_SECONDS):
        self._inner = inner
        self._store = store
        self._ttl = ttl_seconds

    @property
    def configured(self) -> bool:
        return bool(getattr(self._inner, "configured", False))

    def check_health(self) -> dict[str, Any]:
        return self._inner.check_health()

    def search(self, query: str) -> list[dict[str, Any]]:
        return self._inner.search(query)

    def discover(self, category: str = "trending_tv") -> list[dict[str, Any]]:
        return self._inner.discover(category)

    def get_details(self, media_type: str, tmdb_id: str) -> dict[str, Any] | None:
        if not self.configured:
            return None
        key = f"tmdb:detail:{media_type}:{tmdb_id}"
        cached = self._store.get_cache(key)
        if cached and cached["ageSeconds"] <= self._ttl:
            return cached["payload"]
        val = self._inner.get_details(media_type, tmdb_id)
        if val is not None:
            self._store.set_cache(key, val)
        return val

    def get_season_episodes(self, tmdb_id: str, season_number: int) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        key = f"tmdb:season:{tmdb_id}:{int(season_number)}"
        cached = self._store.get_cache(key)
        if cached and cached["ageSeconds"] <= self._ttl:
            return cached["payload"]
        val = self._inner.get_season_episodes(tmdb_id, int(season_number))
        if val:
            self._store.set_cache(key, val)
        return val


def build_adapters(settings: dict[str, Any], *, store: FluxaStore | None = None) -> dict[str, Any]:
    svcs = settings.get("services") or {}
    tmdb_raw = TmdbAdapter(svcs.get("tmdb") or {})
    return {
        "moviepilot": MoviePilotAdapter(svcs.get("moviepilot") or {}),
        "torra": TorraAdapter(svcs.get("torra") or {}),
        "qbittorrent": QbittorrentAdapter(svcs.get("qbittorrent") or {}),
        "symedia": SymediaAdapter(svcs.get("symedia") or {}),
        "emby": EmbyAdapter(svcs.get("emby") or {}),
        "tmdb": CachedTmdbProxy(tmdb_raw, store) if store is not None else tmdb_raw,
    }


def _unconfigured_health(service: str, name: str, web_url: str) -> dict[str, Any]:
    return {
        "service": service,
        "name": name,
        "configured": False,
        "connected": False,
        "status": "unconfigured",
        "message": "未配置",
        "webUrl": web_url,
    }


def _error_health(service: str, name: str, exc: Exception, web_url: str) -> dict[str, Any]:
    return {
        "service": service,
        "name": name,
        "configured": True,
        "connected": False,
        "status": "error",
        "message": sanitize_text(exc),
        "webUrl": web_url,
    }


def collect_upstream_snapshot(
    adapters: dict[str, Any],
    *,
    store: FluxaStore | None = None,
    force_emby: bool = False,
) -> dict[str, Any]:
    """Collect read-only facts from all configured upstream services concurrently — ONCE per service!

    Eliminates duplicate health+data fetches and reuses SQLite-cached Emby library snapshots (5 min TTL)
    so refreshes stay fast even on large libraries.
    """

    def _fetch_mp():
        mp = adapters["moviepilot"]
        if not mp.configured:
            return _unconfigured_health("moviepilot", "MoviePilot Neo", mp.app_url()), [], []
        try:
            subs = mp.list_subscriptions()
            hist = _safe_call(mp.list_download_history, [])
            health = {
                "service": "moviepilot",
                "name": "MoviePilot Neo",
                "configured": True,
                "connected": True,
                "status": "ok",
                "message": f"在线 · {len(subs)} 条订阅",
                "count": len(subs),
                "webUrl": mp.app_url(),
            }
            return health, subs, hist
        except Exception as exc:
            return _error_health("moviepilot", "MoviePilot Neo", exc, mp.app_url()), [], []

    def _fetch_torra():
        tr = adapters["torra"]
        empty_sec = {"available": False, "activeRuns": [], "successFiles": [], "failedFiles": [], "recentRuns": [], "webUrl": tr.app_url()}
        if not tr.configured:
            return _unconfigured_health("torra", "Torra", tr.app_url()), [], empty_sec
        try:
            subs = tr.list_subscriptions()
            sec = _safe_call(tr.get_secupload_evidence, empty_sec)
            health = {
                "service": "torra",
                "name": "Torra",
                "configured": True,
                "connected": True,
                "status": "ok",
                "message": f"在线 · {len(subs)} 条订阅",
                "count": len(subs),
                "webUrl": tr.app_url(),
            }
            return health, subs, sec
        except Exception as exc:
            return _error_health("torra", "Torra", exc, tr.app_url()), [], empty_sec

    def _fetch_qb():
        qb = adapters["qbittorrent"]
        if not qb.configured:
            return _unconfigured_health("qbittorrent", "qBittorrent", qb.app_url()), []
        try:
            torrents = qb.list_torrents()
            active = sum(1 for t in torrents if t["status"] in {"downloading", "stalled", "checking"})
            issues = sum(1 for t in torrents if t["status"] in {"missing_files", "error", "stalled"})
            msg = f"在线 · {active} 个下载中" + (f"，{issues} 个异常" if issues else "")
            health = {
                "service": "qbittorrent",
                "name": "qBittorrent",
                "configured": True,
                "connected": True,
                "status": "warn" if issues else "ok",
                "message": msg,
                "count": len(torrents),
                "webUrl": qb.app_url(),
            }
            return health, torrents
        except Exception as exc:
            return _error_health("qbittorrent", "qBittorrent", exc, qb.app_url()), []

    def _fetch_sa():
        sa = adapters["symedia"]
        if not sa.configured:
            return _unconfigured_health("symedia", "Symedia (SA)", sa.app_url()), []
        try:
            records = sa.list_transfer_history(count=120)
            fails = sum(1 for r in records if r["outcome"] in {"failed", "identify_failed"})
            msg = f"在线 · 最近 {len(records)} 条记录" + (f"，{fails} 条异常" if fails else "")
            health = {
                "service": "symedia",
                "name": "Symedia (SA)",
                "configured": True,
                "connected": True,
                "status": "warn" if fails else "ok",
                "message": msg,
                "count": len(records),
                "webUrl": sa.app_url(),
            }
            return health, records
        except Exception as exc:
            return _error_health("symedia", "Symedia (SA)", exc, sa.app_url()), []

    def _fetch_emby():
        emby = adapters["emby"]
        empty_lib = {"available": False, "movies": {}, "series": {}, "episodes": {}, "episodeDetails": {}, "recentToday": [], "truncated": False, "truncationWarning": ""}
        if not emby.configured:
            return _unconfigured_health("emby", "Emby", emby.app_url()), empty_lib, False, ""
        if store is not None and not force_emby:
            cached = store.get_cache("snapshot:emby")
            if cached and cached["ageSeconds"] <= EMBY_CACHE_TTL_SECONDS and isinstance(cached["payload"], dict) and cached["payload"].get("available"):
                lib = cached["payload"]
                total = len(lib.get("movies") or {}) + len(lib.get("series") or {})
                health = {
                    "service": "emby",
                    "name": "Emby",
                    "configured": True,
                    "connected": True,
                    "status": "ok",
                    "message": f"在线 · {total} 部影视（缓存 {int(cached['ageSeconds'])}s）",
                    "count": total,
                    "webUrl": emby.app_url(),
                }
                return health, lib, True, str(cached.get("updatedAt") or "")
        lib = _safe_call(emby.fetch_library_snapshot, empty_lib)
        if lib.get("available"):
            now_iso = _now_iso()
            if store is not None:
                store.set_cache("snapshot:emby", lib, updated_iso=now_iso)
            total = len(lib.get("movies") or {}) + len(lib.get("series") or {})
            health = {
                "service": "emby",
                "name": "Emby",
                "configured": True,
                "connected": True,
                "status": "ok",
                "message": f"在线 · {total} 部影视",
                "count": total,
                "webUrl": emby.app_url(),
            }
            return health, lib, False, now_iso
        return {
            "service": "emby",
            "name": "Emby",
            "configured": True,
            "connected": False,
            "status": "error",
            "message": lib.get("error") or "Emby 连接失败",
            "webUrl": emby.app_url(),
        }, lib, False, ""

    def _fetch_tmdb_health():
        tmdb = adapters["tmdb"]
        if not tmdb.configured:
            return _unconfigured_health("tmdb", "TMDB", "https://www.themoviedb.org")
        if store is not None:
            cached = store.get_cache("snapshot:tmdb_health")
            if cached and cached["ageSeconds"] <= TMDB_HEALTH_CACHE_TTL_SECONDS and isinstance(cached["payload"], dict):
                return cached["payload"]
        health = tmdb.check_health()
        if store is not None and health.get("connected"):
            store.set_cache("snapshot:tmdb_health", health)
        return health

    with ThreadPoolExecutor(max_workers=6) as pool:
        f_mp = pool.submit(_fetch_mp)
        f_tr = pool.submit(_fetch_torra)
        f_qb = pool.submit(_fetch_qb)
        f_sa = pool.submit(_fetch_sa)
        f_emby = pool.submit(_fetch_emby)
        f_tmdb = pool.submit(_fetch_tmdb_health)

        mp_h, mp_subs, mp_hist = f_mp.result()
        tr_h, tr_subs, tr_sec = f_tr.result()
        qb_h, qb_tasks = f_qb.result()
        sa_h, sa_hist = f_sa.result()
        emby_h, emby_lib, emby_from_cache, emby_cached_at_iso = f_emby.result()
        tmdb_h = f_tmdb.result()

        # If Emby came from 5-min cache but SA has a newer archive/replace record, refresh Emby once
        # so freshly archived siblings (e.g. 1 min after an identify_failed record) are confirmed immediately.
        if emby_from_cache and emby_cached_at_iso and adapters["emby"].configured:
            try:
                from app.domain.sa_recovery import _parse_sa_dt
                emby_dt = datetime.fromisoformat(emby_cached_at_iso)
                if emby_dt.tzinfo is None:
                    emby_dt = emby_dt.replace(tzinfo=BEIJING_TZ)
                has_newer_archive = any(
                    rec.get("outcome") in {"archived", "replaced"}
                    and (sa_dt := _parse_sa_dt(str(rec.get("date") or ""))) is not None
                    and sa_dt > emby_dt
                    for rec in sa_hist
                )
                if has_newer_archive:
                    fresh_lib = _safe_call(adapters["emby"].fetch_library_snapshot, None)
                    if isinstance(fresh_lib, dict) and fresh_lib.get("available"):
                        emby_lib = fresh_lib
                        if store is not None:
                            store.set_cache("snapshot:emby", fresh_lib, updated_iso=_now_iso())
                        total = len(fresh_lib.get("movies") or {}) + len(fresh_lib.get("series") or {})
                        emby_h = {
                            **emby_h,
                            "status": "ok",
                            "message": f"在线 · {total} 部影视",
                            "count": total,
                        }
            except Exception:
                pass

        services = [mp_h, tr_h, qb_h, sa_h, emby_h, tmdb_h]
        return {
            "generatedAt": _now_iso(),
            "services": services,
            "serviceMap": {s["service"]: s for s in services},
            "mpSubs": mp_subs,
            "mpHistory": mp_hist,
            "torraSubs": tr_subs,
            "torraSecupload": tr_sec,
            "qbTasks": qb_tasks,
            "saHistory": sa_hist,
            "emby": emby_lib,
        }


class SnapshotCoordinator:
    """SQLite-backed stale-while-revalidate snapshot cache + single-flight background refresher.

    Guarantees homepage/subscriptions/media pages render in milliseconds from SQLite instead of
    blocking 15-45s on live upstream fetches every refresh.
    """

    def __init__(self, store: FluxaStore, *, fresh_ttl: float = SNAPSHOT_FRESH_TTL_SECONDS):
        self.store = store
        self.fresh_ttl = fresh_ttl
        self._refresh_lock = threading.Lock()
        self._refreshing = False
        self._poller_started = False

    @property
    def is_refreshing(self) -> bool:
        return self._refreshing

    def start_background_poller(self, interval_seconds: int = 90) -> None:
        with self._refresh_lock:
            if self._poller_started:
                return
            self._poller_started = True

        def _loop():
            # Warm cache shortly after boot without slowing container startup
            time.sleep(2)
            while True:
                try:
                    settings = self.store.get_settings()
                    adapters = build_adapters(settings, store=self.store)
                    if any(a.configured for a in adapters.values()):
                        self.refresh_now(force_emby=False)
                except Exception:
                    pass
                time.sleep(max(30, int(interval_seconds)))

        threading.Thread(target=_loop, name="fluxa-snapshot-refresher", daemon=True).start()

    def refresh_now(self, *, force_emby: bool = False) -> dict[str, Any]:
        with self._refresh_lock:
            self._refreshing = True
            try:
                settings = self.store.get_settings()
                adapters = build_adapters(settings, store=self.store)
                snap = collect_upstream_snapshot(adapters, store=self.store, force_emby=force_emby)
                self.store.set_cache("snapshot:upstream", snap, updated_iso=snap["generatedAt"])
                return snap
            finally:
                self._refreshing = False

    def trigger_background_refresh(self, *, force_emby: bool = False) -> bool:
        if self._refreshing:
            return False

        def _bg():
            try:
                self.refresh_now(force_emby=force_emby)
            except Exception:
                pass

        threading.Thread(target=_bg, name="fluxa-snapshot-bg", daemon=True).start()
        return True

    def get_snapshot(
        self,
        *,
        force_refresh: bool = False,
        force_emby: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return `(snapshot, cache_meta)` using SQLite cache + stale-while-revalidate."""
        if force_refresh:
            snap = self.refresh_now(force_emby=force_emby)
            return snap, {
                "fromCache": False,
                "stale": False,
                "refreshing": False,
                "cacheAgeSeconds": 0.0,
                "updatedAt": snap["generatedAt"],
            }

        cached = self.store.get_cache("snapshot:upstream")
        if cached and isinstance(cached.get("payload"), dict):
            age = float(cached["ageSeconds"])
            stale = age > self.fresh_ttl
            refreshing = self._refreshing
            if stale and not refreshing:
                refreshing = self.trigger_background_refresh(force_emby=False)
            return cached["payload"], {
                "fromCache": True,
                "stale": stale,
                "refreshing": refreshing,
                "cacheAgeSeconds": age,
                "updatedAt": cached["updatedIso"],
            }

        # Cold start (empty cache): if nothing configured, return immediately; otherwise warm once and persist
        snap = self.refresh_now(force_emby=False)
        return snap, {
            "fromCache": False,
            "stale": False,
            "refreshing": False,
            "cacheAgeSeconds": 0.0,
            "updatedAt": snap["generatedAt"],
        }

    def invalidate(self) -> None:
        self.store.clear_cache_prefix("snapshot:")


def _safe_call(fn, fallback):
    try:
        return fn()
    except Exception:
        return fallback


def merge_subscriptions(snapshot: dict[str, Any], prefs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate MoviePilot Neo + Torra subscriptions by mediaKey, surfacing duplicates clearly."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in list(snapshot.get("mpSubs") or []) + list(snapshot.get("torraSubs") or []):
        mkey = row.get("mediaKey") or f"title:{compact_title(row.get('title'))}"
        if not mkey:
            continue
        pref = prefs.get(mkey, {})
        entry = grouped.get(mkey)
        if not entry:
            entry = {
                "mediaKey": row.get("mediaKey") or "",
                "groupKey": mkey,
                "mediaType": row.get("mediaType") or "movie",
                "tmdbId": row.get("tmdbId") or "",
                "title": row.get("title") or "未命名作品",
                "year": row.get("year") or "",
                "posterUrl": row.get("posterUrl") or "",
                "intent": pref.get("intent") or "follow",
                "notes": pref.get("notes") or "",
                "sources": [],
                "seasons": [],
                "duplicateProviders": False,
                "ruleSummary": row.get("ruleSummary") or "",
                "originReason": row.get("originReason") or "",
                "updatedAt": row.get("updatedAt") or "",
            }
            grouped[mkey] = entry
        if not entry["posterUrl"] and row.get("posterUrl"):
            entry["posterUrl"] = row["posterUrl"]
        if row.get("seasonNumber") and row["seasonNumber"] not in entry["seasons"]:
            entry["seasons"].append(row["seasonNumber"])
            entry["seasons"].sort()
        entry["sources"].append({
            "source": row["source"],
            "sourceLabel": row["sourceLabel"],
            "subscriptionId": row["subscriptionId"],
            "seasonNumber": row.get("seasonNumber"),
            "ruleSummary": row.get("ruleSummary") or "",
            "originReason": row.get("originReason") or "",
            "webUrl": row.get("webUrl") or "",
        })

    items = list(grouped.values())
    for item in items:
        provider_set = {s["source"] for s in item["sources"]}
        item["duplicateProviders"] = len(provider_set) > 1
    items.sort(key=lambda x: (x["duplicateProviders"], x["updatedAt"], x["title"]), reverse=True)
    return items


_TITLE_CUT_RE = re.compile(
    r"(?i)(?:"
    r"(?<![A-Za-z0-9])S\d{1,2}(?:E\d{1,4})?(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])EP\d{1,4}(?![A-Za-z0-9])|"
    r"第\s*\d{1,4}\s*[集话話季]|"
    r"(?<![A-Za-z0-9])(?:2160p|1080p|1080i|720p|480p|4k|web[- .]?dl|web[- .]?rip|bdrip|blu[- .]?ray|remux|hdtv)(?![A-Za-z0-9])|"
    r"\{tmdb(?:id)?=\d+\}"
    r")"
)
_YEAR_TOKEN_RE = re.compile(r"(?<!\d)(19[5-9]\d|20[0-3]\d)(?!\d)")
_TMDB_TAG_RE = re.compile(r"(?i)\{?tmdb(?:[-_ ]?id)?[=:](\d{1,10})\}?")
_LEADING_GROUP_RE = re.compile(r"^\s*(?:[\[【][^\]】]{1,28}[\]】]\s*)+")
_TRAILING_GROUP_RE = re.compile(r"[-@][A-Za-z0-9][A-Za-z0-9_.]{1,24}\s*$")


def _extract_candidate_stem_and_year(text: str) -> tuple[str, str, str]:
    raw = re.sub(r"\.(?:mkv|mp4|ts|iso|strm|torrent)$", "", str(text or "").strip(), flags=re.I)
    tmdb_m = _TMDB_TAG_RE.search(raw)
    inline_tmdb = tmdb_m.group(1) if tmdb_m else ""
    year_m = _YEAR_TOKEN_RE.search(raw)
    cand_year = year_m.group(1) if year_m else ""

    stem = _LEADING_GROUP_RE.sub("", raw)
    stem = _TRAILING_GROUP_RE.sub("", stem)
    cut_m = _TITLE_CUT_RE.search(stem)
    if cut_m and cut_m.start() >= 2:
        stem = stem[: cut_m.start()]
    if year_m:
        ym = _YEAR_TOKEN_RE.search(stem)
        if ym and ym.start() >= 2:
            stem = stem[: ym.start()]
    stem = re.sub(r"[\.\-_/\[\]\(\)【】]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem, cand_year, inline_tmdb


def _title_tokens(text: str) -> tuple[list[str], str]:
    cleaned = re.sub(r"[\.\-_/\[\]\(\)【】:：!！?？,，]+", " ", str(text or "").lower())
    latin_and_nums = re.findall(r"[a-z0-9]+", cleaned)
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]+", cleaned))
    return latin_and_nums, cjk


def _title_matches(
    target_title: str,
    candidate_text: str,
    extra_titles: list[str] | None = None,
    *,
    target_year: str = "",
    target_tmdb_id: str = "",
    media_type: str = "",
) -> bool:
    """Strict work-identity title matcher that NEVER confuses 'Alien' with 'Alien 2' / 'Aliens' / 'Alien Romulus'."""
    stem, cand_year, inline_tmdb = _extract_candidate_stem_and_year(candidate_text)
    if target_tmdb_id and inline_tmdb and inline_tmdb != str(target_tmdb_id):
        return False
    if media_type == "movie" and target_year and cand_year:
        try:
            if abs(int(target_year) - int(cand_year)) > 1:
                return False
        except ValueError:
            pass

    cand_latin, cand_cjk = _title_tokens(stem)
    if not cand_latin and not cand_cjk:
        return False

    for t in [target_title, *(extra_titles or [])]:
        t_clean = str(t or "").strip()
        if not t_clean:
            continue
        t_stem, _, _ = _extract_candidate_stem_and_year(t_clean)
        t_latin, t_cjk = _title_tokens(t_stem or t_clean)
        if not t_latin and not t_cjk:
            continue

        # Exact full token match (both CJK & Latin/numbers must agree — 'alien' != 'alien 2'!)
        if t_latin and cand_latin and t_cjk and cand_cjk:
            if t_latin == cand_latin and t_cjk == cand_cjk:
                return True
            continue
        if t_cjk and cand_cjk:
            # Ensure numeric sequel suffix isn't silently dropped (e.g. '异形' must NOT match '异形 2')
            t_nums = [x for x in t_latin if x.isdigit()]
            c_nums = [x for x in cand_latin if x.isdigit()]
            if t_cjk == cand_cjk and t_nums == c_nums:
                return True
        if t_latin and cand_latin:
            # Require exact Latin/numeric token equality so 'Alien' NEVER matches 'Alien 2' or 'Alien Romulus'
            if t_latin == cand_latin:
                return True
    return False


def partition_actionable_issues(
    snapshot: dict[str, Any],
    prefs: dict[str, dict[str, Any]],
    *,
    store: FluxaStore | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return `(active_issues, ignored_issues)`.

    - Never false-alarms on SA rule_protected / version_rejected skips.
    - Auto-classifies redundant SA identify failures as 「已有版本，已忽略」 ONLY when:
      1) same work + same season + same episode is reliably verified,
      2) current Emby inventory confirms the episode is currently in library (NOT old history alone or failed Emby query),
      3) no collection upgrade / exclusive cut / effects-sub / lossless-audio exception applies,
      4) the user has not explicitly restored this issue back to the inbox.
    - Preserves original failure records in `ignored_issues` so users can inspect or restore anytime.
    """
    muted_keys: set[str] = set()
    for p in prefs.values():
        for mk in p.get("mutedIssueKeys") or []:
            muted_keys.add(str(mk))

    restored_keys: set[str] = set()
    if store is not None:
        r_cache = store.get_cache("issues:restored")
        if r_cache and isinstance(r_cache.get("payload"), list):
            restored_keys = {str(x) for x in r_cache["payload"]}

    issues: list[dict[str, Any]] = []
    ignored_issues: list[dict[str, Any]] = []

    def _emit(item: dict[str, Any], *, auto_ignored: bool = False, ignored_label: str = "", ignored_reason: str = "") -> None:
        ikey = item["issueKey"]
        if ikey in muted_keys:
            ignored_issues.append({
                **item,
                "ignoredKind": "manual",
                "ignoredLabel": "已手动忽略",
                "ignoredReason": "已由你手动忽略（原记录保留，可随时恢复到待办）",
                "canRestore": True,
            })
            return
        if auto_ignored and ikey not in restored_keys:
            ignored_issues.append({
                **item,
                "severity": "warn",
                "stageLabel": ignored_label or "已有版本，已忽略",
                "errorText": ignored_reason or item["errorText"],
                "ignoredKind": "auto_existing_version",
                "ignoredLabel": ignored_label or "已有版本，已忽略",
                "ignoredReason": ignored_reason or item["errorText"],
                "canRestore": True,
            })
            return
        issues.append(item)

    subs = merge_subscriptions(snapshot, prefs)
    sub_by_hash = {h["downloadHash"]: h for h in (snapshot.get("mpHistory") or []) if h.get("downloadHash")}

    # 1. Service connectivity issues (configured but unreachable)
    for svc in snapshot.get("services") or []:
        if svc.get("configured") and not svc.get("connected"):
            issue_key = f"service:{svc['service']}"
            _emit({
                "issueKey": issue_key,
                "severity": "error",
                "stage": svc["service"],
                "stageLabel": svc["name"],
                "title": f"{svc['name']} 连接失败",
                "mediaKey": "",
                "episodeLabel": "",
                "errorText": svc.get("message") or "服务无法连接",
                "retryAction": None,
                "appUrl": svc.get("webUrl") or "",
                "appLabel": f"打开 {svc['name']}",
            })

    # 2. qBittorrent stalled / missing files / error tasks
    for task in snapshot.get("qbTasks") or []:
        if task["status"] not in {"missing_files", "error", "stalled"}:
            continue
        issue_key = f"qb:{task['hash']}"
        hist = sub_by_hash.get(task["hash"])
        mkey = hist["mediaKey"] if hist else ""
        if not mkey:
            matched_sub = next((s for s in subs if _title_matches(s["title"], task["name"])), None)
            if matched_sub:
                mkey = matched_sub["mediaKey"]
        s_num, e_num = extract_season_episode(task["name"])
        ep_label = f"S{s_num:02d}E{e_num:02d}" if s_num and e_num else (f"S{s_num:02d}" if s_num else "")
        err_msg = (
            f"qB 缺少本地文件（进度 {task['progressPercent']}%）"
            if task["status"] == "missing_files"
            else f"qB 下载无速度（进度 {task['progressPercent']}%）"
            if task["status"] == "stalled"
            else f"qB 任务报错：{task['rawState']}"
        )
        _emit({
            "issueKey": issue_key,
            "severity": "error" if task["status"] in {"missing_files", "error"} else "warn",
            "stage": "qbittorrent",
            "stageLabel": "qB 下载",
            "title": (hist["title"] if hist and hist.get("title") else task["name"]),
            "subtitle": task["name"],
            "mediaKey": mkey,
            "episodeLabel": ep_label,
            "errorText": err_msg,
            "retryAction": (
                {
                    "actionType": "qb_resume",
                    "label": "恢复此任务",
                    "payload": {"hash": task["hash"], "name": task["name"]},
                }
                if task["status"] == "stalled"
                else None
            ),
            "appUrl": task.get("webUrl") or "",
            "appLabel": "打开 qB",
        })

    # 3. Torra 115 upload/secupload failed files
    sec = snapshot.get("torraSecupload") or {}
    for fail in sec.get("failedFiles") or []:
        fname = fail.get("fileName") or fail.get("path") or "未知文件"
        issue_key = f"torra115:{fail.get('runId')}:{fname}"
        matched_sub = next((s for s in subs if _title_matches(s["title"], fname)), None)
        s_num, e_num = extract_season_episode(fname)
        ep_label = f"S{s_num:02d}E{e_num:02d}" if s_num and e_num else ""
        _emit({
            "issueKey": issue_key,
            "severity": "error",
            "stage": "cloud115",
            "stageLabel": "115 上传",
            "title": matched_sub["title"] if matched_sub else fname,
            "subtitle": fname,
            "mediaKey": matched_sub["mediaKey"] if matched_sub else "",
            "episodeLabel": ep_label,
            "errorText": fail.get("error") or "115 秒传/上传失败",
            "retryAction": (
                {
                    "actionType": "torra_retry_upload",
                    "label": "重试此上传任务",
                    "payload": {
                        "taskKey": fail.get("taskKey") or "",
                        "targetItemId": fail.get("targetItemId") or "",
                        "fileName": fname,
                    },
                }
                if fail.get("taskKey")
                else None
            ),
            "appUrl": sec.get("webUrl") or "",
            "appLabel": "打开 Torra",
        })

    # 4. Symedia (SA) real failures or identify failures ONLY (never rule_protected / version_rejected!)
    sa_history = snapshot.get("saHistory") or []
    emby = snapshot.get("emby") or {}
    succeeded_files = {
        (rec.get("fileName") or "").casefold()
        for rec in sa_history
        if rec["outcome"] in {"archived", "replaced"} and rec.get("fileName")
    }
    seen_failed_files: set[str] = set()
    for rec in sa_history:
        fname_key = (rec.get("fileName") or "").casefold()
        # Post-archive sequel/year sanity audit on "archived" movie records (Hostel Part II -> 1690)
        if rec["outcome"] in {"archived", "replaced"} and rec.get("mediaType") == "movie" and rec.get("fileName"):
            from app.domain.sa_recovery import check_sequel_identity_conflict, parse_media_filename
            p_src = parse_media_filename(rec["fileName"])
            dest_year_m = re.search(r"\b((?:19|20)\d{2})\b", rec.get("dest") or "")
            conflict = check_sequel_identity_conflict(
                source_name=p_src["arcTitle"],
                source_year=p_src["year"],
                target_title=rec.get("title") or "",
                target_year=dest_year_m.group(1) if dest_year_m else "",
            )
            if conflict["hasConflict"]:
                issue_key = f"sa_conflict:{rec['id'] or rec['fileName']}"
                _emit({
                    "issueKey": issue_key,
                    "severity": "warn",
                    "stage": "symedia",
                    "stageLabel": "SA 疑似串片",
                    "title": rec["title"],
                    "subtitle": rec["fileName"],
                    "mediaKey": rec.get("mediaKey") or "",
                    "episodeLabel": "",
                    "errorText": f"SA 显示归档成功但疑似认错作品：{conflict['warningText']}",
                    "retryAction": None,
                    "saRecord": rec,
                    "appUrl": rec.get("webUrl") or "",
                    "appLabel": "打开 SA 核对",
                })
            continue

        if rec["outcome"] not in {"failed", "identify_failed", "auth_or_connection_error"}:
            continue
        if fname_key and (fname_key in succeeded_files or fname_key in seen_failed_files):
            continue
        if fname_key:
            seen_failed_files.add(fname_key)
        issue_key = f"sa:{rec['id'] or rec['fileName']}"

        issue_severity = "error"
        issue_stage_label = "SA 识别失败" if rec["outcome"] == "identify_failed" else "SA 故障"
        issue_error_text = f"{rec['outcomeLabel']}：{rec['reasonText']}"
        issue_media_key = rec.get("mediaKey") or ""
        ep_label = f"{rec['seasons']}{rec['episodes']}".strip()
        should_auto_ignore = False
        auto_ignore_reason = ""

        if rec["outcome"] == "identify_failed":
            rec_id = str(rec.get("id") or "")
            resolved_mt = ""
            resolved_tmdb = ""
            resolved_ts: int | None = None
            resolved_te: int | None = None

            if store is not None and rec_id:
                latest_job = store.get_latest_sa_recovery_for_record(rec_id)
                if latest_job and latest_job.get("status") == "resolved":
                    continue
                if latest_job and latest_job.get("status") == "skipped_existing" and emby.get("available"):
                    resolved_mt = latest_job.get("mediaType") or "tv"
                    resolved_tmdb = str(latest_job.get("tmdbId") or "")
                    resolved_ts = latest_job.get("targetSeason")
                    resolved_te = latest_job.get("targetEpisode")

                if not resolved_tmdb:
                    covered = store.get_cache(f"sa_covered:{rec_id}")
                    if covered and isinstance(covered.get("payload"), dict):
                        cp = covered["payload"]
                        if cp.get("tmdbId"):
                            resolved_mt = str(cp.get("mediaType") or "tv")
                            resolved_tmdb = str(cp.get("tmdbId") or "")
                            resolved_ts = cp.get("targetSeason")
                            resolved_te = cp.get("targetEpisode")

            if rec.get("fileName"):
                from app.domain.sa_recovery import (
                    detect_existing_version_for_target,
                    find_corroborated_sa_sibling_archive,
                    parse_media_filename,
                )
                p_fail = parse_media_filename(rec["fileName"])
                if not resolved_tmdb:
                    mapping = (
                        store.find_sa_mapping(p_fail["arcKey"], p_fail["sourceSeason"])
                        if store is not None and p_fail.get("arcKey") and p_fail.get("sourceSeason")
                        else None
                    )
                    if mapping and p_fail.get("sourceEpisode"):
                        resolved_mt = mapping["mediaType"]
                        resolved_tmdb = mapping["tmdbId"]
                        resolved_ts = int(mapping["targetSeason"])
                        resolved_te = int(p_fail["sourceEpisode"]) + int(mapping.get("episodeOffset") or 0)
                    else:
                        sibling = find_corroborated_sa_sibling_archive(
                            record=rec,
                            parsed=p_fail,
                            sa_history=sa_history,
                        )
                        if sibling:
                            resolved_mt = sibling["mediaType"]
                            resolved_tmdb = sibling["tmdbId"]
                            resolved_ts = sibling["targetSeason"]
                            resolved_te = sibling["targetEpisode"]
                        elif str(rec.get("tmdbId") or "").isdigit():
                            resolved_mt = rec.get("mediaType") or p_fail.get("inferredMediaType") or "tv"
                            resolved_tmdb = str(rec.get("tmdbId"))
                            resolved_ts = p_fail.get("sourceSeason")
                            resolved_te = p_fail.get("sourceEpisode")

                if resolved_tmdb:
                    target_mkey = f"{resolved_mt or 'tv'}:{resolved_tmdb}"
                    target_intent = (prefs.get(target_mkey) or {}).get("intent") or "follow"
                    ev = detect_existing_version_for_target(
                        media_type=resolved_mt or "tv",
                        tmdb_id=resolved_tmdb,
                        target_season=resolved_ts,
                        target_episode=resolved_te,
                        failed_file_name=rec["fileName"],
                        emby=emby,
                        sa_history=sa_history,
                        media_intent=target_intent,
                    )
                    issue_media_key = target_mkey
                    if resolved_ts and resolved_te and not ep_label:
                        ep_label = f"S{resolved_ts:02d}E{resolved_te:02d}"

                    if ev.get("autoIgnoreEligible"):
                        should_auto_ignore = True
                        auto_ignore_reason = ev.get("autoIgnoreSummary") or ev.get("summary") or ""
                    elif ev.get("exists"):
                        # Either Emby could not confirm current library presence, OR an upgrade/cut/sub/audio exception matched!
                        issue_severity = "warn"
                        issue_stage_label = ev.get("badgeLabel") or "同集已有入库（未自动忽略）"
                        issue_error_text = ev.get("summary") or issue_error_text

        _emit(
            {
                "issueKey": issue_key,
                "severity": issue_severity,
                "stage": "symedia",
                "stageLabel": issue_stage_label,
                "title": rec["title"],
                "subtitle": rec["fileName"],
                "mediaKey": issue_media_key,
                "episodeLabel": ep_label,
                "errorText": issue_error_text,
                "retryAction": None,
                "saRecord": rec if rec["outcome"] == "identify_failed" else None,
                "appUrl": rec.get("webUrl") or "",
                "appLabel": "打开 SA 处理",
            },
            auto_ignored=should_auto_ignore,
            ignored_label="已有版本，已忽略",
            ignored_reason=auto_ignore_reason,
        )

    return issues, ignored_issues


def build_actionable_issues(
    snapshot: dict[str, Any],
    prefs: dict[str, dict[str, Any]],
    *,
    store: FluxaStore | None = None,
) -> list[dict[str, Any]]:
    """Return active actionable issues (auto-ignored redundant versions are placed in ignored_issues)."""
    active, _ = partition_actionable_issues(snapshot, prefs, store=store)
    return active


def build_media_pipeline(
    *,
    media_type: str,
    tmdb_id: str,
    snapshot: dict[str, Any],
    adapters: dict[str, Any],
    store: FluxaStore,
) -> dict[str, Any]:
    """Build the 5-step single-media pipeline view directly answering Pain Point 1."""
    media_key = f"{media_type}:{tmdb_id}"
    pref = store.get_preference(media_key)
    decision_logs = store.list_decision_logs(media_key=media_key, limit=20)
    tmdb_detail = adapters["tmdb"].get_details(media_type, tmdb_id)

    all_subs = merge_subscriptions(snapshot, {media_key: pref})
    sub_entry = next((s for s in all_subs if s.get("mediaKey") == media_key), None)

    title = (
        (tmdb_detail or {}).get("title")
        or (sub_entry or {}).get("title")
        or f"{media_type.upper()} #{tmdb_id}"
    )
    original_title = (tmdb_detail or {}).get("originalTitle") or ""
    target_year = str((tmdb_detail or {}).get("year") or (sub_entry or {}).get("year") or "")
    alt_titles = list((tmdb_detail or {}).get("alternativeTitles") or [])
    extra_titles = [t for t in [original_title, *alt_titles[:8]] if t]

    def _matches_this_work(text: str, record_media_key: str = "") -> bool:
        """If record carries an explicit DIFFERENT mediaKey/TMDB ID, NEVER fall back to title matching!"""
        if record_media_key:
            return record_media_key == media_key
        return _title_matches(
            title,
            text,
            extra_titles,
            target_year=target_year,
            target_tmdb_id=tmdb_id,
            media_type=media_type,
        )

    # Step 1: Subscription & Torrent Selection (MP Neo + Torra)
    all_mp_hist = snapshot.get("mpHistory") or []
    mp_hist_matches = [
        h for h in all_mp_hist
        if _matches_this_work(h.get("title", "") or h.get("torrentName", ""), h.get("mediaKey") or "")
    ]
    mp_hashes = {h["downloadHash"] for h in mp_hist_matches if h.get("downloadHash")}
    other_mp_hashes = {
        h["downloadHash"]
        for h in all_mp_hist
        if h.get("downloadHash") and h.get("mediaKey") and h.get("mediaKey") != media_key
    }

    if sub_entry:
        providers = " + ".join(sorted({s["sourceLabel"] for s in sub_entry["sources"]}))
        sub_status = "warn" if sub_entry["duplicateProviders"] else "ok"
        sub_label = f"{providers}（重复订阅提醒）" if sub_entry["duplicateProviders"] else f"已由 {providers} 订阅"
        sub_detail = sub_entry["ruleSummary"]
    else:
        mp_online = snapshot["serviceMap"].get("moviepilot", {}).get("connected")
        torra_online = snapshot["serviceMap"].get("torra", {}).get("connected")
        if not mp_online and not torra_online:
            sub_status = "unconfirmed"
            sub_label = "未确认"
            sub_detail = "MoviePilot Neo 与 Torra 均未连接，无法确认订阅状态"
        else:
            sub_status = "idle"
            sub_label = "尚未订阅"
            sub_detail = "在 MP Neo 与 Torra 中未找到此作品订阅"

    step_subscription = {
        "step": "subscription",
        "label": "订阅与选种",
        "status": sub_status,
        "statusLabel": sub_label,
        "summary": sub_detail,
        "intent": pref.get("intent") or "follow",
        "sources": sub_entry["sources"] if sub_entry else [],
        "duplicateProviders": bool(sub_entry and sub_entry["duplicateProviders"]),
        "recentSelections": mp_hist_matches[:5],
        "decisionLogs": decision_logs[:5],
    }

    # Step 2: qBittorrent Download & Seeding
    qb_svc = snapshot["serviceMap"].get("qbittorrent") or {}
    matched_qb = [
        t for t in (snapshot.get("qbTasks") or [])
        if (t["hash"] and t["hash"] in mp_hashes)
        or (t["hash"] not in other_mp_hashes and _matches_this_work(t["name"]))
    ]
    if not qb_svc.get("connected"):
        qb_status, qb_label, qb_summary = "unconfirmed", "未确认", qb_svc.get("message") or "qBittorrent 未连接"
    elif not matched_qb:
        qb_status, qb_label, qb_summary = "idle", "暂无 qB 任务", "当前 qBittorrent 中未找到匹配种子"
    else:
        if any(t["status"] in {"missing_files", "error"} for t in matched_qb):
            qb_status, qb_label = "error", "下载异常（缺文件/出错）"
        elif any(t["status"] == "stalled" for t in matched_qb):
            qb_status, qb_label = "warn", "下载无速度"
        elif any(t["status"] in {"downloading", "checking", "queued"} for t in matched_qb):
            active = next(t for t in matched_qb if t["status"] in {"downloading", "checking", "queued"})
            qb_status, qb_label = "active", f"下载中 · {active['progressPercent']}%"
        elif any(t["status"] == "paused" for t in matched_qb):
            qb_status, qb_label = "warn", "已暂停"
        else:
            qb_status, qb_label = "ok", f"已完成 / 做种中（{len(matched_qb)} 个）"
        qb_summary = f"匹配到 {len(matched_qb)} 个 qB 任务"

    step_qb = {
        "step": "qbittorrent",
        "label": "qB 下载与保种",
        "status": qb_status,
        "statusLabel": qb_label,
        "summary": qb_summary,
        "tasks": matched_qb[:15],
        "webUrl": adapters["qbittorrent"].app_url(),
    }

    # Step 3: 115 Upload / Secupload (Torra)
    sec = snapshot.get("torraSecupload") or {}
    matched_sec_fail = [f for f in (sec.get("failedFiles") or []) if _matches_this_work(f.get("fileName", ""))]
    matched_sec_ok = [f for f in (sec.get("successFiles") or []) if _matches_this_work(f.get("fileName", ""))]
    active_runs = sec.get("activeRuns") or []
    matched_active_runs = [
        run for run in active_runs
        if _matches_this_work(f"{run.get('message', '')} {run.get('taskKey', '')} {run.get('targetItemId', '')}")
    ]
    if not sec.get("available"):
        c115_status, c115_label, c115_summary = "unconfirmed", "未确认", sec.get("error") or "未获取到 Torra 115 上传回执"
    elif matched_sec_fail:
        c115_status, c115_label = "error", f"上传失败（{len(matched_sec_fail)} 个文件）"
        c115_summary = matched_sec_fail[0].get("error") or "115 秒传/上传失败"
    elif matched_sec_ok:
        c115_status, c115_label = "ok", f"已确认上传 115（{len(matched_sec_ok)} 个文件）"
        c115_summary = f"最近上传：{matched_sec_ok[0]['fileName']}"
    elif matched_active_runs:
        c115_status, c115_label = "active", "115 上传任务运行中"
        c115_summary = "Torra 正在执行与本作品关联的 115 上传任务（逐文件结果待回执）"
    elif active_runs:
        # Other works uploading MUST NOT make this unrelated work show "uploading"!
        c115_status, c115_label = "unconfirmed", "未确认"
        c115_summary = f"Torra 近期记录中未找到本作品的逐文件 115 上传证据（当前队列另有 {len(active_runs)} 个其他/未归属上传任务运行中）"
    else:
        # Strict honesty: never infer 115 upload success from qB completion!
        c115_status, c115_label = "unconfirmed", "未确认"
        c115_summary = "Torra 近期记录中未找到该作品的逐文件 115 上传证据"

    step_115 = {
        "step": "cloud115",
        "label": "115 上传",
        "status": c115_status,
        "statusLabel": c115_label,
        "summary": c115_summary,
        "failedFiles": matched_sec_fail[:10],
        "successFiles": matched_sec_ok[:10],
        "webUrl": sec.get("webUrl") or adapters["torra"].app_url(),
    }

    # Step 4: Symedia (SA) Archive & STRM
    sa_svc = snapshot["serviceMap"].get("symedia") or {}
    matched_sa = [
        r for r in (snapshot.get("saHistory") or [])
        if _matches_this_work(f"{r.get('title', '')} {r.get('fileName', '')}", r.get("mediaKey") or "")
    ]
    if not sa_svc.get("connected"):
        sa_status, sa_label, sa_summary = "unconfirmed", "未确认", sa_svc.get("message") or "Symedia (SA) 未连接"
    elif not matched_sa:
        sa_status, sa_label, sa_summary = "unconfirmed", "未确认", "SA 近期整理历史中暂无匹配记录"
    else:
        latest_sa = matched_sa[0]
        if latest_sa["outcome"] in {"archived", "replaced"}:
            sa_status, sa_label = "ok", latest_sa["outcomeLabel"]
        elif latest_sa["outcome"] == "rule_protected":
            sa_status, sa_label = "protected", latest_sa["outcomeLabel"]
        elif latest_sa["outcome"] in {"identify_failed", "failed"}:
            sa_status, sa_label = "error", latest_sa["outcomeLabel"]
        else:
            sa_status, sa_label = "unconfirmed", "未确认"
        sa_summary = latest_sa["reasonText"]

    step_sa = {
        "step": "symedia",
        "label": "SA 整理 / STRM",
        "status": sa_status,
        "statusLabel": sa_label,
        "summary": sa_summary,
        "records": matched_sa[:15],
        "webUrl": adapters["symedia"].app_url("history"),
    }

    # Step 5: Emby Library Entry (strictly says "Emby 已有条目", never claims verified playback)
    emby = snapshot.get("emby") or {}
    emby_episodes = set(tuple(pair) for pair in (emby.get("episodes") or {}).get(tmdb_id, []))
    if not emby.get("available"):
        emby_status, emby_label, emby_summary, emby_url = (
            "unconfirmed", "未确认", emby.get("error") or "Emby 未连接", adapters["emby"].app_url()
        )
    elif media_type == "movie":
        m_item = (emby.get("movies") or {}).get(tmdb_id)
        if m_item:
            emby_status = "ok"
            emby_label = "Emby 已有条目" + ("（STRM）" if m_item.get("isStrm") else "")
            emby_summary = "媒体库中已存在对应电影条目（实际播放以 Emby 客户端为准）"
            emby_url = m_item.get("webUrl") or adapters["emby"].app_url()
        elif emby.get("truncated"):
            emby_status, emby_label = "unconfirmed", "未确认（库存快照截断）"
            emby_summary = emby.get("truncationWarning") or "Emby 媒体库过大且快照未完整覆盖，不能确认未入库"
            emby_url = adapters["emby"].app_url()
        else:
            emby_status, emby_label = "idle", "尚未进入 Emby"
            emby_summary = "Emby 媒体库中暂未查到该电影"
            emby_url = adapters["emby"].app_url()
    else:
        s_item = (emby.get("series") or {}).get(tmdb_id)
        if s_item or emby_episodes:
            emby_status = "ok"
            emby_label = f"Emby 已有 {len(emby_episodes)} 集" if emby_episodes else "Emby 已有剧集条目"
            emby_summary = (
                "已核对 Emby 分集条目（注意：库存快照存在截断，未列出的分集请以 Emby 为准）"
                if emby.get("truncated")
                else "已核对 Emby 分集条目（实际播放以 Emby 客户端为准）"
            )
            emby_url = (s_item or {}).get("webUrl") or adapters["emby"].app_url()
        elif emby.get("truncated"):
            emby_status, emby_label = "unconfirmed", "未确认（库存快照截断）"
            emby_summary = emby.get("truncationWarning") or "Emby 媒体库过大且快照未完整覆盖，不能确认未入库"
            emby_url = adapters["emby"].app_url()
        else:
            emby_status, emby_label = "idle", "尚未进入 Emby"
            emby_summary = "Emby 媒体库中暂未查到该剧集"
            emby_url = adapters["emby"].app_url()

    step_emby = {
        "step": "emby",
        "label": "Emby 条目",
        "status": emby_status,
        "statusLabel": emby_label,
        "summary": emby_summary,
        "episodeCount": len(emby_episodes),
        "webUrl": emby_url,
    }

    # Optional episode breakdown for TV shows (latest/subscribed season)
    episodes_view: list[dict[str, Any]] = []
    if media_type == "tv" and adapters["tmdb"].configured:
        target_seasons = (sub_entry or {}).get("seasons") or []
        if not target_seasons and tmdb_detail and tmdb_detail.get("seasons"):
            target_seasons = [tmdb_detail["seasons"][-1]["seasonNumber"]]
        for s_num in target_seasons[-2:]:
            for ep in adapters["tmdb"].get_season_episodes(tmdb_id, int(s_num)):
                ep_num = ep["episodeNumber"]
                in_emby = (int(s_num), int(ep_num)) in emby_episodes
                ep_tag = f"S{s_num:02d}E{ep_num:02d}"
                ep_qb = next((t for t in matched_qb if extract_season_episode(t["name"]) == (int(s_num), int(ep_num))), None)
                ep_sa = next((r for r in matched_sa if ep_tag.lower() in f"{r.get('seasons','')}{r.get('episodes','')} {r.get('fileName','')}".lower()), None)
                if in_emby:
                    ep_state, ep_state_label = "in_emby", "Emby 已有"
                elif ep_qb and ep_qb["status"] in {"missing_files", "error", "stalled"}:
                    ep_state, ep_state_label = "issue", ep_qb["statusLabel"]
                elif ep_sa and ep_sa["outcome"] in {"failed", "identify_failed"}:
                    ep_state, ep_state_label = "issue", ep_sa["outcomeLabel"]
                elif ep_qb:
                    ep_state, ep_state_label = "processing", f"qB {ep_qb['progressPercent']}%"
                elif ep_sa and ep_sa["outcome"] == "rule_protected":
                    ep_state, ep_state_label = "protected", "SA 规则跳过"
                else:
                    today_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
                    if ep["airDate"] and ep["airDate"] > today_str:
                        ep_state, ep_state_label = "upcoming", "待播出"
                    else:
                        ep_state, ep_state_label = "unconfirmed", "未确认"
                episodes_view.append({
                    **ep,
                    "state": ep_state,
                    "stateLabel": ep_state_label,
                })

    return {
        "generatedAt": snapshot["generatedAt"],
        "media": {
            "mediaKey": media_key,
            "mediaType": media_type,
            "tmdbId": tmdb_id,
            "title": title,
            "originalTitle": original_title,
            "year": (tmdb_detail or {}).get("year") or (sub_entry or {}).get("year") or "",
            "posterUrl": (tmdb_detail or {}).get("posterUrl") or (sub_entry or {}).get("posterUrl") or "",
            "overview": (tmdb_detail or {}).get("overview") or "",
            "intent": pref.get("intent") or "follow",
            "notes": pref.get("notes") or "",
        },
        "steps": [step_subscription, step_qb, step_115, step_sa, step_emby],
        "episodes": episodes_view,
    }
