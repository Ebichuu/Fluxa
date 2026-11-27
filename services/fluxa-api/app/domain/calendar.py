from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.adapters.emby import is_emby_identity_incomplete
from app.adapters.http_utils import extract_season_episode
from app.domain.pipeline import _title_matches, merge_subscriptions

BEIJING_TZ = timezone(timedelta(hours=8))
DEFAULT_MAX_CALENDAR_SERIES = 80


def build_following_calendar(
    *,
    snapshot: dict[str, Any],
    tmdb_adapter: Any,
    prefs: dict[str, dict[str, Any]],
    start_date: date | None = None,
    days: int = 7,
    max_series: int = DEFAULT_MAX_CALENDAR_SERIES,
) -> dict[str, Any]:
    """Build a crisp following calendar with honest completeness & truncation reporting.

    States: upcoming | processing | in_emby | issue | unconfirmed
    """
    today = datetime.now(BEIJING_TZ).date()
    start = start_date or (today - timedelta(days=2))
    span = max(1, min(42, int(days)))
    end = start + timedelta(days=span - 1)

    subs = merge_subscriptions(snapshot, prefs)
    emby = snapshot.get("emby") or {}
    qb_tasks = snapshot.get("qbTasks") or []
    sa_hist = snapshot.get("saHistory") or []

    days_map: dict[str, list[dict[str, Any]]] = {}
    for offset in range(span):
        d = start + timedelta(days=offset)
        days_map[d.isoformat()] = []

    all_tv_subs = [s for s in subs if s["mediaType"] == "tv" and s["tmdbId"]]
    # Prioritize actively followed series before collection series
    all_tv_subs.sort(key=lambda s: (0 if s.get("intent") == "follow" else 1, s.get("title") or ""))
    cap = max(1, int(max_series))
    tv_subs = all_tv_subs[:cap] if tmdb_adapter.configured else []
    omitted_count = max(0, len(all_tv_subs) - len(tv_subs)) if tmdb_adapter.configured else len(all_tv_subs)

    if tmdb_adapter.configured and tv_subs:
        def _fetch_sub_eps(sub: dict[str, Any]) -> tuple[dict[str, Any], int, list[dict[str, Any]]]:
            seasons = sub.get("seasons") or [1]
            s_num = int(seasons[-1])
            return sub, s_num, tmdb_adapter.get_season_episodes(sub["tmdbId"], s_num)

        with ThreadPoolExecutor(max_workers=6) as pool:
            fetched_rows = list(pool.map(_fetch_sub_eps, tv_subs))

        for sub, s_num, eps in fetched_rows:
            tmdb_id = sub["tmdbId"]
            title = sub["title"]
            emby_eps = set(tuple(p) for p in (emby.get("episodes") or {}).get(tmdb_id, []))

            for ep in eps:
                air_str = ep.get("airDate") or ""
                if air_str not in days_map:
                    continue
                ep_num = ep["episodeNumber"]
                ep_tag = ep["episodeLabel"]
                in_emby = (s_num, int(ep_num)) in emby_eps
                matched_qb = next(
                    (
                        t for t in qb_tasks
                        if _title_matches(title, t["name"], target_tmdb_id=tmdb_id, media_type="tv")
                        and extract_season_episode(t["name"]) == (s_num, int(ep_num))
                    ),
                    None,
                )
                matched_sa = next(
                    (
                        r for r in sa_hist
                        if r.get("mediaKey") == sub["mediaKey"]
                        and ep_tag.lower() in f"{r.get('seasons','')}{r.get('episodes','')} {r.get('fileName','')}".lower()
                    ),
                    None,
                )
                if in_emby:
                    state, label = "in_emby", "Emby 已有"
                elif (matched_qb and matched_qb["status"] in {"missing_files", "error", "stalled"}) or (
                    matched_sa and matched_sa["outcome"] in {"failed", "identify_failed"}
                ):
                    state, label = "issue", "卡住异常"
                elif matched_qb:
                    state, label = "processing", f"下载 {matched_qb['progressPercent']}%"
                elif air_str > today.isoformat():
                    state, label = "upcoming", "待播出"
                else:
                    state, label = "unconfirmed", "未确认"

                days_map[air_str].append({
                    "date": air_str,
                    "mediaKey": sub["mediaKey"],
                    "mediaType": "tv",
                    "tmdbId": tmdb_id,
                    "title": title,
                    "posterUrl": sub.get("posterUrl") or "",
                    "episodeLabel": ep_tag,
                    "episodeName": ep.get("name") or "",
                    "state": state,
                    "stateLabel": label,
                    "intent": sub.get("intent") or "follow",
                })

    ordered_days = [
        {
            "date": day_key,
            "isToday": day_key == today.isoformat(),
            "items": items,
        }
        for day_key, items in sorted(days_map.items())
    ]
    today_items = days_map.get(today.isoformat(), [])
    truncated = bool(tmdb_adapter.configured and omitted_count > 0)
    trunc_warning = (
        f"追更日历当前覆盖前 {len(tv_subs)}/{len(all_tv_subs)} 部订阅剧集，仍有 {omitted_count} 部未拉取排期（“今日无更新”不代表全部剧集均无更新）"
        if truncated
        else ""
    )
    return {
        "today": today.isoformat(),
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "todayItems": today_items,
        "days": ordered_days,
        "totalTvSubscriptions": len(all_tv_subs),
        "checkedTvSubscriptions": len(tv_subs),
        "truncated": truncated,
        "omittedCount": omitted_count,
        "truncationWarning": trunc_warning,
        "embyTruncated": bool(emby.get("truncated")),
        "embyTruncationWarning": str(emby.get("truncationWarning") or ""),
        "embyIdentityIncomplete": is_emby_identity_incomplete(emby),
        "embyIdentityWarning": str(emby.get("identityWarning") or (
            "Emby 存在未映射 TMDB 的 SeriesId/分集，不能把映射失败当作库内不存在"
            if is_emby_identity_incomplete(emby)
            else ""
        )),
    }
