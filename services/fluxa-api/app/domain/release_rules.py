from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.adapters.rss import extract_release_group, extract_version_tags

BEIJING_TZ = timezone(timedelta(hours=8))


def _parse_date(value: str) -> date | None:
    raw = str(value or "").strip()[:10]
    if len(raw) != 10:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _norm(value: str) -> str:
    return str(value or "").strip().casefold()


def find_site_config(
    site_official_groups: list[dict[str, Any]],
    *,
    site_id: str = "",
    site_name: str = "",
    site_domain: str = "",
) -> dict[str, Any] | None:
    sid = _norm(site_id)
    sname = _norm(site_name)
    sdom = _norm(site_domain)
    for row in site_official_groups or []:
        if sid and _norm(row.get("siteId", "")) == sid:
            return row
        if sname and (_norm(row.get("siteName", "")) == sname or _norm(row.get("siteId", "")) == sname):
            return row
        if sdom:
            for dom in row.get("domains") or []:
                d = _norm(dom)
                if d and (sdom == d or sdom.endswith(f".{d}")):
                    return row
    return None


def evaluate_release_group(
    *,
    title: str,
    site_official_groups: list[dict[str, Any]],
    site_id: str = "",
    site_name: str = "",
    site_domain: str = "",
    release_group: str = "",
    require_site_official: bool = False,
    block_cross_site_repost: bool = True,
) -> dict[str, Any]:
    """Judge official release group strictly by (source_site + that site's official groups).

    Never uses a flat global whitelist of famous release groups.
    """
    group = str(release_group or "").strip() or extract_release_group(title)
    tags = extract_version_tags(title)
    site_cfg = find_site_config(
        site_official_groups,
        site_id=site_id,
        site_name=site_name,
        site_domain=site_domain,
    )
    resolved_site_name = (site_cfg or {}).get("siteName") or site_name or site_id or site_domain or "未知站点"

    if not group:
        allowed = not require_site_official
        return {
            "releaseGroup": "",
            "sourceSite": resolved_site_name,
            "versionTags": tags,
            "classification": "unknown_group",
            "classificationLabel": "未识别发布组",
            "ownerSite": "",
            "allowed": allowed,
            "ruleName": "站点官组校验",
            "reasonText": (
                f"未能从标题中识别发布组（来源站：{resolved_site_name}）"
                + ("，当前要求本站官组，已拦截" if not allowed else "")
            ),
        }

    group_norm = _norm(group)
    # Check if group belongs to the CURRENT source site
    if site_cfg:
        own_groups = {_norm(g) for g in (site_cfg.get("officialGroups") or []) if str(g).strip()}
        if group_norm in own_groups:
            return {
                "releaseGroup": group,
                "sourceSite": resolved_site_name,
                "versionTags": tags,
                "classification": "site_official",
                "classificationLabel": "本站官组",
                "ownerSite": resolved_site_name,
                "allowed": True,
                "ruleName": "本站官组命中",
                "reasonText": f"来源站「{resolved_site_name}」且发布组「{group}」属于该站官组",
            }

    # Check if group belongs to ANY OTHER site's official groups => cross-site repost!
    other_owners: list[str] = []
    for row in site_official_groups or []:
        if site_cfg and _norm(row.get("siteId", "")) == _norm(site_cfg.get("siteId", "")):
            continue
        other_groups = {_norm(g) for g in (row.get("officialGroups") or []) if str(g).strip()}
        if group_norm in other_groups:
            other_owners.append(str(row.get("siteName") or row.get("siteId") or ""))

    if other_owners:
        owner_label = " / ".join(other_owners)
        allowed = not block_cross_site_repost and not require_site_official
        return {
            "releaseGroup": group,
            "sourceSite": resolved_site_name,
            "versionTags": tags,
            "classification": "cross_site_repost",
            "classificationLabel": "跨站转载组",
            "ownerSite": owner_label,
            "allowed": allowed,
            "ruleName": "跨站转载拦截" if not allowed else "跨站转载提醒",
            "reasonText": (
                f"来源站为「{resolved_site_name}」，但发布组「{group}」属于「{owner_label}」官组（跨站转载）"
                + ("，已按规则拦截" if not allowed else "")
            ),
        }

    if not site_cfg:
        allowed = not require_site_official
        return {
            "releaseGroup": group,
            "sourceSite": resolved_site_name,
            "versionTags": tags,
            "classification": "site_unmapped",
            "classificationLabel": "站点未配置官组",
            "ownerSite": "",
            "allowed": allowed,
            "ruleName": "站点官组未映射",
            "reasonText": f"站点「{resolved_site_name}」尚未在设置中配置官组列表，无法确认「{group}」是否为本站官组",
        }

    allowed = not require_site_official
    return {
        "releaseGroup": group,
        "sourceSite": resolved_site_name,
        "versionTags": tags,
        "classification": "non_official",
        "classificationLabel": "非本站官组",
        "ownerSite": "",
        "allowed": allowed,
        "ruleName": "非官组过滤" if not allowed else "非本站官组",
        "reasonText": (
            f"发布组「{group}」不在「{resolved_site_name}」官组列表中"
            + ("，当前要求仅选本站官组，已拦截" if not allowed else "")
        ),
    }


def evaluate_subscription_guardrail(
    item: dict[str, Any],
    guardrails: dict[str, Any],
    *,
    intent: str = "follow",
    from_chart: bool = True,
    today: date | None = None,
) -> dict[str, Any]:
    """Distinguish new movies/shows, new season of an older show, and old movies/finished series."""
    now_date = today or datetime.now(BEIJING_TZ).date()
    media_type = str(item.get("mediaType") or "movie")
    release_date = _parse_date(str(item.get("releaseDate") or ""))
    new_max_days = int(guardrails.get("newReleaseMaxAgeDays") or 365)
    new_season_days = int(guardrails.get("newSeasonMaxAgeDays") or 180)
    block_old_movies = bool(guardrails.get("blockOldMoviesFromCharts", True))
    block_old_series = bool(guardrails.get("blockFinishedOldSeriesFromCharts", True))
    only_latest_season = bool(guardrails.get("onlySubscribeLatestSeasonForReturning", True))

    if media_type == "movie":
        if release_date is None:
            return {
                "category": "unknown_date",
                "categoryLabel": "上映日期未确认",
                "allowed": True,
                "recommendedSeason": None,
                "recommendedIntent": intent,
                "ruleName": "上映日期未知",
                "reasonText": "TMDB 未返回明确上映日期，请人工确认是否为需要的新片",
            }
        age_days = (now_date - release_date).days
        if age_days <= new_max_days:
            return {
                "category": "new_movie",
                "categoryLabel": "新片",
                "allowed": True,
                "recommendedSeason": None,
                "recommendedIntent": intent,
                "ruleName": "新片放行",
                "reasonText": f"上映于 {release_date.isoformat()}（在近 {new_max_days} 天窗口内），允许订阅",
            }
        if from_chart and block_old_movies and intent != "collect":
            return {
                "category": "old_movie",
                "categoryLabel": "老电影",
                "allowed": False,
                "recommendedSeason": None,
                "recommendedIntent": "collect",
                "ruleName": "榜单老电影防误订",
                "reasonText": (
                    f"上映于 {release_date.isoformat()}（距今超过 {new_max_days} 天），"
                    "榜单自动追更已拦截；如确需洗版收藏请手动按「收藏」添加"
                ),
            }
        return {
            "category": "old_movie",
            "categoryLabel": "老电影（收藏）",
            "allowed": True,
            "recommendedSeason": None,
            "recommendedIntent": "collect",
            "ruleName": "手动收藏放行",
            "reasonText": f"上映于 {release_date.isoformat()}，按「收藏」意图手动放行",
        }

    # TV series: explicitly distinguish NEW SERIES vs OLD SERIES WITH NEW SEASON vs FINISHED OLD SERIES
    seasons = [s for s in (item.get("seasons") or []) if isinstance(s, dict) and int(s.get("seasonNumber") or 0) > 0]
    latest_season = max(seasons, key=lambda s: int(s.get("seasonNumber") or 0)) if seasons else None
    latest_season_num = int(latest_season["seasonNumber"]) if latest_season else 1
    latest_season_date = _parse_date(str((latest_season or {}).get("airDate") or ""))
    last_air_date = _parse_date(str(item.get("lastAirDate") or ""))
    next_ep = item.get("nextEpisodeToAir") if isinstance(item.get("nextEpisodeToAir"), dict) else None
    next_ep_season = int(next_ep.get("season_number") or 0) if next_ep else 0
    status_lower = str(item.get("status") or "").strip().lower()

    is_first_air_new = release_date is not None and (now_date - release_date).days <= new_max_days
    if is_first_air_new:
        target_season = next_ep_season or latest_season_num or 1
        return {
            "category": "new_series",
            "categoryLabel": "新剧",
            "allowed": True,
            "recommendedSeason": target_season,
            "recommendedIntent": "follow",
            "ruleName": "新剧追更放行",
            "reasonText": f"首播于 {release_date.isoformat()}，属于新剧，建议追更第 {target_season} 季",
        }

    # Check if an older show has a NEW season airing recently or upcoming
    has_recent_season = (
        (latest_season_date is not None and (now_date - latest_season_date).days <= new_season_days)
        or (last_air_date is not None and (now_date - last_air_date).days <= new_season_days)
        or bool(next_ep)
    )
    if has_recent_season and status_lower not in {"ended", "canceled"}:
        target_season = next_ep_season or latest_season_num
        season_note = (
            f"仅订阅最新第 {target_season} 季（不倒灌旧季）"
            if only_latest_season
            else f"建议订阅第 {target_season} 季"
        )
        return {
            "category": "returning_new_season",
            "categoryLabel": f"老剧新一季 (S{target_season:02d})",
            "allowed": True,
            "recommendedSeason": target_season,
            "recommendedIntent": "follow",
            "ruleName": "老剧新一季仅订最新季",
            "reasonText": (
                f"首播于 {release_date.isoformat() if release_date else '较早年份'}，"
                f"但第 {target_season} 季近期播出/即将开播，{season_note}"
            ),
        }

    # Old / finished TV series
    if from_chart and block_old_series and intent != "collect":
        return {
            "category": "old_finished_series",
            "categoryLabel": "老剧 / 已完结",
            "allowed": False,
            "recommendedSeason": latest_season_num,
            "recommendedIntent": "collect",
            "ruleName": "榜单老剧防误订",
            "reasonText": (
                f"首播于 {release_date.isoformat() if release_date else '较早年份'} 且近期无新季开播，"
                "榜单自动追更已拦截；如确需补旧番/收藏请手动按「收藏」添加"
            ),
        }

    return {
        "category": "old_finished_series",
        "categoryLabel": "老剧（收藏）",
        "allowed": True,
        "recommendedSeason": latest_season_num,
        "recommendedIntent": "collect",
        "ruleName": "手动收藏放行",
        "reasonText": "已按「收藏」意图手动放行旧剧",
    }
