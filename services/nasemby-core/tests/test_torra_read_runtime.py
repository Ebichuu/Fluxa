from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlsplit


MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, urlsplit(url).path, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeTorraClient:
    def get_summary(self):
        return {
            "configured": True,
            "connected": True,
            "webUrl": "http://torra.example.test:9029",
            "lastCheckedAt": "2026-07-16T11:00:00.000Z",
            "counts": {"total": 3, "active": 1, "completed": 1, "running": 1},
        }


class TorraReadRuntimeContractTests(unittest.TestCase):
    def test_unconfigured_summary_and_public_route_boundary(self):
        from app import main

        application = main.create_app(access_environment={})
        response = application.test_client().get("/api/torra/summary")
        routes = {
            (rule.rule, method)
            for rule in application.url_map.iter_rules()
            for method in (rule.methods or set())
        }

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["configured"])
        self.assertFalse(payload["connected"])
        self.assertEqual(payload["counts"], {
            "total": 0,
            "active": 0,
            "completed": 0,
            "running": 0,
        })
        self.assertEqual(payload["error"], "未配置 Torra 地址或认证信息")
        self.assertIn(("/api/torra/summary", "GET"), routes)
        self.assertNotIn(("/api/v1/subscriptions/save", "POST"), routes)

    def test_token_summary_extracts_nested_rows_and_counts(self):
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        rows = [
            {"id": "active", "enabled": True, "completed": False},
            {"id": "completed", "enabled": True, "completed": True},
            {"id": "disabled", "enabled": False, "completed": False, "is_running": True},
        ]
        session = FakeSession([
            FakeResponse(payload={"data": {"subscriptions": rows}}),
            FakeResponse(status=404, payload={}),
            FakeResponse(status=404, payload={}),
        ])
        client = TorraReadClient(
            TorraReadConfig(base_url="http://torra.example.test:9029/", token="fixed-token"),
            session=session,
            clock=lambda: datetime(2026, 7, 16, 11, 0, tzinfo=timezone.utc),
        )

        summary = client.get_summary()

        self.assertEqual(summary, {
            "configured": True,
            "connected": True,
            "webUrl": "http://torra.example.test:9029",
            "lastCheckedAt": "2026-07-16T11:00:00.000Z",
            "counts": {"total": 3, "active": 1, "completed": 1, "running": 1},
            "searchAutomation": {
                "capabilityState": "unsupported",
                "subscriptionModes": {
                    "state": "unsupported",
                    "counts": {
                        "rssPreferred": None,
                        "automaticSearch": None,
                        "unknown": 3,
                    },
                    "reasonCode": "TORRA_SUBSCRIPTION_MODE_NOT_EXPOSED",
                    "reasonText": "Torra 未提供可确认的订阅级搜索模式",
                },
                "schedules": {
                    "state": "unsupported",
                    "rss": None,
                    "automaticSearch": None,
                    "reasonCode": "TORRA_SCHEDULES_ENDPOINT_UNAVAILABLE",
                },
                "recentBatchState": "unsupported",
                "recentBatch": None,
                "recentBatchReasonCode": "TORRA_BATCH_HISTORY_ENDPOINT_UNAVAILABLE",
                "adjustmentPreview": {
                    "state": "blocked",
                    "canApply": False,
                    "eligibleSubscriptions": 0,
                    "blockedSubscriptions": 3,
                    "reasonCode": "TORRA_SUBSCRIPTION_MODE_NOT_EXPOSED",
                    "reasonText": "无法安全确认哪些订阅可调整为 RSS 优先",
                },
            },
        })
        self.assertEqual(session.requests[0][1], "/api/v1/subscriptions")
        self.assertEqual(session.requests[0][2]["headers"]["Authorization"], "Bearer fixed-token")

    def test_summary_reads_official_search_batch_evidence_without_exposing_ids(self):
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        rows = [
            {"id": "remote-subscription-a", "enabled": True, "completed": False},
            {"id": "remote-subscription-b", "enabled": True, "completed": False},
        ]
        session = FakeSession([
            FakeResponse(payload={"data": {"subscriptions": rows}}),
            FakeResponse(payload={"data": {"items": [
                {
                    "id": "subscription_batch:rss",
                    "enabled": True,
                    "last_run_at": "2026-07-30T08:00:00+08:00",
                    "next_run_at": "2026-07-30T08:30:00+08:00",
                },
                {
                    "id": "subscription_batch:auto",
                    "enabled": False,
                    "last_run_at": "2026-07-29T08:00:00+08:00",
                    "next_run_at": "",
                },
            ]}}),
            FakeResponse(payload={"data": {"items": [
                {
                    "id": "private-job-id",
                    "kind": "subscription.batch_run",
                    "status": "success",
                    "trigger_source": "scheduler",
                    "created_at": "2026-07-30T08:00:00+08:00",
                    "started_at": "2026-07-30T08:00:01+08:00",
                    "finished_at": "2026-07-30T08:03:00+08:00",
                },
            ]}}),
            FakeResponse(payload={"data": {
                "id": "private-job-id",
                "kind": "subscription.batch_run",
                "status": "success",
                "trigger_source": "scheduler",
                "started_at": "2026-07-30T08:00:01+08:00",
                "finished_at": "2026-07-30T08:03:00+08:00",
                "payload": {
                    "mode_override": "rss",
                    "subscription_ids": ["remote-subscription-a", "remote-subscription-b"],
                },
                "result": {
                    "subscription_count": 2,
                    "site_request_count": 6,
                },
            }}),
        ])
        client = TorraReadClient(
            TorraReadConfig(base_url="http://torra.example.test", token="fixed-token"),
            session=session,
            clock=lambda: datetime(2026, 7, 30, 0, 5, tzinfo=timezone.utc),
        )

        summary = client.get_summary()

        automation = summary["searchAutomation"]
        self.assertEqual(automation["capabilityState"], "partial")
        self.assertEqual(automation["subscriptionModes"]["counts"], {
            "rssPreferred": None,
            "automaticSearch": None,
            "unknown": 2,
        })
        self.assertEqual(automation["schedules"], {
            "state": "confirmed",
            "rss": {
                "registered": True,
                "enabled": True,
                "lastRunAt": "2026-07-30T08:00:00+08:00",
                "nextRunAt": "2026-07-30T08:30:00+08:00",
            },
            "automaticSearch": {
                "registered": True,
                "enabled": False,
                "lastRunAt": "2026-07-29T08:00:00+08:00",
                "nextRunAt": "",
            },
            "reasonCode": "",
        })
        self.assertEqual(automation["recentBatchState"], "confirmed")
        self.assertEqual(automation["recentBatch"], {
            "mode": "rss",
            "status": "success",
            "trigger": "scheduler",
            "startedAt": "2026-07-30T08:00:01+08:00",
            "finishedAt": "2026-07-30T08:03:00+08:00",
            "subscriptionCount": 2,
            "estimatedSiteRequests": 6,
        })
        self.assertEqual(automation["adjustmentPreview"]["blockedSubscriptions"], 2)
        self.assertNotIn("private-job-id", str(summary))
        self.assertNotIn("remote-subscription-a", str(summary))
        self.assertEqual(
            [(request[0], request[1]) for request in session.requests],
            [
                ("GET", "/api/v1/subscriptions"),
                ("GET", "/api/v1/jobs/schedules"),
                ("GET", "/api/v1/jobs"),
                ("GET", "/api/v1/jobs/private-job-id"),
            ],
        )

    def test_search_batch_unknown_mode_is_not_inferred_from_titles(self):
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        session = FakeSession([
            FakeResponse(payload={"data": {"subscriptions": [{"id": "remote-a"}]}}),
            FakeResponse(payload={"data": []}),
            FakeResponse(payload={"data": {"items": [{
                "id": "job-a",
                "kind": "subscription.batch_run",
                "status": "running",
                "display_name": "RSS 自动搜索批次",
                "created_at": "2026-07-30T09:00:00+08:00",
            }]}}),
            FakeResponse(payload={"data": {
                "id": "job-a",
                "kind": "subscription.batch_run",
                "status": "running",
                "display_name": "RSS 自动搜索批次",
                "payload": {"subscription_ids": ["remote-a"]},
            }}),
        ])
        client = TorraReadClient(
            TorraReadConfig(base_url="http://torra.example.test", token="fixed-token"),
            session=session,
        )

        automation = client.get_summary()["searchAutomation"]

        self.assertEqual(automation["recentBatchState"], "confirmed")
        self.assertEqual(automation["recentBatch"]["mode"], "unknown")
        self.assertEqual(automation["recentBatch"]["subscriptionCount"], 1)
        self.assertNotIn("RSS 自动搜索批次", str(automation))

    def test_password_auth_relogs_once_after_unauthorized(self):
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        session = FakeSession([
            FakeResponse(payload={"access_token": "token-one"}),
            FakeResponse(status=401, payload={}),
            FakeResponse(payload={"token": "token-two"}),
            FakeResponse(payload={"subscriptions": []}),
        ])
        client = TorraReadClient(
            TorraReadConfig(
                base_url="http://torra.example.test",
                username="user",
                password="password",
            ),
            session=session,
        )

        self.assertEqual(client.list_subscriptions(), [])
        self.assertEqual([item[0] for item in session.requests], ["POST", "GET", "POST", "GET"])
        self.assertEqual(session.requests[0][2]["data"], {
            "username": "user",
            "password": "password",
        })
        self.assertEqual(session.requests[1][2]["headers"]["Authorization"], "Bearer token-one")
        self.assertEqual(session.requests[3][2]["headers"]["Authorization"], "Bearer token-two")

    def test_meta_weight_rules_use_only_the_official_read_endpoint(self):
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        rules = [{"id": "rule-1", "media_type": "tv", "category": ["tv::anime"]}]
        session = FakeSession([FakeResponse(payload={"success": True, "data": rules})])
        client = TorraReadClient(
            TorraReadConfig(base_url="http://torra.example.test", token="fixed-token"),
            session=session,
        )

        self.assertEqual(client.list_meta_weight_rules(), rules)
        self.assertEqual(
            [(request[0], request[1]) for request in session.requests],
            [("GET", "/api/v1/meta_weight/rules")],
        )

        invalid = TorraReadClient(
            TorraReadConfig(base_url="http://torra.example.test", token="fixed-token"),
            session=FakeSession([FakeResponse(payload={"success": True, "data": {}})]),
        )
        with self.assertRaisesRegex(RuntimeError, "rule response is invalid"):
            invalid.list_meta_weight_rules()

    def test_secupload_summary_exposes_only_readable_plugin_evidence(self):
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        session = FakeSession([FakeResponse(payload={
            "success": True,
            "data": {
                "manifest": {"key": "secupload_115", "enabled": True},
                "config_items": [{
                    "item_id": "category-tv",
                    "name": "电视剧",
                    "enabled": True,
                    "values": {
                        "cookie": "must-not-escape",
                        "temp_path": "/private/pending",
                        "fallback_upload_after_failures": "3",
                        "notify_times": 3,
                    },
                    "updated_at": "2026-07-23T15:00:00",
                }],
                "tasks": [{
                    "key": "retry_pending",
                    "name": "重试临时目录",
                    "allow_schedule": True,
                    "allow_manual_run": True,
                }],
                "schedules": [{
                    "task_key": "retry_pending",
                    "target_item_id": "category-tv",
                    "enabled": True,
                    "cron": "0 */8 * * *",
                    "next_run_at": "2026-07-24T00:00:00+08:00",
                    "last_run_at": "2026-07-23T16:00:00",
                }],
                "recent_runs": [{
                    "run_id": "run-1",
                    "task_key": "retry_pending",
                    "target_item_id": "category-tv",
                    "trigger": "schedule",
                    "status": "success",
                    "message": "电视剧 临时目录重试完成，成功 4 个，失败 1 个",
                    "started_at": "2026-07-23T16:00:00",
                    "finished_at": "2026-07-23T16:00:07",
                    "created_at": "2026-07-23T16:00:00",
                }],
            },
        })])
        client = TorraReadClient(
            TorraReadConfig(base_url="http://torra.example.test", token="fixed-token"),
            session=session,
            clock=lambda: datetime(2026, 7, 23, 8, 30, tzinfo=timezone.utc),
        )

        summary = client.get_secupload_summary()

        self.assertTrue(summary["connected"])
        self.assertTrue(summary["readable"])
        self.assertFalse(summary["perFileEvidence"])
        self.assertEqual(summary["activeRuns"], 0)
        self.assertEqual(summary["latestRun"]["counts"], {"success": 4, "failed": 1})
        self.assertEqual(summary["nextRunAt"], "2026-07-24T00:00:00+08:00")
        self.assertEqual(summary["configItems"], [{
            "itemId": "category-tv",
            "name": "电视剧",
            "enabled": True,
            "updatedAt": "2026-07-23T15:00:00",
            "fallbackUploadAfterFailures": 3,
            "notifyAfterFailures": 3,
        }])
        self.assertNotIn("must-not-escape", str(summary))
        self.assertNotIn("/private/pending", str(summary))
        self.assertEqual(session.requests[0][1], "/api/v1/plugins/secupload_115")

    def test_secupload_summary_aggregates_latest_scheduled_batch(self):
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        recent_runs = [
            {
                "run_id": f"run-{index}",
                "task_key": "retry_pending",
                "target_item_id": f"category-{index}",
                "trigger": "schedule",
                "status": "success",
                "message": f"任务完成，成功 {success} 个，失败 {failed} 个",
                "started_at": f"2026-07-24T08:00:0{index}",
                "finished_at": f"2026-07-24T08:00:1{index}",
            }
            for index, (success, failed) in enumerate(((6, 0), (0, 0), (8, 0)))
        ]
        session = FakeSession([FakeResponse(payload={
            "data": {
                "manifest": {"enabled": True},
                "recent_runs": [
                    *recent_runs,
                    {
                        "run_id": "old",
                        "task_key": "retry_pending",
                        "target_item_id": "category-old",
                        "trigger": "schedule",
                        "status": "success",
                        "message": "任务完成，成功 3 个，失败 1 个",
                        "started_at": "2026-07-24T00:00:00",
                        "finished_at": "2026-07-24T00:00:05",
                    },
                ],
                "schedules": [{
                    "task_key": "retry_pending",
                    "target_item_id": "category-0",
                    "enabled": True,
                    "next_run_at": "2026-07-24T16:00:00+08:00",
                }],
            },
        })])
        client = TorraReadClient(
            TorraReadConfig(base_url="http://torra.example.test", token="fixed-token"),
            session=session,
        )

        summary = client.get_secupload_summary()

        self.assertEqual(summary["latestBatch"]["runCount"], 3)
        self.assertEqual(summary["latestBatch"]["counts"], {"success": 14, "failed": 0})
        self.assertEqual(summary["latestBatch"]["startedAt"], "2026-07-24T08:00:00")
        self.assertEqual(summary["recentBatches"][1]["counts"], {"success": 3, "failed": 1})
        self.assertEqual(summary["nextRunAt"], "2026-07-24T16:00:00+08:00")

    def test_secupload_summary_prefers_structured_counts_and_sanitizes_file_details(self):
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        private_path = "/private/pending/Show.S01E01.mkv"
        session = FakeSession([FakeResponse(payload={
            "data": {
                "manifest": {"enabled": True},
                "schedules": [{
                    "task_key": "retry_pending",
                    "target_item_id": "category-tv",
                    "enabled": True,
                    "next_run_at": "2026-07-24T16:00:00+08:00",
                }],
                "recent_runs": [{
                    "run_id": "private-run-1",
                    "task_key": "retry_pending",
                    "target_item_id": "category-tv",
                    "trigger": "schedule",
                    "status": "success",
                    "message": "旧文本，成功 99 个，失败 99 个",
                    "started_at": "2026-07-24T08:00:00+08:00",
                    "finished_at": "2026-07-24T08:00:06+08:00",
                    "result": {
                        "success_count": 2,
                        "failed_count": 2,
                        "failure_details": [
                            {
                                "file_name": "Show.S01E01.mkv",
                                "path": private_path,
                                "outcome": "pending_failed",
                                "attempts": 4,
                                "last_error": "network timeout token=must-not-escape",
                                "last_attempt_at": "2026-07-24T08:00:05+08:00",
                            },
                            {
                                "file_name": "Show.S01E01.mkv",
                                "path": private_path,
                                "outcome": "pending_failed",
                                "attempts": 3,
                                "last_error": "duplicate private error",
                            },
                            {
                                "path": r"C:\private\Show.S01E02.mkv",
                                "outcome": "sample_failed",
                                "attempts": "2",
                                "last_error": "cookie authentication failed at http://private.example.test",
                            },
                        ],
                    },
                }],
            },
        })])
        client = TorraReadClient(
            TorraReadConfig(base_url="http://torra.example.test", token="fixed-token"),
            session=session,
        )

        summary = client.get_secupload_summary()

        self.assertEqual(summary["latestRun"]["counts"], {"success": 2, "failed": 2})
        self.assertEqual(summary["latestBatch"]["counts"], {"success": 2, "failed": 2})
        self.assertTrue(summary["perFileEvidence"])
        self.assertEqual(len(summary["failureFiles"]), 2)
        self.assertEqual(
            [(row["displayName"], row["errorCategory"], row["retryCount"]) for row in summary["failureFiles"]],
            [
                ("Show.S01E01.mkv", "network_failed", 4),
                ("Show.S01E02.mkv", "authentication_failed", 2),
            ],
        )
        self.assertTrue(all(row["plannedRetryAt"] == "2026-07-24T16:00:00+08:00" for row in summary["failureFiles"]))
        serialized = str(summary)
        for private_value in (private_path, r"C:\private", "must-not-escape", "private.example.test"):
            self.assertNotIn(private_value, serialized)

    def test_secupload_summary_accepts_result_arrays_and_missing_retry_count(self):
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        session = FakeSession([FakeResponse(payload={
            "data": {
                "manifest": {"enabled": True},
                "recent_runs": [{
                    "task_key": "retry_pending",
                    "target_item_id": "category-tv",
                    "trigger": "manual",
                    "status": "failed",
                    "message": "任务运行失败",
                    "started_at": "2026-07-24T09:00:00+08:00",
                    "finished_at": "2026-07-24T09:00:02+08:00",
                    "result": [{
                        "success_count": 0,
                        "failed_count": 1,
                        "failure_details": {
                            "/private/pending/Movie.2026.mkv": {
                                "outcome": "pending_failed",
                                "last_error": "unknown failure",
                            },
                        },
                    }],
                }],
            },
        })])

        summary = TorraReadClient(
            TorraReadConfig(base_url="http://torra.example.test", token="fixed-token"),
            session=session,
        ).get_secupload_summary()

        self.assertEqual(summary["latestRun"]["counts"], {"success": 0, "failed": 1})
        self.assertEqual(summary["failureFiles"][0]["displayName"], "Movie.2026.mkv")
        self.assertIsNone(summary["failureFiles"][0]["retryCount"])
        self.assertNotIn("/private/pending", str(summary))

    def test_secupload_retry_run_uses_official_task_route_and_returns_new_run_id(self):
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        session = FakeSession([FakeResponse(payload={
            "success": True,
            "data": {"run_id": "run-manual-1"},
        })])
        client = TorraReadClient(
            TorraReadConfig(base_url="http://torra.example.test", token="fixed-token"),
            session=session,
        )

        result = client.run_secupload_retry("category-tv", previous_run_ids=["run-old"])

        self.assertEqual(result, {"runId": "run-manual-1"})
        method, path, kwargs = session.requests[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/api/v1/plugins/secupload_115/tasks/retry_pending/run")
        self.assertEqual(kwargs["json"]["target_item_id"], "category-tv")

    def test_secupload_retry_run_rejects_missing_or_stale_run_id(self):
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        session = FakeSession([FakeResponse(payload={
            "success": True,
            "data": {"run_id": "run-old"},
        })])
        client = TorraReadClient(
            TorraReadConfig(base_url="http://torra.example.test", token="fixed-token"),
            session=session,
        )

        with self.assertRaises(RuntimeError):
            client.run_secupload_retry("category-tv", previous_run_ids=["run-old"])

    def test_duplicate_matching_prefers_tmdb_type_and_season(self):
        from app.torra_read_runtime import find_subscription

        rows = [
            {"id": "wrong-season", "tmdb_id": 100, "media_type": "tv", "season_number": 1},
            {"id": "right-season", "tmdb_id": "100", "media_type": "series", "season_number": 2},
            {"id": "movie", "name": "测试 电影", "media_type": "电影", "year": "2026"},
        ]

        self.assertEqual(find_subscription(rows, {
            "mediaType": "tv",
            "tmdbId": "100",
            "seasonNumber": 2,
            "title": "无关标题",
            "year": "2026",
        })["id"], "right-season")
        self.assertEqual(find_subscription(rows, {
            "mediaType": "movie",
            "tmdbId": "",
            "seasonNumber": 0,
            "title": "测试电影",
            "year": "2026",
        })["id"], "movie")

    def test_safe_push_uses_nonempty_path_downloader_and_runs_saved_subscription(self):
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        session = FakeSession([
            FakeResponse(payload={"subscriptions": []}),
            FakeResponse(payload={"success": True, "message": "saved"}),
            FakeResponse(payload={"success": True, "message": "running"}),
        ])
        client = TorraReadClient(
            TorraReadConfig(base_url="http://torra.example.test", token="fixed-token"),
            session=session,
        )
        subscription = {
            "id": "mcc_tv_100_2",
            "name": "测试剧",
            "media_type": "tv",
            "tmdb_id": 100,
            "season_number": 2,
            "year": "2026",
            "downloader_id": "downloader-1",
            "save_path": "/downloads/03-日韩剧",
        }

        result = client.push_subscription(subscription)

        self.assertTrue(result["success"])
        self.assertTrue(result["pushed"])
        self.assertTrue(result["searchTriggered"])
        self.assertEqual([request[0] for request in session.requests], ["GET", "POST", "POST"])
        self.assertEqual(session.requests[1][1], "/api/v1/subscriptions/save")
        self.assertEqual(session.requests[1][2]["json"], {"subscription": subscription})
        self.assertEqual(session.requests[2][1], "/api/v1/subscriptions/run/mcc_tv_100_2")

    def test_network_error_is_safe_and_injected_route_works(self):
        import requests

        from app import main
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        session = Mock()
        session.request.side_effect = requests.ConnectionError(
            "failed http://torra.invalid/api/v1/subscriptions?token=must-not-escape"
        )
        failed = TorraReadClient(
            TorraReadConfig(base_url="http://torra.invalid", token="must-not-escape"),
            session=session,
        ).get_summary()
        self.assertFalse(failed["connected"])
        self.assertEqual(failed["error"], "Torra 请求失败")
        self.assertNotIn("must-not-escape", str(failed))

        response = main.create_app(
            access_environment={},
            torra_client_factory=lambda _config: FakeTorraClient(),
        ).test_client().get("/api/torra/summary")
        self.assertTrue(response.get_json()["connected"])


if __name__ == "__main__":
    unittest.main()
