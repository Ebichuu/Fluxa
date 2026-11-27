from __future__ import annotations

import hashlib
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.adapters.emby import EmbyAdapter
from app.adapters.qbittorrent import QbittorrentAdapter
from app.domain.actions import execute_bounded_action, preview_action_impact
from app.domain.calendar import build_following_calendar
from app.domain.pipeline import build_media_pipeline
from app.domain.rss_auto_download import extract_torrent_info_hash, run_rss_auto_rule
from app.main import create_app
from app.storage import FluxaStore


def _make_torrent(name: str) -> tuple[bytes, str]:
    nb = name.encode("utf-8")
    info = b"d6:lengthi1024e4:name" + str(len(nb)).encode() + b":" + nb + b"12:piece lengthi16384e6:pieces20:" + (b"0" * 20) + b"e"
    raw = b"d8:announce15:http://pt.local4:info" + info + b"e"
    return raw, hashlib.sha1(info).hexdigest().lower()


class AuditSecurityAndAccuracyTests(unittest.TestCase):
    # -- 1. qB single-task pause/resume boundary (NEVER hash=all or multi-hash) --

    def test_1_qb_pause_rejects_hash_all_multi_hash_and_unknown_hash(self):
        h_valid = "a" * 40
        h_seed = "b" * 40

        class _FakeQb:
            configured = True

            def __init__(self):
                self.calls = []
                self.torrents = [
                    {"hash": h_valid, "name": "Movie.A", "status": "downloading", "statusLabel": "下载中", "savePath": "/dl", "isSeeding": False},
                    {"hash": h_seed, "name": "Movie.Seed", "status": "seeding", "statusLabel": "做种中", "savePath": "/dl", "isSeeding": True},
                ]

            def list_torrents(self):
                return list(self.torrents)

            def get_torrent_by_hash(self, h: str):
                return next((t for t in self.torrents if t["hash"] == h), None)

            def pause_or_resume(self, h: str, action: str):
                self.calls.append((h, action))
                return {"ok": True, "message": f"paused {h}"}

        with tempfile.TemporaryDirectory() as tmp:
            store = FluxaStore(Path(tmp) / "test.sqlite3")
            settings = store.get_settings()
            qb = _FakeQb()
            adapters = {"qbittorrent": qb}

            # hash=all MUST be denied in preview AND blocked in execute!
            prev_all = preview_action_impact(action_type="qb_pause", payload={"hash": "all"}, settings=settings, adapters=adapters)
            self.assertFalse(prev_all["allowed"])
            with self.assertRaises(ValueError):
                execute_bounded_action(action_type="qb_pause", payload={"hash": "all"}, confirmed=True, settings=settings, store=store, adapters=adapters)

            # Multi-hash 'a|b' MUST be blocked!
            with self.assertRaises(ValueError):
                execute_bounded_action(action_type="qb_pause", payload={"hash": f"{h_valid}|{h_seed}"}, confirmed=True, settings=settings, store=store, adapters=adapters)

            # Non-existent single hex hash MUST be blocked!
            with self.assertRaises(ValueError):
                execute_bounded_action(action_type="qb_pause", payload={"hash": "c" * 40}, confirmed=True, settings=settings, store=store, adapters=adapters)
            self.assertEqual(len(qb.calls), 0)

            # Real adapter layer ALSO rejects hash=all directly!
            real_qb = QbittorrentAdapter({"baseUrl": "http://qb.local"})
            with self.assertRaises(ValueError):
                real_qb.pause_or_resume("all", "pause")
            with self.assertRaises(ValueError):
                real_qb.pause_or_resume(f"{h_valid}|{h_seed}", "resume")

            # Existing seeding hash requires explicit confirmation; non-seeding valid single hash succeeds!
            res_seed = execute_bounded_action(action_type="qb_pause", payload={"hash": h_seed}, confirmed=False, settings=settings, store=store, adapters=adapters)
            self.assertTrue(res_seed.get("requiresConfirm"))
            res_ok = execute_bounded_action(action_type="qb_pause", payload={"hash": h_valid}, confirmed=False, settings=settings, store=store, adapters=adapters)
            self.assertTrue(res_ok["ok"])
            self.assertEqual(qb.calls, [(h_valid, "pause")])

    # -- 2. Upstream subscribe business rejection & read-back verification --

    def test_2_upstream_subscribe_rejects_business_error_and_unconfirmed_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(db_path=Path(tmp) / "test.sqlite3")
            client = app.test_client()
            client.post("/api/auth/setup", json={"username": "admin", "password": "StrongPassword123"})

            class _NoopTmdb:
                configured = False
                def get_details(self, *a, **k): return None

            class _FakeUpstream:
                configured = True

                def __init__(self, add_resp, subs_after):
                    self.add_resp = add_resp
                    self.subs_after = subs_after

                def add_subscription(self, **kwargs):
                    return self.add_resp

                def list_subscriptions(self):
                    return list(self.subs_after)

            payload = {
                "mediaType": "movie",
                "tmdbId": "555001",
                "title": "Test New Movie",
                "year": "2026",
                "intent": "follow",
                "target": "moviepilot",
            }

            # Case A: Neo returns ok=False (business rejection) -> MUST return 502 & log failed (NOT subscribed!)
            mp_reject = _FakeUpstream({"ok": False, "message": "规则拒绝：无匹配官组"}, [])
            with patch("app.routes.build_adapters", return_value={"moviepilot": mp_reject, "torra": mp_reject, "tmdb": _NoopTmdb()}):
                r_rej = client.post("/api/subscribe", json=payload)
                self.assertEqual(r_rej.status_code, 502)
                self.assertFalse(r_rej.get_json()["ok"])
                self.assertIn("规则拒绝", r_rej.get_json()["error"])

            # Case B: Torra returns ok=True, BUT read-back list_subscriptions() does NOT contain it -> MUST return 502 unconfirmed!
            torra_ghost = _FakeUpstream({"ok": True, "message": "ok"}, [])
            with patch("app.routes.build_adapters", return_value={"moviepilot": torra_ghost, "torra": torra_ghost, "tmdb": _NoopTmdb()}):
                r_ghost = client.post("/api/subscribe", json={**payload, "target": "torra"})
                self.assertEqual(r_ghost.status_code, 502)
                self.assertFalse(r_ghost.get_json()["ok"])
                self.assertTrue(r_ghost.get_json()["unconfirmed"])

            # Verify neither Case A nor Case B recorded outcome='subscribed'
            logs = app.config["FLUXA_STORE"].list_decision_logs(media_key="movie:555001")
            self.assertTrue(all(l["outcome"] != "subscribed" for l in logs))

            # Case C: Neo returns ok=True AND read-back confirms subscription exists -> 200 verified=True!
            mp_ok = _FakeUpstream(
                {"ok": True, "message": "已添加订阅"},
                [{"mediaType": "movie", "tmdbId": "555001", "seasonNumber": None}],
            )
            with patch("app.routes.build_adapters", return_value={"moviepilot": mp_ok, "torra": mp_ok, "tmdb": _NoopTmdb()}):
                r_ok = client.post("/api/subscribe", json=payload)
                self.assertEqual(r_ok.status_code, 200)
                self.assertTrue(r_ok.get_json()["ok"])
                self.assertTrue(r_ok.get_json()["verified"])

    # -- 3. Cross-work isolation: Alien vs Alien 2 + unrelated 115 upload --

    def test_3_cross_work_isolation_alien_vs_alien2_and_other_upload(self):
        class _FakeTmdb:
            configured = True
            def get_details(self, mt, tid):
                if mt == "movie" and tid == "348":
                    return {"mediaKey": "movie:348", "mediaType": "movie", "tmdbId": "348", "title": "Alien", "originalTitle": "Alien", "alternativeTitles": ["异形"], "year": "1979", "posterUrl": "", "overview": ""}
                return None

        class _Noop:
            configured = False
            def app_url(self, *a, **k): return ""

        with tempfile.TemporaryDirectory() as tmp:
            store = FluxaStore(Path(tmp) / "test.sqlite3")
            snapshot = {
                "generatedAt": "2026-11-26T23:00:00+08:00",
                "services": [],
                "serviceMap": {
                    "moviepilot": {"connected": True},
                    "torra": {"connected": True},
                    "qbittorrent": {"connected": True},
                    "symedia": {"connected": True},
                    "emby": {"connected": True},
                },
                "mpSubs": [],
                "torraSubs": [],
                "mpHistory": [
                    {"mediaKey": "movie:679", "title": "Alien 2", "torrentName": "Alien.2.1986.1080p.BluRay-Group", "downloadHash": "1" * 40},
                    {"mediaKey": "", "title": "Alien 2", "torrentName": "Alien.2.1986.1080p.WEB-DL-Group", "downloadHash": "2" * 40},
                ],
                "qbTasks": [
                    {"hash": "1" * 40, "name": "Alien.2.1986.1080p.BluRay-Group", "status": "downloading", "progressPercent": 50},
                    {"hash": "2" * 40, "name": "Alien.2.1986.1080p.WEB-DL-Group", "status": "downloading", "progressPercent": 40},
                    {"hash": "3" * 40, "name": "Alien.Romulus.2024.2160p.WEB-DL-Group", "status": "seeding", "progressPercent": 100},
                ],
                "torraSecupload": {
                    "available": True,
                    "activeRuns": [{"runId": "r1", "taskKey": "anime_frieren", "targetItemId": "", "status": "running", "message": "Uploading Frieren S01E05"}],
                    "successFiles": [{"fileName": "Alien.2.1986.1080p.BluRay-Group.mkv"}],
                    "failedFiles": [],
                    "webUrl": "",
                },
                "saHistory": [
                    {"id": "10", "mediaKey": "movie:679", "title": "Alien 2", "fileName": "Alien.2.1986.1080p.BluRay-Group.mkv", "outcome": "archived", "outcomeLabel": "归档成功", "reasonText": "ok"},
                    {"id": "11", "mediaKey": "", "title": "Alien 2", "fileName": "Alien.2.1986.1080p.WEB-DL-Group.mkv", "outcome": "archived", "outcomeLabel": "归档成功", "reasonText": "ok"},
                ],
                "emby": {"available": True, "movies": {}, "series": {}, "episodes": {}, "truncated": False},
            }
            adapters = {"tmdb": _FakeTmdb(), "qbittorrent": _Noop(), "torra": _Noop(), "symedia": _Noop(), "emby": _Noop(), "moviepilot": _Noop()}
            pipe = build_media_pipeline(media_type="movie", tmdb_id="348", snapshot=snapshot, adapters=adapters, store=store)
            steps = {s["step"]: s for s in pipe["steps"]}

            self.assertEqual(steps["subscription"]["recentSelections"], [])
            self.assertEqual(steps["qbittorrent"]["tasks"], [])
            self.assertEqual(steps["symedia"]["records"], [])
            # Unrelated work uploading MUST NOT mark Alien as 'active' (上传中)!
            self.assertEqual(steps["cloud115"]["status"], "unconfirmed")
            self.assertEqual(steps["cloud115"]["successFiles"], [])

            # Genuine Alien (1979) task MUST match cleanly
            snapshot["qbTasks"].append({"hash": "4" * 40, "name": "Alien.1979.1080p.BluRay.x264-ADWeb", "status": "seeding", "progressPercent": 100})
            pipe2 = build_media_pipeline(media_type="movie", tmdb_id="348", snapshot=snapshot, adapters=adapters, store=store)
            steps2 = {s["step"]: s for s in pipe2["steps"]}
            self.assertEqual(len(steps2["qbittorrent"]["tasks"]), 1)
            self.assertEqual(steps2["qbittorrent"]["tasks"][0]["hash"], "4" * 40)

    # -- 4. RSS failed & submitted_unconfirmed rechecked & unresolved alert preserved --

    def test_4_rss_failed_and_unconfirmed_tracked_and_rechecked_next_cycle(self):
        class _SwitchQb:
            configured = True

            def __init__(self):
                self.tasks = {}
                self.readback_ready = False
                self.added = []

            def list_torrents(self):
                return list(self.tasks.values())

            def add_torrent(self, **kwargs):
                self.added.append(kwargs)
                h = extract_torrent_info_hash(kwargs["torrent_bytes"])
                if self.readback_ready and h:
                    self.tasks[h] = {"hash": h, "name": kwargs.get("filename", "")}
                return {"ok": True}

            def get_torrent_by_hash(self, h: str):
                return self.tasks.get(h.lower())

        with tempfile.TemporaryDirectory() as tmp:
            store = FluxaStore(Path(tmp) / "test.sqlite3")
            settings = store.get_settings()
            rule = store.save_rss_auto_rule({
                "id": "rule-track",
                "siteId": "audiences",
                "siteName": "观众",
                "officialGroup": "ADWeb",
                "qualityPolicyId": "none",
                "url": "https://pt.local/rss",
                "baselineInitialized": True,
                "enabled": True,
            })

            t_unconf, h_unconf = _make_torrent("Unconfirmed.S01E01-ADWeb.mkv")
            feed = [
                {"title": "Unconfirmed.S01E01-ADWeb", "downloadUrl": "https://pt/u.torrent", "detailUrl": "https://pt/u"},
                {"title": "Broken.S01E02-ADWeb", "downloadUrl": "https://pt/broken.torrent", "detailUrl": "https://pt/b"},
            ]

            def _dl(u):
                if "broken" in u:
                    raise RuntimeError("503 upstream timeout")
                return t_unconf

            qb = _SwitchQb()
            # Cycle 1: u -> submitted_unconfirmed, broken -> failed
            r1 = run_rss_auto_rule(rule["id"], store=store, settings=settings, adapters={"qbittorrent": qb}, feed_fetcher=lambda *_: feed, torrent_downloader=_dl, sleep_fn=lambda _: None)
            self.assertEqual(r1["unconfirmed"], 1)
            self.assertEqual(r1["failed"], 1)
            self.assertEqual(r1["rule"]["lastStatus"], "error")

            # Cycle 2: u now appears in qB -> reconfirmed! broken retries once and fails again -> alert MUST remain!
            qb.tasks[h_unconf] = {"hash": h_unconf, "name": "Unconfirmed"}
            r2 = run_rss_auto_rule(rule["id"], store=store, settings=settings, adapters={"qbittorrent": qb}, feed_fetcher=lambda *_: feed, torrent_downloader=_dl, sleep_fn=lambda _: None)
            self.assertEqual(r2["reconfirmed"], 1)
            self.assertEqual(r2["added"], 1)
            self.assertEqual(r2["unresolvedFailed"], 1)
            self.assertEqual(r2["unresolvedUnconfirmed"], 0)
            self.assertEqual(r2["rule"]["lastStatus"], "error")
            self.assertIn("未解决失败", r2["message"])
            self.assertEqual(r2["rule"]["addedCount"], 1)

            # Cycle 3: no new entries, retries exhausted -> status MUST STILL stay error (NEVER silently 'ok / 暂无新条目'!)
            r3 = run_rss_auto_rule(rule["id"], store=store, settings=settings, adapters={"qbittorrent": qb}, feed_fetcher=lambda *_: feed, torrent_downloader=_dl, sleep_fn=lambda _: None)
            self.assertEqual(r3["rule"]["lastStatus"], "error")
            self.assertIn("未解决失败", r3["message"])

    # -- 5. Concurrent manual check + poller NEVER double-submits or double-counts --

    def test_5_rss_concurrent_runs_never_double_submit_or_double_count(self):
        class _SlowQb:
            configured = True

            def __init__(self):
                self.tasks = {}
                self.added = []
                self._lock = threading.Lock()

            def list_torrents(self):
                return list(self.tasks.values())

            def add_torrent(self, **kwargs):
                time.sleep(0.15)
                h = extract_torrent_info_hash(kwargs["torrent_bytes"])
                with self._lock:
                    self.added.append(h)
                    self.tasks[h] = {"hash": h, "name": kwargs.get("filename", "")}
                return {"ok": True}

            def get_torrent_by_hash(self, h: str):
                with self._lock:
                    return self.tasks.get(h.lower())

        with tempfile.TemporaryDirectory() as tmp:
            store = FluxaStore(Path(tmp) / "test.sqlite3")
            settings = store.get_settings()
            rule = store.save_rss_auto_rule({
                "id": "rule-concurrent",
                "siteId": "audiences",
                "siteName": "观众",
                "officialGroup": "ADWeb",
                "qualityPolicyId": "none",
                "url": "https://pt.local/rss",
                "baselineInitialized": True,
                "enabled": True,
            })
            t_raw, h_raw = _make_torrent("Concurrent.S01E01-ADWeb.mkv")
            feed = [{"title": "Concurrent.S01E01-ADWeb", "downloadUrl": "https://pt/c.torrent", "detailUrl": "https://pt/c"}]
            qb = _SlowQb()
            results = []
            barrier = threading.Barrier(2)

            def _worker():
                barrier.wait()
                res = run_rss_auto_rule(
                    rule["id"],
                    store=store,
                    settings=settings,
                    adapters={"qbittorrent": qb},
                    feed_fetcher=lambda *_: feed,
                    torrent_downloader=lambda _: t_raw,
                    sleep_fn=lambda _: None,
                )
                results.append(res)

            t1 = threading.Thread(target=_worker)
            t2 = threading.Thread(target=_worker)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            self.assertEqual(len(qb.added), 1)
            self.assertEqual(sum(r["added"] for r in results), 1)
            self.assertEqual(sum(1 for r in results if r.get("busy")), 1)
            self.assertEqual(store.get_rss_auto_rule(rule["id"])["addedCount"], 1)

            # Direct cross-process SQLite claim guard also blocks duplicate hash claim
            c1, _ = store.claim_rss_auto_item_for_submit(rule_id=rule["id"], item_key="other-key", torrent_hash=h_raw, title="dup")
            self.assertFalse(c1)

    # -- 6. Emby pagination completeness + Calendar truncation honesty --

    def test_6_emby_pagination_and_calendar_truncation_honesty(self):
        class _PagedEmbyResp:
            def __init__(self, body):
                self.status_code = 200
                self.content = b"1"
                self._body = body
            def json(self):
                return self._body

        # 3 movies/series across 2 pages (page_size=2): parent Series 'ser-late' sits on PAGE 2!
        # 3 episodes across 2 pages (page_size=2) belonging to 'ser-late'!
        parent_items = [
            {"Id": "m1", "Type": "Movie", "Name": "M1", "ProviderIds": {"Tmdb": "101"}, "Path": "/m1.mkv", "DateCreated": "2026-11-20"},
            {"Id": "m2", "Type": "Movie", "Name": "M2", "ProviderIds": {"Tmdb": "102"}, "Path": "/m2.mkv", "DateCreated": "2026-11-19"},
            {"Id": "ser-late", "Type": "Series", "Name": "LongShow", "ProviderIds": {"Tmdb": "205493"}, "Path": "/s", "DateCreated": "2022-01-01"},
        ]
        episode_items = [
            {"Id": "e1", "Type": "Episode", "SeriesId": "ser-late", "SeriesName": "LongShow", "ParentIndexNumber": 3, "IndexNumber": 8, "Path": "/s/S03E08.strm", "DateCreated": "2026-11-19"},
            {"Id": "e2", "Type": "Episode", "SeriesId": "ser-late", "SeriesName": "LongShow", "ParentIndexNumber": 3, "IndexNumber": 9, "Path": "/s/S03E09.strm", "DateCreated": "2026-11-26"},
            {"Id": "e3", "Type": "Episode", "SeriesId": "ser-late", "SeriesName": "LongShow", "ParentIndexNumber": 3, "IndexNumber": 10, "Path": "/s/S03E10.strm", "DateCreated": "2026-11-26"},
        ]

        class _PagedSession:
            def get(self, url, **kwargs):
                from urllib.parse import parse_qs, urlsplit
                qs = parse_qs(urlsplit(url).query)
                itypes = (qs.get("IncludeItemTypes") or [""])[0]
                start = int((qs.get("StartIndex") or ["0"])[0])
                limit = int((qs.get("Limit") or ["2000"])[0])
                src = parent_items if "Series" in itypes else episode_items
                return _PagedEmbyResp({"Items": src[start : start + limit], "TotalRecordCount": len(src)})

        emby = EmbyAdapter({"baseUrl": "http://emby.local", "apiKey": "k", "userId": "u1"}, session=_PagedSession())
        snap = emby.fetch_library_snapshot(page_size=2)
        self.assertTrue(snap["available"])
        self.assertTrue(snap["complete"])
        self.assertFalse(snap["truncated"])
        self.assertEqual(snap["orphanEpisodeCount"], 0)
        self.assertEqual(snap["episodes"]["205493"], [(3, 8), (3, 9), (3, 10)])

        # Truncation flag when max_episodes cap is hit
        snap_trunc = emby.fetch_library_snapshot(page_size=2, max_episodes=2)
        self.assertTrue(snap_trunc["truncated"])
        self.assertIn("未完整覆盖", snap_trunc["truncationWarning"])

        # Calendar truncation honesty when subscribed TV shows exceed max_series
        class _FakeCalTmdb:
            configured = True
            def get_season_episodes(self, tid, snum):
                return []

        cal_snap = {
            "mpSubs": [
                {"source": "moviepilot", "sourceLabel": "MP", "subscriptionId": str(i), "mediaKey": f"tv:{1000+i}", "mediaType": "tv", "tmdbId": str(1000+i), "title": f"Show {i}", "seasons": [1], "seasonNumber": 1}
                for i in range(4)
            ],
            "torraSubs": [],
            "qbTasks": [],
            "saHistory": [],
            "emby": snap_trunc,
        }
        cal = build_following_calendar(snapshot=cal_snap, tmdb_adapter=_FakeCalTmdb(), prefs={}, max_series=2)
        self.assertEqual(cal["totalTvSubscriptions"], 4)
        self.assertEqual(cal["checkedTvSubscriptions"], 2)
        self.assertTrue(cal["truncated"])
        self.assertEqual(cal["omittedCount"], 2)
        self.assertIn("2/4", cal["truncationWarning"])
        self.assertTrue(cal["embyTruncated"])

    def test_7_sqlite_snapshot_cache_and_auto_ignore_covered_cr_failure(self):
        """验证 SQLite 快照缓存（秒开不重复打上游）+ 同集已有 CR 版时首页异常待办自动默认忽略 LINETV 失败。"""
        from app.domain.pipeline import CachedTmdbProxy, SnapshotCoordinator, build_actionable_issues

        with tempfile.TemporaryDirectory() as tmp:
            store = FluxaStore(Path(tmp) / "test.sqlite3")
            # 1. Persistent SQLite snapshot cache returns immediately without hitting upstream again
            cached_snap = {
                "generatedAt": "2026-11-27T00:00:00+08:00",
                "services": [],
                "serviceMap": {},
                "mpSubs": [],
                "mpHistory": [],
                "torraSubs": [],
                "torraSecupload": {},
                "qbTasks": [],
                "saHistory": [],
                "emby": {"available": True, "movies": {}, "series": {}, "episodes": {}, "recentToday": []},
            }
            store.set_cache("snapshot:upstream", cached_snap, updated_iso=cached_snap["generatedAt"])
            coord = SnapshotCoordinator(store, fresh_ttl=300.0)
            snap, meta = coord.get_snapshot(force_refresh=False)
            self.assertTrue(meta["fromCache"])
            self.assertFalse(meta["stale"])
            self.assertEqual(snap["generatedAt"], "2026-11-27T00:00:00+08:00")

            # 2. CachedTmdbProxy caches season episodes in SQLite (only calls upstream ONCE)
            class _CountingTmdb:
                configured = True
                def __init__(self):
                    self.calls = 0
                def get_season_episodes(self, tid, snum):
                    self.calls += 1
                    return [{"seasonNumber": snum, "episodeNumber": 9, "episodeLabel": f"S{snum:02d}E09", "name": "Ep9", "airDate": "2026-11-26"}]

            raw_tmdb = _CountingTmdb()
            proxy = CachedTmdbProxy(raw_tmdb, store)
            self.assertEqual(len(proxy.get_season_episodes("205493", 3)), 1)
            self.assertEqual(len(proxy.get_season_episodes("205493", 3)), 1)
            self.assertEqual(raw_tmdb.calls, 1)

            # 3. Auto-ignore SA identify_failed LINETV item when CR version already exists for 205493 S03E09!
            linetv_file = "Shin.Tennis.no.Oujisama.U.17.World.Cup.Kesshou.Member.Ketteisen.S01E09.2026.1080p.LINETV.WEB-DL.H264.AAC-ADWeb.mkv"
            cr_file = "The.Prince.of.Tennis.II.S03E09.2026.1080p.CR.WEB-DL.H264.AAC-ADWeb.mkv"
            store.save_sa_mapping(
                arc_key="shintenshinooujisama_test",
                arc_title="Shin Tennis no Oujisama U 17 World Cup Kesshou Member Ketteisen",
                source_season=1,
                media_type="tv",
                tmdb_id="205493",
                canonical_title="新网球王子 U-17世界杯篇",
                target_season=3,
            )
            store.set_cache("sa_covered:88638", {
                "autoIgnoreEligible": True,
                "noRecoveryNeeded": True,
                "mediaType": "tv",
                "tmdbId": "205493",
                "targetSeason": 3,
                "targetEpisode": 9,
                "existingSourceTag": "CR",
            })
            snap_with_cr = {
                **cached_snap,
                "emby": {"available": True, "movies": {}, "series": {}, "episodes": {"205493": [(3, 9)]}, "recentToday": []},
                "saHistory": [
                    {
                        "id": "88638",
                        "fileName": linetv_file,
                        "title": linetv_file,
                        "mediaType": "tv",
                        "mediaKey": "",
                        "seasons": "",
                        "episodes": "",
                        "outcome": "identify_failed",
                        "outcomeLabel": "SA 识别失败",
                        "reasonText": "没有查询到媒体信息",
                        "webUrl": "",
                    },
                    {
                        "id": "88910",
                        "fileName": cr_file,
                        "title": "新网球王子",
                        "mediaType": "tv",
                        "mediaKey": "tv:205493",
                        "tmdbId": "205493",
                        "seasons": "S03",
                        "episodes": "E09",
                        "seasonEpisode": "S03E09",
                        "dest": f"/emby/{cr_file}",
                        "outcome": "archived",
                        "outcomeLabel": "归档成功",
                        "reasonText": "已完成",
                        "webUrl": "",
                    },
                ],
            }
            issues = build_actionable_issues(snap_with_cr, {}, store=store)
            self.assertEqual(issues, [])

    # -- 8. Emby missing SeriesId targeted lookup (454815 -> 350389 -> TMDB 295790 -> S02E18) + cache + incomplete identity honesty --

    def test_8_emby_missing_series_targeted_lookup_cache_and_incomplete_identity_honesty(self):
        """回归 454815 → 350389 → TMDB 295790 → S02E18：
        1. Episode 的 SeriesId 未出现在 Series 列表时，定向查询剧集 ProviderIds 并持久化缓存；
        2. 下一次快照命中缓存不再重复查询；
        3. 若定向查询失败或缺少 TMDB ProviderId，绝不把映射失败当作库内不存在；即使快照标 complete=True 也不视作身份关联完整。
        """
        from app.adapters.emby import is_emby_identity_incomplete
        from app.domain.sa_recovery import detect_existing_version_for_target

        class _Resp:
            def __init__(self, body, status_code=200):
                self.status_code = status_code
                self.content = b"1"
                self._body = body
            def json(self):
                return self._body

        targeted_queries: list[str] = []

        class _MissingSeriesEmbySession:
            def __init__(self, *, fail_series_id: str = ""):
                self.fail_series_id = fail_series_id

            def get(self, url, **kwargs):
                from urllib.parse import parse_qs, urlsplit
                parsed = urlsplit(url)
                qs = parse_qs(parsed.query)
                # Targeted single-item lookup: /Users/u1/Items/350389
                if parsed.path.endswith("/Items/350389"):
                    targeted_queries.append("single:350389")
                    if self.fail_series_id == "350389":
                        return _Resp({"error": "upstream timeout"}, status_code=500)
                    return _Resp({
                        "Id": "350389",
                        "Type": "Series",
                        "Name": "回归测试剧集",
                        "ProviderIds": {"Tmdb": "295790"},
                        "DateCreated": "2026-10-01",
                    })
                if parsed.path.endswith("/Items/999001"):
                    targeted_queries.append("single:999001")
                    return _Resp({"error": "not found"}, status_code=404)

                # Batch targeted lookup: /Users/u1/Items?Ids=350389
                ids_param = (qs.get("Ids") or [""])[0]
                if ids_param:
                    targeted_queries.append(f"batch:{ids_param}")
                    wanted = [x for x in ids_param.split(",") if x]
                    items = []
                    for sid in wanted:
                        if sid == "350389" and self.fail_series_id != "350389":
                            items.append({
                                "Id": "350389",
                                "Type": "Series",
                                "Name": "回归测试剧集",
                                "ProviderIds": {"Tmdb": "295790"},
                                "DateCreated": "2026-10-01",
                            })
                    return _Resp({"Items": items, "TotalRecordCount": len(items)})

                itypes = (qs.get("IncludeItemTypes") or [""])[0]
                if "Series" in itypes:
                    # Deliberately OMIT Series 350389 from Movie,Series listing!
                    return _Resp({
                        "Items": [
                            {"Id": "m1", "Type": "Movie", "Name": "OtherMovie", "ProviderIds": {"Tmdb": "101"}, "Path": "/m1.mkv", "DateCreated": "2026-11-20"},
                        ],
                        "TotalRecordCount": 1,
                    })
                # Episode 454815 belongs to SeriesId 350389 (S02E18), NOT present in parent_rows!
                eps = [
                    {
                        "Id": "454815",
                        "Type": "Episode",
                        "SeriesId": "350389",
                        "SeriesName": "回归测试剧集",
                        "Name": "第 18 集",
                        "ParentIndexNumber": 2,
                        "IndexNumber": 18,
                        "Path": "/CloudNAS/CloudDrive/115/媒体库/S02E18.strm",
                        "DateCreated": "2026-11-27",
                    }
                ]
                if self.fail_series_id == "999001":
                    eps.append({
                        "Id": "454999",
                        "Type": "Episode",
                        "SeriesId": "999001",
                        "SeriesName": "BrokenSeries",
                        "Name": "第 1 集",
                        "ParentIndexNumber": 1,
                        "IndexNumber": 1,
                        "Path": "/CloudNAS/S01E01.strm",
                        "DateCreated": "2026-11-27",
                    })
                return _Resp({"Items": eps, "TotalRecordCount": len(eps)})

        with tempfile.TemporaryDirectory() as tmp:
            store = FluxaStore(Path(tmp) / "test.sqlite3")
            cfg = {"baseUrl": "http://emby.local", "apiKey": "k", "userId": "u1"}

            # 1. First snapshot: 454815 -> 350389 not in Series list -> targeted lookup resolves TMDB 295790 -> S02E18
            emby1 = EmbyAdapter(cfg, session=_MissingSeriesEmbySession(), store=store)
            snap1 = emby1.fetch_library_snapshot()
            self.assertTrue(snap1["available"])
            self.assertTrue(snap1["complete"])
            self.assertTrue(snap1["identityComplete"])
            self.assertFalse(is_emby_identity_incomplete(snap1))
            self.assertEqual(snap1["orphanEpisodeCount"], 0)
            self.assertEqual(snap1["resolvedMissingSeriesCount"], 1)
            self.assertEqual(snap1["episodes"]["295790"], [(2, 18)])
            self.assertIn("S02E18", snap1["episodeDetails"]["295790"])
            self.assertEqual(snap1["episodeDetails"]["295790"]["S02E18"][0]["itemId"], "454815")
            self.assertEqual(len(targeted_queries), 1)

            # 2. Second snapshot on a fresh EmbyAdapter instance hits SQLite cache -> ZERO extra targeted queries!
            targeted_queries.clear()
            emby2 = EmbyAdapter(cfg, session=_MissingSeriesEmbySession(), store=store)
            snap2 = emby2.fetch_library_snapshot()
            self.assertEqual(targeted_queries, [])
            self.assertEqual(snap2["episodes"]["295790"], [(2, 18)])
            self.assertTrue(snap2["identityComplete"])

            # 3. When targeted lookup FAILS (e.g. Series 999001 unreachable), NEVER treat mapping failure as "not in library"!
            store2 = FluxaStore(Path(tmp) / "test_fail.sqlite3")
            emby_fail = EmbyAdapter(cfg, session=_MissingSeriesEmbySession(fail_series_id="350389"), store=store2)
            snap_fail = emby_fail.fetch_library_snapshot()
            self.assertTrue(snap_fail["available"])
            self.assertTrue(snap_fail["paginationComplete"])
            self.assertFalse(snap_fail["identityComplete"])
            self.assertFalse(snap_fail["complete"])
            self.assertEqual(snap_fail["orphanEpisodeCount"], 1)
            self.assertIn("350389", snap_fail["failedSeriesLookupIds"])
            self.assertIn("350389", snap_fail["unresolvedSeriesIds"])
            self.assertIn("不能把映射失败当作库内不存在", snap_fail["identityWarning"])

            # 4. Downstream build_media_pipeline & detect_existing_version_for_target NEVER assert "absent" when
            #    identity mapping is incomplete — EVEN IF a snapshot falsely sets complete=True!
            class _FakeTmdb295790:
                configured = True
                def get_details(self, mt, tid):
                    return {"mediaKey": f"{mt}:{tid}", "mediaType": mt, "tmdbId": tid, "title": "回归测试剧集", "originalTitle": "", "alternativeTitles": [], "year": "2026", "posterUrl": "", "seasons": []}
                def get_season_episodes(self, tid, snum):
                    return []

            class _Idle:
                configured = False
                def app_url(self, *a, **kw):
                    return ""

            adapters = {
                "moviepilot": _Idle(),
                "torra": _Idle(),
                "qbittorrent": _Idle(),
                "symedia": _Idle(),
                "emby": emby_fail,
                "tmdb": _FakeTmdb295790(),
            }
            # Even with complete=True forced alongside orphanEpisodeCount=1 / failedSeriesLookupIds!
            legacy_complete_but_incomplete_identity = {
                **snap_fail,
                "complete": True,
                "truncated": False,
            }
            self.assertTrue(is_emby_identity_incomplete(legacy_complete_but_incomplete_identity))
            pipe_snap = {
                "generatedAt": "2026-11-27T20:00:00+08:00",
                "services": [],
                "serviceMap": {"emby": {"connected": True}},
                "mpSubs": [],
                "mpHistory": [],
                "torraSubs": [],
                "torraSecupload": {"available": False},
                "qbTasks": [],
                "saHistory": [],
                "emby": legacy_complete_but_incomplete_identity,
            }
            pipe = build_media_pipeline(media_type="tv", tmdb_id="295790", snapshot=pipe_snap, adapters=adapters, store=store2)
            emby_step = next(s for s in pipe["steps"] if s["step"] == "emby")
            self.assertEqual(emby_step["status"], "unconfirmed")
            self.assertIn("身份关联不完整", emby_step["statusLabel"])
            self.assertNotIn("尚未进入 Emby", emby_step["statusLabel"])

            # And SA existing-version check must NOT falsely claim "当前 Emby 库内未查到该集"
            ev = detect_existing_version_for_target(
                media_type="tv",
                tmdb_id="295790",
                target_season=2,
                target_episode=18,
                failed_file_name="Show.S02E18.1080p.Baha.WEB-DL.mp4",
                emby=legacy_complete_but_incomplete_identity,
                sa_history=[{
                    "id": "99001", "mediaType": "tv", "tmdbId": "295790",
                    "seasons": "S02", "episodes": "E18", "seasonEpisode": "S02E18",
                    "src": "/00-待整理/Show.S02E18.LINETV.mkv", "dest": "/emby/S02E18.strm",
                    "fileName": "Show.S02E18.LINETV.mkv", "outcome": "archived",
                }],
            )
            self.assertFalse(ev["confirmedInLibrary"])
            self.assertFalse(ev["autoIgnoreEligible"])
            self.assertTrue(ev["embyIdentityIncomplete"])
            self.assertIn("映射失败", ev["summary"])
            self.assertNotIn("库内未查到", ev["summary"])


if __name__ == "__main__":
    unittest.main()
