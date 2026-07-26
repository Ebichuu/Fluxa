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


class FakeSubscriptionWorkbench:
    def __init__(self, items=None, errors=None):
        self.items = items or []
        self.errors = errors or []

    def snapshot(self, *, limit=None):
        return {"ok": True, "items": self.items, "errors": self.errors}


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


def secupload_service_payload(*, failed=1, schedule_enabled=True, next_run_at="2026-07-22T18:00:00+08:00", active=False):
    return {
        "configured": True,
        "connected": True,
        "readable": True,
        "pluginEnabled": True,
        "perFileEvidence": False,
        "activeRuns": 1 if active else 0,
        "configItems": [{
            "itemId": "raw-category-anime",
            "name": "00-日漫",
            "enabled": True,
            "fallbackUploadAfterFailures": 3,
        }],
        "tasks": [{
            "key": "retry_pending",
            "name": "重试临时目录",
            "allowSchedule": True,
            "allowManualRun": True,
        }],
        "schedules": [{
            "taskKey": "retry_pending",
            "targetItemId": "raw-category-anime",
            "enabled": schedule_enabled,
            "nextRunAt": next_run_at,
        }],
        "recentRuns": [{
            "runId": "run-latest",
            "taskKey": "retry_pending",
            "targetItemId": "raw-category-anime",
            "trigger": "schedule",
            "status": "running" if active else "success",
            "counts": {"success": 0, "failed": failed},
            "startedAt": "2026-07-22T12:00:00+08:00",
            "finishedAt": "2026-07-22T12:00:03+08:00",
        }],
        "latestBatch": {
            "taskKey": "retry_pending",
            "trigger": "schedule",
            "status": "running" if active else ("failed" if failed else "success"),
            "counts": {"success": 0, "failed": failed},
            "startedAt": "2026-07-22T12:00:00+08:00",
            "finishedAt": "2026-07-22T12:00:03+08:00",
        },
        "nextRunAt": next_run_at,
        "lastCheckedAt": "2026-07-22T12:00:04+08:00",
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
        self.assertEqual(result["counts"]["completedTargetsToday"], 1)
        self.assertEqual(result["counts"]["pending"], 0)

    def test_today_ingest_uses_shanghai_date_during_utc_evening_boundary(self):
        before_midnight = item(item_id="before-midnight", library_time="2026-07-21T15:59:59Z")
        before_midnight["tmdbId"] = "201"
        at_midnight = item(item_id="at-midnight", library_time="2026-07-21T16:00:00Z")
        at_midnight["tmdbId"] = "202"
        before_eight = item(item_id="before-eight", library_time="2026-07-21T23:00:00Z")
        before_eight["tmdbId"] = "203"
        app = self.build_app([before_midnight, at_midnight, before_eight])
        shanghai_morning = datetime(2026, 7, 21, 23, 30, tzinfo=timezone.utc)

        result = HomeSummaryService(app, clock=lambda: shanghai_morning).snapshot()

        self.assertEqual(result["counts"]["ingestedToday"], 2)
        self.assertEqual(result["counts"]["completedTargetsToday"], 2)
        archived_focus = next(value for value in result["focusItems"] if value["key"] == "archived_today")
        self.assertEqual(archived_focus["href"], "/tasks?userState=completed&completedDate=2026-07-22")

    def test_today_archive_uses_symedia_success_count_without_changing_legacy_target_count(self):
        app = self.build_app([item()])
        app.extensions["mcc_task_chain_service"].payload["services"]["symedia"]["totals"] = {
            "processedToday": 31,
            "archivedToday": 24,
            "protectedToday": 7,
            "failedToday": 0,
        }

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()

        self.assertEqual(result["counts"]["archivedToday"], 24)
        self.assertEqual(result["counts"]["completedTargetsToday"], 1)
        self.assertEqual(result["counts"]["ingestedToday"], 1)
        self.assertIn("归档文件 24 · 完成作品/季 1", result["detail"])

    def test_enabled_scheduler_without_runtime_is_neutral_diagnostic(self):
        app = self.build_app([item()], scheduler_enabled=True)

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()

        self.assertEqual(result["healthState"], "normal")
        scheduler_diagnostic = next(
            diagnostic for diagnostic in result["diagnostics"]
            if diagnostic.get("source") == "subscription-scheduler"
        )
        self.assertEqual(scheduler_diagnostic["code"], "SCHEDULER_NOT_STARTED")

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

    def test_collected_rss_without_matcher_run_is_neutral_diagnostic(self):
        app = self.build_app([item()], scheduler_enabled=True, scheduler_started=True)
        app.extensions["mcc_private_rss"] = FakeRssService()

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()

        self.assertEqual(result["healthState"], "normal")
        rss_diagnostic = next(
            diagnostic for diagnostic in result["diagnostics"]
            if diagnostic.get("source") == "private-rss"
        )
        self.assertEqual(rss_diagnostic["code"], "RSS_MATCHER_NOT_RUN")

    def test_home_reuses_task_v2_protection_classification(self):
        app = self.build_app([protected_item()], scheduler_enabled=True, scheduler_started=True)

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()
        focus = {value["key"]: value for value in result["focusItems"]}

        self.assertEqual(result["healthState"], "normal")
        self.assertEqual(result["counts"]["protected"], 1)
        self.assertEqual(result["counts"]["actionRequired"], 0)
        self.assertEqual(focus["downloaded_not_archived"]["value"], 0)
        self.assertEqual(focus["downloaded_not_archived"]["state"], "normal")

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
        self.assertEqual(result["healthState"], "normal")
        self.assertEqual(result["issueTotal"], 0)
        diagnostic = next(value for value in result["diagnostics"] if value["code"] == "TASK_IDENTITY_PENDING")
        self.assertEqual(diagnostic["count"], 1)
        self.assertIn("identityState=unidentified", diagnostic["href"])
        self.assertIn("无法准确判断秒传积压", diagnostic["reasonText"])

    def test_home_counts_qb_tasks_and_concurrent_targets_separately(self):
        value = item()
        value["state"] = "active"
        value["steps"][0]["status"] = "active"
        value["qbControl"] = {"total": 3, "active": 3, "completed": 0, "paused": 0}
        app = self.build_app([value], scheduler_enabled=False)

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()

        self.assertEqual(result["counts"]["activeDownloadTasks"], 3)
        self.assertEqual(result["counts"]["concurrentDownloadGroups"], 1)
        self.assertEqual(result["healthState"], "waiting")
        self.assertEqual(result["headline"], "有 3 个任务正在处理")
        self.assertIn("qB 下载任务 3", result["detail"])

    def test_focus_items_report_verified_zero_and_precise_hrefs(self):
        app = self.build_app([item()], scheduler_enabled=True, scheduler_started=True)
        services = app.extensions["mcc_task_chain_service"].payload["services"]
        services["symedia"]["totals"] = {"archivedToday": 0}
        services["torra"]["secupload115"] = {
            "readable": True,
            "perFileEvidence": False,
            "latestBatch": {"counts": {"success": 13, "failed": 0}},
        }
        app.extensions["mcc_subscription_workbench"] = FakeSubscriptionWorkbench([
            {"id": "subscription-1", "missingEpisodes": []},
        ])

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()
        focus = {value["key"]: value for value in result["focusItems"]}

        self.assertEqual(
            [value["key"] for value in result["focusItems"]],
            [
                "current_downloads", "secupload_failures", "downloaded_not_archived",
                "archived_today", "missing_episodes", "action_required",
            ],
        )
        for key in (
            "current_downloads", "secupload_failures", "downloaded_not_archived",
            "archived_today", "missing_episodes", "action_required",
        ):
            self.assertEqual(focus[key]["value"], 0)
            self.assertEqual(focus[key]["state"], "normal")
        self.assertEqual(focus["current_downloads"]["href"], "/tasks?userState=in_progress")
        self.assertEqual(focus["archived_today"]["href"], "/tasks?userState=completed&completedDate=2026-07-22")
        self.assertEqual(focus["missing_episodes"]["href"], "/following?missingEpisodes=1")
        self.assertEqual(focus["action_required"]["href"], "/tasks?userState=action_required")

    def test_focus_items_only_raise_failures_from_explicit_evidence(self):
        pending_archive = item(library_status="blocked")
        pending_archive["state"] = "blocked"
        pending_archive["steps"][-1].update({"source": "Symedia", "reasonCode": "SYMEDIA_LIBRARY_FAILED"})
        app = self.build_app([pending_archive], scheduler_enabled=True, scheduler_started=True)
        services = app.extensions["mcc_task_chain_service"].payload["services"]
        services["symedia"]["totals"] = {"archivedToday": 4}
        services["torra"]["secupload115"] = {
            "readable": True,
            "perFileEvidence": False,
            "latestBatch": {"counts": {"success": 8, "failed": 2}},
        }
        app.extensions["mcc_subscription_workbench"] = FakeSubscriptionWorkbench([
            {"id": "subscription-1", "missingEpisodes": ["E05", "E06"]},
        ])

        focus = {
            value["key"]: value
            for value in HomeSummaryService(app, clock=lambda: NOW).snapshot()["focusItems"]
        }

        self.assertEqual(focus["secupload_failures"]["value"], 2)
        self.assertEqual(focus["secupload_failures"]["state"], "action_required")
        self.assertEqual(focus["downloaded_not_archived"]["value"], 1)
        self.assertEqual(focus["downloaded_not_archived"]["state"], "action_required")
        self.assertEqual(focus["missing_episodes"]["value"], 2)
        self.assertEqual(focus["missing_episodes"]["state"], "action_required")
        self.assertEqual(focus["action_required"]["value"], 1)
        self.assertEqual(focus["action_required"]["state"], "action_required")

    def test_focus_items_ignore_expired_download_completion(self):
        expired = item(library_status="waiting")
        expired["steps"][0]["freshUntil"] = "2026-07-22T01:00:00Z"
        app = self.build_app([expired], scheduler_enabled=True, scheduler_started=True)

        focus = {
            value["key"]: value
            for value in HomeSummaryService(app, clock=lambda: NOW).snapshot()["focusItems"]
        }

        self.assertEqual(focus["downloaded_not_archived"]["value"], 0)
        self.assertEqual(focus["downloaded_not_archived"]["state"], "normal")

    def test_home_does_not_report_expired_active_downloads_as_current(self):
        expired = item(library_status="waiting")
        expired.update({"state": "active", "activeDownloadTasks": 2})
        expired["steps"][0].update({
            "status": "active",
            "freshUntil": "2026-07-22T01:00:00Z",
        })
        app = self.build_app([expired], scheduler_enabled=True, scheduler_started=True)

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()
        focus = {value["key"]: value for value in result["focusItems"]}

        self.assertEqual(result["counts"]["activeDownloadTasks"], 0)
        self.assertEqual(result["counts"]["downloading"], 0)
        self.assertEqual(focus["current_downloads"]["value"], 0)
        self.assertEqual(focus["current_downloads"]["state"], "normal")
        self.assertNotEqual(result["healthState"], "waiting")

    def test_focus_items_ignore_unidentified_historical_download(self):
        historical = item(library_status="waiting")
        historical.update({"tmdbId": "", "confidence": "unlinked"})
        app = self.build_app([historical], scheduler_enabled=True, scheduler_started=True)

        focus = {
            value["key"]: value
            for value in HomeSummaryService(app, clock=lambda: NOW).snapshot()["focusItems"]
        }

        self.assertEqual(focus["downloaded_not_archived"]["value"], 0)
        self.assertEqual(focus["downloaded_not_archived"]["state"], "normal")

    def test_focus_items_use_unknown_instead_of_unverified_zero(self):
        app = self.build_app([item()], scheduler_enabled=True, scheduler_started=True)
        services = app.extensions["mcc_task_chain_service"].payload["services"]
        services["qb"] = {"connected": False, "error": ""}
        services["symedia"].pop("totals", None)

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()
        focus = {
            value["key"]: value
            for value in result["focusItems"]
        }

        for key in ("current_downloads", "secupload_failures", "downloaded_not_archived", "archived_today", "missing_episodes"):
            self.assertIsNone(focus[key]["value"])
            self.assertEqual(focus[key]["state"], "unknown")
        self.assertEqual(focus["action_required"]["value"], 0)
        self.assertEqual(focus["action_required"]["state"], "normal")
        self.assertIsNone(result["counts"]["activeDownloadTasks"])
        self.assertIsNone(result["counts"]["archivedToday"])
        self.assertEqual(result["healthState"], "evidence_insufficient")
        self.assertEqual(result["headline"], "核心服务状态尚待确认")
        self.assertIn("归档文件 未知", result["detail"])
        self.assertIn("qB 下载任务 未知", result["detail"])

    def test_recovering_secupload_uses_processing_semantics_and_system_issue_link(self):
        app = self.build_app([item()], scheduler_enabled=True, scheduler_started=True)
        services = app.extensions["mcc_task_chain_service"].payload["services"]
        services["torra"]["secupload115"] = secupload_service_payload(failed=1)

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()
        focus = {value["key"]: value for value in result["focusItems"]}

        self.assertEqual(focus["secupload_failures"]["label"], "秒传待恢复")
        self.assertEqual(focus["secupload_failures"]["value"], 1)
        self.assertEqual(focus["secupload_failures"]["state"], "processing")
        self.assertEqual(focus["secupload_failures"]["href"], "/tasks?systemIssue=secupload_failures")
        self.assertEqual(result["counts"]["actionRequired"], 0)
        self.assertEqual(focus["action_required"]["value"], 0)
        self.assertEqual(result["healthState"], "waiting")
        issue = result["systemIssues"][0]
        self.assertEqual((issue["id"], issue["state"]), ("secupload_failures", "recovering"))
        serialized = str(result)
        self.assertNotIn("raw-category-anime", serialized)
        self.assertNotIn("run-latest", serialized)

    def test_action_required_secupload_shows_handle_label_without_red_reclassification(self):
        app = self.build_app([item()], scheduler_enabled=True, scheduler_started=True)
        services = app.extensions["mcc_task_chain_service"].payload["services"]
        services["torra"]["secupload115"] = secupload_service_payload(
            failed=1, schedule_enabled=False, next_run_at="",
        )

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()
        focus = {value["key"]: value for value in result["focusItems"]}

        self.assertEqual(focus["secupload_failures"]["label"], "秒传需要处理")
        self.assertEqual(focus["secupload_failures"]["value"], 1)
        self.assertEqual(focus["secupload_failures"]["state"], "action_required")
        self.assertEqual(focus["secupload_failures"]["href"], "/tasks?systemIssue=secupload_failures")
        self.assertEqual(result["systemIssues"][0]["state"], "action_required")
        self.assertTrue(result["systemIssues"][0]["manualRetry"]["supported"])

    def test_unreadable_secupload_stays_unknown_without_red_count(self):
        app = self.build_app([item()], scheduler_enabled=True, scheduler_started=True)
        services = app.extensions["mcc_task_chain_service"].payload["services"]
        services["torra"]["secupload115"] = {
            "configured": True,
            "connected": False,
            "readable": False,
            "error": "http://torra.private/api token=must-not-escape",
        }

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()
        focus = {value["key"]: value for value in result["focusItems"]}

        self.assertEqual(focus["secupload_failures"]["state"], "unknown")
        self.assertIsNone(focus["secupload_failures"]["value"])
        self.assertEqual(result["counts"]["actionRequired"], 0)
        self.assertEqual(result["systemIssues"][0]["state"], "unknown")
        self.assertNotIn("must-not-escape", str(result))

    def test_missing_episode_zero_requires_complete_subscription_coverage(self):
        app = self.build_app([item()], scheduler_enabled=True, scheduler_started=True)
        app.extensions["mcc_subscription_workbench"] = FakeSubscriptionWorkbench([
            {"id": "subscription-1", "missingEpisodes": []},
            {"id": "subscription-2"},
        ])

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()
        focus = {value["key"]: value for value in result["focusItems"]}

        self.assertIsNone(focus["missing_episodes"]["value"])
        self.assertEqual(focus["missing_episodes"]["state"], "unknown")
        self.assertIn("尚未提供", focus["missing_episodes"]["detail"])


if __name__ == "__main__":
    unittest.main()
