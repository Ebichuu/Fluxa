from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.adapters.symedia import classify_symedia_record
from app.domain.pipeline import build_actionable_issues, build_media_pipeline
from app.storage import FluxaStore


class _StubAdapter:
    configured = False
    def app_url(self, *args, **kwargs):
        return "http://example.local"
    def get_details(self, media_type, tmdb_id):
        return {"mediaKey": f"{media_type}:{tmdb_id}", "mediaType": media_type, "tmdbId": tmdb_id, "title": "三体", "originalTitle": "Three-Body", "year": "2026", "posterUrl": "", "overview": "", "seasons": [{"seasonNumber": 1}]}
    def get_season_episodes(self, tmdb_id, season_number):
        return [{"seasonNumber": 1, "episodeNumber": 1, "episodeLabel": "S01E01", "name": "第一集", "airDate": "2026-11-20"}]


class PipelineTests(unittest.TestCase):
    def test_symedia_low_score_is_protected_not_error(self):
        outcome, label, _ = classify_symedia_record({"status": False, "errmsg": "权重得分低于现有版本，跳过覆盖"})
        self.assertEqual(outcome, "rule_protected")
        self.assertIn("正常权重拒绝", label)

    def test_pipeline_honesty_unconfirmed_115_and_no_false_sa_alarm(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FluxaStore(Path(tmp) / "test.sqlite3")
            snapshot = {
                "generatedAt": "2026-11-26T18:00:00+08:00",
                "services": [
                    {"service": "moviepilot", "name": "MoviePilot Neo", "configured": True, "connected": True, "status": "ok", "message": "在线"},
                    {"service": "torra", "name": "Torra", "configured": True, "connected": True, "status": "ok", "message": "在线"},
                    {"service": "qbittorrent", "name": "qBittorrent", "configured": True, "connected": True, "status": "ok", "message": "在线"},
                    {"service": "symedia", "name": "Symedia (SA)", "configured": True, "connected": True, "status": "ok", "message": "在线"},
                    {"service": "emby", "name": "Emby", "configured": True, "connected": True, "status": "ok", "message": "在线"},
                ],
                "serviceMap": {
                    "moviepilot": {"connected": True},
                    "torra": {"connected": True},
                    "qbittorrent": {"connected": True},
                    "symedia": {"connected": True},
                    "emby": {"connected": True},
                },
                "mpSubs": [{"source": "moviepilot", "sourceLabel": "MoviePilot Neo", "subscriptionId": "12", "mediaKey": "tv:999", "mediaType": "tv", "tmdbId": "999", "title": "三体", "year": "2026", "seasonNumber": 1, "ruleSummary": "MP 默认规则", "originReason": "手动", "updatedAt": "", "webUrl": ""}],
                "mpHistory": [],
                "torraSubs": [],
                "torraSecupload": {"available": True, "activeRuns": [], "successFiles": [], "failedFiles": [], "recentRuns": [], "webUrl": ""},
                "qbTasks": [{"hash": "a" * 40, "name": "三体.S01E01.2160p", "progress": 1.0, "progressPercent": 100.0, "status": "seeding", "statusLabel": "做种中（保种保护）", "rawState": "uploading", "dlspeed": 0, "upspeed": 1024, "isSeeding": True, "seedingProtected": True, "webUrl": ""}],
                "saHistory": [{"id": "1", "mediaKey": "tv:999", "title": "三体", "fileName": "三体.S01E01.mkv", "seasons": "S01", "episodes": "E01", "outcome": "rule_protected", "outcomeLabel": "被 SA 权重规则拒绝（正常保护）", "reasonText": "已有更高分版本", "webUrl": ""}],
                "emby": {"available": True, "movies": {}, "series": {"999": {"itemId": "emby1", "title": "三体", "webUrl": ""}}, "episodes": {"999": [[1, 1]]}, "recentToday": []},
            }
            issues = build_actionable_issues(snapshot, {})
            self.assertEqual(len(issues), 0)  # SA rule_protected must NOT produce a red error!

            stub = _StubAdapter()
            adapters = {"moviepilot": stub, "torra": stub, "qbittorrent": stub, "symedia": stub, "emby": stub, "tmdb": stub}
            pipeline = build_media_pipeline(media_type="tv", tmdb_id="999", snapshot=snapshot, adapters=adapters, store=store)
            steps = {s["step"]: s for s in pipeline["steps"]}
            # qB completed does NOT fake 115 upload success -> must be unconfirmed!
            self.assertEqual(steps["cloud115"]["status"], "unconfirmed")
            self.assertEqual(steps["cloud115"]["statusLabel"], "未确认")
            self.assertEqual(steps["symedia"]["status"], "protected")
            self.assertEqual(steps["emby"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
