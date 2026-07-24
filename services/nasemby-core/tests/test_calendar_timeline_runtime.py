from __future__ import annotations

import unittest
import json
from unittest.mock import patch

from flask import Flask

from app.calendar_timeline_runtime import _entry_status, register_calendar_timeline


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


class FakeReconciliationService:
    def __init__(self, rows, source_error=""):
        self.rows = rows
        self.source_error = source_error
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return {
            "sourceError": self.source_error,
            "items": self.rows,
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

    def test_library_evidence_sets_in_library_and_removes_later_acquisition_time(self):
        item = FakeTaskService().full_snapshot()["items"][0]
        item["episodeEvidence"] = [
            {
                "seasonNumber": 2,
                "episodeStart": 3,
                "episodeEnd": 3,
                "numberingScheme": "season_episode",
                "stage": "download",
                "source": "qBittorrent",
                "observedAt": "2026-07-22T10:17:00Z",
                "status": "done",
            },
            {
                "seasonNumber": 2,
                "episodeStart": 3,
                "episodeEnd": 3,
                "numberingScheme": "season_episode",
                "stage": "library",
                "source": "Symedia",
                "observedAt": "2026-07-22T08:01:00Z",
                "status": "done",
            },
        ]
        self.app.extensions["mcc_task_chain_v2_service"] = FakeTaskService([item])

        calendar = self.client.get("/api/v2/calendar?year=2026&month=7&type=tv").get_json()["calendar"]
        entry = calendar["entries"][0]

        self.assertEqual(entry["libraryAt"], "2026-07-22T08:01:00Z")
        self.assertEqual(entry["acquiredAt"], "")
        self.assertTrue(entry["inLibrary"])
        self.assertEqual(entry["status"], "library")
        self.assertEqual(calendar["stats"]["inLibrary"], 1)
        self.assertEqual(calendar["stats"]["pending"], 0)

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

    def test_past_episode_requires_explicit_follow_scope_before_marking_missing(self):
        base = {
            "date": "2001-01-01",
            "airAt": "2001-01-01T00:00:00+08:00",
            "subscriptionCreatedAt": "2000-12-01T00:00:00Z",
            "allowedDelayHours": 24,
        }

        self.assertEqual(_entry_status(base, "2026-07-23"), "unknown")
        self.assertEqual(_entry_status({**base, "followScopeExplicit": True}, "2026-07-23"), "missing")
        self.assertEqual(_entry_status({
            **base,
            "followScopeExplicit": True,
            "subscriptionCreatedAt": "2002-01-01T00:00:00Z",
        }, "2026-07-23"), "unknown")

    def test_calendar_hides_episode_aired_before_subscription_without_history_scope(self):
        def pre_subscription_loader(year, month, media_type):
            return {
                "success": True,
                "year": year,
                "month": month,
                "type": media_type,
                "entries": [{
                    "date": "2026-07-18",
                    "key": "sub-late",
                    "title": "晚订阅剧",
                    "media_type": "tv",
                    "tmdb_id": "404",
                    "season_number": 1,
                    "episode_number": 1,
                    "subscription_created_at": "2026-07-24T00:00:00Z",
                    "follow_scope_explicit": True,
                    "include_past_episodes": False,
                }],
                "stats": {"entries": 1, "titles": 1, "in_library": 0, "pending": 1},
                "errors": [],
            }

        application = Flask(f"{__name__}-pre-subscription")
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService([])
        register_calendar_timeline(application, calendar_loader=pre_subscription_loader)
        payload = application.test_client().get("/api/v2/calendar?year=2026&month=7&type=tv").get_json()["calendar"]

        self.assertEqual(payload["entries"], [])
        self.assertEqual(payload["stats"]["excludedBeforeSubscription"], 1)
        self.assertEqual(payload["stats"]["statusCounts"]["unknown"], 0)

    def test_protection_evidence_is_not_counted_as_missing(self):
        entry = {
            "date": "2001-01-01",
            "followScopeExplicit": True,
            "healthState": "protected",
            "reasonCode": "QUALITY_LOWER_THAN_TARGET",
        }

        self.assertEqual(_entry_status(entry, "2026-07-23"), "protected")

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

    def test_calendar_includes_only_torra_rows_without_creating_local_subscription(self):
        reconciliation = FakeReconciliationService([{
            "id": "torra:remote-ref",
            "remoteRef": "remote-ref",
            "title": "远端追更",
            "mediaType": "tv",
            "tmdbId": "202",
            "seasonNumber": 1,
            "reconciliationState": "only_torra",
            "observedAt": "2026-07-23T00:00:00Z",
        }])
        self.app.extensions["mcc_subscription_reconciliation"] = reconciliation

        def remote_entries(item, year, month, media_type):
            self.assertTrue(item["read_only"])
            self.assertEqual(item["source_label"], "Torra 只读追更")
            self.assertEqual(item["tmdb_id"], "202")
            return ([{
                "date": "2026-07-23",
                "key": item["subscription_key"],
                "title": item["title"],
                "media_type": "tv",
                "tmdb_id": item["tmdb_id"],
                "source_label": item["source_label"],
                "season_number": 1,
                "episode_number": 1,
                "episode_label": "S01E01",
                "in_library": False,
                "subscription_created_at": item["subscribed_at"],
                "follow_scope_explicit": True,
                "include_past_episodes": False,
                "allowed_delay_hours": 24,
            }], "")

        with patch("app.calendar_timeline_runtime.discover_runtime.build_subscription_calendar_entries_for_item", remote_entries):
            payload = self.client.get("/api/v2/calendar?year=2026&month=7&type=tv").get_json()

        remote = next(entry for entry in payload["calendar"]["entries"] if entry["tmdbId"] == "202")
        self.assertEqual(remote["sourceLabel"], "Torra 只读追更")
        self.assertEqual(remote["subscriptionCreatedAt"], "2026-07-23T00:00:00Z")
        self.assertEqual(reconciliation.calls, 1)

    def test_torra_calendar_source_error_is_public_and_not_raw(self):
        self.app.extensions["mcc_subscription_reconciliation"] = FakeReconciliationService(
            [],
            source_error="token=must-not-escape /private/source",
        )

        payload = self.client.get("/api/v2/calendar?year=2026&month=7&type=tv").get_json()

        errors = payload["calendar"]["errors"]
        self.assertIn("Torra 只读追更暂时无法读取", errors)
        self.assertNotIn("must-not-escape", str(errors))
        self.assertNotIn("/private/source", str(errors))

    def test_invalid_detail_date_and_range_are_rejected(self):
        invalid_date = self.client.get("/api/v2/calendar?date=2026-02-30&view=detail")
        invalid_range = self.client.get("/api/v2/calendar?from=2026-07-01&to=2027-01-01&view=summary")
        missing_detail_date = self.client.get("/api/v2/calendar?view=detail")

        self.assertEqual(invalid_date.status_code, 400)
        self.assertEqual(invalid_range.status_code, 400)
        self.assertEqual(missing_detail_date.status_code, 400)


if __name__ == "__main__":
    unittest.main()
