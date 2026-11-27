from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from flask import Blueprint, current_app, jsonify, make_response, request

from app.adapters.http_utils import sanitize_text
from app.adapters.rss import fetch_and_parse_rss
from app.auth import SESSION_COOKIE, SESSION_TTL_SECONDS
from app.config import redact_settings
from app.domain.actions import execute_bounded_action, preview_action_impact, send_telegram_test
from app.domain.calendar import build_following_calendar
from app.domain.pipeline import (
    build_actionable_issues,
    build_adapters,
    build_media_pipeline,
    collect_upstream_snapshot,
    merge_subscriptions,
    partition_actionable_issues,
)
from app.domain.release_rules import evaluate_release_group, evaluate_subscription_guardrail
from app.domain.quality_policies import get_quality_policy, list_quality_policies
from app.domain.rss_auto_download import run_rss_auto_rule
from app.domain.sa_recovery import diagnose_sa_failure, execute_sa_recovery, verify_sa_recovery

api = Blueprint("fluxa_api", __name__, url_prefix="/api")


def _store():
    return current_app.config["FLUXA_STORE"]


def _auth():
    return current_app.config["FLUXA_AUTH"]


def _snapshots():
    return current_app.config["FLUXA_SNAPSHOTS"]


# -- Auth -----------------------------------------------------------------

@api.get("/auth/session")
def auth_session():
    auth = _auth()
    initialized = auth.is_initialized()
    token = request.cookies.get(SESSION_COOKIE, "")
    user = auth.verify_token(token) if initialized else None
    return jsonify({
        "initialized": initialized,
        "authenticated": bool(user),
        "username": user["username"] if user else "",
    })


@api.post("/auth/setup")
def auth_setup():
    body = request.get_json(silent=True) or {}
    try:
        admin = _auth().setup(body.get("username", ""), body.get("password", ""))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    token = _auth().create_token(admin)
    resp = make_response(jsonify({"ok": True, "username": admin["username"]}))
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_SECONDS, httponly=True, samesite="Lax")
    return resp


@api.post("/auth/login")
def auth_login():
    body = request.get_json(silent=True) or {}
    admin = _auth().verify_login(body.get("username", ""), body.get("password", ""))
    if not admin:
        return jsonify({"ok": False, "error": "账号或密码错误"}), 401
    token = _auth().create_token(admin)
    resp = make_response(jsonify({"ok": True, "username": admin["username"]}))
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_SECONDS, httponly=True, samesite="Lax")
    return resp


@api.post("/auth/logout")
def auth_logout():
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie(SESSION_COOKIE, samesite="Lax")
    return resp


# -- Homepage: Following Calendar + Progress Panel ------------------------

@api.get("/dashboard")
def get_dashboard():
    store = _store()
    settings = store.get_settings()
    force_refresh = str(request.args.get("refresh") or "").strip().lower() in {"1", "true", "yes"}
    force_emby = str(request.args.get("forceEmby") or "").strip().lower() in {"1", "true", "yes"}
    sync_wait = str(request.args.get("sync") or "").strip().lower() in {"1", "true", "yes"}
    snapshot, cache_meta = _snapshots().get_snapshot(
        force_refresh=force_refresh,
        force_emby=force_emby,
        wait=sync_wait,
    )
    adapters = build_adapters(settings, store=store)
    prefs = store.list_preferences()

    issues, ignored_issues = partition_actionable_issues(snapshot, prefs, store=store)
    subs = merge_subscriptions(snapshot, prefs)

    # Parse optional calendar range
    days = int(request.args.get("days") or 7)
    start_raw = str(request.args.get("startDate") or "").strip()
    start_date = None
    if start_raw:
        try:
            start_date = date.fromisoformat(start_raw)
        except ValueError:
            start_date = None
    calendar = build_following_calendar(
        snapshot=snapshot,
        tmdb_adapter=adapters["tmdb"],
        prefs=prefs,
        start_date=start_date,
        days=days,
    )

    active_qb = [
        t for t in (snapshot.get("qbTasks") or [])
        if t["status"] in {"downloading", "stalled", "paused", "checking", "queued", "missing_files", "error"}
    ]
    sec = snapshot.get("torraSecupload") or {}
    emby = snapshot.get("emby") or {}
    from app.adapters.emby import is_emby_identity_incomplete
    emby_unmapped = emby.get("unmappedSeries") or []
    emby_identity = {
        "available": bool(emby.get("available")),
        "dataReadComplete": bool(emby.get("dataReadComplete", not emby.get("truncated"))),
        "identityComplete": bool(emby.get("identityComplete", not is_emby_identity_incomplete(emby))),
        "identityStatus": str(emby.get("identityStatus") or ("complete" if not is_emby_identity_incomplete(emby) else "partial_unmapped")),
        "totalMoviesAndSeries": int(emby.get("totalMoviesAndSeries") or 0),
        "totalEpisodes": int(emby.get("totalEpisodes") or 0),
        "fetchedEpisodes": int(emby.get("fetchedEpisodes") or 0),
        "orphanEpisodeCount": int(emby.get("orphanEpisodeCount") or 0),
        "unmappedSeriesCount": len(emby_unmapped) if emby_unmapped else len(emby.get("unresolvedSeriesIds") or []),
        "failedSeriesLookupCount": len(emby.get("failedSeriesLookupIds") or []),
        "identityWarning": str(emby.get("identityWarning") or ""),
        "unmappedSeries": emby_unmapped[:50],
    }

    return jsonify({
        "generatedAt": snapshot["generatedAt"],
        "cache": cache_meta,
        "services": snapshot["services"],
        "counts": {
            "todayUpdates": len(calendar["todayItems"]),
            "inEmbyToday": len(emby.get("recentToday") or []),
            "activeDownloads": len(active_qb),
            "activeUploads": len(sec.get("activeRuns") or []),
            "issues": len(issues),
            "ignoredIssues": len(ignored_issues),
            "subscriptions": len(subs),
        },
        "issues": issues,
        "ignoredIssues": ignored_issues,
        "embyIdentity": emby_identity,
        "calendar": calendar,
        "activeDownloads": active_qb[:20],
        "activeUploads": (sec.get("activeRuns") or [])[:10],
        "recentEmby": (emby.get("recentToday") or [])[:20],
    })


@api.post("/refresh")
def trigger_refresh():
    """Trigger a single-flight non-blocking background refresh (optional forceEmby=true)."""
    body = request.get_json(silent=True) or {}
    force_emby = bool(body.get("forceEmby", False))
    res = _snapshots().trigger_background_refresh(force_emby=force_emby, trigger="manual")
    return jsonify({"ok": True, **res}), 202


@api.get("/refresh/status")
def get_refresh_status():
    """Return lightweight background refresh status, timing, error, and cache freshness."""
    return jsonify({"ok": True, "refresh": _snapshots().get_refresh_status()})


# -- Single-Media 5-Step Pipeline -----------------------------------------

@api.get("/media/<media_type>/<tmdb_id>")
def get_media_pipeline(media_type: str, tmdb_id: str):
    if media_type not in {"movie", "tv"} or not tmdb_id.isdigit():
        return jsonify({"error": "无效的作品标识"}), 400
    store = _store()
    settings = store.get_settings()
    force_refresh = str(request.args.get("refresh") or "").strip().lower() in {"1", "true", "yes"}
    snapshot, _ = _snapshots().get_snapshot(force_refresh=force_refresh)
    adapters = build_adapters(settings, store=store)
    data = build_media_pipeline(
        media_type=media_type,
        tmdb_id=tmdb_id,
        snapshot=snapshot,
        adapters=adapters,
        store=store,
    )
    return jsonify(data)


@api.patch("/media/<media_type>/<tmdb_id>/preference")
def update_media_preference(media_type: str, tmdb_id: str):
    if media_type not in {"movie", "tv"} or not tmdb_id.isdigit():
        return jsonify({"error": "无效的作品标识"}), 400
    body = request.get_json(silent=True) or {}
    pref = _store().set_preference(
        f"{media_type}:{tmdb_id}",
        intent=body.get("intent"),
        preferred_target=body.get("preferredTarget"),
        notes=body.get("notes"),
        mute_issue_key=body.get("muteIssueKey"),
        unmute_issue_key=body.get("unmuteIssueKey"),
    )
    return jsonify({"ok": True, "preference": pref})


@api.post("/issues/mute")
def mute_issue():
    body = request.get_json(silent=True) or {}
    issue_key = str(body.get("issueKey") or "").strip()
    if not issue_key:
        return jsonify({"error": "缺少 issueKey"}), 400
    store = _store()
    store.set_preference("_global", mute_issue_key=issue_key)
    r_cache = store.get_cache("issues:restored")
    restored = [str(x) for x in (r_cache["payload"] if r_cache and isinstance(r_cache.get("payload"), list) else []) if str(x) != issue_key]
    store.set_cache("issues:restored", restored)
    return jsonify({"ok": True})


@api.post("/issues/restore")
def restore_issue():
    """Restore either a manually muted or auto-ignored ('已有版本，已忽略') issue back to the inbox."""
    body = request.get_json(silent=True) or {}
    issue_key = str(body.get("issueKey") or "").strip()
    if not issue_key:
        return jsonify({"error": "缺少 issueKey"}), 400
    store = _store()
    store.set_preference("_global", unmute_issue_key=issue_key)
    r_cache = store.get_cache("issues:restored")
    restored = [str(x) for x in (r_cache["payload"] if r_cache and isinstance(r_cache.get("payload"), list) else [])]
    if issue_key not in restored:
        restored.append(issue_key)
    store.set_cache("issues:restored", restored)
    return jsonify({"ok": True})


# -- Global Search --------------------------------------------------------

@api.get("/search")
def global_search():
    query = str(request.args.get("q") or "").strip()
    if not query:
        return jsonify({"items": []})
    store = _store()
    settings = store.get_settings()
    adapters = build_adapters(settings)
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Check direct mediaKey like "tv:12345" or "movie:678"
    if ":" in query:
        parts = query.lower().split(":", 1)
        if parts[0] in {"movie", "tv"} and parts[1].isdigit():
            detail = adapters["tmdb"].get_details(parts[0], parts[1])
            if detail:
                results.append(detail)
                seen.add(detail["mediaKey"])

    # Search TMDB
    for item in adapters["tmdb"].search(query):
        if item["mediaKey"] not in seen:
            results.append(item)
            seen.add(item["mediaKey"])

    # Also match existing MP Neo / Torra subscriptions if TMDB is not configured
    if not results:
        snapshot, _ = _snapshots().get_snapshot(force_refresh=False)
        q_low = query.casefold()
        for sub in merge_subscriptions(snapshot, store.list_preferences()):
            if q_low in sub["title"].casefold() and sub["mediaKey"] and sub["mediaKey"] not in seen:
                results.append({
                    "mediaKey": sub["mediaKey"],
                    "mediaType": sub["mediaType"],
                    "tmdbId": sub["tmdbId"],
                    "title": sub["title"],
                    "year": sub["year"],
                    "posterUrl": sub["posterUrl"],
                    "overview": sub["ruleSummary"],
                })
                seen.add(sub["mediaKey"])

    return jsonify({"items": results[:16]})


# -- Subscriptions & Selection Guardrails (Pain Point 2) ------------------

@api.get("/subscriptions")
def list_subscriptions_view():
    store = _store()
    settings = store.get_settings()
    force_refresh = str(request.args.get("refresh") or "").strip().lower() in {"1", "true", "yes"}
    snapshot, cache_meta = _snapshots().get_snapshot(force_refresh=force_refresh)
    prefs = store.list_preferences()
    items = merge_subscriptions(snapshot, prefs)
    logs = store.list_decision_logs(limit=40)
    return jsonify({
        "generatedAt": snapshot["generatedAt"],
        "cache": cache_meta,
        "items": items,
        "decisionLogs": logs,
        "guardrails": settings["guardrails"],
    })


@api.post("/guardrails/check-release")
def check_release_group():
    """Test or evaluate any torrent title against (source_site + site official groups / official tag)."""
    body = request.get_json(silent=True) or {}
    settings = _store().get_settings()
    g = settings["guardrails"]
    result = evaluate_release_group(
        title=str(body.get("title") or ""),
        site_official_groups=g.get("siteOfficialGroups") or [],
        site_id=str(body.get("siteId") or ""),
        site_name=str(body.get("siteName") or ""),
        site_domain=str(body.get("siteDomain") or ""),
        release_group=str(body.get("releaseGroup") or ""),
        has_official_tag=bool(body.get("hasOfficialTag", False)),
        site_tags=body.get("siteTags") if isinstance(body.get("siteTags"), list) else None,
        require_site_official=bool(g.get("requireSiteOfficialGroup", True)),
        block_cross_site_repost=bool(g.get("blockCrossSiteRepost", True)),
        no_fallback_to_non_official=bool(g.get("noFallbackToNonOfficial", True)),
    )
    return jsonify(result)


@api.get("/rss/preview")
def rss_preview():
    """Fetch enabled RSS sources on demand and annotate each torrent with site×official-group check."""
    store = _store()
    settings = store.get_settings()
    g = settings["guardrails"]
    sources = [s for s in store.list_rss_sources() if s["enabled"]]
    all_items: list[dict[str, Any]] = []
    errors: list[str] = []
    for src in sources[:5]:
        try:
            parsed = fetch_and_parse_rss(src["url"], site_id=src["siteId"], site_name=src["name"])
            for row in parsed[:25]:
                check = evaluate_release_group(
                    title=row["title"],
                    site_official_groups=g.get("siteOfficialGroups") or [],
                    site_id=src["siteId"],
                    site_name=src["name"],
                    site_domain=row["siteDomain"],
                    release_group=row["releaseGroup"],
                    require_site_official=bool(g.get("requireSiteOfficialGroup", True)),
                    block_cross_site_repost=bool(g.get("blockCrossSiteRepost", True)),
                    no_fallback_to_non_official=bool(g.get("noFallbackToNonOfficial", True)),
                )
                all_items.append({**row, "sourceName": src["name"], "groupCheck": check})
        except Exception as exc:
            errors.append(f"{src['name']}：{sanitize_text(exc)}")
    all_items.sort(key=lambda x: x.get("publishedAt") or "", reverse=True)
    return jsonify({"items": all_items[:60], "errors": errors, "sourceCount": len(sources)})


@api.get("/rss/sources")
def list_rss_sources():
    return jsonify({"items": _store().list_rss_sources()})


@api.post("/rss/sources")
def save_rss_source():
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    url = str(body.get("url") or "").strip()
    site_id = str(body.get("siteId") or "").strip()
    if not name or not url.startswith(("http://", "https://")):
        return jsonify({"error": "请填写有效的来源名称和 HTTP(S) RSS 地址"}), 400
    source_id = str(body.get("id") or uuid.uuid4().hex[:12])
    saved = _store().save_rss_source(source_id, name, site_id, url, bool(body.get("enabled", True)))
    return jsonify({"ok": True, "item": saved})


@api.delete("/rss/sources/<source_id>")
def delete_rss_source(source_id: str):
    _store().delete_rss_source(source_id)
    return jsonify({"ok": True})


# -- Discover & Controllable Subscribe ------------------------------------

@api.get("/discover")
def discover_media():
    category = str(request.args.get("category") or "trending_tv").strip()
    store = _store()
    settings = store.get_settings()
    adapters = build_adapters(settings)
    if not adapters["tmdb"].configured:
        return jsonify({"configured": False, "items": [], "message": "请先在设置页配置 TMDB API Key 或 Bearer Token"})

    raw_items = adapters["tmdb"].discover(category)
    guardrails = settings["guardrails"]
    annotated: list[dict[str, Any]] = []
    for item in raw_items:
        # Enrich TV details only for items with older first_air_date so we accurately detect returning new seasons!
        enriched = item
        if item["mediaType"] == "tv":
            g_quick = evaluate_subscription_guardrail(item, guardrails, from_chart=True)
            if g_quick["category"] == "old_finished_series":
                detail = adapters["tmdb"].get_details("tv", item["tmdbId"])
                if detail:
                    enriched = detail
        guardrail = evaluate_subscription_guardrail(enriched, guardrails, from_chart=True)
        annotated.append({**enriched, "guardrail": guardrail})
    return jsonify({"configured": True, "category": category, "items": annotated})


@api.post("/subscribe")
def create_subscription():
    """Push a subscription to MoviePilot Neo or Torra with transparent guardrail evaluation & decision log."""
    body = request.get_json(silent=True) or {}
    media_type = str(body.get("mediaType") or "movie")
    tmdb_id = str(body.get("tmdbId") or "").strip()
    title = str(body.get("title") or "").strip()
    year = str(body.get("year") or "").strip()
    season = body.get("seasonNumber")
    intent = "collect" if body.get("intent") == "collect" else "follow"
    target = str(body.get("target") or "").strip()
    source_label = str(body.get("sourceLabel") or "Fluxa 手动订阅").strip()
    from_chart = bool(body.get("fromChart", False))
    override_guardrail = bool(body.get("overrideGuardrail", False))

    if media_type not in {"movie", "tv"} or not tmdb_id.isdigit() or not title:
        return jsonify({"error": "缺少必要的媒体类型、TMDB ID 或片名"}), 400

    store = _store()
    settings = store.get_settings()
    adapters = build_adapters(settings)
    target = target or settings["guardrails"].get("defaultSubscribeTarget") or "moviepilot"

    detail = adapters["tmdb"].get_details(media_type, tmdb_id) if adapters["tmdb"].configured else None
    eval_item = detail or {"mediaType": media_type, "tmdbId": tmdb_id, "title": title, "releaseDate": f"{year}-01-01" if year else ""}
    guardrail = evaluate_subscription_guardrail(eval_item, settings["guardrails"], intent=intent, from_chart=from_chart)

    if not guardrail["allowed"] and not override_guardrail:
        store.add_decision_log(
            media_key=f"{media_type}:{tmdb_id}",
            title=title,
            decision_type="subscribe",
            outcome="blocked",
            source_label=source_label,
            rule_name=guardrail["ruleName"],
            reason_text=guardrail["reasonText"],
            details={"guardrail": guardrail, "intent": intent},
        )
        return jsonify({
            "ok": False,
            "blocked": True,
            "guardrail": guardrail,
            "message": guardrail["reasonText"],
        }), 409

    final_season = int(season) if season else guardrail.get("recommendedSeason")
    media_key = f"{media_type}:{tmdb_id}"
    try:
        if target == "torra":
            adapter = adapters["torra"]
            res = adapter.add_subscription(
                title=title,
                media_type=media_type,
                tmdb_id=int(tmdb_id),
                year=year,
                season=final_season if media_type == "tv" else None,
            )
            target_label = "Torra"
        else:
            adapter = adapters["moviepilot"]
            res = adapter.add_subscription(
                title=title,
                media_type=media_type,
                tmdb_id=int(tmdb_id),
                year=year,
                season=final_season if media_type == "tv" else None,
                best_version=(intent == "collect"),
            )
            target_label = "MoviePilot Neo"
    except Exception as exc:
        err_msg = sanitize_text(exc)
        store.add_decision_log(
            media_key=media_key,
            title=title,
            decision_type="subscribe",
            outcome="failed",
            source_label=source_label,
            rule_name=f"{target_label} 请求异常",
            reason_text=err_msg,
            details={"target": target, "season": final_season, "intent": intent},
        )
        return jsonify({"ok": False, "error": err_msg}), 502

    # 1. Check upstream business outcome (never treat HTTP 200 + success=false as subscribed!)
    if not res.get("ok"):
        err_msg = sanitize_text(res.get("message") or f"{target_label} 拒绝了该订阅请求")
        store.add_decision_log(
            media_key=media_key,
            title=title,
            decision_type="subscribe",
            outcome="failed",
            source_label=source_label,
            rule_name=f"{target_label} 拒绝订阅",
            reason_text=err_msg,
            details={"target": target, "season": final_season, "intent": intent},
        )
        return jsonify({"ok": False, "error": err_msg}), 502

    # 2. Read back upstream subscription list to verify it actually exists!
    readback_confirmed = False
    readback_err = ""
    try:
        live_subs = adapter.list_subscriptions()
        for sub in live_subs:
            if sub.get("mediaType") != media_type or str(sub.get("tmdbId") or "") != tmdb_id:
                continue
            if media_type == "tv" and final_season and sub.get("seasonNumber") is not None:
                if int(sub["seasonNumber"]) != int(final_season):
                    continue
            readback_confirmed = True
            break
    except Exception as exc:
        readback_err = sanitize_text(exc)

    if not readback_confirmed:
        unconfirmed_msg = (
            f"{target_label} 虽返回请求响应，但读回订阅列表失败（{readback_err}），无法确认《{title}》已生效"
            if readback_err
            else f"{target_label} 虽返回请求响应，但读回订阅列表未查到《{title}》（TMDB #{tmdb_id}），未确认订阅成功"
        )
        store.add_decision_log(
            media_key=media_key,
            title=title,
            decision_type="subscribe",
            outcome="unconfirmed",
            source_label=source_label,
            rule_name=f"{target_label} 读回未确认",
            reason_text=unconfirmed_msg,
            details={"target": target, "season": final_season, "intent": intent},
        )
        return jsonify({"ok": False, "unconfirmed": True, "error": unconfirmed_msg}), 502

    store.set_preference(media_key, intent=intent, preferred_target=target)
    log_entry = store.add_decision_log(
        media_key=media_key,
        title=title,
        decision_type="subscribe",
        outcome="subscribed",
        source_label=source_label,
        rule_name=guardrail["ruleName"],
        reason_text=f"已提交至 {target_label} 并读回确认生效（{'收藏' if intent == 'collect' else '追更'}）：{guardrail['reasonText']}",
        details={"target": target, "season": final_season, "intent": intent, "guardrail": guardrail, "verified": True},
    )
    return jsonify({"ok": True, "verified": True, "message": res["message"], "decisionLog": log_entry})


# -- Bounded Single-Task Actions (Pain Point 3) ---------------------------

@api.post("/actions/preview")
def action_preview():
    body = request.get_json(silent=True) or {}
    try:
        preview = preview_action_impact(
            action_type=str(body.get("actionType") or ""),
            payload=body.get("payload") or {},
            settings=_store().get_settings(),
        )
        return jsonify(preview)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@api.post("/actions/execute")
def action_execute():
    body = request.get_json(silent=True) or {}
    try:
        res = execute_bounded_action(
            action_type=str(body.get("actionType") or ""),
            payload=body.get("payload") or {},
            confirmed=bool(body.get("confirmed", False)),
            settings=_store().get_settings(),
            store=_store(),
        )
        return jsonify(res)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@api.get("/audit")
def list_audit():
    return jsonify({"items": _store().list_audit(limit=50)})


# -- SA Identify Failure Diagnosis, Scoped Mappings & Safe Recovery -------

@api.post("/sa-recovery/diagnose")
def sa_recovery_diagnose():
    body = request.get_json(silent=True) or {}
    record = body.get("record") if isinstance(body.get("record"), dict) else {}
    if not record.get("fileName") and not record.get("src"):
        return jsonify({"error": "缺少 SA 失败记录的文件名或路径"}), 400
    store = _store()
    settings = store.get_settings()
    snapshot, _ = _snapshots().get_snapshot(force_refresh=False)
    adapters = build_adapters(settings, store=store)
    result = diagnose_sa_failure(
        record=record,
        settings=settings,
        store=store,
        manual_tmdb_id=str(body.get("manualTmdbId") or "").strip(),
        manual_media_type=str(body.get("manualMediaType") or "").strip(),
        adapters=adapters,
        snapshot=snapshot,
    )
    return jsonify(result)


@api.post("/sa-recovery/execute")
def sa_recovery_execute():
    body = request.get_json(silent=True) or {}
    try:
        result = execute_sa_recovery(
            payload=body,
            settings=_store().get_settings(),
            store=_store(),
        )
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@api.post("/sa-recovery/jobs/<int:job_id>/verify")
def sa_recovery_verify(job_id: int):
    try:
        result = verify_sa_recovery(
            job_id=job_id,
            settings=_store().get_settings(),
            store=_store(),
        )
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404


@api.get("/sa-recovery/mappings")
def list_sa_mappings():
    store = _store()
    return jsonify({
        "mappings": store.list_sa_mappings(),
        "jobs": store.list_sa_recovery_jobs(limit=30),
    })


@api.delete("/sa-recovery/mappings/<int:mapping_id>")
def delete_sa_mapping(mapping_id: int):
    _store().delete_sa_mapping(mapping_id)
    return jsonify({"ok": True})


# -- Site + Official-Group RSS Auto-Download -> qB ------------------------

def _redact_rule(rule: dict[str, Any]) -> dict[str, Any]:
    copy = dict(rule)
    raw_url = str(copy.get("url") or "")
    copy["urlMasked"] = sanitize_text(raw_url.split("?", 1)[0] + ("?***" if "?" in raw_url else ""), 160)
    copy["hasUrl"] = bool(raw_url)
    copy["url"] = ""
    policy = get_quality_policy(str(copy.get("qualityPolicyId") or "none"))
    copy["qualityPolicyId"] = policy["id"]
    copy["qualityPolicyName"] = policy["shortName"]
    return copy


@api.get("/rss-auto/rules")
def list_rss_auto():
    store = _store()
    settings = store.get_settings()
    adapters = build_adapters(settings)
    qb_categories = adapters["qbittorrent"].list_categories() if adapters["qbittorrent"].configured else []
    rules = [_redact_rule(r) for r in store.list_rss_auto_rules()]
    items = store.list_rss_auto_items(limit=60)
    return jsonify({
        "rules": rules,
        "items": items,
        "qbCategories": qb_categories,
        "qbConfigured": adapters["qbittorrent"].configured,
        "sites": settings["guardrails"].get("siteOfficialGroups") or [],
        "qualityPolicies": list_quality_policies(),
    })


@api.post("/rss-auto/rules")
def save_rss_auto():
    body = request.get_json(silent=True) or {}
    store = _store()
    settings = store.get_settings()
    rule_id = str(body.get("id") or uuid.uuid4().hex[:12]).strip()
    existing = store.get_rss_auto_rule(rule_id)

    site_id = str(body.get("siteId") or "").strip()
    site_cfg = next(
        (s for s in (settings["guardrails"].get("siteOfficialGroups") or []) if s.get("siteId") == site_id),
        None,
    )
    site_name = str(body.get("siteName") or (site_cfg or {}).get("siteName") or site_id).strip()
    official_group = str(body.get("officialGroup") or "").strip()
    raw_url = str(body.get("url") or "").strip()
    final_url = raw_url or (existing["url"] if existing else "")
    policy = get_quality_policy(str(body.get("qualityPolicyId") or (existing["qualityPolicyId"] if existing else "general_hq")))

    if not site_name or not official_group:
        return jsonify({"error": "请选择站点并填写本站官组名称"}), 400
    if not final_url.startswith(("http://", "https://")):
        return jsonify({"error": "请填写有效的 HTTP(S) 官组 RSS 地址"}), 400

    saved = store.save_rss_auto_rule({
        "id": rule_id,
        "siteId": site_id or site_name.lower(),
        "siteName": site_name,
        "officialGroup": official_group,
        "url": final_url,
        "savePath": str(body.get("savePath") or "").strip(),
        "category": str(body.get("category") or "").strip(),
        "qualityPolicyId": policy["id"],
        "enabled": bool(body.get("enabled", True)),
        "pollIntervalMinutes": int(body.get("pollIntervalMinutes") or 5),
    })

    # If newly created and enabled, immediately initialize baseline (without downloading history!)
    init_message = "已保存规则"
    if saved.get("enabled") and not saved.get("baselineInitialized"):
        res = run_rss_auto_rule(rule_id, store=store, settings=settings)
        saved = res.get("rule") or saved
        init_message = res.get("message") or init_message

    return jsonify({"ok": True, "rule": _redact_rule(saved), "message": init_message})


@api.patch("/rss-auto/rules/<rule_id>/toggle")
def toggle_rss_auto(rule_id: str):
    store = _store()
    rule = store.get_rss_auto_rule(rule_id)
    if not rule:
        return jsonify({"error": "规则不存在"}), 404
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled", not rule["enabled"]))
    next_policy = rule.get("qualityPolicyId") or "none"
    if "qualityPolicyId" in body:
        next_policy = get_quality_policy(str(body.get("qualityPolicyId") or "none"))["id"]
    saved = store.save_rss_auto_rule({**rule, "enabled": enabled, "qualityPolicyId": next_policy})
    return jsonify({"ok": True, "rule": _redact_rule(saved)})


@api.post("/rss-auto/rules/<rule_id>/run")
def run_rss_auto_now(rule_id: str):
    try:
        res = run_rss_auto_rule(rule_id, store=_store(), settings=_store().get_settings())
        if res.get("rule"):
            res["rule"] = _redact_rule(res["rule"])
        return jsonify(res)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404


@api.delete("/rss-auto/rules/<rule_id>")
def delete_rss_auto(rule_id: str):
    _store().delete_rss_auto_rule(rule_id)
    return jsonify({"ok": True})


# -- Settings & Connectivity Test -----------------------------------------

@api.get("/settings")
def get_settings():
    return jsonify(redact_settings(_store().get_settings()))


@api.put("/settings")
def save_settings():
    body = request.get_json(silent=True) or {}
    updated = _store().save_settings(body)
    _snapshots().invalidate()
    _snapshots().trigger_background_refresh(force_emby=True)
    return jsonify({"ok": True, "settings": redact_settings(updated)})


@api.post("/settings/test/<service>")
def test_service(service: str):
    settings = _store().get_settings()
    if service == "telegram":
        try:
            return jsonify(send_telegram_test(settings))
        except Exception as exc:
            return jsonify({"ok": False, "message": sanitize_text(exc)}), 400
    adapters = build_adapters(settings)
    if service not in adapters:
        return jsonify({"ok": False, "message": f"未知服务：{service}"}), 404
    health = adapters[service].check_health()
    return jsonify({"ok": health["connected"], **health})
