from __future__ import annotations

import unittest

from flask import Flask

from app.calendar_timeline_runtime import register_calendar_timeline


class FakeTaskService:
    def full_snapshot(self):
        return {
            "version": "tasks-v1",
            "items": [{
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
            }],
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


if __name__ == "__main__":
    unittest.main()
