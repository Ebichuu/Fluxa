from __future__ import annotations

import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from flask import Flask

from app.health_state_runtime import SchedulerStatusRegistry
from app.home_summary_runtime import HomeSummaryService, register_home_summary


NOW = datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc)


class FakeTaskChainService:
    def __init__(self, payload):
        self.payload = payload

    def get_chain(self):
        return self.payload


class FakeTaskChainV2Service:
    def __init__(self, payload, archive_summary):
        self.payload = payload
        self.archive = archive_summary

    def full_snapshot(self):
        return self.payload

    def archive_summary(self, archived_date, payload=None):
        return {**self.archive, "date": archived_date, "timezone": "Asia/Shanghai"}


class FailingTaskChainV2Service:
    def __init__(self):
        self.calls = 0

    def full_snapshot(self):
        self.calls += 1
        raise TimeoutError("task chain unavailable")


class FakeQbClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def summary(self):
        self.calls += 1
        return self.payload


class FakeRssRepository:
    def __init__(self, error_sources=0, resource_counts=None):
        self.error_sources = error_sources
        self.resource_counts = resource_counts or {"newToday": 12, "needsReview": 3, "upgradeAvailable": 2}
        self.resource_bounds = None

    def summary(self, enabled):
        return {"enabled": enabled, "items": 347, "matches": 0, "matcherRan": False, "errorSources": self.error_sources, "lastSuccessAt": "2026-07-22T01:55:00Z"}

    def resource_center_summary(self, published_from, published_before):
        self.resource_bounds = (published_from, published_before)
        return dict(self.resource_counts)


class FakeRssService:
    def __init__(self, error_sources=0, resource_counts=None):
        self.repository = FakeRssRepository(error_sources, resource_counts)

    def collection_enabled(self):
        return True


class FakeSubscriptionWorkbench:
    def __init__(self, items=None, errors=None, stats=None):
        self.items = items or []
        self.errors = errors or []
        self.stats = stats or {}

    def snapshot(self, *, limit=None):
        return {"ok": True, "items": self.items, "errors": self.errors, "stats": self.stats}


def pipeline_fact(stage, state, *, observed_at="2026-07-22T01:00:00Z", scope="episode", reason_code="", reason_text=""):
    return {
        "stage": stage,
        "state": state,
        "scope": scope,
        "evidence": "missing" if state == "unknown" else "verified",
        "observedAt": observed_at,
        "freshUntil": "2026-07-23T00:00:00Z",
        "source": {"qb": "qBittorrent", "symedia": "Symedia", "emby": "Emby"}.get(stage, stage),
        "sourceRef": f"{stage}-public-test-ref",
        "reasonCode": reason_code or f"{stage.upper()}_{state.upper()}",
        "reasonText": reason_text or f"{stage} {state}",
    }


def item(*, item_id="chain-1", updated_at="2026-07-22T01:00:00Z", library_status="done", library_time="2026-07-22T01:00:00Z"):
    symedia_state = {"done": "succeeded", "blocked": "failed", "waiting": "waiting"}.get(library_status, "unknown")
    facts = [
        pipeline_fact("qb", "succeeded", observed_at=updated_at, reason_code="QB_DOWNLOAD_SUCCEEDED"),
        pipeline_fact(
            "symedia",
            symedia_state,
            observed_at=library_time,
            reason_code="SYMEDIA_LIBRARY_FAILED" if symedia_state == "failed" else "SYMEDIA_LIBRARY_SUCCEEDED",
            reason_text="Symedia 未查询到对应媒体信息" if symedia_state == "failed" else "Symedia 已完成整理入库",
        ),
    ]
    if library_status == "done":
        facts.append(pipeline_fact(
            "emby", "succeeded", observed_at=library_time,
            reason_code="EMBY_EPISODE_INDEXED", reason_text="Emby 已收录目标集",
        ))
    return {
        "id": item_id,
        "title": "测试剧",
        "mediaType": "tv",
        "tmdbId": "123",
        "seasonNumber": 1,
        "episodeNumber": 1,
        "state": "completed" if library_status == "done" else "waiting",
        "updatedAt": updated_at,
        "steps": [
            {"key": "download", "status": "done", "evidence": "verified"},
            {"key": "library", "status": library_status, "evidence": "verified", "timestamp": library_time},
        ],
        "pipelineFacts": facts,
    }


def protected_item():
    value = item(library_status="blocked")
    value["state"] = "blocked"
    value["steps"][-1].update({
        "detail": "现有版本评分更高，跳过归档",
        "source": "Symedia",
    })
    value["pipelineFacts"] = [
        pipeline_fact("qb", "succeeded", reason_code="QB_DOWNLOAD_SUCCEEDED"),
        pipeline_fact(
            "symedia", "protected", reason_code="QUALITY_HIGHER_VERSION_EXISTS",
            reason_text="现有版本评分更高，跳过归档",
        ),
    ]
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

    def test_resource_center_summary_uses_shanghai_day_and_stays_neutral(self):
        app = self.build_app([])
        rss = FakeRssService(resource_counts={
            "newToday": 12,
            "needsReview": 3,
            "followNeedsReview": 1,
            "unlinkedItems": 346,
            "upgradeAvailable": 2,
        })
        app.extensions["mcc_private_rss"] = rss

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()

        self.assertEqual(result["resourceCenter"], {
            "counts": {
                "newToday": 12,
                "followNewToday": 0,
                "needsReview": 3,
                "followNeedsReview": 1,
                "unlinkedItems": 346,
                "upgradeAvailable": 2,
                "needsDecision": 0,
            },
            "confirmation": "confirmed",
            "observedAt": "2026-07-22T02:00:00Z",
        })
        self.assertEqual(rss.repository.resource_bounds, (
            "2026-07-21T16:00:00Z",
            "2026-07-22T16:00:00Z",
        ))
        self.assertNotEqual(result["healthState"], "action_required")

    def test_resource_center_summary_falls_back_to_legacy_review_count(self):
        app = self.build_app([])
        app.extensions["mcc_private_rss"] = FakeRssService()

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()

        self.assertEqual(result["resourceCenter"]["counts"]["needsReview"], 3)
        self.assertEqual(result["resourceCenter"]["counts"]["followNeedsReview"], 3)
        self.assertIsNone(result["resourceCenter"]["counts"]["unlinkedItems"])

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
        self.assertEqual(archived_focus["href"], "/tasks?archivedDate=2026-07-22")

    def test_home_issue_copy_keeps_confirmed_media_result_above_residual_failure(self):
        value = item(library_status="done")
        value["pipelineFacts"] = [
            pipeline_fact("qb", "failed", scope="file", reason_code="QB_STALLED", reason_text="qB 下载持续无活动"),
            pipeline_fact("symedia", "succeeded", scope="file", reason_code="SYMEDIA_ORGANIZED", reason_text="Symedia 已完成整理入库"),
        ]
        value["mediaResult"] = {
            "state": "archived",
            "stage": "symedia",
            "resultText": "已整理入库",
            "observedAt": "2026-07-22T01:00:00Z",
            "eventAt": "2026-07-22T00:55:00Z",
        }
        value["residualIssues"] = [{
            "stage": "qb",
            "reasonCode": "QB_STALLED",
            "reasonText": "qB 下载持续无活动",
            "observedAt": "2026-07-22T01:00:00Z",
            "resourceCount": 1,
        }]
        result = HomeSummaryService(self.build_app([value]), clock=lambda: NOW).snapshot()
        issue = next(row for row in result["issues"] if row.get("issueKind") == "media")

        self.assertEqual(issue["headline"], "《测试剧》S01E01已整理入库")
        self.assertIn("另有 1 个遗留资源需处理", issue["reasonText"])

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
        self.assertEqual(result["statisticsMeta"]["archivedToday"]["confirmation"], "partial")
        self.assertEqual(result["counts"]["completedTargetsToday"], 1)
        self.assertEqual(result["counts"]["ingestedToday"], 1)
        self.assertIn("今日入库 24 个文件", result["detail"])

    def test_today_archive_prefers_recomputed_v2_file_and_link_counts(self):
        app = Flask(f"{__name__}-archive-v2")
        payload = chain_payload([item()])
        app.extensions["mcc_task_chain_v2_service"] = FakeTaskChainV2Service(payload, {
            "archivedFiles": 35,
            "linkedFiles": 30,
            "linkedTasks": 18,
            "unlinkedFiles": 5,
        })

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()

        self.assertEqual(result["counts"]["archivedToday"], 35)
        self.assertEqual(result["statisticsMeta"]["archivedToday"], {
            "scope": "home_today",
            "unit": "file",
            "observedAt": "2026-07-22T02:00:00Z",
            "confirmation": "confirmed",
        })
        self.assertEqual(result["archiveSummary"]["linkedTasks"], 18)
        archived = next(value for value in result["focusItems"] if value["key"] == "archived_today")
        self.assertIn("关联 18 个任务", archived["detail"])
        self.assertIn("未关联 5 个文件", archived["detail"])

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
        # 服务异常保留在 issues（有自己的处理入口），不计入任务中心口径的角标计数
        self.assertEqual(payload["counts"]["actionRequired"], 0)
        self.assertEqual(payload["counts"]["mediaActionRequired"], 0)
        self.assertEqual(payload["counts"]["auxiliaryAlerts"], 1)
        self.assertEqual(payload["headline"], "有 1 项辅助能力提醒")
        self.assertTrue(any(issue["source"] == "symedia" for issue in payload["issues"]))

    def test_action_required_count_matches_task_center_and_keeps_rss_issue_deep_link(self):
        # 口径：counts.actionRequired == 任务中心 userState=action_required 实际列出的任务链数量；
        # RSS 来源失败仍出现在 issues 列表（深链去种子库），但不计入角标。
        blocked = item(library_status="blocked")
        blocked["state"] = "blocked"
        blocked["steps"][-1].update({
            "detail": "0 成功 / 1 失败 · 未查询到媒体信息",
            "source": "Symedia",
            "reasonCode": "SYMEDIA_LIBRARY_FAILED",
        })
        app = self.build_app([blocked], scheduler_enabled=True, scheduler_started=True)
        app.extensions["mcc_private_rss"] = FakeRssService(error_sources=1)

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()
        focus = {value["key"]: value for value in result["focusItems"]}

        self.assertEqual(result["counts"]["actionRequired"], 1)
        self.assertEqual(result["counts"]["mediaActionRequired"], 1)
        self.assertEqual(result["counts"]["auxiliaryAlerts"], 1)
        self.assertEqual(focus["action_required"]["value"], 1)
        self.assertEqual(focus["action_required"]["href"], "/tasks?outcomeState=action_required")
        rss_issue = next(value for value in result["issues"] if value["source"] == "private-rss")
        self.assertEqual(rss_issue["reasonCode"], "RSS_COLLECTION_FAILED")
        # issues 列表允许多于计数（RSS 项走自己的种子库入口）
        self.assertEqual(result["issueTotal"], 2)
        self.assertEqual(result["healthState"], "action_required")

    def test_action_required_counts_resources_and_reliable_works_separately(self):
        def blocked_resource(
            item_id,
            *,
            media_type="tv",
            tmdb_id="123",
            season=1,
            episode=1,
            title="测试剧",
            identity_state="linked",
        ):
            value = item(item_id=item_id, library_status="blocked")
            value.update({
                "state": "blocked",
                "mediaType": media_type,
                "tmdbId": tmdb_id,
                "seasonNumber": season,
                "episodeNumber": episode,
                "title": title,
                "identityState": identity_state,
            })
            return value

        resources = [
            blocked_resource("s1e1", episode=1),
            blocked_resource("s1e2", episode=2),
            blocked_resource("s1e3", episode=3),
            blocked_resource("s2e1", season=2, episode=1),
            blocked_resource("movie", media_type="movie", tmdb_id="999", season=0, episode=None, title="测试电影"),
            blocked_resource(
                "unknown-e1", tmdb_id="", episode=1, title="Show A", identity_state="unidentified",
            ),
            blocked_resource(
                "unknown-e2", tmdb_id="", episode=2, title="ＳＨＯＷ　Ａ！", identity_state="unidentified",
            ),
            blocked_resource(
                "unknown-e3", tmdb_id="", episode=3, title="Show A Extra", identity_state="unidentified",
            ),
        ]
        app = self.build_app(resources, scheduler_enabled=True, scheduler_started=True)

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()
        focus = {value["key"]: value for value in result["focusItems"]}

        self.assertEqual(result["counts"]["actionRequired"], 8)
        self.assertEqual(result["counts"]["mediaActionRequired"], 8)
        self.assertEqual(result["counts"]["actionRequiredResources"], 8)
        self.assertEqual(result["counts"]["actionRequiredWorks"], 6)
        self.assertEqual(result["counts"]["actionRequiredGroups"], 5)
        self.assertEqual(result["counts"]["actionRequiredIdentityUnconfirmedResources"], 3)
        self.assertEqual(result["problemGroupSummary"], {
            "actionRequiredGroups": 5,
            "actionRequiredResources": 8,
            "actionRequiredIdentityUnconfirmedResources": 3,
        })
        self.assertEqual(result["problemGroupTotal"], 5)
        self.assertEqual(sum(group["resourceCount"] for group in result["problemGroups"]), 8)
        self.assertEqual(result["headline"], "有 5 个问题需要处理 · 涉及 8 个资源 · 正在下载 0 个 · 今日入库 未知 个文件")
        self.assertEqual(focus["action_required"]["value"], 5)
        self.assertIn("涉及 8 个资源", focus["action_required"]["detail"])
        self.assertIn("3 条身份未确认", focus["action_required"]["detail"])

    def test_action_required_groups_use_all_resources_and_keep_conflicts_separate(self):
        resources = []
        for episode in range(1, 10):
            value = item(item_id=f"unknown-{episode}", library_status="blocked")
            value.update({
                "state": "blocked",
                "identityState": "unidentified",
                "mediaType": "tv",
                "tmdbId": "",
                "seasonNumber": 1,
                "episodeNumber": episode,
                "title": "机械 分组",
            })
            resources.append(value)
        conflict = item(item_id="conflict", library_status="blocked")
        conflict.update({
            "state": "blocked",
            "identityState": "conflict",
            "mediaType": "tv",
            "tmdbId": "123",
            "seasonNumber": 1,
            "episodeNumber": 10,
            "title": "机械分组",
        })
        resources.append(conflict)
        untitled = item(item_id="untitled", library_status="blocked")
        untitled.update({
            "state": "blocked",
            "identityState": "unidentified",
            "mediaType": "tv",
            "tmdbId": "",
            "seasonNumber": 1,
            "episodeNumber": 11,
            "title": "",
        })
        resources.append(untitled)
        app = self.build_app(resources, scheduler_enabled=True, scheduler_started=True)

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()

        self.assertEqual(result["issueTotal"], 11)
        self.assertEqual(len(result["issues"]), 8)
        self.assertEqual(result["counts"]["actionRequired"], 11)
        self.assertEqual(result["counts"]["actionRequiredGroups"], 3)
        self.assertEqual(result["counts"]["actionRequiredIdentityUnconfirmedResources"], 11)
        self.assertEqual(result["problemGroupSummary"]["actionRequiredResources"], 11)
        self.assertEqual(result["problemGroupTotal"], 3)

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
        self.assertEqual(issue["targetKey"], "tv:tmdb:123:season:1:episode:1")
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

        issue = next(value for value in HomeSummaryService(app, clock=lambda: NOW).snapshot()["issues"] if value["source"] == "Symedia")

        self.assertEqual(issue["headline"], "《测试剧》S01E05作品识别失败")
        self.assertEqual(issue["displayTitle"], "测试剧 S01E05")
        self.assertEqual(issue["seasonNumber"], 1)
        self.assertEqual(issue["episodeNumber"], 5)
        self.assertEqual(issue["reasonText"], "作品识别失败")
        self.assertEqual(issue["primaryAction"]["label"], "查看解决方式")
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
            if value["source"] == "Symedia"
        )

        self.assertEqual(issue["displayTitle"], "测试剧 S01E05")
        self.assertEqual(issue["headline"], "《测试剧》S01E05作品识别失败")
        self.assertEqual(issue["reasonText"], "作品识别失败")
        self.assertEqual(issue["secondaryReasonText"], "任务尚未关联到可靠媒体身份")
        self.assertNotIn("/storage/", f"{issue['headline']} {issue['reasonText']} {issue['secondaryReasonText']}")

    def test_verified_download_failure_precedes_identity_prompt_and_uses_target_deep_link(self):
        blocked = item(item_id="qb:e94", library_status="waiting")
        blocked.update({"state": "blocked", "title": "E94", "tmdbId": "", "confidence": "unlinked"})
        blocked["steps"][0].update({
            "status": "blocked",
            "evidence": "verified",
            "source": "qBittorrent",
            "reasonCode": "QB_DOWNLOAD_FAILED",
            "detail": "下载任务返回错误",
        })
        blocked["pipelineFacts"] = [
            pipeline_fact(
                "qb", "failed", reason_code="QB_DOWNLOAD_FAILED",
                reason_text="下载任务返回错误",
            ),
            pipeline_fact("symedia", "unknown", reason_code="SYMEDIA_RESULT_UNKNOWN"),
        ]
        app = self.build_app([blocked], scheduler_enabled=False)

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()
        issue = next(value for value in result["issues"] if value["title"] == "E94")
        group = next(value for value in result["problemGroups"] if value["title"] == "E94")

        self.assertIn("下载需要检查", issue["headline"])
        self.assertEqual(issue["primaryAction"]["label"], "检查下载任务")
        self.assertEqual(issue["secondaryReasonText"], "任务尚未关联到可靠媒体身份")
        self.assertNotIn("qbActive=1", issue["primaryAction"]["href"])
        query = parse_qs(urlparse(issue["primaryAction"]["href"]).query)
        self.assertEqual(query["chainId"], [issue["chainId"]])
        self.assertEqual(query["targetKey"], [issue["targetKey"]])
        self.assertEqual(query["outcomeState"], ["action_required"])
        self.assertEqual(group["primaryAction"]["href"], issue["primaryAction"]["href"])

    def test_home_collapses_unlinked_inferred_records_into_one_identity_notice(self):
        blocked = item(library_status="blocked")
        blocked.update({"state": "blocked", "tmdbId": "", "confidence": "unlinked"})
        blocked["pipelineFacts"] = []
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
        value["pipelineFacts"] = [pipeline_fact(
            "qb", "active", reason_code="QB_DOWNLOAD_ACTIVE", reason_text="qB 正在下载或排队",
        )]
        value["qbControl"] = {"total": 3, "active": 3, "completed": 0, "paused": 0}
        app = self.build_app([value], scheduler_enabled=False)

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()

        self.assertEqual(result["counts"]["activeDownloadTasks"], 3)
        self.assertEqual(result["counts"]["concurrentDownloadGroups"], 1)
        self.assertEqual(result["counts"]["inProgress"], 1)
        self.assertEqual(result["healthState"], "waiting")
        self.assertEqual(result["headline"], "有 1 项任务正在处理")
        self.assertIn("正在下载 3 个", result["detail"])

    def test_home_uses_global_qb_active_count_even_when_media_chain_needs_action(self):
        blocked = item(library_status="blocked")
        blocked["state"] = "blocked"
        app = self.build_app([blocked], scheduler_enabled=False)
        qb = FakeQbClient({
            "configured": True,
            "connected": True,
            "lastCheckedAt": "2026-07-22T02:00:00.000Z",
            "counts": {"active": 2},
        })
        app.extensions["mcc_qbittorrent_client"] = qb

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()

        self.assertEqual(result["counts"]["mediaActionRequired"], 1)
        self.assertEqual(result["counts"]["activeDownloadTasks"], 2)
        self.assertEqual(qb.calls, 1)
        focus = next(row for row in result["focusItems"] if row["key"] == "current_downloads")
        self.assertEqual(focus["value"], 2)
        self.assertEqual(focus["href"], "/tasks?qbActive=1")

    def test_home_distinguishes_verified_zero_from_unreadable_qb_count(self):
        app = self.build_app([item()], scheduler_enabled=False)
        qb = FakeQbClient({
            "configured": True,
            "connected": True,
            "lastCheckedAt": "2026-07-22T02:00:00.000Z",
            "counts": {"active": 0},
        })
        app.extensions["mcc_qbittorrent_client"] = qb

        verified_zero = HomeSummaryService(app, clock=lambda: NOW).snapshot()
        self.assertEqual(verified_zero["counts"]["activeDownloadTasks"], 0)
        self.assertEqual(
            verified_zero["statisticsMeta"]["activeDownloadTasks"]["confirmation"],
            "confirmed",
        )

        qb.payload = {
            "configured": True,
            "connected": False,
            "lastCheckedAt": "2026-07-22T02:00:00.000Z",
            "counts": {"active": 0},
            "error": "qBittorrent 请求失败",
        }
        unreadable = HomeSummaryService(app, clock=lambda: NOW).snapshot()
        self.assertIsNone(unreadable["counts"]["activeDownloadTasks"])
        self.assertEqual(
            unreadable["statisticsMeta"]["activeDownloadTasks"]["confirmation"],
            "unknown",
        )

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
                "archived_today", "missing_episodes", "reconciliation_action_required",
                "action_required",
            ],
        )
        for key in (
            "current_downloads", "secupload_failures", "downloaded_not_archived",
            "archived_today", "missing_episodes", "action_required",
            "reconciliation_action_required",
        ):
            self.assertEqual(focus[key]["value"], 0)
            self.assertEqual(focus[key]["state"], "normal")
        self.assertEqual(focus["current_downloads"]["href"], "/tasks?qbActive=1")
        self.assertEqual(focus["archived_today"]["href"], "/tasks?archivedDate=2026-07-22")
        self.assertEqual(focus["missing_episodes"]["href"], "/following?missingEpisodes=1")
        self.assertEqual(focus["action_required"]["href"], "/tasks?outcomeState=action_required")

    def test_reconciliation_focus_is_auxiliary_and_does_not_raise_media_action_count(self):
        app = self.build_app([item()], scheduler_enabled=True, scheduler_started=True)
        app.extensions["mcc_subscription_workbench"] = FakeSubscriptionWorkbench(
            [{"id": "subscription-1", "missingEpisodes": []}],
            stats={"reconciliationActionRequired": 3},
        )

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()
        focus = {value["key"]: value for value in result["focusItems"]}

        self.assertEqual(result["counts"]["mediaActionRequired"], 0)
        self.assertEqual(result["counts"]["reconciliationActionRequired"], 3)
        self.assertEqual(focus["reconciliation_action_required"]["value"], 3)
        self.assertEqual(focus["reconciliation_action_required"]["unit"], "个目标")
        self.assertEqual(focus["reconciliation_action_required"]["state"], "normal")
        self.assertIn("不计入媒体异常", focus["reconciliation_action_required"]["detail"])

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
        expired["pipelineFacts"][0]["freshUntil"] = "2026-07-22T01:00:00Z"
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
        expired["pipelineFacts"] = [{
            **pipeline_fact("qb", "active", reason_code="QB_DOWNLOAD_ACTIVE"),
            "freshUntil": "2026-07-22T01:00:00Z",
        }]
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
        historical["pipelineFacts"] = []
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
        self.assertEqual(result["statisticsMeta"]["activeDownloadTasks"]["confirmation"], "unknown")
        self.assertEqual(result["statisticsMeta"]["archivedToday"]["confirmation"], "unknown")
        self.assertEqual(result["healthState"], "evidence_insufficient")
        self.assertEqual(result["headline"], "核心服务状态尚待确认")
        self.assertIn("今日入库 未知", result["detail"])
        self.assertIn("正在下载 未知", result["detail"])

    def test_recovering_secupload_uses_processing_semantics_and_system_issue_link(self):
        app = self.build_app([item()], scheduler_enabled=True, scheduler_started=True)
        services = app.extensions["mcc_task_chain_service"].payload["services"]
        services["torra"]["secupload115"] = secupload_service_payload(failed=3)

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()
        focus = {value["key"]: value for value in result["focusItems"]}

        self.assertEqual(focus["secupload_failures"]["label"], "秒传待恢复")
        self.assertEqual(focus["secupload_failures"]["value"], 3)
        self.assertEqual(focus["secupload_failures"]["state"], "processing")
        self.assertEqual(focus["secupload_failures"]["href"], "/tasks?systemIssue=secupload_failures")
        self.assertEqual(result["counts"]["actionRequired"], 0)
        self.assertEqual(result["counts"]["inProgress"], 3)
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

        self.assertEqual(focus["missing_episodes"]["value"], 0)
        self.assertEqual(focus["missing_episodes"]["state"], "unknown")
        self.assertEqual(focus["missing_episodes"]["unconfirmedCount"], 1)
        self.assertEqual(focus["missing_episodes"]["confirmation"], "partial")
        self.assertIn("尚未提供", focus["missing_episodes"]["detail"])

    def test_partial_confirmation_counts_are_disjoint_and_actionable(self):
        unknown_archive = item(item_id="chain-unknown", library_status="waiting")
        unknown_archive["episodeNumber"] = 2
        unknown_archive["pipelineFacts"] = [pipeline_fact("qb", "succeeded")]
        failed_archive = item(item_id="chain-failed", library_status="blocked")
        app = self.build_app([unknown_archive, failed_archive], scheduler_enabled=True, scheduler_started=True)
        app.extensions["mcc_subscription_workbench"] = FakeSubscriptionWorkbench([
            {"id": "subscription-1", "missingEpisodes": ["E05", "E06"]},
            {"id": "subscription-2"},
        ])

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()
        focus = {value["key"]: value for value in result["focusItems"]}

        self.assertEqual(focus["downloaded_not_archived"]["value"], 1)
        self.assertEqual(focus["downloaded_not_archived"]["unconfirmedCount"], 1)
        self.assertEqual(focus["downloaded_not_archived"]["confirmation"], "partial")
        self.assertEqual(focus["missing_episodes"]["value"], 2)
        self.assertEqual(focus["missing_episodes"]["unconfirmedCount"], 1)
        self.assertEqual(focus["missing_episodes"]["confirmation"], "partial")

    def test_missing_episode_errors_preserve_confirmed_counts(self):
        app = self.build_app([item()], scheduler_enabled=True, scheduler_started=True)
        app.extensions["mcc_subscription_workbench"] = FakeSubscriptionWorkbench(
            [
                {"id": "subscription-1", "missingEpisodes": ["E05", "E06"]},
                {"id": "subscription-2"},
            ],
            errors=["subscription row failed"],
        )

        result = HomeSummaryService(app, clock=lambda: NOW).snapshot()
        focus = {value["key"]: value for value in result["focusItems"]}

        self.assertEqual(focus["missing_episodes"]["value"], 2)
        self.assertEqual(focus["missing_episodes"]["unconfirmedCount"], 2)
        self.assertEqual(focus["missing_episodes"]["confirmation"], "partial")
        self.assertEqual(focus["missing_episodes"]["state"], "action_required")

    def test_module_collection_isolates_chain_failure_and_reads_chain_once(self):
        app = Flask(f"{__name__}-isolated-home-modules")
        chain_service = FailingTaskChainV2Service()
        qb_client = FakeQbClient({"connected": True, "counts": {"active": 2}})
        app.extensions["mcc_task_chain_v2_service"] = chain_service
        app.extensions["mcc_qbittorrent_client"] = qb_client
        app.extensions["mcc_private_rss"] = FakeRssService()
        app.extensions["mcc_subscription_workbench"] = FakeSubscriptionWorkbench([
            {"id": "subscription-1", "missingEpisodes": ["E05"]},
        ])

        modules = HomeSummaryService(app, clock=lambda: NOW).snapshot_modules()

        self.assertEqual(chain_service.calls, 1)
        self.assertEqual(qb_client.calls, 1)
        for module_key in ("task_pipeline", "archive_today", "secupload", "service_health"):
            self.assertEqual(modules[module_key]["status"], "failed")
        self.assertEqual(modules["qb_activity"]["status"], "success")
        self.assertEqual(modules["qb_activity"]["payload"]["activeDownloadTasks"], 2)
        self.assertEqual(modules["rss_resource_center"]["status"], "success")
        self.assertEqual(modules["subscription_progress"]["status"], "success")
        self.assertEqual(
            modules["subscription_progress"]["payload"]["focusItem"]["value"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
