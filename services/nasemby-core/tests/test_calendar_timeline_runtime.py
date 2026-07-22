from __future__ import annotations

import unittest
import json

from flask import Flask

from app.calendar_timeline_runtime import register_calendar_timeline


class FakeTaskService:
    def __init__(self, items=None):
        self.items = items

    def full_snapshot(self):
        items = self.items if self.items is not None else [{
            "title": "测试剧",
            "mediaType": "tv",
            "tmdbId": "101",
            "seasonNumber": 2,
            "chainId": "chain:101",
            "targetKey": "tv:tmdb:101:season:2",
            "subscriptionId": "sub-1",
            "sourceIds": {"subscriptionIds": ["sub-1"]},
            "healthState": "waiting",
            "reasonCode": "STAGE_IN_PROGRESS",
            "reasonText": "正在下载",
            "observedAt": "2026-07-22T01:30:00Z",
            "freshUntil": "2026-07-22T01:35:00Z",
            "episodeEvidence": [{
                "seasonNumber": 2,
                "episodeStart": 3,
                "episodeEnd": 3,
                "numberingScheme": "season_episode",
                "stage": "download",
                "artifactKey": "artifact:hash-1",
                "source": "qBittorrent",
                "observedAt": "2026-07-22T01:00:00Z",
                "matchMethod": "artifact_exact",
                "status": "active",
                "reasonCode": "",
                "reasonText": "",
            }],
            "stages": [
                {
                    "stage": "download", "status": "active", "evidence": "verified",
                    "observedAt": "2026-07-22T01:00:00Z", "source": "qBittorrent",
                },
                {
                    "stage": "library", "status": "waiting", "evidence": "missing",
                    "observedAt": "2026-07-22T01:30:00Z", "source": "",
                },
            ],
        }]
        return {
            "version": "tasks-v1",
            "items": items,
        }


def calendar_loader(year, month, media_type):
    return {
        "success": True,
        "year": year,
        "month": month,
        "type": media_type,
        "entries": [{
            "date": "2026-07-22",
            "key": "sub-1",
            "title": "测试剧",
            "media_type": "tv",
            "tmdb_id": "101",
            "season_number": 2,
            "episode_number": 3,
            "episode_label": "S02E03",
            "in_library": False,
        }],
        "stats": {"entries": 1, "titles": 1, "in_library": 0, "pending": 1},
        "errors": [],
    }


class CalendarTimelineRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.extensions["mcc_task_chain_v2_service"] = FakeTaskService()
        register_calendar_timeline(self.app, calendar_loader=calendar_loader)
        self.client = self.app.test_client()

    def test_calendar_combines_air_acquisition_and_task_identity(self):
        response = self.client.get("/api/v2/calendar?year=2026&month=7&type=tv")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        entry = payload["calendar"]["entries"][0]
        self.assertEqual(entry["airAt"], "2026-07-22T00:00:00+08:00")
        self.assertEqual(entry["acquiredAt"], "2026-07-22T01:00:00Z")
        self.assertEqual(entry["libraryAt"], "")
        self.assertEqual(entry["chainId"], "chain:101")
        self.assertEqual(entry["targetKey"], "tv:tmdb:101:season:2")
        self.assertEqual(payload["calendar"]["timeZone"], "Asia/Shanghai")
        self.assertEqual(payload["calendar"]["stats"]["acquired"], 1)

    def test_calendar_validates_query_and_supports_etag(self):
        invalid = self.client.get("/api/v2/calendar?month=13")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.get_json()["code"], "CALENDAR_RANGE_INVALID")
        first = self.client.get("/api/v2/calendar?year=2026&month=7")
        unchanged = self.client.get(
            "/api/v2/calendar?year=2026&month=7",
            headers={"If-None-Match": first.headers["ETag"]},
        )
        self.assertEqual(unchanged.status_code, 304)

    def test_season_level_stage_does_not_mark_episode_acquired_or_library(self):
        season_only = FakeTaskService().full_snapshot()["items"][0]
        season_only["episodeEvidence"] = []
        season_only["healthState"] = "normal"
        season_only["stages"][-1] = {
            "stage": "library",
            "status": "done",
            "evidence": "verified",
            "observedAt": "2026-07-22T02:00:00Z",
            "source": "Symedia",
        }
        self.app.extensions["mcc_task_chain_v2_service"] = FakeTaskService([season_only])

        entry = self.client.get("/api/v2/calendar?year=2026&month=7&type=tv").get_json()["calendar"]["entries"][0]

        self.assertEqual(entry["acquiredAt"], "")
        self.assertEqual(entry["libraryAt"], "")
        self.assertEqual(entry["healthState"], "evidence_insufficient")
        self.assertEqual(entry["reasonCode"], "CALENDAR_EPISODE_EVIDENCE_MISSING")

    def test_summary_and_date_detail_keep_legacy_request_compatible(self):
        legacy = self.client.get("/api/v2/calendar?year=2026&month=7&type=tv")
        summary = self.client.get("/api/v2/calendar?year=2026&month=7&type=tv&view=summary")
        detail = self.client.get("/api/v2/calendar?date=2026-07-22&type=tv&view=detail")

        self.assertTrue(legacy.get_json()["calendar"]["entries"])
        self.assertEqual(summary.get_json()["calendar"]["entries"], [])
        self.assertEqual(summary.get_json()["calendar"]["days"][0]["date"], "2026-07-22")
        self.assertEqual(summary.get_json()["calendar"]["days"][0]["statusCounts"]["acquiring"], 1)
        self.assertEqual(len(detail.get_json()["calendar"]["entries"]), 1)
        self.assertEqual(detail.get_json()["calendar"]["view"], "detail")

    def test_month_summary_stays_below_size_target(self):
        def large_loader(year, month, media_type):
            payload = calendar_loader(year, month, media_type)
            source = payload["entries"][0]
            payload["entries"] = [
                {
                    **source,
                    "key": f"sub-{index}",
                    "title": f"测试剧 {index}",
                    "episode_number": index + 1,
                    "episode_label": f"S02E{index + 1:03d}",
                }
                for index in range(892)
            ]
            return payload

        application = Flask(f"{__name__}-large")
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService([])
        register_calendar_timeline(application, calendar_loader=large_loader)
        response = application.test_client().get("/api/v2/calendar?year=2026&month=7&type=tv&view=summary")

        self.assertEqual(response.status_code, 200)
        self.assertLess(len(json.dumps(response.get_json(), ensure_ascii=False).encode("utf-8")), 200_000)
        self.assertEqual(response.get_json()["calendar"]["days"][0]["total"], 892)

    def test_invalid_detail_date_and_range_are_rejected(self):
        invalid_date = self.client.get("/api/v2/calendar?date=2026-02-30&view=detail")
        invalid_range = self.client.get("/api/v2/calendar?from=2026-07-01&to=2027-01-01&view=summary")
        missing_detail_date = self.client.get("/api/v2/calendar?view=detail")

        self.assertEqual(invalid_date.status_code, 400)
        self.assertEqual(invalid_range.status_code, 400)
        self.assertEqual(missing_detail_date.status_code, 400)


if __name__ == "__main__":
    unittest.main()
