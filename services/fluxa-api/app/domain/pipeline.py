from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from app.adapters.emby import EmbyAdapter
from app.adapters.http_utils import compact_title, extract_season_episode
from app.adapters.moviepilot import MoviePilotAdapter
from app.adapters.qbittorrent import QbittorrentAdapter
from app.adapters.symedia import SymediaAdapter
from app.adapters.tmdb import TmdbAdapter
from app.adapters.torra import TorraAdapter
from app.storage import FluxaStore

BEIJING_TZ = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(BEIJING_TZ).isoformat(timespec="seconds")


def build_adapters(settings: dict[str, Any]) -> dict[str, Any]:
    svcs = settings.get("services") or {}
    return {
        "moviepilot": MoviePilotAdapter(svcs.get("moviepilot") or {}),
        "torra": TorraAdapter(svcs.get("torra") or {}),
        "qbittorrent": QbittorrentAdapter(svcs.get("qbittorrent") or {}),
        "symedia": SymediaAdapter(svcs.get("symedia") or {}),
        "emby": EmbyAdapter(svcs.get("emby") or {}),
        "tmdb": TmdbAdapter(svcs.get("tmdb") or {}),
    }


def collect_upstream_snapshot(adapters: dict[str, Any]) -> dict[str, Any]:
    """Collect read-only facts from all configured upstream services concurrently."""
    with ThreadPoolExecutor(max_workers=6) as pool:
        f_mp_health = pool.submit(adapters["moviepilot"].check_health)
        f_torra_health = pool.submit(adapters["torra"].check_health)
        f_qb_health = pool.submit(adapters["qbittorrent"].check_health)
        f_sa_health = pool.submit(adapters["symedia"].check_health)
        f_emby_health = pool.submit(adapters["emby"].check_health)
        f_tmdb_health = pool.submit(adapters["tmdb"].check_health)

        f_mp_subs = pool.submit(lambda: _safe_call(adapters["moviepilot"].list_subscriptions, []))
        f_mp_hist = pool.submit(lambda: _safe_call(adapters["moviepilot"].list_download_history, []))
        f_torra_subs = pool.submit(lambda: _safe_call(adapters["torra"].list_subscriptions, []))
        f_torra_sec = pool.submit(lambda: _safe_call(adapters["torra"].get_secupload_evidence, {"available": False, "activeRuns": [], "successFiles": [], "failedFiles": [], "recentRuns": [], "webUrl": adapters["torra"].app_url()}))
        f_qb_tasks = pool.submit(lambda: _safe_call(adapters["qbittorrent"].list_torrents, []))
        f_sa_hist = pool.submit(lambda: _safe_call(adapters["symedia"].list_transfer_history, []))
        f_emby_lib = pool.submit(lambda: _safe_call(adapters["emby"].fetch_library_snapshot, {"available": False, "movies": {}, "series": {}, "episodes": {}, "recentToday": []}))

        services = [
            f_mp_health.result(),
            f_torra_health.result(),
            f_qb_health.result(),
            f_sa_health.result(),
            f_emby_health.result(),
            f_tmdb_health.result(),
        ]
        return {
            "generatedAt": _now_iso(),
            "services": services,
            "serviceMap": {s["service"]: s for s in services},
            "mpSubs": f_mp_subs.result(),
            "mpHistory": f_mp_hist.result(),
            "torraSubs": f_torra_subs.result(),
            "torraSecupload": f_torra_sec.result(),
            "qbTasks": f_qb_tasks.result(),
            "saHistory": f_sa_hist.result(),
            "emby": f_emby_lib.result(),
        }


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


def _title_matches(target_title: str, candidate_text: str, extra_titles: list[str] | None = None) -> bool:
    cand = compact_title(candidate_text)
    if not cand:
        return False
    for t in [target_title, *(extra_titles or [])]:
        ct = compact_title(t)
        if len(ct) >= 2 and ct in cand:
            return True
    return False


def build_actionable_issues(snapshot: dict[str, Any], prefs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract only real, actionable problems — never false-alarming on SA rule_protected skips."""
    muted_keys: set[str] = set()
    for p in prefs.values():
        for mk in p.get("mutedIssueKeys") or []:
            muted_keys.add(str(mk))

    issues: list[dict[str, Any]] = []
    subs = merge_subscriptions(snapshot, prefs)
    sub_by_hash = {h["downloadHash"]: h for h in (snapshot.get("mpHistory") or []) if h.get("downloadHash")}

    # 1. Service connectivity issues (configured but unreachable)
    for svc in snapshot.get("services") or []:
        if svc.get("configured") and not svc.get("connected"):
            issue_key = f"service:{svc['service']}"
            if issue_key in muted_keys:
                continue
            issues.append({
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
        if issue_key in muted_keys:
            continue
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
        issues.append({
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
        if issue_key in muted_keys:
            continue
        matched_sub = next((s for s in subs if _title_matches(s["title"], fname)), None)
        s_num, e_num = extract_season_episode(fname)
        ep_label = f"S{s_num:02d}E{e_num:02d}" if s_num and e_num else ""
        issues.append({
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
    # Chain same-file records so a file that later succeeded no longer surfaces stale failure alarms.
    sa_history = snapshot.get("saHistory") or []
    succeeded_files = {
        (rec.get("fileName") or "").casefold()
        for rec in sa_history
        if rec["outcome"] in {"archived", "replaced"} and rec.get("fileName")
    }
    seen_failed_files: set[str] = set()
    for rec in sa_history:
        fname_key = (rec.get("fileName") or "").casefold()
        # Post-archive sequel/year sanity audit on "archived" movie records (Point 3: Hostel Part II -> 1690)
        if rec["outcome"] in {"archived", "replaced"} and rec.get("mediaType") == "movie" and rec.get("fileName"):
            from app.domain.sa_recovery import check_sequel_identity_conflict, parse_media_filename
            p_src = parse_media_filename(rec["fileName"])
            dest_year_m = __import__("re").search(r"\b((?:19|20)\d{2})\b", rec.get("dest") or "")
            conflict = check_sequel_identity_conflict(
                source_name=p_src["arcTitle"],
                source_year=p_src["year"],
                target_title=rec.get("title") or "",
                target_year=dest_year_m.group(1) if dest_year_m else "",
            )
            if conflict["hasConflict"]:
                issue_key = f"sa_conflict:{rec['id'] or rec['fileName']}"
                if issue_key not in muted_keys:
                    issues.append({
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
        if issue_key in muted_keys:
            continue
        ep_label = f"{rec['seasons']}{rec['episodes']}".strip()
        issues.append({
            "issueKey": issue_key,
            "severity": "error",
            "stage": "symedia",
            "stageLabel": "SA 识别失败" if rec["outcome"] == "identify_failed" else "SA 故障",
            "title": rec["title"],
            "subtitle": rec["fileName"],
            "mediaKey": rec.get("mediaKey") or "",
            "episodeLabel": ep_label,
            "errorText": f"{rec['outcomeLabel']}：{rec['reasonText']}",
            "retryAction": None,
            "saRecord": rec if rec["outcome"] == "identify_failed" else None,
            "appUrl": rec.get("webUrl") or "",
            "appLabel": "打开 SA 处理",
        })

    return issues


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
    extra_titles = [t for t in [original_title] if t]

    # Step 1: Subscription & Torrent Selection (MP Neo + Torra)
    mp_hist_matches = [
        h for h in (snapshot.get("mpHistory") or [])
        if h.get("mediaKey") == media_key or _title_matches(title, h.get("title", ""), extra_titles)
    ]
    mp_hashes = {h["downloadHash"] for h in mp_hist_matches if h.get("downloadHash")}

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
        if (t["hash"] and t["hash"] in mp_hashes) or _title_matches(title, t["name"], extra_titles)
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
    matched_sec_fail = [f for f in (sec.get("failedFiles") or []) if _title_matches(title, f.get("fileName", ""), extra_titles)]
    matched_sec_ok = [f for f in (sec.get("successFiles") or []) if _title_matches(title, f.get("fileName", ""), extra_titles)]
    if not sec.get("available"):
        c115_status, c115_label, c115_summary = "unconfirmed", "未确认", sec.get("error") or "未获取到 Torra 115 上传回执"
    elif matched_sec_fail:
        c115_status, c115_label = "error", f"上传失败（{len(matched_sec_fail)} 个文件）"
        c115_summary = matched_sec_fail[0].get("error") or "115 秒传/上传失败"
    elif matched_sec_ok:
        c115_status, c115_label = "ok", f"已确认上传 115（{len(matched_sec_ok)} 个文件）"
        c115_summary = f"最近上传：{matched_sec_ok[0]['fileName']}"
    elif sec.get("activeRuns"):
        c115_status, c115_label = "active", "115 上传任务运行中"
        c115_summary = "Torra 正在执行秒传/上传队列（逐文件结果待回执）"
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
        if r.get("mediaKey") == media_key or _title_matches(title, r.get("title", "") + " " + r.get("fileName", ""), extra_titles)
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
        else:
            emby_status, emby_label = "idle", "尚未进入 Emby"
            emby_summary = "Emby 媒体库中暂未查到该电影"
            emby_url = adapters["emby"].app_url()
    else:
        s_item = (emby.get("series") or {}).get(tmdb_id)
        if s_item or emby_episodes:
            emby_status = "ok"
            emby_label = f"Emby 已有 {len(emby_episodes)} 集" if emby_episodes else "Emby 已有剧集条目"
            emby_summary = "已核对 Emby 分集条目（实际播放以 Emby 客户端为准）"
            emby_url = (s_item or {}).get("webUrl") or adapters["emby"].app_url()
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
