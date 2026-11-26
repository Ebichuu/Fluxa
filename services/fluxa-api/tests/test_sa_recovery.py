from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.adapters.symedia import SymediaAdapter, classify_symedia_record
from app.domain.pipeline import build_actionable_issues, partition_actionable_issues
from app.domain.sa_recovery import (
    build_spec_preserving_filename,
    check_sequel_identity_conflict,
    diagnose_sa_failure,
    execute_sa_recovery,
    parse_media_filename,
    verify_sa_recovery,
)
from app.storage import FluxaStore

BEIJING_TZ = timezone(timedelta(hours=8))

E09_FILE = "Shin.Tennis.no.Oujisama.U.17.World.Cup.Kesshou.Member.Ketteisen.S01E09.2026.1080p.LINETV.WEB-DL.H264.AAC-ADWeb.mkv"
E10_FILE = "Shin.Tennis.no.Oujisama.U.17.World.Cup.Kesshou.Member.Ketteisen.S01E10.2026.1080p.LINETV.WEB-DL.H264.AAC-ADWeb.mkv"
OTHER_ARC_FILE = "Shin.Tennis.no.Oujisama.U.17.World.Cup.S01E01.2022.1080p.WEB-DL.H264-ADWeb.mkv"


class _FakeTmdb:
    configured = True

    def search(self, query: str):
        # Full Romaji returns EMPTY just like real TMDB!
        if "Kesshou" in query or "Ketteisen" in query:
            return []
        if "U-17 World Cup" in query or "U 17 World Cup" in query:
            return [
                {
                    "mediaKey": "tv:205493",
                    "mediaType": "tv",
                    "tmdbId": "205493",
                    "title": "新网球王子 U-17世界杯篇",
                    "originalTitle": "新テニスの王子様 U-17 WORLD CUP",
                    "year": "2022",
                    "posterUrl": "",
                }
            ]
        return []

    def get_details(self, media_type: str, tmdb_id: str):
        if media_type == "tv" and tmdb_id == "205493":
            return {
                "mediaKey": "tv:205493",
                "mediaType": "tv",
                "tmdbId": "205493",
                "title": "新网球王子 U-17世界杯篇",
                "originalTitle": "新テニスの王子様 U-17 WORLD CUP",
                "year": "2022",
                "posterUrl": "",
                "seasons": [
                    {"seasonNumber": 1, "name": "第 1 季", "airDate": "2022-07-07", "episodeCount": 13},
                    {"seasonNumber": 2, "name": "半决赛", "airDate": "2024-10-03", "episodeCount": 13},
                    {"seasonNumber": 3, "name": "决赛成员决定战", "airDate": "2026-10-01", "episodeCount": 13},
                ],
            }
        return None

    def get_season_episodes(self, tmdb_id: str, season_number: int):
        if tmdb_id == "205493" and season_number == 3:
            return [
                {"seasonNumber": 3, "episodeNumber": 9, "episodeLabel": "S03E09", "name": "不妥协的愿望", "airDate": "2026-11-26"},
                {"seasonNumber": 3, "episodeNumber": 10, "episodeLabel": "S03E10", "name": "第 10 集", "airDate": "2026-12-03"},
            ]
        if tmdb_id == "205493" and season_number == 1:
            return [
                {"seasonNumber": 1, "episodeNumber": 9, "episodeLabel": "S01E09", "name": "旧集", "airDate": "2022-09-01"},
            ]
        return []


class _FakeSymedia:
    configured = True

    def __init__(self):
        self.submitted = []
        self.history = []

    def list_transfer_tasks(self):
        return [{"id": "task-anime", "name": "动漫归档", "sourcePath": "/待整理", "targetPath": "/媒体库/动漫"}]

    def probe_recovery_capabilities(self):
        return {"transferManual": True, "manualIdentifyParams": ["tmdbid", "season", "episode"], "openapiChecked": True}

    def submit_manual_transfer(self, **kwargs):
        self.submitted.append(kwargs)
        return {"ok": True, "message": "已向 SA 提交单文件归档请求"}

    def list_transfer_history(self, count=150):
        return list(self.history)


class _FakeEmby:
    configured = True

    def __init__(self, episodes=None):
        self.episodes = episodes or {}

    def fetch_library_snapshot(self):
        return {"available": True, "movies": {}, "series": {}, "episodes": self.episodes, "recentToday": []}


class _Noop:
    configured = False
    def app_url(self, *a, **k): return ""
    def list_subscriptions(self): return []
    def list_download_history(self): return []
    def list_torrents(self): return []


class SaRecoveryAcceptanceTests(unittest.TestCase):
    def test_e09_real_sample_diagnosis_confirm_reuse_and_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FluxaStore(Path(tmp) / "test.sqlite3")
            sa = _FakeSymedia()
            emby = _FakeEmby()
            adapters = {"tmdb": _FakeTmdb(), "symedia": sa, "emby": emby, "moviepilot": _Noop(), "torra": _Noop(), "qbittorrent": _Noop()}
            snapshot = {"services": [], "serviceMap": {}, "mpSubs": [], "torraSubs": [], "emby": emby.fetch_library_snapshot()}
            settings = store.get_settings()

            # 1. SA error "没有查询到媒体信息" classified as identify_failed
            outcome, _, _ = classify_symedia_record({"status": 0, "errmsg": "没有查询到媒体信息"})
            self.assertEqual(outcome, "identify_failed")

            # 2. Diagnose real E09 sample -> progressive Romaji trimming finds TMDB 205493 / S03E09!
            e09_record = {
                "id": "88638",
                "fileName": E09_FILE,
                "src": f"/09-归档失败/{E09_FILE}",
                "date": "2026-11-26 01:17:04",
                "outcome": "identify_failed",
                "reasonText": "没有查询到媒体信息",
                "fileSize": 853737524,
            }
            diag = diagnose_sa_failure(record=e09_record, settings=settings, store=store, adapters=adapters, snapshot=snapshot)
            self.assertTrue(diag["requiresConfirm"])  # First season remap MUST require confirm
            self.assertGreaterEqual(len(diag["proposals"]), 1)
            top = diag["proposals"][0]
            self.assertEqual(top["tmdbId"], "205493")
            self.assertEqual(top["targetSeason"], 3)
            self.assertEqual(top["targetEpisode"], 9)
            self.assertEqual(top["seasonName"], "决赛成员决定战")
            self.assertEqual(top["episodeName"], "不妥协的愿望")
            self.assertIn("2026.1080p.LINETV.WEB-DL.H264.AAC-ADWeb.mkv", top["suggestedFileName"])

            # 3. Unconfirmed execution MUST be rejected
            with self.assertRaises(ValueError):
                execute_sa_recovery(
                    payload={
                        "saRecordId": "88638",
                        "fileName": E09_FILE,
                        "srcPath": e09_record["src"],
                        "failedDate": e09_record["date"],
                        "tmdbId": "205493",
                        "canonicalTitle": "新网球王子 U-17世界杯篇",
                        "sourceSeason": 1,
                        "sourceEpisode": 9,
                        "targetSeason": 3,
                        "targetEpisode": 9,
                        "transferTaskId": "task-anime",
                        "confirmed": False,
                    },
                    settings=settings,
                    store=store,
                    adapters=adapters,
                )

            # 4. Confirmed single-file submit -> status MUST be 'submitted' (NOT 'resolved'!)
            exec_res = execute_sa_recovery(
                payload={
                    "saRecordId": "88638",
                    "fileName": E09_FILE,
                    "srcPath": e09_record["src"],
                    "failedDate": e09_record["date"],
                    "tmdbId": "205493",
                    "canonicalTitle": "新网球王子 U-17世界杯篇",
                    "sourceSeason": 1,
                    "sourceEpisode": 9,
                    "targetSeason": 3,
                    "targetEpisode": 9,
                    "seasonName": "决赛成员决定战",
                    "transferTaskId": "task-anime",
                    "saveMapping": True,
                    "confirmed": True,
                },
                settings=settings,
                store=store,
                adapters=adapters,
            )
            self.assertTrue(exec_res["ok"])
            self.assertEqual(exec_res["job"]["status"], "submitted")
            self.assertEqual(len(sa.submitted), 1)
            self.assertEqual(sa.submitted[0]["tmdbid" if "tmdbid" in sa.submitted[0] else "tmdb_id"], "205493")

            # 5. Verify before SA creates a new record & after timeout -> 'not_processed' (Point 4)
            later = datetime.now(BEIJING_TZ) + timedelta(minutes=20)
            v_timeout = verify_sa_recovery(job_id=exec_res["job"]["id"], settings=settings, store=store, adapters=adapters, now=later)
            self.assertFalse(v_timeout["resolved"])
            self.assertEqual(v_timeout["status"], "not_processed")

            # 6. SA succeeds without STRM/Emby yet -> still 'processing', NOT 'resolved'!
            sa.history = [{
                "id": "88900",
                "outcome": "archived",
                "tmdbId": "205493",
                "seasonEpisode": "S03E09",
                "seasons": "S03",
                "episodes": "E09",
                "src": e09_record["src"],
                "dest": "/媒体库/动漫/新网球王子/Season 3/S03E09.mkv",
                "date": "2026-11-26 21:40:00",
            }]
            v_no_strm = verify_sa_recovery(job_id=exec_res["job"]["id"], settings=settings, store=store, adapters=adapters)
            self.assertFalse(v_no_strm["resolved"])
            self.assertEqual(v_no_strm["status"], "processing")

            # 7. Once Emby/STRM confirmed -> NOW marked 'resolved'!
            emby.episodes["205493"] = [[3, 9]]
            v_ok = verify_sa_recovery(job_id=exec_res["job"]["id"], settings=settings, store=store, adapters=adapters)
            self.assertTrue(v_ok["resolved"])
            self.assertEqual(v_ok["status"], "resolved")

            # 8. Subsequent E10 of the SAME arc automatically reuses the saved scoped mapping!
            e10_record = {
                "id": "89100",
                "fileName": E10_FILE,
                "src": f"/09-归档失败/{E10_FILE}",
                "date": "2026-12-03 01:17:04",
                "outcome": "identify_failed",
                "reasonText": "没有查询到媒体信息",
            }
            diag_e10 = diagnose_sa_failure(record=e10_record, settings=settings, store=store, adapters=adapters, snapshot=snapshot)
            self.assertTrue(diag_e10["canAutoApply"])
            self.assertFalse(diag_e10["requiresConfirm"])
            self.assertEqual(diag_e10["savedMapping"]["targetSeason"], 3)

            # 9. Different arc of the SAME franchise (S01E01 without Kesshou Member Ketteisen) MUST NOT reuse S03!
            other_record = {
                "id": "89101",
                "fileName": OTHER_ARC_FILE,
                "src": f"/09-归档失败/{OTHER_ARC_FILE}",
                "date": "2026-11-26 02:00:00",
                "outcome": "identify_failed",
                "reasonText": "没有查询到媒体信息",
            }
            diag_other = diagnose_sa_failure(record=other_record, settings=settings, store=store, adapters=adapters, snapshot=snapshot)
            self.assertIsNone(diag_other["savedMapping"])
            self.assertFalse(diag_other["canAutoApply"])
            self.assertTrue(diag_other["requiresConfirm"])

    def test_weight_rejection_prefixed_with_transfer_error_is_not_an_issue(self):
        # Point 5: SA writes "文件转移错误：源文件权重小于等于目标文件，取消覆盖"
        outcome, label, _ = classify_symedia_record({
            "status": 0,
            "errmsg": "文件转移错误：新压制版权重小于等于库内Remux，取消覆盖",
            "src_detail": {"totalScore": 120},
            "dst_detail": {"totalScore": 200},
        })
        self.assertEqual(outcome, "rule_protected")
        self.assertIn("正常权重拒绝", label)

    def test_sequel_conflict_hostel_part_ii_vs_tmdb_1690(self):
        # Point 3: Hostel Part II (2007) mis-archived to Hostel (2005)
        conflict = check_sequel_identity_conflict(
            source_name="Hostel Part II",
            source_year="2007",
            target_title="人皮客栈",
            target_original_title="Hostel",
            target_year="2005",
        )
        self.assertTrue(conflict["hasConflict"])
        self.assertEqual(conflict["sourceSequel"], 2)
        self.assertIsNone(conflict["targetSequel"])

    def test_promo_noise_cleaning_and_multi_disc_marker_preservation(self):
        # Point 2: Blukook collection noise + Disc marker preservation
        parsed = parse_media_filename("012.不撸酷客.焚尸人.英SR版.原盘正片全花絮.Disc1.1969.1080p.BluRay.REMUX-DIY.mkv")
        self.assertTrue(parsed["hasSpecialMarkers"])
        self.assertNotIn("不撸酷客", parsed["cleanedTitle"])
        renamed = build_spec_preserving_filename(parsed, canonical_title="焚尸人", media_type="movie")
        self.assertIn("Disc1", renamed)
        self.assertIn("1080p.BluRay.REMUX-DIY.mkv", renamed)

    def test_e09_regression_excludes_317883_35465_recalls_205493_and_readonly(self):
        """Regression for real bug: 'U 17' must NOT generate query '17', must exclude Daha 17 (317883)
        and 代号17 (35465), recall 205493 / S03E09, and perform zero file moves or SA submits during diagnose.
        """
        class _PollutedTmdb(_FakeTmdb):
            def search(self, query: str):
                # Even if an unrelated noisy hit appeared alongside valid hits, identity gate MUST reject them!
                if query.strip() == "17":
                    return [
                        {"mediaKey": "tv:317883", "mediaType": "tv", "tmdbId": "317883", "title": "Daha 17", "originalTitle": "Daha 17", "year": "2026", "posterUrl": ""},
                        {"mediaKey": "tv:35465", "mediaType": "tv", "tmdbId": "35465", "title": "代号17", "originalTitle": "代号17", "year": "2010", "posterUrl": ""},
                    ]
                hits = super().search(query)
                if hits:
                    return [
                        {"mediaKey": "tv:317883", "mediaType": "tv", "tmdbId": "317883", "title": "Daha 17", "originalTitle": "Daha 17", "year": "2026", "posterUrl": ""},
                        {"mediaKey": "tv:35465", "mediaType": "tv", "tmdbId": "35465", "title": "代号17", "originalTitle": "代号17", "year": "2010", "posterUrl": ""},
                        *hits,
                    ]
                return []

            def get_details(self, media_type: str, tmdb_id: str):
                if tmdb_id == "317883":
                    return {
                        "mediaKey": "tv:317883", "mediaType": "tv", "tmdbId": "317883",
                        "title": "Daha 17", "originalTitle": "Daha 17", "alternativeTitles": [], "year": "2026", "posterUrl": "",
                        "seasons": [{"seasonNumber": 1, "name": "第 1 季", "airDate": "2026-05-31", "episodeCount": 12}],
                    }
                if tmdb_id == "35465":
                    return {
                        "mediaKey": "tv:35465", "mediaType": "tv", "tmdbId": "35465",
                        "title": "代号17", "originalTitle": "代号17", "alternativeTitles": [], "year": "2010", "posterUrl": "",
                        "seasons": [{"seasonNumber": 1, "name": "第 1 季", "airDate": "2010-01-01", "episodeCount": 12}],
                    }
                return super().get_details(media_type, tmdb_id)

        class _OnlyNoiseTmdb(_PollutedTmdb):
            def search(self, query: str):
                return [
                    {"mediaKey": "tv:317883", "mediaType": "tv", "tmdbId": "317883", "title": "Daha 17", "originalTitle": "Daha 17", "year": "2026", "posterUrl": ""},
                    {"mediaKey": "tv:35465", "mediaType": "tv", "tmdbId": "35465", "title": "代号17", "originalTitle": "代号17", "year": "2010", "posterUrl": ""},
                ]

        with tempfile.TemporaryDirectory() as tmp:
            store = FluxaStore(Path(tmp) / "test.sqlite3")
            settings = store.get_settings()
            sa = _FakeSymedia()
            emby = _FakeEmby()
            # Create a real temp file to verify diagnosis NEVER renames or moves it
            src_file = Path(tmp) / E09_FILE
            src_file.write_bytes(b"fake-video")

            parsed = parse_media_filename(E09_FILE)
            # 1. Pure digit query "17" must NEVER be generated!
            self.assertNotIn("17", parsed["searchQueries"])
            self.assertTrue(any("U-17 World Cup" in q for q in parsed["searchQueries"]))

            e09_record = {
                "id": "88638",
                "fileName": E09_FILE,
                "src": str(src_file),
                "date": "2026-11-26 01:17:04",
                "outcome": "identify_failed",
                "reasonText": "没有查询到媒体信息",
            }
            snapshot = {"services": [], "serviceMap": {}, "mpSubs": [], "torraSubs": [], "emby": emby.fetch_library_snapshot()}
            adapters = {"tmdb": _PollutedTmdb(), "symedia": sa, "emby": emby, "moviepilot": _Noop(), "torra": _Noop(), "qbittorrent": _Noop()}
            diag = diagnose_sa_failure(record=e09_record, settings=settings, store=store, adapters=adapters, snapshot=snapshot)

            proposal_ids = {p["tmdbId"] for p in diag["proposals"]}
            self.assertNotIn("317883", proposal_ids)
            self.assertNotIn("35465", proposal_ids)
            self.assertEqual(diag["proposals"][0]["tmdbId"], "205493")
            self.assertEqual(diag["proposals"][0]["targetSeason"], 3)
            self.assertEqual(diag["proposals"][0]["targetEpisode"], 9)
            self.assertEqual(diag["recognitionStatus"], "matched")
            # Read-only guarantee: file untouched & zero SA submits
            self.assertTrue(src_file.is_file())
            self.assertEqual(len(sa.submitted), 0)

            # 2. When ONLY unrelated shows exist, must return unreliable & empty proposals (never fabricate!)
            noise_adapters = {**adapters, "tmdb": _OnlyNoiseTmdb()}
            diag_noise = diagnose_sa_failure(record=e09_record, settings=settings, store=store, adapters=noise_adapters, snapshot=snapshot)
            self.assertEqual(diag_noise["proposals"], [])
            self.assertEqual(diag_noise["recognitionStatus"], "unreliable")
            self.assertIn("无法可靠识别", diag_noise["statusMessage"])
            self.assertGreaterEqual(diag_noise["filteredOutCount"], 2)

    def test_same_file_chain_hides_old_failure_after_success(self):
        snapshot = {
            "services": [],
            "mpHistory": [],
            "mpSubs": [],
            "torraSubs": [],
            "qbTasks": [],
            "torraSecupload": {},
            "saHistory": [
                {"id": "88900", "fileName": E09_FILE, "title": "新网球王子", "mediaType": "tv", "outcome": "archived", "outcomeLabel": "归档成功", "reasonText": "已完成", "seasons": "S03", "episodes": "E09", "dest": "/a/S03E09.mkv", "webUrl": ""},
                {"id": "88638", "fileName": E09_FILE, "title": E09_FILE, "mediaType": "tv", "outcome": "identify_failed", "outcomeLabel": "SA 识别失败", "reasonText": "没有查询到媒体信息", "seasons": "", "episodes": "", "dest": "", "webUrl": ""},
            ],
        }
        issues = build_actionable_issues(snapshot, {})
        self.assertEqual(len(issues), 0)

    def test_sa_openapi_ref_param_verification_and_refusal_when_unsupported(self):
        """1. 必须核对 SA 真实 OpenAPI $ref 接口参数：不支持 TMDB/季集覆盖时不得静默原样提交。"""
        class _FakeResp:
            def __init__(self, status_code, body):
                self.status_code = status_code
                self._body = body
                self.content = b"1"
            def json(self):
                return self._body

        class _MockSessionNoOverride:
            def __init__(self):
                self.requests = []
            def get(self, url, **kwargs):
                if url.endswith("/openapi.json"):
                    return _FakeResp(200, {
                        "paths": {
                            "/api/v1/transfer/manual": {
                                "post": {
                                    "requestBody": {
                                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ManualReq"}}}
                                    }
                                }
                            }
                        },
                        "components": {
                            "schemas": {
                                "ManualItem": {
                                    "type": "object",
                                    "properties": {"type": {"type": "string"}, "path": {"type": "string"}},
                                },
                                "ManualReq": {
                                    "type": "object",
                                    "properties": {
                                        "items": {"type": "array", "items": {"$ref": "#/components/schemas/ManualItem"}},
                                        "transfer_task_id": {"type": "string"},
                                    },
                                },
                            }
                        },
                    })
                return _FakeResp(404, {})
            def request(self, method, url, **kwargs):
                self.requests.append((method, url, kwargs))
                return _FakeResp(200, {"success": True})

        sess = _MockSessionNoOverride()
        adapter = SymediaAdapter({"baseUrl": "http://sa.local", "token": "tok"}, session=sess)
        caps = adapter.probe_recovery_capabilities()
        self.assertTrue(caps["openapiChecked"])
        self.assertTrue(caps["transferManual"])
        self.assertFalse(caps["supportsTmdbParam"])
        self.assertFalse(caps["supportsSeasonEpisodeParam"])
        self.assertFalse(caps["supportsExplicitIdentify"])
        self.assertEqual(set(caps["manualBodyFields"]), {"items", "transfer_task_id"})

        with self.assertRaises(RuntimeError) as ctx:
            adapter.submit_manual_transfer(
                file_path=f"/09-归档失败/{E09_FILE}",
                transfer_task_id="task-1",
                tmdb_id="205493",
                media_type="tv",
                season=3,
                episode=9,
                capabilities=caps,
                require_explicit_identify=True,
            )
        self.assertIn("不支持直接传入 TMDB ID", str(ctx.exception))
        self.assertEqual(len(sess.requests), 0)

        # Also verify exact parameter binding when SA OpenAPI DOES declare query/item params
        class _MockSessionWithParams(_MockSessionNoOverride):
            def get(self, url, **kwargs):
                if url.endswith("/openapi.json"):
                    return _FakeResp(200, {
                        "paths": {
                            "/api/v1/transfer/manual": {
                                "post": {
                                    "parameters": [{"name": "tmdb_id", "in": "query"}],
                                    "requestBody": {
                                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ManualReq2"}}}
                                    },
                                }
                            }
                        },
                        "components": {
                            "schemas": {
                                "ManualItem2": {
                                    "type": "object",
                                    "properties": {
                                        "type": {"type": "string"},
                                        "path": {"type": "string"},
                                        "season_num": {"type": "integer"},
                                        "episode_num": {"type": "integer"},
                                    },
                                },
                                "ManualReq2": {
                                    "type": "object",
                                    "properties": {
                                        "items": {"type": "array", "items": {"$ref": "#/components/schemas/ManualItem2"}},
                                        "transfer_task_id": {"type": "string"},
                                    },
                                },
                            }
                        },
                    })
                return _FakeResp(404, {})

        sess2 = _MockSessionWithParams()
        adapter2 = SymediaAdapter({"baseUrl": "http://sa.local", "token": "tok"}, session=sess2)
        caps2 = adapter2.probe_recovery_capabilities()
        self.assertTrue(caps2["supportsExplicitIdentify"])
        res2 = adapter2.submit_manual_transfer(
            file_path=f"/09-归档失败/{E09_FILE}",
            transfer_task_id="task-1",
            tmdb_id="205493",
            media_type="tv",
            season=3,
            episode=9,
            capabilities=caps2,
            require_explicit_identify=True,
        )
        self.assertEqual(res2["overrideMode"], "openapi_params")
        self.assertEqual(sess2.requests[0][2]["params"], {"tmdb_id": 205493})
        self.assertEqual(sess2.requests[0][2]["json"]["items"][0]["season_num"], 3)
        self.assertEqual(sess2.requests[0][2]["json"]["items"][0]["episode_num"], 9)

    def test_existing_preferred_cr_version_prompts_no_recovery_needed_over_linetv(self):
        """2a. 同集已有符合偏好的 CR 版时，诊断提示「已有版本，无需补库」，并阻止继续恢复 LINETV 版。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = FluxaStore(Path(tmp) / "test.sqlite3")
            settings = store.get_settings()
            sa = _FakeSymedia()
            cr_file = "The.Prince.of.Tennis.II.U-17.World.Cup.S03E09.1080p.CR.WEB-DL.AAC2.0.H.264-VARYG.mkv"
            sa.history = [
                {
                    "id": "88910",
                    "mediaKey": "tv:205493",
                    "mediaType": "tv",
                    "tmdbId": "205493",
                    "title": "新网球王子 U-17世界杯篇",
                    "seasons": "S03",
                    "episodes": "E09",
                    "seasonEpisode": "S03E09",
                    "src": f"/CloudNAS/CloudDrive/115/00-待整理/00-日漫/{cr_file}",
                    "dest": f"/CloudNAS/CloudDrive/115/媒体库/日漫/新网球王子/Season 3/S03E09.CR.WEB-DL.mkv",
                    "fileName": cr_file,
                    "outcome": "archived",
                    "outcomeLabel": "归档成功",
                    "reasonText": "已完成整理归档",
                    "date": "2026-11-26 02:10:00",
                }
            ]
            emby = _FakeEmby(episodes={"205493": [(3, 9)]})
            adapters = {"tmdb": _FakeTmdb(), "symedia": sa, "emby": emby, "moviepilot": _Noop(), "torra": _Noop(), "qbittorrent": _Noop()}
            snapshot = {
                "services": [],
                "serviceMap": {},
                "mpSubs": [],
                "torraSubs": [],
                "saHistory": sa.history,
                "emby": {
                    **emby.fetch_library_snapshot(),
                    "episodeDetails": {
                        "205493": {
                            "S03E09": [{"fileName": cr_file, "path": f"/emby/S03E09/{cr_file}", "isStrm": True}]
                        }
                    },
                },
            }
            e09_record = {
                "id": "88638",
                "fileName": E09_FILE,
                "src": f"/CloudNAS/CloudDrive/115/09-归档失败/{E09_FILE}",
                "date": "2026-11-26 01:17:04",
                "outcome": "identify_failed",
                "reasonText": "没有查询到媒体信息",
            }
            diag = diagnose_sa_failure(record=e09_record, settings=settings, store=store, adapters=adapters, snapshot=snapshot)
            self.assertTrue(diag["noRecoveryNeeded"])
            self.assertIn("已有版本，已忽略", diag["statusMessage"])
            self.assertIn("已有版本，无需补库", diag["proposals"][0]["existingVersion"]["summary"])
            self.assertIn("CR", diag["statusMessage"])
            self.assertIn("LINETV", diag["statusMessage"])
            self.assertFalse(diag["canAutoApply"])
            self.assertEqual(diag["proposals"][0]["existingVersion"]["existingSourceTag"], "CR")
            self.assertEqual(diag["proposals"][0]["existingVersion"]["failedSourceTag"], "LINETV")

            # Attempting to execute recovery of LINETV MUST be blocked with「已有版本，无需补库」
            with self.assertRaises(ValueError) as ctx:
                execute_sa_recovery(
                    payload={
                        "saRecordId": "88638",
                        "fileName": E09_FILE,
                        "srcPath": e09_record["src"],
                        "failedDate": e09_record["date"],
                        "mediaType": "tv",
                        "tmdbId": "205493",
                        "canonicalTitle": "新网球王子 U-17世界杯篇",
                        "sourceSeason": 1,
                        "sourceEpisode": 9,
                        "targetSeason": 3,
                        "targetEpisode": 9,
                        "transferTaskId": "task-anime",
                        "confirmed": True,
                    },
                    settings=settings,
                    store=store,
                    adapters=adapters,
                )
            self.assertIn("已有版本，无需补库", str(ctx.exception))
            self.assertEqual(len(sa.submitted), 0)

    def test_verify_tracks_moved_file_new_failure_and_ignores_unrelated_cr_success(self):
        """2b. 核验必须追踪文件移动后的新失败记录，绝不能误拿同集 CR 版的成功记录当作 LINETV 恢复成功；若指定季集未生效也必须判失败。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = FluxaStore(Path(tmp) / "test.sqlite3")
            settings = store.get_settings()
            sa = _FakeSymedia()
            emby = _FakeEmby()
            adapters = {"tmdb": _FakeTmdb(), "symedia": sa, "emby": emby, "moviepilot": _Noop(), "torra": _Noop(), "qbittorrent": _Noop()}

            # Submit LINETV E09 recovery while Emby/SA don't yet have it
            exec_res = execute_sa_recovery(
                payload={
                    "saRecordId": "88638",
                    "fileName": E09_FILE,
                    "srcPath": f"/CloudNAS/CloudDrive/115/00-待整理/00-日漫/{E09_FILE}",
                    "failedDate": "2026-11-26 01:17:04",
                    "mediaType": "tv",
                    "tmdbId": "205493",
                    "canonicalTitle": "新网球王子 U-17世界杯篇",
                    "sourceSeason": 1,
                    "sourceEpisode": 9,
                    "targetSeason": 3,
                    "targetEpisode": 9,
                    "transferTaskId": "task-anime",
                    "confirmed": True,
                },
                settings=settings,
                store=store,
                adapters=adapters,
            )
            job_id = exec_res["job"]["id"]

            # Now SA history has:
            # 1) A DIFFERENT CR release archived for 205493 S03E09 (#88910) AND present in Emby
            # 2) Our LINETV file moved to /09-归档失败/ and FAILED AGAIN (#88950) because SA ignored tmdbid/season!
            emby.episodes["205493"] = [(3, 9)]
            cr_file = "The.Prince.of.Tennis.II.U-17.World.Cup.S03E09.1080p.CR.WEB-DL-VARYG.mkv"
            sa.history = [
                {
                    "id": "88950",
                    "mediaKey": "",
                    "mediaType": "tv",
                    "tmdbId": "",
                    "title": E09_FILE,
                    "seasons": "",
                    "episodes": "",
                    "seasonEpisode": "",
                    "src": f"/CloudNAS/CloudDrive/115/09-归档失败/{E09_FILE}",
                    "dest": "",
                    "fileName": E09_FILE,
                    "outcome": "identify_failed",
                    "outcomeLabel": "SA 识别失败",
                    "reasonText": "没有查询到媒体信息",
                    "date": "2026-11-26 22:45:00",
                },
                {
                    "id": "88910",
                    "mediaKey": "tv:205493",
                    "mediaType": "tv",
                    "tmdbId": "205493",
                    "title": "新网球王子 U-17世界杯篇",
                    "seasons": "S03",
                    "episodes": "E09",
                    "seasonEpisode": "S03E09",
                    "src": f"/CloudNAS/CloudDrive/115/00-待整理/00-日漫/{cr_file}",
                    "dest": "/CloudNAS/CloudDrive/115/媒体库/日漫/Season 3/S03E09.CR.strm",
                    "fileName": cr_file,
                    "outcome": "archived",
                    "outcomeLabel": "归档成功",
                    "reasonText": "已完成整理归档",
                    "date": "2026-11-26 02:10:00",
                },
            ]
            ver = verify_sa_recovery(job_id=job_id, settings=settings, store=store, adapters=adapters)
            self.assertFalse(ver["resolved"])
            self.assertEqual(ver["status"], "failed")
            self.assertEqual(ver["job"]["verification"]["newFailureRecordId"], "88950")
            self.assertIn("/09-归档失败/", ver["job"]["verification"]["movedSrcPath"])
            self.assertIn("未在 SA 实际处理结果中生效", ver["message"])
            self.assertIn("已有版本，无需补库", ver["message"])
            self.assertIn("CR", ver["message"])

            # Case B: SA processed THIS LINETV file, but archived it to WRONG season S01E09 (specified S03E09 did NOT take effect!)
            sa.history = [
                {
                    "id": "88960",
                    "mediaKey": "tv:205493",
                    "mediaType": "tv",
                    "tmdbId": "205493",
                    "title": "新网球王子 U-17世界杯篇",
                    "seasons": "S01",
                    "episodes": "E09",
                    "seasonEpisode": "S01E09",
                    "src": f"/CloudNAS/CloudDrive/115/09-归档失败/{E09_FILE}",
                    "dest": "/CloudNAS/CloudDrive/115/媒体库/日漫/Season 1/S01E09.strm",
                    "fileName": E09_FILE,
                    "outcome": "archived",
                    "outcomeLabel": "归档成功",
                    "reasonText": "已完成整理归档",
                    "date": "2026-11-26 22:50:00",
                }
            ]
            ver_wrong = verify_sa_recovery(job_id=job_id, settings=settings, store=store, adapters=adapters)
            self.assertFalse(ver_wrong["resolved"])
            self.assertEqual(ver_wrong["status"], "failed")
            self.assertIn("指定映射未生效", ver_wrong["message"])

    def test_kuroka_baha_failed_with_linetv_archived_shows_non_missing_and_needs_compare(self):
        """真实样本 #88706：Baha版识别失败（罗马音搜 TMDB 为空），1 分钟后别名 LINETV 版已入 TMDB 293124 S02E09。
        首页异常待办与诊断必须显示「Baha版识别失败；同集已有LINETV版入库」，不误导为缺库，也不凭平台名直接判优劣。
        """
        baha_file = "Kuroka.de.Seijo.ni.Hyou.S02E09.2026.1080p.Baha.WEB-DL.x264.AAC-ADWeb.mp4"
        linetv_file = "Kizu.darake.Seijo.yori.Houfuku.wo.Komete.S02E09.2026.1080p.LINETV.WEB-DL.H264.AAC-ADWeb.mkv"

        class _KurokaTmdb:
            configured = True
            def search(self, query: str):
                # Romaji search returns empty, matching real TMDB behavior!
                return []
            def get_details(self, mt: str, tid: str):
                if mt == "tv" and tid == "293124":
                    return {
                        "mediaKey": "tv:293124",
                        "mediaType": "tv",
                        "tmdbId": "293124",
                        "title": "黑化吧！圣女大人",
                        "originalTitle": "傷だらけ聖女より報復をこめて",
                        "alternativeTitles": [],
                        "year": "2025",
                        "posterUrl": "",
                        "seasons": [
                            {"seasonNumber": 1, "name": "第 1 季", "airDate": "2025-07-01", "episodeCount": 12},
                            {"seasonNumber": 2, "name": "第 2 季", "airDate": "2026-10-02", "episodeCount": 12},
                        ],
                    }
                return None
            def get_season_episodes(self, tid: str, snum: int):
                if tid == "293124" and snum == 2:
                    return [{"seasonNumber": 2, "episodeNumber": 9, "episodeLabel": "S02E09", "name": "第 9 集", "airDate": "2026-11-27"}]
                return []

        sa_history = [
            {
                "id": "88709",
                "mediaKey": "tv:293124",
                "mediaType": "tv",
                "tmdbId": "293124",
                "title": "黑化吧！圣女大人",
                "seasons": "S02",
                "episodes": "E09",
                "seasonEpisode": "S02E09",
                "src": f"/CloudNAS/CloudDrive/115/00-待整理/00-日漫/{linetv_file}",
                "dest": f"/CloudNAS/CloudDrive/115/媒体库/日漫/黑化吧！圣女大人/Season 2/{linetv_file}",
                "fileName": linetv_file,
                "outcome": "archived",
                "outcomeLabel": "归档成功",
                "reasonText": "已完成整理归档",
                "date": "2026-11-27 00:31:37",
                "webUrl": "",
            },
            {
                "id": "88706",
                "mediaKey": "",
                "mediaType": "tv",
                "tmdbId": "",
                "title": "Kuroka de Seijo ni Hyou",
                "seasons": "S02",
                "episodes": "E09",
                "seasonEpisode": "S02E09",
                "src": f"/CloudNAS/CloudDrive/115/00-待整理/00-日漫/{baha_file}",
                "dest": "",
                "fileName": baha_file,
                "fileSize": 466328147,
                "outcome": "identify_failed",
                "outcomeLabel": "SA 识别失败",
                "reasonText": f"文件转移错误: /CloudNAS/CloudDrive/115/00-待整理/00-日漫/{baha_file},没有查询到媒体信息",
                "date": "2026-11-27 00:30:18",
                "webUrl": "",
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            store = FluxaStore(Path(tmp) / "test.sqlite3")
            settings = store.get_settings()
            emby = _FakeEmby(episodes={"293124": [(2, 9)]})
            sa = _FakeSymedia()
            sa.history = sa_history
            snapshot = {
                "services": [],
                "serviceMap": {},
                "mpSubs": [],
                "mpHistory": [],
                "torraSubs": [],
                "qbTasks": [],
                "torraSecupload": {},
                "saHistory": sa_history,
                "emby": emby.fetch_library_snapshot(),
            }

            # 1. With Emby confirming 293124 S02E09 is CURRENTLY in library (and no exception),
            #    #88706 MUST be auto-ignored as 「已有版本，已忽略」: removed from active_issues, preserved in ignored_issues!
            active, ignored = partition_actionable_issues(snapshot, {}, store=store)
            self.assertEqual(active, [])
            self.assertEqual(len(ignored), 1)
            ig = ignored[0]
            self.assertEqual(ig["ignoredKind"], "auto_existing_version")
            self.assertEqual(ig["ignoredLabel"], "已有版本，已忽略")
            self.assertEqual(ig["mediaKey"], "tv:293124")
            self.assertEqual(ig["episodeLabel"], "S02E09")
            self.assertTrue(ig["canRestore"])
            self.assertIsNotNone(ig["saRecord"])
            self.assertIn("已有版本，已忽略", ig["ignoredReason"])
            self.assertIn("忽略不等于修复成功", ig["ignoredReason"])
            self.assertIn("Baha版识别失败", ig["ignoredReason"])
            self.assertIn("LINETV版", ig["ignoredReason"])
            self.assertEqual(len(sa.submitted), 0)  # Zero retry / rename / delete side effects!

            # 2. Restoring from 「已忽略」 moves #88706 right back into active_issues!
            store.set_cache("issues:restored", [ig["issueKey"]])
            active_restored, ignored_restored = partition_actionable_issues(snapshot, {}, store=store)
            self.assertEqual(len(active_restored), 1)
            self.assertEqual(ignored_restored, [])
            store.set_cache("issues:restored", [])

            # 3. Safety boundary: Emby unavailable OR only old SA history without current Emby entry -> MUST NOT auto-ignore!
            snap_no_emby = {**snapshot, "emby": {"available": False, "movies": {}, "series": {}, "episodes": {}}}
            active_no_emby, ignored_no_emby = partition_actionable_issues(snap_no_emby, {}, store=store)
            self.assertEqual(len(active_no_emby), 1)
            self.assertEqual(ignored_no_emby, [])
            self.assertIn("未自动忽略", active_no_emby[0]["errorText"])

            snap_old_hist_only = {**snapshot, "emby": {"available": True, "movies": {}, "series": {}, "episodes": {}}}
            active_old, ignored_old = partition_actionable_issues(snap_old_hist_only, {}, store=store)
            self.assertEqual(len(active_old), 1)
            self.assertEqual(ignored_old, [])
            self.assertIn("未自动忽略", active_old[0]["errorText"])

            # 4. Exception guards: collection intent, exclusive cut (AT-X/无修), or effects-sub (特效字幕/ASS) -> MUST NOT auto-ignore!
            active_collect, ignored_collect = partition_actionable_issues(snapshot, {"tv:293124": {"intent": "collect"}}, store=store)
            self.assertEqual(len(active_collect), 1)
            self.assertEqual(ignored_collect, [])
            self.assertIn("收藏", active_collect[0]["errorText"])

            special_baha = "Kuroka.de.Seijo.ni.Hyou.S02E09.2026.1080p.AT-X.特效字幕.ASS.Baha.WEB-DL.x264.AAC-ADWeb.mp4"
            snap_special = {
                **snapshot,
                "saHistory": [
                    sa_history[0],
                    {**sa_history[1], "fileName": special_baha, "src": f"/CloudNAS/CloudDrive/115/00-待整理/00-日漫/{special_baha}"},
                ],
            }
            active_sp, ignored_sp = partition_actionable_issues(snap_special, {}, store=store)
            self.assertEqual(len(active_sp), 1)
            self.assertEqual(ignored_sp, [])
            self.assertIn("不走通用自动忽略", active_sp[0]["errorText"])

            # 5. Diagnosis recalls TMDB 293124 S02E09 via corroborated SA sibling even though TMDB Romaji search is empty!
            adapters = {"tmdb": _KurokaTmdb(), "symedia": sa, "emby": emby, "moviepilot": _Noop(), "torra": _Noop(), "qbittorrent": _Noop()}
            diag = diagnose_sa_failure(record=sa_history[1], settings=settings, store=store, adapters=adapters, snapshot=snapshot)
            self.assertEqual(diag["recognitionStatus"], "matched")
            self.assertEqual(diag["proposals"][0]["tmdbId"], "293124")
            self.assertEqual(diag["proposals"][0]["targetSeason"], 2)
            self.assertEqual(diag["proposals"][0]["targetEpisode"], 9)
            ev = diag["proposals"][0]["existingVersion"]
            self.assertTrue(ev["exists"])
            self.assertTrue(ev["confirmedInLibrary"])
            self.assertTrue(ev["autoIgnoreEligible"])
            self.assertEqual(ev["failedSourceTag"], "Baha")
            self.assertEqual(ev["existingSourceTag"], "LINETV")
            self.assertIn("Baha版识别失败；同集已有LINETV版入库", ev["shortBanner"])


if __name__ == "__main__":
    unittest.main()
