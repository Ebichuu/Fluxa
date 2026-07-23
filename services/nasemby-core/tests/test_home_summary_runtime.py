from __future__ import annotations

import unittest
from datetime import datetime, timezone

from flask import Flask

from app.health_state_runtime import SchedulerStatusRegistry
from app.home_summary_runtime import HomeSummaryService, register_home_summary


NOW = datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc)


class FakeTaskChainService:
    def __init__(self, payload):
        self.payload = payload

    def get_chain(self):
        return self.payload


class FakeRssRepository:
    def summary(self, enabled):
        return {"enabled": enabled, "items": 347, "matches": 0, "matcherRan": False, "errorSources": 0, "lastSuccessAt": "2026-07-22T01:55:00Z"}


class FakeRssService:
    repository = FakeRssRepository()

    def collection_enabled(self):
        return True


def item(*, item_id="chain-1", updated_at="2026-07-22T01:00:00Z", library_status="done", library_time="2026-07-22T01:00:00Z"):
    return {
        "id": item_id,
        "title": "测试剧",
        "mediaType": "tv",
        "tmdbId": "123",
        "seasonNumber": 1,
        "state": "completed" if library_status == "done" else "waiting",
        "updatedAt": updated_at,
        "steps": [
            {"key": "download", "status": "done", "evidence": "verified"},
            {"key": "library", "status": library_status, "evidence": "verified", "timestamp": library_time},
        ],
    }


def protected_item():
    value = item(library_status="blocked")
    value["state"] = "blocked"
    value["steps"][-1].update({
        "detail": "现有版本评分更高，跳过归档",
        "source": "Symedia",
    })
    return value


def chain_payload(items):
    return {
        "generatedAt": "2026-07-22T02:00:00Z",
        "items": items,
        "services": {
            name: {"connected": True, "error": ""}
            for name in ("torra", "qb", "symedia", "emby")
        },
    }


class HomeSummaryRuntimeTests(unittest.TestCase):
    def build_app(self, items, *, scheduler_enabled=False, scheduler_started=False):
        app = Flask(__name__)
        app.extensions["mcc_task_chain_service"] = FakeTaskChainService(chain_payload(items))
        registry = SchedulerStatusRegistry(clock=lambda: "2026-07-22T02:00:00Z")
        registry.register("subscription-task", enabled=scheduler_enabled)
        if scheduler_started:
            registry.mark_started("subscription-task")
        app.extensions["mcc_scheduler_status"] = registry
        return app

    def test_today_ingest_uses_library_evidence_and_deduplicates_target(self):
        app = self.build_app([
            item(item_id="old", updated_at="2026-07-22T00:30:00Z", library_time="2026-07-21T23:00:00Z"),
            item(item_id="new", updated_at="2026-07-22T01:30:00Z"),
        ])

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()

        self.assertEqual(result["counts"]["ingestedToday"], 1)
        self.assertEqual(result["counts"]["pending"], 0)

    def test_enabled_scheduler_without_runtime_is_not_reported_normal(self):
        app = self.build_app([item()], scheduler_enabled=True)

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()

        self.assertEqual(result["healthState"], "evidence_insufficient")
        scheduler_issue = next(issue for issue in result["issues"] if issue["source"] == "subscription-scheduler")
        self.assertEqual(scheduler_issue["reasonCode"], "SCHEDULER_NOT_STARTED")

    def test_endpoint_returns_actionable_service_failure(self):
        app = self.build_app([item()], scheduler_enabled=True, scheduler_started=True)
        app.extensions["mcc_task_chain_service"].payload["services"]["symedia"] = {
            "connected": False,
            "error": "连接超时",
        }
        register_home_summary(app, clock=lambda: NOW)

        payload = app.test_client().get("/api/v2/home/summary").get_json()

        self.assertEqual(payload["healthState"], "action_required")
        self.assertEqual(payload["counts"]["actionRequired"], 1)
        self.assertTrue(any(issue["source"] == "symedia" for issue in payload["issues"]))

    def test_collected_rss_without_matcher_run_is_insufficient_evidence(self):
        app = self.build_app([item()], scheduler_enabled=True, scheduler_started=True)
        app.extensions["mcc_private_rss"] = FakeRssService()

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()

        self.assertEqual(result["healthState"], "evidence_insufficient")
        rss_issue = next(issue for issue in result["issues"] if issue["source"] == "private-rss")
        self.assertEqual(rss_issue["reasonCode"], "RSS_MATCHER_NOT_RUN")

    def test_home_reuses_task_v2_protection_classification(self):
        app = self.build_app([protected_item()], scheduler_enabled=True, scheduler_started=True)

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()

        self.assertEqual(result["healthState"], "protected")
        self.assertEqual(result["counts"]["protected"], 1)
        self.assertEqual(result["counts"]["actionRequired"], 0)

    def test_issue_uses_standard_task_identity_and_splits_pending_states(self):
        blocked = item(library_status="blocked")
        blocked["state"] = "blocked"
        blocked["steps"][-1].update({"detail": "归档失败", "source": "Symedia"})
        app = self.build_app([blocked], scheduler_enabled=False)

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()

        issue = next(value for value in result["issues"] if value["title"] == "测试剧")
        self.assertEqual(issue["targetKey"], "tv:tmdb:123:season:1")
        self.assertTrue(issue["chainId"].startswith("chain:"))
        self.assertEqual(result["counts"]["waiting"], 0)
        self.assertEqual(result["counts"]["evidenceInsufficient"], 0)

    def test_home_issue_uses_episode_copy_without_paths_or_internal_ids(self):
        blocked = item(item_id="symedia:private", library_status="blocked")
        blocked["state"] = "blocked"
        blocked["episodeNumber"] = 5
        blocked["steps"][-1].update({
            "detail": "0 成功 / 1 失败 · /vol/private/云月大陆.S01E05.mkv 未找到媒体信息",
            "source": "Symedia",
            "reasonCode": "SYMEDIA_LIBRARY_FAILED",
        })
        app = self.build_app([blocked], scheduler_enabled=False)

        issue = next(value for value in HomeSummaryService(app, clock=lambda: NOW).snapshot()["issues"] if value["source"] == "task-chain")

        self.assertEqual(issue["headline"], "《测试剧》S01E05识别失败")
        self.assertEqual(issue["displayTitle"], "测试剧 S01E05")
        self.assertEqual(issue["seasonNumber"], 1)
        self.assertEqual(issue["episodeNumber"], 5)
        self.assertEqual(issue["reasonText"], "Symedia 未查询到对应媒体信息")
        public_text = f"{issue['headline']} {issue['reasonText']}"
        self.assertNotIn("/vol/", public_text)
        self.assertNotIn("symedia:private", public_text)

    def test_home_issue_uses_episode_evidence_and_keeps_identity_as_secondary_reason(self):
        blocked = item(item_id="symedia:episode-evidence", library_status="blocked")
        blocked.update({"state": "blocked", "tmdbId": "", "confidence": "unlinked"})
        blocked["steps"][-1].update({
            "detail": "/storage/cloud/云月大陆/S01E05.mkv 未查询到媒体信息",
            "source": "Symedia",
            "reasonCode": "SYMEDIA_LIBRARY_FAILED",
        })
        blocked["episodeEvidence"] = [{
            "seasonNumber": 1,
            "episodeStart": 5,
            "episodeEnd": 5,
            "stage": "library",
            "status": "blocked",
            "reasonCode": "SYMEDIA_LIBRARY_FAILED",
            "observedAt": "2026-07-22T01:00:00Z",
        }]
        app = self.build_app([blocked], scheduler_enabled=False)

        issue = next(
            value
            for value in HomeSummaryService(app, clock=lambda: NOW).snapshot()["issues"]
            if value["source"] == "task-chain"
        )

        self.assertEqual(issue["displayTitle"], "测试剧 S01E05")
        self.assertEqual(issue["headline"], "《测试剧》S01E05识别失败")
        self.assertEqual(issue["reasonText"], "Symedia 未查询到对应媒体信息")
        self.assertEqual(issue["secondaryReasonText"], "任务尚未关联到可靠媒体身份")
        self.assertNotIn("/storage/", f"{issue['headline']} {issue['reasonText']} {issue['secondaryReasonText']}")

    def test_home_collapses_unlinked_inferred_records_into_one_identity_notice(self):
        blocked = item(library_status="blocked")
        blocked.update({"state": "blocked", "tmdbId": "", "confidence": "unlinked"})
        blocked["steps"][-1].update({
            "evidence": "inferred",
            "detail": "下载完成 501 小时后仍没有 Symedia 记录",
            "source": "Symedia",
        })
        app = self.build_app([blocked], scheduler_enabled=False)

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()

        self.assertEqual(result["counts"]["suspectedBlocked"], 0)
        self.assertEqual(result["counts"]["actionRequired"], 0)
        self.assertEqual(result["counts"]["evidenceInsufficient"], 1)
        self.assertEqual(result["counts"]["identityPending"], 1)
        issue = next(value for value in result["issues"] if value["source"] == "task-identity")
        self.assertEqual(issue["headline"], "任务身份尚未完成关联")
        self.assertEqual(issue["reasonCode"], "TASK_IDENTITY_AGGREGATION_INCOMPLETE")
        self.assertIn("无法准确判断秒传积压", issue["reasonText"])


if __name__ == "__main__":
    unittest.main()
