from __future__ import annotations

import base64
import hashlib
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from app.adapters.http_utils import sanitize_text
from app.adapters.rss import fetch_and_parse_rss
from app.domain.pipeline import build_adapters
from app.domain.quality_policies import (
    compare_quality_candidates,
    evaluate_candidate_quality,
    get_quality_policy,
    parse_release_quality_specs,
)
from app.storage import FluxaStore

BEIJING_TZ = timezone(timedelta(hours=8))
MAX_TORRENT_BYTES = 5 * 1024 * 1024
MAGNET_HEX_RE = re.compile(r"(?i)urn:btih:([0-9a-f]{40})\b")
MAGNET_B32_RE = re.compile(r"(?i)urn:btih:([a-z2-7]{32})\b")


def _now_dt() -> datetime:
    return datetime.now(BEIJING_TZ)


def _now_iso() -> str:
    return _now_dt().isoformat(timespec="seconds")


def _bencode_skip(data: bytes, idx: int) -> int:
    """Advance index past one bencoded value; used to slice the raw `info` dict bytes cleanly."""
    if idx >= len(data):
        raise ValueError("unexpected EOF")
    ch = data[idx : idx + 1]
    if ch == b"i":
        end = data.index(b"e", idx + 1)
        return end + 1
    if ch == b"l":
        idx += 1
        while data[idx : idx + 1] != b"e":
            idx = _bencode_skip(data, idx)
        return idx + 1
    if ch == b"d":
        idx += 1
        while data[idx : idx + 1] != b"e":
            idx = _bencode_skip(data, idx)
            idx = _bencode_skip(data, idx)
        return idx + 1
    if b"0" <= ch <= b"9":
        colon = data.index(b":", idx)
        length = int(data[idx:colon])
        end = colon + 1 + length
        if end > len(data):
            raise ValueError("truncated string")
        return end
    raise ValueError(f"invalid bencode token at {idx}")


def extract_torrent_info_hash(torrent_bytes: bytes) -> str:
    """Compute exact 40-char lowercase BitTorrent v1 info_hash from raw .torrent bytes."""
    if not torrent_bytes or not torrent_bytes.startswith(b"d"):
        return ""
    try:
        idx = 1
        while idx < len(torrent_bytes) and torrent_bytes[idx : idx + 1] != b"e":
            colon = torrent_bytes.index(b":", idx)
            klen = int(torrent_bytes[idx:colon])
            kstart = colon + 1
            kend = kstart + klen
            key = torrent_bytes[kstart:kend]
            vstart = kend
            vend = _bencode_skip(torrent_bytes, vstart)
            if key == b"info":
                return hashlib.sha1(torrent_bytes[vstart:vend]).hexdigest().lower()
            idx = vend
    except Exception:
        return ""
    return ""


def extract_magnet_hash(url: str) -> str:
    text = str(url or "").strip()
    m_hex = MAGNET_HEX_RE.search(text)
    if m_hex:
        return m_hex.group(1).lower()
    m_b32 = MAGNET_B32_RE.search(text)
    if m_b32:
        try:
            raw = base64.b32decode(m_b32.group(1).upper())
            return raw.hex().lower()
        except Exception:
            return ""
    return ""


def _item_key(entry: dict[str, Any]) -> str:
    dl = str(entry.get("downloadUrl") or "").strip()
    detail = str(entry.get("detailUrl") or "").strip()
    title = str(entry.get("title") or "").strip()
    raw = detail or dl or title
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:32]


def _default_download_torrent(url: str) -> bytes:
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Fluxa/2.1"})
    resp.raise_for_status()
    if len(resp.content) > MAX_TORRENT_BYTES:
        raise ValueError("种子文件超过 5 MiB 限制")
    return resp.content


def run_rss_auto_rule(
    rule_id: str,
    *,
    store: FluxaStore,
    settings: dict[str, Any],
    adapters: dict[str, Any] | None = None,
    feed_fetcher=None,
    torrent_downloader=None,
    sleep_fn=time.sleep,
) -> dict[str, Any]:
    """Run one check cycle for a single (site + site official group) RSS auto-download rule.

    Boundaries:
      - First run establishes baseline ONLY (skips historical entries, never floods qB).
      - Subsequent runs deduplicate by torrent info_hash (against both local history and qB live tasks).
      - After adding to qB, reads back from qB to confirm task existence.
      - Never deletes old tasks, stops seeding, or modifies SA rules.
    """
    rule = store.get_rss_auto_rule(rule_id)
    if not rule:
        raise ValueError("RSS 自动下载规则不存在")

    adapters = adapters or build_adapters(settings)
    qb = adapters["qbittorrent"]
    fetch_feed = feed_fetcher or (lambda url, sid, sname: fetch_and_parse_rss(url, site_id=sid, site_name=sname))
    download_torrent = torrent_downloader or _default_download_torrent
    checked_at = _now_iso()

    try:
        entries = fetch_feed(rule["url"], rule["siteId"], rule["siteName"])
    except Exception as exc:
        msg = f"拉取 RSS 失败：{sanitize_text(exc)}"
        updated = store.update_rss_auto_rule_status(
            rule_id,
            baseline_initialized=rule["baselineInitialized"],
            last_checked_at=checked_at,
            last_status="error",
            last_message=msg,
        )
        return {"ok": False, "rule": updated, "message": msg, "added": 0, "skipped": 0, "failed": 0}

    # First-time baseline initialization: record existing entries WITHOUT downloading history!
    if not rule["baselineInitialized"]:
        for entry in entries:
            key = _item_key(entry)
            mag_hash = extract_magnet_hash(entry.get("downloadUrl") or "")
            store.record_rss_auto_item(
                rule_id=rule_id,
                item_key=key,
                torrent_hash=mag_hash,
                title=entry.get("title") or "未命名条目",
                download_url=entry.get("downloadUrl") or "",
                status="baseline_skipped",
                error_text="首次启用建立基线，不补下载历史条目",
                qb_category=rule["category"],
                qb_save_path=rule["savePath"],
            )
        msg = f"已建立初始基线（记录 {len(entries)} 条历史条目，不补下载；后续仅自动下载新条目）"
        updated = store.update_rss_auto_rule_status(
            rule_id,
            baseline_initialized=True,
            last_checked_at=checked_at,
            last_status="ok",
            last_message=msg,
        )
        return {"ok": True, "baselineInit": True, "rule": updated, "message": msg, "added": 0, "skipped": len(entries), "failed": 0}

    if not qb.configured:
        msg = "qBittorrent 未配置，无法推送新条目"
        updated = store.update_rss_auto_rule_status(
            rule_id,
            baseline_initialized=True,
            last_checked_at=checked_at,
            last_status="error",
            last_message=msg,
        )
        return {"ok": False, "rule": updated, "message": msg, "added": 0, "skipped": 0, "failed": 0}

    # Fetch live qB torrent hashes once for accurate hash deduplication
    try:
        live_qb_tasks = qb.list_torrents()
        existing_qb_hashes = {t["hash"].lower() for t in live_qb_tasks if t.get("hash")}
    except Exception as exc:
        msg = f"连接 qBittorrent 失败：{sanitize_text(exc)}"
        updated = store.update_rss_auto_rule_status(
            rule_id,
            baseline_initialized=True,
            last_checked_at=checked_at,
            last_status="error",
            last_message=msg,
        )
        return {"ok": False, "rule": updated, "message": msg, "added": 0, "skipped": 0, "failed": 0}

    seen_keys = store.seen_rss_auto_item_keys(rule_id)
    policy_id = str(rule.get("qualityPolicyId") or "none").strip() or "none"
    added = 0
    unconfirmed = 0
    skipped = 0
    quality_skipped = 0
    insufficient_skipped = 0
    failed = 0
    last_err = ""

    # Process oldest-new first so qB receives chronological order
    new_entries = [e for e in reversed(entries) if _item_key(e) not in seen_keys]

    # Step 1: Evaluate quality policy BEFORE downloading .torrent or pushing to qB
    grouped_qualified: dict[str, list[dict[str, Any]]] = {}
    passthrough_candidates: list[dict[str, Any]] = []

    for entry in new_entries:
        key = _item_key(entry)
        title = str(entry.get("title") or "未命名种子")
        specs = parse_release_quality_specs(
            title=title,
            description=str(entry.get("description") or ""),
            size_bytes=int(entry.get("sizeBytes") or 0),
            season_number=entry.get("seasonNumber"),
            episode_number=entry.get("episodeNumber"),
        )
        eval_res = evaluate_candidate_quality(specs, policy_id)
        cand = {"entry": entry, "key": key, "title": title, "specs": specs, "eval": eval_res}

        if policy_id == "none":
            passthrough_candidates.append(cand)
            continue

        decision = eval_res["decision"]
        if decision == "reject":
            quality_skipped += 1
            store.record_rss_auto_item(
                rule_id=rule_id,
                item_key=key,
                torrent_hash=extract_magnet_hash(entry.get("downloadUrl") or ""),
                title=title,
                download_url="",
                status="quality_rejected",
                error_text=eval_res["reason"],
                qb_category=rule["category"],
                qb_save_path=rule["savePath"],
                work_episode_key=specs["workEpisodeKey"],
                quality_policy_id=policy_id,
                quality_meta={"specs": specs, "eval": eval_res},
            )
            continue

        if decision == "insufficient_evidence":
            insufficient_skipped += 1
            store.record_rss_auto_item(
                rule_id=rule_id,
                item_key=key,
                torrent_hash=extract_magnet_hash(entry.get("downloadUrl") or ""),
                title=title,
                download_url="",
                status="insufficient_evidence",
                error_text=eval_res["reason"],
                qb_category=rule["category"],
                qb_save_path=rule["savePath"],
                work_episode_key=specs["workEpisodeKey"],
                quality_policy_id=policy_id,
                quality_meta={"specs": specs, "eval": eval_res},
            )
            continue

        grouped_qualified.setdefault(specs["workEpisodeKey"], []).append(cand)

    # Step 2: Same-work, same-season, same-episode multi-version selection (different works/episodes NEVER compete!)
    qualified_winners: list[dict[str, Any]] = list(passthrough_candidates)
    for work_ep_key, group in grouped_qualified.items():
        winner = group[0]
        for cand in group[1:]:
            cmp_res = compare_quality_candidates(cand, winner, policy_id)
            if cmp_res["winner"] == "a":
                winner = cand

        for cand in group:
            if cand is winner:
                continue
            cmp_res = compare_quality_candidates(winner, cand, policy_id)
            quality_skipped += 1
            reason = (
                f"同作品同集（{winner['specs']['scopeLabel']}）择优落选，"
                f"优先选择「{winner['title'][:64]}」：{cmp_res['reason']}"
            )
            store.record_rss_auto_item(
                rule_id=rule_id,
                item_key=cand["key"],
                torrent_hash=extract_magnet_hash(cand["entry"].get("downloadUrl") or ""),
                title=cand["title"],
                download_url="",
                status="quality_outperformed",
                error_text=reason,
                qb_category=rule["category"],
                qb_save_path=rule["savePath"],
                work_episode_key=work_ep_key,
                quality_policy_id=policy_id,
                quality_meta={"specs": cand["specs"], "eval": cand["eval"]},
            )

        # Step 3: Check against already-confirmed versions for the SAME work & episode
        # Never download a lower-tier or non-significant-upgrade version when an acceptable/better one exists!
        prior_rows = store.list_confirmed_rss_versions_for_scope(rule_id, work_ep_key)
        blocked_by_existing = False
        for prior in prior_rows:
            p_meta = prior.get("qualityMeta") if isinstance(prior.get("qualityMeta"), dict) else {}
            p_specs = p_meta.get("specs") if isinstance(p_meta.get("specs"), dict) else parse_release_quality_specs(title=prior["title"])
            p_eval = p_meta.get("eval") if isinstance(p_meta.get("eval"), dict) else evaluate_candidate_quality(p_specs, policy_id)
            cmp_res = compare_quality_candidates(winner, {"specs": p_specs, "eval": p_eval}, policy_id)
            if cmp_res["winner"] != "a" or not cmp_res["significantUpgrade"]:
                blocked_by_existing = True
                quality_skipped += 1
                reason = (
                    f"同作品同集（{winner['specs']['scopeLabel']}）已有合格/更优版本「{prior['title'][:64]}」，"
                    f"当前版本无明显提升或档位偏低（{cmp_res['reason']}），已跳过"
                )
                store.record_rss_auto_item(
                    rule_id=rule_id,
                    item_key=winner["key"],
                    torrent_hash=extract_magnet_hash(winner["entry"].get("downloadUrl") or ""),
                    title=winner["title"],
                    download_url="",
                    status="quality_existing_sufficient",
                    error_text=reason,
                    qb_category=rule["category"],
                    qb_save_path=rule["savePath"],
                    work_episode_key=work_ep_key,
                    quality_policy_id=policy_id,
                    quality_meta={"specs": winner["specs"], "eval": winner["eval"]},
                )
                break

        if not blocked_by_existing:
            qualified_winners.append(winner)

    # Step 4: Hash dedup + Push qualified winners to qB + Read-back verify
    for cand in qualified_winners:
        entry = cand["entry"]
        key = cand["key"]
        title = cand["title"]
        specs = cand["specs"]
        eval_res = cand["eval"]
        dl_url = str(entry.get("downloadUrl") or "").strip()
        if not dl_url:
            failed += 1
            last_err = "RSS 条目缺少下载链接"
            store.record_rss_auto_item(
                rule_id=rule_id, item_key=key, torrent_hash="", title=title, download_url="",
                status="failed", error_text=last_err, qb_category=rule["category"], qb_save_path=rule["savePath"],
                work_episode_key=specs["workEpisodeKey"], quality_policy_id=policy_id,
                quality_meta={"specs": specs, "eval": eval_res},
            )
            continue

        torrent_bytes: bytes | None = None
        info_hash = extract_magnet_hash(dl_url)
        if not dl_url.lower().startswith("magnet:"):
            try:
                torrent_bytes = download_torrent(dl_url)
                info_hash = extract_torrent_info_hash(torrent_bytes)
            except Exception as exc:
                failed += 1
                last_err = f"下载种子失败：{sanitize_text(exc)}"
                store.record_rss_auto_item(
                    rule_id=rule_id, item_key=key, torrent_hash="", title=title, download_url="",
                    status="failed", error_text=last_err, qb_category=rule["category"], qb_save_path=rule["savePath"],
                    work_episode_key=specs["workEpisodeKey"], quality_policy_id=policy_id,
                    quality_meta={"specs": specs, "eval": eval_res},
                )
                continue

        # Dedup by torrent info_hash against BOTH live qB tasks and previously processed records!
        if info_hash and (info_hash in existing_qb_hashes or store.has_rss_auto_hash(info_hash)):
            skipped += 1
            dup_status = "duplicate_in_qb" if info_hash in existing_qb_hashes else "duplicate_seen"
            dup_msg = "qB 中已存在相同 Hash 任务，已跳过" if dup_status == "duplicate_in_qb" else "该 Hash 已处理过，已跳过"
            store.record_rss_auto_item(
                rule_id=rule_id, item_key=key, torrent_hash=info_hash, title=title, download_url="",
                status=dup_status, error_text=dup_msg, qb_category=rule["category"], qb_save_path=rule["savePath"],
                work_episode_key=specs["workEpisodeKey"], quality_policy_id=policy_id,
                quality_meta={"specs": specs, "eval": eval_res},
            )
            continue

        # Submit to qBittorrent with configured save_path & category
        try:
            qb.add_torrent(
                torrent_bytes=torrent_bytes,
                torrent_url="" if torrent_bytes else dl_url,
                filename=f"{info_hash or key}.torrent",
                save_path=rule["savePath"],
                category=rule["category"],
                tags=f"fluxa-rss,{rule['officialGroup']}" if rule["officialGroup"] else "fluxa-rss",
            )
        except Exception as exc:
            failed += 1
            last_err = sanitize_text(exc)
            store.record_rss_auto_item(
                rule_id=rule_id, item_key=key, torrent_hash=info_hash, title=title, download_url="",
                status="failed", error_text=last_err, qb_category=rule["category"], qb_save_path=rule["savePath"],
                work_episode_key=specs["workEpisodeKey"], quality_policy_id=policy_id,
                quality_meta={"specs": specs, "eval": eval_res},
            )
            continue

        # Read back from qBittorrent to verify the task actually exists!
        confirmed = False
        if info_hash:
            for attempt in range(2):
                if attempt > 0:
                    sleep_fn(0.8)
                found = qb.get_torrent_by_hash(info_hash)
                if found:
                    confirmed = True
                    existing_qb_hashes.add(info_hash)
                    break

        if confirmed:
            added += 1
            reason_note = eval_res["reason"] if policy_id != "none" else ""
            store.record_rss_auto_item(
                rule_id=rule_id, item_key=key, torrent_hash=info_hash, title=title, download_url="",
                status="confirmed_in_qb", error_text=reason_note, qb_category=rule["category"], qb_save_path=rule["savePath"],
                work_episode_key=specs["workEpisodeKey"], quality_policy_id=policy_id,
                quality_meta={"specs": specs, "eval": eval_res},
            )
            policy_label = get_quality_policy(policy_id)["shortName"]
            store.add_audit(
                action_type="rss_auto_add_qb",
                target_service="qbittorrent",
                target_id=info_hash,
                summary=f"[{rule['siteName']}·{rule['officialGroup']}·{policy_label}] 已核验入 qB：{title[:80]}",
                status="success",
            )
        else:
            unconfirmed += 1
            store.record_rss_auto_item(
                rule_id=rule_id, item_key=key, torrent_hash=info_hash, title=title, download_url="",
                status="submitted_unconfirmed",
                error_text="已提交至 qB，但读回暂未查到该 Hash 任务（请检查 qB 是否已接收或重复）",
                qb_category=rule["category"], qb_save_path=rule["savePath"],
                work_episode_key=specs["workEpisodeKey"], quality_policy_id=policy_id,
                quality_meta={"specs": specs, "eval": eval_res},
            )

    if not new_entries:
        msg = "检查完成，暂无新条目"
        status_code = "ok"
    elif failed > 0 and added == 0:
        msg = f"检查完成：{failed} 条添加失败（{last_err}）"
        status_code = "error"
    else:
        parts = []
        if added:
            parts.append(f"已确认入 qB {added} 条")
        if unconfirmed:
            parts.append(f"待确认 {unconfirmed} 条")
        if quality_skipped:
            parts.append(f"质量策略跳过 {quality_skipped} 条")
        if insufficient_skipped:
            parts.append(f"依据不足跳过 {insufficient_skipped} 条")
        if skipped:
            parts.append(f"Hash 去重跳过 {skipped} 条")
        if failed:
            parts.append(f"失败 {failed} 条")
        msg = "检查完成：" + "，".join(parts)
        status_code = "warn" if (failed or unconfirmed) else "ok"

    updated = store.update_rss_auto_rule_status(
        rule_id,
        baseline_initialized=True,
        last_checked_at=checked_at,
        last_status=status_code,
        last_message=msg,
        added_delta=added,
    )
    return {
        "ok": status_code != "error",
        "rule": updated,
        "message": msg,
        "added": added,
        "unconfirmed": unconfirmed,
        "skipped": skipped,
        "qualitySkipped": quality_skipped,
        "insufficientSkipped": insufficient_skipped,
        "failed": failed,
    }


class RssAutoPoller:
    """Lightweight single-flight background poller for enabled site+official-group RSS rules."""

    def __init__(self, store: FluxaStore):
        self.store = store
        self._lock = threading.Lock()
        self._started = False

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        t = threading.Thread(target=self._loop, name="fluxa-rss-auto-poller", daemon=True)
        t.start()

    def _loop(self) -> None:
        while True:
            time.sleep(30)
            self.tick_once()

    def tick_once(self) -> None:
        if not self._lock.acquire(blocking=False):
            return
        try:
            now = _now_dt()
            settings = self.store.get_settings()
            for rule in self.store.list_rss_auto_rules():
                if not rule["enabled"]:
                    continue
                due = True
                if rule["lastCheckedAt"]:
                    try:
                        last_dt = datetime.fromisoformat(rule["lastCheckedAt"].replace("Z", "+00:00"))
                        if last_dt.tzinfo is None:
                            last_dt = last_dt.replace(tzinfo=BEIJING_TZ)
                        if (now - last_dt).total_seconds() < rule["pollIntervalMinutes"] * 60:
                            due = False
                    except ValueError:
                        due = True
                if due:
                    try:
                        run_rss_auto_rule(rule["id"], store=self.store, settings=settings)
                    except Exception:
                        pass
        finally:
            self._lock.release()
