from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.adapters.symedia import classify_symedia_record
from app.domain.pipeline import build_actionable_issues
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


if __name__ == "__main__":
    unittest.main()
