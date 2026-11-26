from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.adapters.http_utils import compact_title, extract_season_episode
from app.domain.pipeline import merge_subscriptions

BEIJING_TZ = timezone(timedelta(hours=8))


def build_following_calendar(
    *,
    snapshot: dict[str, Any],
    tmdb_adapter: Any,
    prefs: dict[str, dict[str, Any]],
    start_date: date | None = None,
    days: int = 7,
) -> dict[str, Any]:
    """Build a crisp following calendar with only 5 honest, human-readable states:
       upcoming | processing | in_emby | issue | unconfirmed
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

    if tmdb_adapter.configured:
        # Cap TMDB season lookups to keep homepage snappy
        tv_subs = [s for s in subs if s["mediaType"] == "tv" and s["tmdbId"]][:20]
        for sub in tv_subs:
            tmdb_id = sub["tmdbId"]
            title = sub["title"]
            seasons = sub.get("seasons") or [1]
            emby_eps = set(tuple(p) for p in (emby.get("episodes") or {}).get(tmdb_id, []))
            sub_ct = compact_title(title)

            for s_num in seasons[-1:]:
                for ep in tmdb_adapter.get_season_episodes(tmdb_id, int(s_num)):
                    air_str = ep.get("airDate") or ""
                    if air_str not in days_map:
                        continue
                    ep_num = ep["episodeNumber"]
                    ep_tag = ep["episodeLabel"]
                    in_emby = (int(s_num), int(ep_num)) in emby_eps
                    matched_qb = next(
                        (t for t in qb_tasks if sub_ct and sub_ct in compact_title(t["name"]) and extract_season_episode(t["name"]) == (int(s_num), int(ep_num))),
                        None,
                    )
                    matched_sa = next(
                        (r for r in sa_hist if r.get("mediaKey") == sub["mediaKey"] and ep_tag.lower() in f"{r.get('seasons','')}{r.get('episodes','')} {r.get('fileName','')}".lower()),
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
    return {
        "today": today.isoformat(),
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "todayItems": today_items,
        "days": ordered_days,
    }
