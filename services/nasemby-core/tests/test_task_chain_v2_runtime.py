from __future__ import annotations

import unittest
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from flask import Flask

from app.resource_identity_runtime import chain_id
from app.resource_task_repository import ResourceTaskRepository
from app.task_chain_v2_runtime import TaskChainV2Service, adapt_task_chain, register_task_chain_v2
from app.task_public_runtime import present_pipeline_fact, safe_public_text


def pipeline_fact(stage, state, *, reason_code, reason_text, scope="file"):
    return {
        "stage": stage,
        "state": state,
        "scope": scope,
        "evidence": "missing" if state == "unknown" else "verified",
        "observedAt": "2026-07-22T03:00:00Z",
        "freshUntil": "2026-07-22T03:05:00Z",
        "source": {"qb": "qBittorrent", "symedia": "Symedia"}.get(stage, stage),
        "sourceRef": f"{stage}-private-ref",
        "reasonCode": reason_code,
        "reasonText": reason_text,
    }


class FakeTaskChain:
    def __init__(self):
        self.calls = 0

    def get_chain(self):
        self.calls += 1
        return {
            "generatedAt": "2026-07-22T03:00:00Z",
            "items": [{
                "id": "subscription:1", "title": "测试剧", "mediaType": "tv", "tmdbId": "101", "seasonNumber": 2,
                "state": "blocked", "confidence": "strong",
                "steps": [{"key": "download", "label": "获取 / 下载", "status": "blocked", "evidence": "verified", "detail": "qB 卡住", "source": "qBittorrent"}],
                "sourceIds": {"subscriptionId": "sub-1", "qbHashes": ["hash-1"], "symediaIds": []},
                "episodeEvidence": [{
                    "seasonNumber": 2, "episodeStart": 3, "episodeEnd": 3,
                    "numberingScheme": "season_episode", "stage": "download",
                    "artifactKey": "artifact:hash-1", "source": "qBittorrent",
                    "observedAt": "2026-07-22T01:00:00Z", "matchMethod": "artifact_exact",
                    "status": "blocked", "reasonCode": "DOWNLOAD_STALLED", "reasonText": "qB 卡住",
                }],
            }],
            "services": {},
        }


class TaskChainV2RuntimeTests(unittest.TestCase):
    def test_manual_resolution_hides_exact_warning_preserves_evidence_and_is_reversible(self):
        with tempfile.TemporaryDirectory() as directory:
            chain = FakeTaskChain().get_chain()
            chain["items"][0]["pipelineFacts"] = [pipeline_fact(
                "symedia",
                "failed",
                reason_code="SYMEDIA_MEDIA_NOT_FOUND",
                reason_text="Symedia 未查询到对应媒体信息",
            )]
            fake = FakeTaskChain()
            fake.get_chain = lambda: chain
            app = Flask(f"{__name__}-manual-resolution")
            app.extensions["mcc_task_chain_service"] = fake
            repository = ResourceTaskRepository(
                Path(directory) / "media.sqlite3",
                clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
            )
            register_task_chain_v2(
                app,
                repository=repository,
                clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
            )
            client = app.test_client()

            before = client.get("/api/v2/tasks/chains?outcomeState=action_required").get_json()
            self.assertEqual(before["page"]["total"], 1)
            chain_id_value = before["items"][0]["chainId"]

            resolved = client.post(
                f"/api/v2/tasks/chains/{chain_id_value}/manual-resolution",
                json={"confirm": True, "snapshotVersion": before["version"]},
            )
            self.assertEqual(resolved.status_code, 200)
            self.assertTrue(resolved.get_json()["changed"])

            action_required = client.get(
                "/api/v2/tasks/chains?outcomeState=action_required"
            ).get_json()
            no_action = client.get(
                "/api/v2/tasks/chains?outcomeState=protected"
            ).get_json()
            self.assertEqual(action_required["page"]["total"], 0)
            self.assertEqual(action_required["problemGroups"], [])
            self.assertEqual(action_required["problemGroupSummary"]["actionRequiredResources"], 0)
            self.assertEqual(no_action["page"]["total"], 1)
            item = no_action["items"][0]
            self.assertEqual(item["outcomeState"], "protected")
            self.assertEqual(item["pipelineOutcome"]["reasonCode"], "TASK_WARNING_MANUALLY_RESOLVED")
            self.assertTrue(item["manualResolution"]["resolved"])
            self.assertEqual(item["manualResolution"]["originalReasonCode"], "SYMEDIA_MEDIA_NOT_FOUND")

            detail = client.get(f"/api/v2/tasks/chains/{chain_id_value}").get_json()["item"]
            symedia_fact = next(fact for fact in detail["pipelineFacts"] if fact["stage"] == "symedia")
            self.assertEqual(symedia_fact["state"], "failed")
            self.assertEqual(symedia_fact["reasonCode"], "SYMEDIA_MEDIA_NOT_FOUND")

            restored = client.delete(
                f"/api/v2/tasks/chains/{chain_id_value}/manual-resolution",
                json={"confirm": True, "snapshotVersion": no_action["version"]},
            )
            self.assertEqual(restored.status_code, 200)
            self.assertTrue(restored.get_json()["changed"])
            after_restore = client.get(
                "/api/v2/tasks/chains?outcomeState=action_required"
            ).get_json()
            self.assertEqual(after_restore["page"]["total"], 1)
            self.assertNotIn("manualResolution", after_restore["items"][0])

    def test_manual_resolution_requires_confirmation_current_version_and_exact_issue(self):
        with tempfile.TemporaryDirectory() as directory:
            chain = FakeTaskChain().get_chain()
            chain["items"][0]["pipelineFacts"] = [pipeline_fact(
                "symedia",
                "failed",
                reason_code="SYMEDIA_MEDIA_NOT_FOUND",
                reason_text="Symedia 未查询到对应媒体信息",
            )]
            fake = FakeTaskChain()
            fake.get_chain = lambda: chain
            app = Flask(f"{__name__}-manual-resolution-guards")
            app.extensions["mcc_task_chain_service"] = fake
            repository = ResourceTaskRepository(
                Path(directory) / "media.sqlite3",
                clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
            )
            register_task_chain_v2(
                app,
                repository=repository,
                clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
            )
            client = app.test_client()
            before = client.get("/api/v2/tasks/chains?outcomeState=action_required").get_json()
            chain_id_value = before["items"][0]["chainId"]

            missing_confirmation = client.post(
                f"/api/v2/tasks/chains/{chain_id_value}/manual-resolution",
                json={"snapshotVersion": before["version"]},
            )
            stale = client.post(
                f"/api/v2/tasks/chains/{chain_id_value}/manual-resolution",
                json={"confirm": True, "snapshotVersion": "stale-version"},
            )
            self.assertEqual(missing_confirmation.status_code, 400)
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(stale.get_json()["code"], "TASK_MANUAL_RESOLUTION_STALE")

            resolved = client.post(
                f"/api/v2/tasks/chains/{chain_id_value}/manual-resolution",
                json={"confirm": True, "snapshotVersion": before["version"]},
            )
            self.assertEqual(resolved.status_code, 200)

            chain["items"][0]["pipelineFacts"][0].update({
                "eventAt": "2026-07-22T03:02:00Z",
                "sourceRef": "symedia-new-failure-ref",
            })
            new_issue = client.get(
                "/api/v2/tasks/chains?outcomeState=action_required&refresh=1"
            ).get_json()
            self.assertEqual(new_issue["page"]["total"], 1)
            self.assertNotIn("manualResolution", new_issue["items"][0])

    def test_public_protected_symedia_reason_uses_version_rule_copy(self):
        fact = present_pipeline_fact(pipeline_fact(
            "symedia",
            "protected",
            reason_code="QUALITY_VERSION_RULE_NOT_MATCHED",
            reason_text="Symedia 未查询到对应媒体信息",
        ))

        self.assertEqual(
            fact["reasonText"],
            "未命中允许入库的版本规则，已保留现有版本",
        )

    def test_bridge_failure_does_not_break_persisted_task_snapshot(self):
        class Repository:
            def record_snapshot(self, _payload):
                return {"persisted": True}

        class BrokenBridge:
            def process_snapshot(self, _payload):
                raise RuntimeError("bridge failed")

        app = Flask(f"{__name__}-bridge-failure")
        app.extensions["mcc_task_chain_service"] = FakeTaskChain()
        service = TaskChainV2Service(
            app,
            repository=Repository(),
            quality_watch_bridge=BrokenBridge(),
            clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
        )

        payload = service.full_snapshot(force=True)

        self.assertTrue(payload["ledger"]["persisted"])
        self.assertEqual(len(payload["items"]), 1)

    @staticmethod
    def _archive_fact(*refs):
        units = [{
            "unitKey": ref,
            "state": "succeeded",
            "scope": "file",
            "evidence": "verified",
            "eventAt": "2026-07-28T02:00:00Z",
            "observedAt": "2026-07-28T03:00:00Z",
            "freshUntil": "2026-07-28T03:05:00Z",
            "sourceRef": ref,
            "reasonCode": "SYMEDIA_ORGANIZED",
            "reasonText": "Symedia 整理入库完成",
        } for ref in refs]
        return {
            "stage": "symedia", "state": "succeeded", "scope": "file", "evidence": "verified",
            "eventAt": "2026-07-28T02:00:00Z",
            "observedAt": "2026-07-28T03:00:00Z", "freshUntil": "2026-07-28T03:05:00Z",
            "source": "Symedia", "sourceRef": refs[0] if len(refs) == 1 else "",
            "reasonCode": "SYMEDIA_ORGANIZED", "reasonText": "Symedia 整理入库完成",
            "units": units,
        }

    def test_archived_date_counts_unique_files_tasks_and_unlinked_files(self):
        chain = FakeTaskChain().get_chain()
        chain["services"] = {"symedia": {"connected": True}}
        chain["items"][0]["pipelineFacts"] = [self._archive_fact("symedia-1", "symedia-2")]
        chain["items"].append({
            "id": "symedia:unlinked", "title": "未关联文件", "mediaType": "unknown", "tmdbId": "",
            "seasonNumber": 0, "state": "completed", "confidence": "unlinked", "origin": "library",
            "steps": [], "sourceIds": {"subscriptionId": "", "qbHashes": [], "symediaIds": ["symedia-3"]},
            "pipelineFacts": [self._archive_fact("symedia-3")],
        })
        app = Flask(f"{__name__}-archive")
        fake = FakeTaskChain()
        fake.get_chain = lambda: chain
        app.extensions["mcc_task_chain_service"] = fake
        register_task_chain_v2(
            app,
            clock=lambda: datetime(2026, 7, 28, 3, 1, tzinfo=timezone.utc),
        )

        response = app.test_client().get("/api/v2/tasks/chains?archivedDate=2026-07-28")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["archiveSummary"], {
            "date": "2026-07-28",
            "timezone": "Asia/Shanghai",
            "archivedFiles": 3,
            "linkedFiles": 2,
            "linkedTasks": 1,
            "unlinkedFiles": 1,
        })
        self.assertEqual(payload["page"]["total"], 1)

    def test_archived_date_rejects_invalid_date_and_unavailable_source(self):
        app = Flask(f"{__name__}-archive-errors")
        app.extensions["mcc_task_chain_service"] = FakeTaskChain()
        register_task_chain_v2(app)
        client = app.test_client()

        invalid = client.get("/api/v2/tasks/chains?archivedDate=2026-02-30")
        unavailable = client.get("/api/v2/tasks/chains?archivedDate=2026-07-28")

        self.assertEqual((invalid.status_code, invalid.get_json()["code"]), (400, "TASK_ARCHIVED_DATE_INVALID"))
        self.assertEqual(
            (unavailable.status_code, unavailable.get_json()["code"]),
            (502, "TASK_ARCHIVE_SOURCE_UNAVAILABLE"),
        )

    def test_archived_date_uses_history_while_symedia_is_offline_and_deduplicates_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            chain = FakeTaskChain().get_chain()
            chain["services"] = {"symedia": {"connected": True}}
            chain["items"][0]["pipelineFacts"] = [self._archive_fact("symedia-1")]
            app = Flask(f"{__name__}-archive-history")
            fake = FakeTaskChain()
            fake.get_chain = lambda: chain
            app.extensions["mcc_task_chain_service"] = fake
            repository = ResourceTaskRepository(
                Path(directory) / "media.sqlite3",
                clock=lambda: datetime(2026, 7, 28, 3, 1, tzinfo=timezone.utc),
            )
            service = TaskChainV2Service(
                app,
                repository=repository,
                clock=lambda: datetime(2026, 7, 28, 3, 1, tzinfo=timezone.utc),
            )

            current = service.full_snapshot(force=True)
            current_summary = service.archive_summary("2026-07-28", current)
            offline_summary = service.archive_summary("2026-07-28", {
                "items": [],
                "services": {"symedia": {"connected": False}},
            })

            self.assertEqual(current_summary["archivedFiles"], 1)
            self.assertEqual(current_summary["linkedTasks"], 1)
            self.assertEqual(offline_summary, current_summary)

    def test_missing_pipeline_evidence_projects_to_no_action(self):
        payload = adapt_task_chain(
            FakeTaskChain().get_chain(),
            now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
        )
        item = payload["items"][0]

        self.assertEqual(
            [fact["stage"] for fact in item["pipelineFacts"]],
            ["torra", "qb", "cloud115", "symedia", "strm", "emby"],
        )
        self.assertTrue(all(
            (fact["state"], fact["evidence"]) == ("unknown", "missing")
            for fact in item["pipelineFacts"]
        ))
        self.assertEqual(item["pipelineOutcome"]["state"], "evidence_insufficient")
        self.assertEqual(payload["outcomeCounts"]["evidence_insufficient"], 1)
        self.assertEqual(item["outcomeState"], "evidence_insufficient")
        self.assertEqual(item["userState"], "no_action")
        self.assertEqual(item["completedAt"], "")
        self.assertEqual(item["confirmedStageCount"], 0)
        self.assertEqual(payload["statisticsMeta"]["playable"], {
            "scope": "current_unique_task_chains",
            "unit": "task_chain",
            "observedAt": "2026-07-22T03:00:00Z",
            "confirmation": "partial",
        })
        self.assertEqual(payload["outcomeCounts"]["playable"], 0)

    def test_list_exposes_shared_problem_groups_before_resource_pagination(self):
        app = Flask(f"{__name__}-problem-groups")
        service = TaskChainV2Service(app)
        items = [{
            "id": f"task-{episode}",
            "chainId": f"chain-{episode}",
            "targetKey": f"tv:tmdb:101:season:2:episode:{episode}",
            "title": "测试剧",
            "mediaType": "tv",
            "tmdbId": "101",
            "seasonNumber": 2,
            "episodeNumber": episode,
            "identityState": "linked",
            "outcomeState": "action_required",
            "pipelineOutcome": {
                "state": "action_required",
                "stage": "symedia",
                "reasonCode": "SYMEDIA_LIBRARY_FAILED",
                "reasonText": "Symedia 未完成媒体入库",
            },
            "reasonCode": "SYMEDIA_LIBRARY_FAILED",
            "reasonText": "Symedia 未完成媒体入库",
            "userReasonText": "Symedia 未完成媒体入库",
            "resultText": "Symedia 未完成媒体入库",
            "primaryAction": {
                "kind": "view_details", "label": "查看原因", "available": True, "reason": "查看证据",
            },
        } for episode in (2, 3, 4)]
        payload = {
            "contractVersion": 2,
            "generatedAt": "2026-07-30T02:00:00Z",
            "version": "snapshot-1",
            "items": items,
            "counts": {"total": 3},
            "services": {},
            "outcomeCounts": {"action_required": 3},
            "problemGroupSummary": {
                "actionRequiredGroups": 1,
                "actionRequiredResources": 3,
                "actionRequiredIdentityUnconfirmedResources": 0,
            },
        }
        service.full_snapshot = lambda force=False: payload

        result = service.list_items(outcome_states=["action_required"], offset=0, limit=1)

        self.assertEqual(result["page"]["total"], 3)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["problemGroupSummary"], {
            "actionRequiredGroups": 1,
            "actionRequiredResources": 3,
            "actionRequiredIdentityUnconfirmedResources": 0,
        })
        self.assertEqual(len(result["problemGroups"]), 1)
        self.assertEqual(result["problemGroups"][0]["resourceCount"], 3)
        self.assertEqual(result["problemGroups"][0]["episodeNumbers"], [2, 3, 4])
        self.assertEqual(len(result["problemGroups"][0]["members"]), 3)

    def test_problem_group_api_preserves_derived_symedia_reason_over_stale_qb_fields(self):
        app = Flask(f"{__name__}-problem-group-derived-reason")
        register_task_chain_v2(app)
        service = app.extensions["mcc_task_chain_v2_service"]
        service.full_snapshot = lambda force=False: {
            "contractVersion": 2,
            "generatedAt": "2026-08-07T02:00:00Z",
            "version": "snapshot-symedia-reason",
            "items": [{
                "id": "task-symedia-failed",
                "chainId": "chain-symedia-failed",
                "targetKey": "tv:tmdb:101:season:1:episode:15",
                "title": "测试剧",
                "mediaType": "tv",
                "tmdbId": "101",
                "seasonNumber": 1,
                "episodeNumber": 15,
                "identityState": "linked",
                "outcomeState": "action_required",
                "pipelineOutcome": {
                    "state": "action_required",
                    "stage": "symedia",
                    "reasonCode": "SYMEDIA_MEDIA_NOT_FOUND",
                    "reasonText": "Symedia 未查询到对应媒体信息",
                },
                "reasonCode": "QB_DOWNLOAD_FAILED",
                "reasonText": "qB 下载任务未正常继续",
                "userReasonText": "qB 下载任务未正常继续",
                "resultText": "qB 下载任务未正常继续",
            }],
            "counts": {"total": 1},
            "services": {},
            "outcomeCounts": {"action_required": 1},
        }

        response = app.test_client().get("/api/v2/tasks/chains?outcomeState=action_required")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["problemGroups"][0]["stage"], "symedia")
        self.assertEqual(payload["problemGroups"][0]["reasonCode"], "SYMEDIA_MEDIA_NOT_FOUND")
        self.assertEqual(payload["problemGroups"][0]["reasonText"], "Symedia 未查询到对应媒体信息")
        self.assertNotIn("qB 下载任务未正常继续", payload["problemGroups"][0]["reasonText"])

    def test_symedia_version_rule_protection_is_no_action_and_not_a_problem_group(self):
        chain = FakeTaskChain().get_chain()
        chain["items"][0]["episodeNumber"] = 7
        chain["items"][0]["pipelineFacts"] = [pipeline_fact(
            "symedia",
            "protected",
            reason_code="QUALITY_VERSION_RULE_NOT_MATCHED",
            reason_text="未命中允许入库的版本规则",
        )]
        app = Flask(f"{__name__}-symedia-version-rule-protection")
        fake = FakeTaskChain()
        fake.get_chain = lambda: chain
        app.extensions["mcc_task_chain_service"] = fake
        register_task_chain_v2(
            app,
            clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
        )

        response = app.test_client().get("/api/v2/tasks/chains?limit=20")
        payload = response.get_json()
        item = payload["items"][0]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(item["outcomeState"], "protected")
        self.assertEqual(item["userState"], "no_action")
        self.assertEqual(
            item["resultText"],
            "未命中允许入库的版本规则，已保留现有版本",
        )
        self.assertEqual(payload["problemGroups"], [])
        self.assertEqual(payload["problemGroupSummary"]["actionRequiredGroups"], 0)

    def test_verified_emby_episode_projects_playable_to_legacy_completed(self):
        chain = FakeTaskChain().get_chain()
        chain["items"][0]["episodeNumber"] = 3
        chain["items"][0]["pipelineFacts"] = [{
            "stage": "emby",
            "state": "succeeded",
            "scope": "episode",
            "evidence": "verified",
            "observedAt": "2026-07-22T03:00:00Z",
            "freshUntil": "2026-07-22T03:05:00Z",
            "source": "Emby",
            "sourceRef": "emby-private-episode-3",
            "reasonCode": "EMBY_EPISODE_INDEXED",
            "reasonText": "Emby 已收录目标集",
        }]

        item = adapt_task_chain(
            chain,
            now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
        )["items"][0]

        self.assertEqual(item["pipelineOutcome"]["state"], "playable")
        self.assertEqual(item["outcomeState"], "playable")
        self.assertEqual(item["userState"], "completed")
        self.assertEqual(item["resultText"], "已可播放")
        self.assertEqual(item["confirmedStageCount"], 1)
        self.assertEqual(item["playableAt"], "2026-07-22T03:00:00Z")
        self.assertEqual(item["completedAt"], item["playableAt"])

    def test_media_result_separates_archived_result_from_residual_qb_failure(self):
        chain = FakeTaskChain().get_chain()
        chain["items"][0].update({"episodeNumber": 3, "targetUnitKey": "episode:3"})
        chain["items"][0]["pipelineFacts"] = [
            pipeline_fact(
                "qb", "failed", scope="file",
                reason_code="QB_STALLED",
                reason_text="qB 下载持续无活动",
            ),
            pipeline_fact(
                "symedia", "succeeded", scope="file",
                reason_code="SYMEDIA_ORGANIZED",
                reason_text="Symedia 整理入库完成",
            ),
        ]

        item = adapt_task_chain(
            chain,
            now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
        )["items"][0]

        self.assertEqual(item["pipelineOutcome"]["state"], "action_required")
        self.assertEqual(item["mediaResult"]["state"], "archived")
        self.assertEqual(item["mediaResult"]["resultText"], "已整理入库")
        self.assertEqual(item["residualIssues"], [{
            "stage": "qb",
            "reasonCode": "QB_STALLED",
            "reasonText": "qB 下载持续无活动",
            "observedAt": "2026-07-22T03:00:00Z",
            "resourceCount": 1,
        }])

    def test_playable_media_result_keeps_upstream_failure_without_losing_completion(self):
        chain = FakeTaskChain().get_chain()
        chain["items"][0].update({"episodeNumber": 3, "targetUnitKey": "episode:3"})
        chain["items"][0]["pipelineFacts"] = [
            pipeline_fact(
                "qb", "failed", scope="file",
                reason_code="QB_STALLED",
                reason_text="qB 下载持续无活动",
            ),
            {
                **pipeline_fact(
                    "emby", "succeeded", scope="episode",
                    reason_code="EMBY_EPISODE_INDEXED",
                    reason_text="Emby 已收录目标集",
                ),
                "unitKey": "episode:3",
            },
        ]

        item = adapt_task_chain(
            chain,
            now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
        )["items"][0]

        self.assertEqual(item["pipelineOutcome"]["state"], "playable")
        self.assertEqual(item["userState"], "completed")
        self.assertEqual(item["mediaResult"]["state"], "playable")
        self.assertEqual(item["residualIssues"][0]["stage"], "qb")

    def test_failure_without_downstream_success_keeps_media_result_unknown(self):
        chain = FakeTaskChain().get_chain()
        chain["items"][0]["pipelineFacts"] = [pipeline_fact(
            "qb", "failed", scope="file",
            reason_code="QB_ERROR",
            reason_text="qB 下载失败",
        )]

        item = adapt_task_chain(
            chain,
            now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
        )["items"][0]

        self.assertEqual(item["pipelineOutcome"]["state"], "action_required")
        self.assertEqual(item["mediaResult"]["state"], "unknown")
        self.assertEqual(item["residualIssues"], [])

    def test_historical_emby_time_is_rederived_into_task_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            chain = FakeTaskChain().get_chain()
            chain["items"][0]["episodeNumber"] = 3
            emby_fact = {
                "stage": "emby",
                "state": "succeeded",
                "scope": "episode",
                "evidence": "verified",
                "eventAt": "2026-07-22T02:00:00Z",
                "observedAt": "2026-07-22T02:00:00Z",
                "freshUntil": "2026-07-22T04:05:00Z",
                "source": "Emby",
                "sourceRef": "emby-private-episode-3",
                "reasonCode": "EMBY_EPISODE_INDEXED",
                "reasonText": "Emby 已收录目标集",
            }
            chain["items"][0]["pipelineFacts"] = [emby_fact]
            app = Flask(f"{__name__}-playable-history")
            fake = FakeTaskChain()
            fake.get_chain = lambda: chain
            app.extensions["mcc_task_chain_service"] = fake
            repository = ResourceTaskRepository(
                Path(directory) / "media.sqlite3",
                clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
            )
            service = TaskChainV2Service(
                app,
                repository=repository,
                clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
            )
            service.full_snapshot(force=True)
            emby_fact.update({
                "eventAt": "2026-07-22T03:00:00Z",
                "observedAt": "2026-07-22T03:00:00Z",
            })

            refreshed = service.full_snapshot(force=True)
            item = refreshed["items"][0]

            self.assertEqual(item["pipelineFacts"][-1]["firstConfirmedPlayableAt"], "2026-07-22T02:00:00Z")
            self.assertEqual(item["pipelineOutcome"]["playableAt"], "2026-07-22T02:00:00Z")
            self.assertEqual(item["playableAt"], "2026-07-22T02:00:00Z")
            self.assertEqual(item["completedAt"], "2026-07-22T02:00:00Z")

    def test_migration_preview_route_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ResourceTaskRepository(
                Path(directory) / "media.sqlite3",
                clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
            )
            repository.record_snapshot({
                "items": [{
                    "chainId": "chain:legacy",
                    "mediaKey": "unknown:title:测试剧",
                    "targetKey": "unknown:title:测试剧:season:2",
                    "identityState": "unidentified",
                    "mediaType": "unknown",
                    "tmdbId": "",
                    "seasonNumber": 2,
                    "title": "测试剧.S02",
                    "sourceIds": {"qbHashes": ["hash-1"], "symediaIds": []},
                    "stages": [],
                }],
            })
            chain = FakeTaskChain().get_chain()
            chain["items"][0]["evidenceOwnership"] = [
                {
                    "artifactKey": "artifact:hash-1",
                    "ownerTargetKey": "tv:tmdb:101:season:2",
                    "matchMethod": "symedia_title_season_unique",
                    "confidence": "fallback",
                    "conflictCandidates": [],
                    "source": "qBittorrent",
                    "mediaType": "tv",
                    "seasonNumber": 2,
                },
                {
                    "artifactKey": "artifact:symedia:anchor-1",
                    "ownerTargetKey": "tv:tmdb:101:season:2",
                    "matchMethod": "symedia_tmdb_anchor",
                    "confidence": "strong",
                    "conflictCandidates": [],
                    "source": "Symedia",
                    "mediaType": "tv",
                    "seasonNumber": 2,
                },
            ]
            app = Flask(__name__)
            fake = FakeTaskChain()
            fake.get_chain = lambda: chain
            app.extensions["mcc_task_chain_service"] = fake
            register_task_chain_v2(
                app,
                repository=repository,
                clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
            )

            response = app.test_client().get("/api/v2/tasks/ledger/migrations/preview")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["artifactMigrations"], 1)
            self.assertTrue(response.get_json()["migrationPlans"][0]["artifactKey"].startswith("artifact:ref:"))
            self.assertNotIn("artifact:hash-1", response.get_data(as_text=True))
            self.assertEqual(repository.get_chain("chain:legacy")["chain_id"], "chain:legacy")
            canonical = chain_id("tv:tmdb:101", "tv:tmdb:101:season:2")
            self.assertIsNone(repository.get_chain(canonical))

    def test_old_chain_filter_and_detail_resolve_to_canonical_chain(self):
        class AliasRepository:
            def record_snapshot(self, payload):
                return {"persisted": True}

            @staticmethod
            def resolve_chain_id(value):
                expected = chain_id("tv:tmdb:101", "tv:tmdb:101:season:2")
                return expected if value == "chain:legacy" else value

        app = Flask(__name__)
        app.extensions["mcc_task_chain_service"] = FakeTaskChain()
        service = register_task_chain_v2(
            app,
            repository=AliasRepository(),
            clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
        )
        expected = chain_id("tv:tmdb:101", "tv:tmdb:101:season:2")

        listing = service.list_items(chain_id_value="chain:legacy")
        detail = service.detail("chain:legacy")

        self.assertEqual(listing["page"]["total"], 1)
        self.assertEqual(listing["items"][0]["chainId"], expected)
        self.assertEqual(detail["item"]["chainId"], expected)

    def test_identity_keys_are_stable_and_health_is_independent(self):
        chain = FakeTaskChain().get_chain()
        chain["items"][0]["pipelineFacts"] = [pipeline_fact(
            "qb", "failed", reason_code="QB_DOWNLOAD_FAILED", reason_text="qB 下载任务未正常继续",
        )]
        item = adapt_task_chain(chain, now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))["items"][0]
        self.assertEqual(item["mediaKey"], "tv:tmdb:101")
        self.assertEqual(item["targetKey"], "tv:tmdb:101:season:2")
        self.assertEqual(item["artifactKeys"], ["artifact:hash-1"])
        self.assertEqual(item["embyEvidenceScope"], "none")
        self.assertTrue(item["chainId"].startswith("chain:"))
        self.assertEqual(item["healthState"], "action_required")
        self.assertEqual(item["userState"], "action_required")
        self.assertEqual(item["resultText"], "qB 下载任务未正常继续")
        self.assertEqual(item["primaryAction"]["kind"], "view_details")
        self.assertEqual(item["stages"][0]["reasonCode"], "DOWNLOAD_BLOCKED")
        self.assertFalse(item["stages"][0]["actions"]["retry"])
        self.assertEqual(
            chain_id(item["mediaKey"], item["targetKey"], ["artifact:old"]),
            chain_id(item["mediaKey"], item["targetKey"], ["artifact:new"]),
        )

    def test_health_filter_is_applied_after_identity_adaptation(self):
        chain = FakeTaskChain().get_chain()
        self.assertEqual(len(adapt_task_chain(chain, health_filter="normal")["items"]), 0)
        self.assertEqual(len(adapt_task_chain(chain, health_filter="action_required")["items"]), 1)

    def test_route_rejects_invalid_filter_and_returns_v2_contract(self):
        app = Flask(__name__)
        app.extensions["mcc_task_chain_service"] = FakeTaskChain()
        register_task_chain_v2(app, clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))
        client = app.test_client()
        invalid = client.get("/api/v2/tasks/chains?health=bad")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(set(invalid.get_json()), {"code", "error", "request_id"})
        response = client.get("/api/v2/tasks/chains?health=action_required")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["contractVersion"], 2)
        self.assertEqual(len(response.get_json()["items"]), 1)
        invalid_user_state = client.get("/api/v2/tasks/chains?userState=invalid")
        self.assertEqual(invalid_user_state.status_code, 400)
        self.assertEqual(invalid_user_state.get_json()["code"], "TASK_USER_STATE_FILTER_INVALID")

    def test_subscription_filter_accepts_public_torra_key_and_legacy_raw_key(self):
        from app.torra_subscription_keys import torra_public_subscription_key

        remote_id = "remote-private-subscription"
        chain = FakeTaskChain().get_chain()
        chain["items"][0]["sourceIds"]["subscriptionId"] = f"torra:{remote_id}"
        app = Flask(f"{__name__}-public-torra-filter")
        fake = FakeTaskChain()
        fake.get_chain = lambda: chain
        app.extensions["mcc_task_chain_service"] = fake
        register_task_chain_v2(app, clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))
        client = app.test_client()

        public = client.get(
            f"/api/v2/tasks/chains?subscriptionId={torra_public_subscription_key(remote_id)}"
        ).get_json()
        legacy = client.get(
            f"/api/v2/tasks/chains?subscriptionId=torra:{remote_id}"
        ).get_json()

        self.assertEqual(public["page"]["total"], 1)
        self.assertEqual(legacy["page"]["total"], 1)

    def test_filtered_snapshot_persists_current_chain_without_transient_history(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Flask(__name__)
            app.extensions["mcc_task_chain_service"] = FakeTaskChain()
            repository = ResourceTaskRepository(
                Path(directory) / "media.sqlite3",
                clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
            )
            service = TaskChainV2Service(
                app,
                repository=repository,
                clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
            )

            payload = service.snapshot(health_filter="normal")
            service.snapshot(health_filter="normal")
            expected_chain_id = chain_id("tv:tmdb:101", "tv:tmdb:101:season:2")

            self.assertEqual(payload["items"], [])
            self.assertEqual(payload["ledger"]["chains"], 1)
            self.assertEqual(repository.get_chain(expected_chain_id)["health_state"], "action_required")
            events = repository.list_events(expected_chain_id)
            self.assertEqual(events, [])

    def test_duplicate_target_records_are_merged_into_one_chain(self):
        chain = FakeTaskChain().get_chain()
        chain["items"].append({
            "id": "qb:hash-2", "title": "测试剧", "mediaType": "tv", "tmdbId": "101", "seasonNumber": 2,
            "state": "completed", "confidence": "strong", "origin": "download", "progress": 100,
            "steps": [{"key": "download", "label": "获取 / 下载", "status": "done", "evidence": "verified", "detail": "下载完成", "source": "qBittorrent"}],
            "sourceIds": {"subscriptionId": "", "qbHashes": ["hash-2"], "symediaIds": []},
        })

        payload = adapt_task_chain(chain, now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))

        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["counts"]["total"], 1)
        self.assertEqual(payload["items"][0]["relatedRecords"], 2)
        self.assertEqual(payload["items"][0]["sourceIds"]["qbHashes"], ["hash-1", "hash-2"])
        self.assertEqual(payload["items"][0]["artifactKeys"], ["artifact:hash-1", "artifact:hash-2"])
        self.assertEqual(payload["items"][0]["healthState"], "action_required")

    def test_concurrent_download_summary_is_aggregated_and_returned_in_v2_list(self):
        chain = FakeTaskChain().get_chain()
        chain["items"][0].update({
            "activeDownloadTasks": 2,
            "completedDownloadTasks": 1,
        })
        chain["items"].append({
            "id": "qb:hash-2",
            "title": "测试剧",
            "mediaType": "tv",
            "tmdbId": "101",
            "seasonNumber": 2,
            "state": "active",
            "confidence": "strong",
            "origin": "download",
            "steps": [{
                "key": "download",
                "label": "获取 / 下载",
                "status": "active",
                "evidence": "verified",
                "source": "qBittorrent",
            }],
            "sourceIds": {"subscriptionId": "", "qbHashes": ["hash-2"], "symediaIds": []},
            "activeDownloadTasks": 3,
            "completedDownloadTasks": 4,
        })

        payload = adapt_task_chain(chain, now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))
        item = payload["items"][0]
        self.assertEqual(item["activeDownloadTasks"], 5)
        self.assertEqual(item["completedDownloadTasks"], 5)
        self.assertEqual(item["concurrentDownloadCount"], 5)

        app = Flask(__name__)
        fake = FakeTaskChain()
        fake.get_chain = lambda: chain
        app.extensions["mcc_task_chain_service"] = fake
        register_task_chain_v2(app, clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))
        listing = app.test_client().get("/api/v2/tasks/chains?limit=1").get_json()
        self.assertEqual(listing["items"][0]["concurrentDownloadCount"], 5)

    def test_qb_active_filter_is_independent_of_media_outcome_and_runs_before_pagination(self):
        app = Flask(f"{__name__}-qb-active")
        app.extensions["mcc_task_chain_service"] = FakeTaskChain()
        service = register_task_chain_v2(
            app,
            clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
        )
        common = {
            "mediaType": "tv",
            "seasonNumber": 1,
            "state": "active",
            "confidence": "strong",
            "healthState": "waiting",
            "identityState": "linked",
            "executionState": "running",
            "updatedAt": "2026-07-22T03:00:00Z",
            "sourceIds": {"subscriptionId": "", "qbHashes": [], "symediaIds": []},
            "steps": [],
            "stages": [],
            "pipelineFacts": [],
        }
        payload = {
            "contractVersion": 2,
            "generatedAt": "2026-07-22T03:00:00Z",
            "version": "qb-active-test",
            "counts": {"total": 4, "active": 1, "blocked": 1, "completed": 0, "waiting": 2, "unlinked": 1},
            "outcomeCounts": {"in_progress": 1, "action_required": 1, "waiting": 2},
            "services": {
                "qb": {"connected": True, "configured": True, "total": 4, "active": 4, "downloadSpeed": 1024},
                "torra": {}, "symedia": {}, "emby": {},
            },
            "items": [
                {
                    **common, "id": "chain-action", "chainId": "chain-action", "targetKey": "tv:1:season:1",
                    "title": "异常但仍下载", "tmdbId": "1", "outcomeState": "action_required",
                    "activeDownloadTasks": 2, "qbControl": {"total": 2, "active": 2},
                },
                {
                    **common, "id": "chain-progress", "chainId": "chain-progress", "targetKey": "tv:2:season:1",
                    "title": "正常下载", "tmdbId": "2", "outcomeState": "in_progress",
                    "activeDownloadTasks": 1, "qbControl": {"total": 1, "active": 1},
                },
                {
                    **common, "id": "qb:orphan", "chainId": "chain-orphan", "targetKey": "artifact:orphan",
                    "title": "未关联 qB 任务", "mediaType": "unknown", "tmdbId": "", "confidence": "unlinked",
                    "identityState": "unidentified", "outcomeState": "waiting",
                    "activeDownloadTasks": 1, "qbControl": {"total": 1, "active": 1},
                },
                {
                    **common, "id": "chain-inactive", "chainId": "chain-inactive", "targetKey": "tv:3:season:1",
                    "title": "没有活动下载", "tmdbId": "3", "outcomeState": "waiting",
                    "activeDownloadTasks": 0, "qbControl": {"total": 0, "active": 0},
                },
            ],
        }
        service.full_snapshot = lambda force=False: payload
        client = app.test_client()

        first_page = client.get("/api/v2/tasks/chains?qbActive=1&limit=1").get_json()
        listing = client.get("/api/v2/tasks/chains?qbActive=1&limit=100").get_json()

        self.assertEqual(first_page["page"]["total"], 3)
        self.assertTrue(first_page["page"]["hasMore"])
        self.assertEqual({row["outcomeState"] for row in listing["items"]}, {"action_required", "in_progress", "waiting"})
        self.assertTrue(any(row["id"].startswith("qb:") for row in listing["items"]))
        self.assertEqual(sum(row["qbControl"]["active"] for row in listing["items"]), 4)
        self.assertTrue(all(row["qbControl"]["active"] > 0 for row in listing["items"]))

        invalid = client.get("/api/v2/tasks/chains?qbActive=0")
        self.assertEqual((invalid.status_code, invalid.get_json()["code"]), (400, "TASK_QB_ACTIVE_FILTER_INVALID"))

    def test_merged_progress_uses_the_whole_stage_chain(self):
        chain = FakeTaskChain().get_chain()
        chain["items"][0]["progress"] = 100
        chain["items"][0]["steps"] = [
            {"key": "subscription", "label": "订阅", "status": "unknown", "evidence": "missing"},
            {"key": "download", "label": "qB 下载", "status": "done", "evidence": "verified"},
            {"key": "cloud115", "label": "115 接管", "status": "active", "evidence": "inferred"},
            {"key": "library", "label": "整理与入库", "status": "waiting", "evidence": "missing"},
        ]

        item = adapt_task_chain(chain, now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))["items"][0]

        self.assertEqual(item["progress"], 38)
        self.assertNotEqual(item["progress"], chain["items"][0]["progress"])

    def test_list_is_paginated_summary_and_detail_keeps_evidence(self):
        app = Flask(__name__)
        fake = FakeTaskChain()
        app.extensions["mcc_task_chain_service"] = fake
        register_task_chain_v2(app, clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))
        client = app.test_client()

        listing = client.get("/api/v2/tasks/chains?limit=1").get_json()
        chain_id_value = listing["items"][0]["chainId"]
        self.assertEqual(listing["page"], {"total": 1, "offset": 0, "limit": 1, "nextOffset": None, "hasMore": False})
        self.assertNotIn("stages", listing["items"][0])
        self.assertNotIn("pipelineFacts", listing["items"][0])
        self.assertIn("pipelineOutcome", listing["items"][0])
        self.assertNotIn("episodeEvidence", listing["items"][0])
        self.assertIn("stageSummary", listing["items"][0])

        detail = client.get(f"/api/v2/tasks/chains/{chain_id_value}").get_json()
        self.assertEqual(detail["item"]["chainId"], chain_id_value)
        self.assertTrue(detail["item"]["stages"])
        self.assertEqual(len(detail["item"]["pipelineFacts"]), 6)
        self.assertTrue(detail["item"]["artifactKeys"])
        self.assertEqual(detail["item"]["episodeEvidence"][0]["episodeStart"], 3)
        self.assertEqual(fake.calls, 1)

    def test_detail_exposes_only_verified_unique_rss_source_match(self):
        class RssRepository:
            def __init__(self):
                self.calls = []

            def find_unique_source_match(self, artifacts, subscriptions, target_key):
                self.calls.append((artifacts, subscriptions, target_key))
                return {"matchId": "rss-match-101"}

        app = Flask(f"{__name__}-rss-source")
        fake = FakeTaskChain()
        rss_repository = RssRepository()
        app.extensions["mcc_task_chain_service"] = fake
        app.extensions["mcc_private_rss"] = SimpleNamespace(repository=rss_repository)
        register_task_chain_v2(app, clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))
        client = app.test_client()
        chain_id_value = client.get("/api/v2/tasks/chains?limit=1").get_json()["items"][0]["chainId"]

        detail = client.get(f"/api/v2/tasks/chains/{chain_id_value}").get_json()["item"]

        self.assertEqual(detail["rssSourceMatch"], {"matchId": "rss-match-101"})
        self.assertEqual(len(rss_repository.calls), 1)
        artifacts, subscriptions, target_key = rss_repository.calls[0]
        self.assertEqual(artifacts, ["artifact:hash-1"])
        self.assertIn("sub-1", subscriptions)
        self.assertEqual(target_key, "tv:tmdb:101:season:2")

    def test_public_list_and_detail_redact_external_ids_paths_jobs_and_urls(self):
        raw_qb = "c" * 40
        raw_torra = "torra-private-task"
        raw_symedia = "symedia-private:/storage/private/task.mkv"
        chain = {
            "generatedAt": "2026-07-26T03:00:00Z",
            "items": [{
                "id": f"qb:{raw_qb}",
                "title": "安全边界测试 /storage/private/title.mkv",
                "mediaType": "tv",
                "tmdbId": "909",
                "seasonNumber": 1,
                "posterUrl": "https://image.private/poster.jpg",
                "state": "blocked",
                "confidence": "strong",
                "source": "ftp://source.private/jobs/1 token=private-token",
                "currentStep": r"\\nas.private\media\task.mkv 10.0.0.1:8080 nas:5000 api_hash=private-api-hash signature=private-signature Bearer private-bearer",
                "steps": [{
                    "key": "library",
                    "label": "整理与入库",
                    "status": "blocked",
                    "evidence": "verified",
                    "detail": "/storage/private/task.mkv 未查询到媒体信息",
                    "technicalReasonText": "job-private-1 http://symedia.private/jobs/1 /storage/private/task.mkv",
                    "reasonCode": "SYMEDIA_LIBRARY_FAILED",
                    "source": "Symedia",
                }],
                "sourceIds": {
                    "subscriptionId": f"torra:{raw_torra}",
                    "torraId": raw_torra,
                    "qbHashes": [raw_qb],
                    "symediaIds": [raw_symedia],
                },
                "episodeEvidence": [{
                    "seasonNumber": 1,
                    "episodeStart": 1,
                    "episodeEnd": 1,
                    "stage": "library",
                    "artifactKey": f"artifact:{raw_symedia}",
                    "status": "blocked",
                }],
            }],
            "services": {
                "qb": {"connected": True, "total": 1, "webUrl": "http://qb.private"},
                "torra": {
                    "connected": True,
                    "total": 1,
                    "webUrl": "http://torra.private",
                    "secupload115": {
                        "readable": True,
                        "perFileEvidence": False,
                        "pluginKey": "plugin-private",
                        "latestRun": {
                            "runId": "run-private",
                            "taskKey": "task-private",
                            "targetItemId": "target-private",
                            "status": "success",
                            "counts": {"success": 1, "failed": 0},
                        },
                    },
                },
                "symedia": {"connected": True, "webUrl": "http://symedia.private"},
                "emby": {"connected": True, "webUrl": "http://emby.private"},
            },
        }
        app = Flask(f"{__name__}-public-redaction")
        fake = FakeTaskChain()
        fake.get_chain = lambda: chain
        app.extensions["mcc_task_chain_service"] = fake
        register_task_chain_v2(app, clock=lambda: datetime(2026, 7, 26, 3, 1, tzinfo=timezone.utc))
        client = app.test_client()

        listing = client.get("/api/v2/tasks/chains?limit=1")
        chain_id_value = listing.get_json()["items"][0]["chainId"]
        detail = client.get(f"/api/v2/tasks/chains/{chain_id_value}")

        self.assertEqual((listing.status_code, detail.status_code), (200, 200))
        serialized = listing.get_data(as_text=True) + detail.get_data(as_text=True)
        for private_value in (
            raw_qb,
            raw_torra,
            raw_symedia,
            "job-private-1",
            "run-private",
            "task-private",
            "target-private",
            "plugin-private",
            "/storage/private",
            "qb.private",
            "torra.private",
            "symedia.private",
            "emby.private",
            "image.private",
            "source.private",
            "private-token",
            "nas.private",
            "10.0.0.1:8080",
            "nas:5000",
            "private-api-hash",
            "private-signature",
            "private-bearer",
        ):
            self.assertNotIn(private_value, serialized)
        item = detail.get_json()["item"]
        self.assertEqual(len(item["sourceIds"]["qbHashes"][0]), 40)
        self.assertNotEqual(item["sourceIds"]["qbHashes"][0], raw_qb)
        self.assertTrue(item["sourceIds"]["torraId"].startswith("torra:"))
        self.assertTrue(item["sourceIds"]["symediaIds"][0].startswith("symedia:"))
        self.assertTrue(item["artifactKeys"][0].startswith("artifact:ref:"))
        self.assertNotIn("technicalReasonText", item["stages"][0])
        self.assertEqual(item["posterUrl"], "")
        self.assertEqual(detail.get_json()["services"]["qb"]["webUrl"], "")
        self.assertNotIn("href", item["primaryAction"])

    def test_public_symedia_failure_hides_relative_media_path_fragments(self):
        self.assertEqual(
            safe_public_text("文件转移错误: S01E01/聪明镇S01E10.mkv"),
            "文件转移错误: [已隐藏]",
        )
        chain = FakeTaskChain().get_chain()
        chain["items"][0]["pipelineFacts"] = [{
            "stage": "symedia",
            "state": "failed",
            "scope": "file",
            "evidence": "verified",
            "eventAt": "2026-07-28T02:00:00Z",
            "observedAt": "2026-07-28T03:00:00Z",
            "freshUntil": "2026-07-28T03:05:00Z",
            "source": "Symedia",
            "sourceRef": "private-symedia-row",
            "reasonCode": "SYMEDIA_LIBRARY_FAILED",
            "reasonText": "文件转移错误: [已隐藏] S01E01/聪明镇S01E10.mkv 未查询到媒体信息",
            "units": [{
                "unitKey": "private-symedia-row",
                "state": "failed",
                "scope": "file",
                "evidence": "verified",
                "eventAt": "2026-07-28T02:00:00Z",
                "observedAt": "2026-07-28T03:00:00Z",
                "freshUntil": "2026-07-28T03:05:00Z",
                "sourceRef": "private-symedia-row",
                "reasonCode": "SYMEDIA_LIBRARY_FAILED",
                "reasonText": "文件转移错误: [已隐藏] S01E01/聪明镇S01E10.mkv 未查询到媒体信息",
            }],
        }]
        app = Flask(f"{__name__}-symedia-relative-path")
        fake = FakeTaskChain()
        fake.get_chain = lambda: chain
        app.extensions["mcc_task_chain_service"] = fake
        register_task_chain_v2(
            app,
            clock=lambda: datetime(2026, 7, 28, 3, 1, tzinfo=timezone.utc),
        )

        listing = app.test_client().get("/api/v2/tasks/chains?limit=1")
        chain_id_value = listing.get_json()["items"][0]["chainId"]
        detail = app.test_client().get(f"/api/v2/tasks/chains/{chain_id_value}")
        payload = detail.get_json()
        symedia = next(
            fact for fact in payload["item"]["pipelineFacts"]
            if fact["stage"] == "symedia"
        )
        serialized = listing.get_data(as_text=True) + detail.get_data(as_text=True)

        self.assertEqual((listing.status_code, detail.status_code), (200, 200))
        self.assertEqual(symedia["reasonText"], "Symedia 未查询到对应媒体信息")
        self.assertEqual(symedia["units"][0]["reasonText"], "Symedia 未查询到对应媒体信息")
        self.assertNotIn("S01E01", serialized)
        self.assertNotIn("聪明镇S01E10.mkv", serialized)

    def test_summary_and_conditional_list_share_cached_snapshot(self):
        app = Flask(__name__)
        fake = FakeTaskChain()
        app.extensions["mcc_task_chain_service"] = fake
        register_task_chain_v2(app, clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))
        client = app.test_client()

        summary = client.get("/api/v2/tasks/summary")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.get_json()["counts"]["total"], 1)
        listing = client.get("/api/v2/tasks/chains")
        unchanged = client.get("/api/v2/tasks/chains", headers={"If-None-Match": listing.headers["ETag"]})
        self.assertEqual(unchanged.status_code, 304)
        self.assertEqual(fake.calls, 1)

    def test_suspected_blocked_is_counted_and_filterable_without_red_failure(self):
        fake = FakeTaskChain()
        fake_payload = fake.get_chain()
        fake_payload["items"][0].update({"tmdbId": "", "confidence": "unlinked"})
        fake_payload["items"][0]["steps"] = [
            {"key": "download", "status": "done", "evidence": "verified", "source": "qBittorrent"},
            {"key": "cloud115", "status": "blocked", "evidence": "inferred", "source": "115", "detail": "长时间没有后续记录"},
        ]
        app = Flask(__name__)
        app.extensions["mcc_task_chain_service"] = FakeTaskChain()
        app.extensions["mcc_task_chain_service"].get_chain = lambda: fake_payload
        register_task_chain_v2(app, clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))
        client = app.test_client()

        summary = client.get("/api/v2/tasks/summary").get_json()
        self.assertEqual(summary["executionCounts"]["suspected_blocked"], 1)
        self.assertEqual(summary["healthCounts"]["action_required"], 0)
        self.assertEqual(summary["healthCounts"]["evidence_insufficient"], 1)
        listing = client.get("/api/v2/tasks/chains?executionState=suspected_blocked").get_json()
        self.assertEqual(listing["page"]["total"], 1)
        self.assertEqual(listing["items"][0]["identityState"], "unidentified")
        self.assertEqual(listing["items"][0]["executionState"], "suspected_blocked")
        self.assertEqual(listing["items"][0]["userState"], "no_action")

        invalid = client.get("/api/v2/tasks/chains?executionState=invalid")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.get_json()["code"], "TASK_EXECUTION_FILTER_INVALID")

    def test_user_state_result_and_completed_date_are_filterable(self):
        chain = FakeTaskChain().get_chain()
        chain["items"][0].update({
            "state": "completed", "confidence": "strong", "episodeNumber": 3,
            "pipelineFacts": [{
                "stage": "emby",
                "state": "succeeded",
                "scope": "episode",
                "evidence": "verified",
                "observedAt": "2026-07-22T02:00:00Z",
                "freshUntil": "2026-07-22T03:05:00Z",
                "source": "Emby",
                "sourceRef": "emby-private-episode-3",
                "reasonCode": "EMBY_EPISODE_INDEXED",
                "reasonText": "Emby 已收录目标集",
            }],
        })
        chain["items"][0]["steps"] = [
            {
                "key": "download", "label": "qB 下载", "status": "done", "evidence": "verified",
                "timestamp": "2026-07-22T01:00:00Z", "source": "qBittorrent",
            },
            {
                "key": "library", "label": "整理与入库", "status": "done", "evidence": "verified",
                "timestamp": "2026-07-22T02:00:00Z", "source": "Symedia",
            },
        ]
        chain["items"][0]["completedDownloadTasks"] = 13
        chain["items"][0]["embyIndexed"] = True
        app = Flask(__name__)
        fake = FakeTaskChain()
        fake.get_chain = lambda: chain
        app.extensions["mcc_task_chain_service"] = fake
        register_task_chain_v2(app, clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))
        client = app.test_client()

        summary = client.get("/api/v2/tasks/summary").get_json()
        self.assertEqual(summary["userCounts"]["completed"], 1)
        self.assertEqual(summary["outcomeCounts"]["playable"], 1)
        outcome_listing = client.get(
            "/api/v2/tasks/chains?outcomeState=playable&outcomeState=protected"
        ).get_json()
        self.assertEqual(outcome_listing["page"]["total"], 1)
        listing = client.get("/api/v2/tasks/chains?userState=completed&completedDate=2026-07-22").get_json()
        self.assertEqual(listing["page"]["total"], 1)
        self.assertEqual(listing["items"][0]["outcomeState"], "playable")
        self.assertEqual(listing["items"][0]["resultText"], "已可播放")
        self.assertEqual(listing["items"][0]["playableAt"], "2026-07-22T02:00:00Z")
        self.assertEqual(listing["items"][0]["completedAt"], "2026-07-22T02:00:00Z")
        invalid_outcome = client.get("/api/v2/tasks/chains?outcomeState=completed")
        self.assertEqual(invalid_outcome.status_code, 400)
        self.assertEqual(invalid_outcome.get_json()["code"], "TASK_OUTCOME_FILTER_INVALID")
        invalid_date = client.get("/api/v2/tasks/chains?completedDate=2026-99-99")
        self.assertEqual(invalid_date.status_code, 400)
        self.assertEqual(invalid_date.get_json()["code"], "TASK_COMPLETED_DATE_INVALID")

    def test_expired_stage_evidence_does_not_mark_task_in_progress_or_completed(self):
        chain = FakeTaskChain().get_chain()
        chain["items"][0].update({"state": "completed", "confidence": "strong", "embyIndexed": True})
        chain["items"][0]["steps"] = [{
            "key": "library",
            "label": "整理与入库",
            "status": "done",
            "evidence": "verified",
            "timestamp": "2026-07-21T01:00:00Z",
            "freshUntil": "2026-07-21T01:05:00Z",
            "source": "Symedia",
        }]
        completed = adapt_task_chain(
            chain,
            now=datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc),
        )["items"][0]
        self.assertEqual(completed["healthState"], "evidence_insufficient")
        self.assertEqual(completed["userState"], "no_action")
        self.assertEqual(completed["completedAt"], "")
        self.assertNotIn("已入库", completed["resultText"])
        self.assertNotIn("Emby 已识别", completed["resultText"])

        chain["items"][0].update({"state": "active", "embyIndexed": False})
        chain["items"][0]["activeDownloadTasks"] = 2
        chain["items"][0]["completedDownloadTasks"] = 13
        chain["items"][0]["steps"][0].update({
            "key": "download",
            "label": "qB 下载",
            "status": "active",
            "source": "qBittorrent",
        })
        active = adapt_task_chain(
            chain,
            now=datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc),
        )["items"][0]
        self.assertEqual(active["userState"], "no_action")
        self.assertEqual(active["activeDownloadTasks"], 0)
        self.assertEqual(active["completedDownloadTasks"], 0)
        self.assertEqual(active["concurrentDownloadCount"], 0)
        self.assertNotIn("正在下载", active["resultText"])
        self.assertNotIn("已下载", active["resultText"])

        chain["items"][0].update({"state": "blocked", "activeDownloadTasks": 0})
        chain["items"][0]["steps"][0].update({
            "status": "blocked",
            "reasonCode": "DOWNLOAD_STALLED",
        })
        blocked = adapt_task_chain(
            chain,
            now=datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc),
        )["items"][0]
        self.assertEqual(blocked["healthState"], "evidence_insufficient")
        self.assertEqual(blocked["userState"], "no_action")
        self.assertFalse(blocked["primaryAction"]["available"])

    def test_upstream_stage_without_evidence_stays_neutral_when_downstream_completed(self):
        # 场景：115/Symedia 下游已有完成证据，qB 无记录、订阅无本地记录，
        # 唯一真失败是 Symedia 识别失败。上游缺证据阶段不得染成失败文案。
        chain = FakeTaskChain().get_chain()
        chain["items"][0]["state"] = "blocked"
        chain["items"][0]["steps"] = [
            {
                "key": "subscription", "label": "订阅", "status": "unknown", "evidence": "missing",
                "detail": "未关联订阅中枢", "source": "",
            },
            {
                "key": "download", "label": "获取 / 下载", "status": "unknown", "evidence": "missing",
                "detail": "未关联 Torra 或 qB 任务", "source": "",
            },
            {
                "key": "cloud115", "label": "进入 115", "status": "done", "evidence": "verified",
                "detail": "Symedia 已收到 1 条源文件记录", "source": "Symedia",
                "timestamp": "2026-07-22T01:00:00Z",
            },
            {
                "key": "library", "label": "入库", "status": "blocked", "evidence": "verified",
                "detail": "0 成功 / 1 失败 · 未查询到媒体信息", "source": "Symedia",
                "reasonCode": "SYMEDIA_LIBRARY_FAILED", "timestamp": "2026-07-22T01:10:00Z",
            },
        ]
        chain["items"][0]["pipelineFacts"] = [pipeline_fact(
            "symedia", "failed", reason_code="SYMEDIA_LIBRARY_FAILED",
            reason_text="Symedia 未查询到对应媒体信息",
        )]

        item = adapt_task_chain(
            chain,
            now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
        )["items"][0]
        stages = {stage["stage"]: stage for stage in item["stages"]}

        for name in ("subscription", "download"):
            self.assertEqual(stages[name]["healthState"], "evidence_insufficient")
            self.assertIn("未关联", stages[name]["reasonText"])
            self.assertIn("未关联", stages[name]["userReasonText"])
            combined = f"{stages[name]['reasonText']} {stages[name]['userReasonText']} {stages[name]['recommendedAction']}"
            self.assertNotIn("未正常继续", combined)
            self.assertNotIn("刷新来源", combined)
        self.assertEqual(stages["cloud115"]["healthState"], "normal")
        self.assertEqual(stages["library"]["healthState"], "action_required")
        self.assertEqual(stages["library"]["reasonText"], "Symedia 未查询到对应媒体信息")
        # 真失败不被洗白：整链仍需要处理，且原因来自 Symedia
        self.assertEqual(item["userState"], "action_required")
        self.assertNotIn("未正常继续", item["resultText"])
        self.assertIn("Symedia", item["resultText"])

    def test_missing_upstream_evidence_without_downstream_completion_keeps_existing_copy(self):
        # 没有下游完成证据时，缺证据的 qB 阶段维持原有口径（不因本次修正扩大改动）。
        chain = FakeTaskChain().get_chain()
        chain["items"][0]["state"] = "waiting"
        chain["items"][0]["steps"] = [
            {
                "key": "download", "label": "获取 / 下载", "status": "unknown", "evidence": "missing",
                "detail": "未关联 Torra 或 qB 任务", "source": "",
            },
            {
                "key": "library", "label": "入库", "status": "waiting", "evidence": "missing",
                "detail": "尚无 Symedia 入库记录", "source": "",
            },
        ]

        item = adapt_task_chain(
            chain,
            now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
        )["items"][0]
        download = next(stage for stage in item["stages"] if stage["stage"] == "download")

        self.assertEqual(download["healthState"], "evidence_insufficient")
        self.assertNotEqual(download["reasonCode"], "STAGE_EVIDENCE_NOT_LINKED")

    def test_stage_exposes_safe_user_reason_and_keeps_technical_reason(self):
        chain = FakeTaskChain().get_chain()
        chain["items"][0]["steps"] = [{
            "key": "library",
            "label": "整理与入库",
            "status": "blocked",
            "evidence": "verified",
            "source": "Symedia",
            "reasonCode": "SYMEDIA_LIBRARY_FAILED",
            "detail": "/storage/cloud/云月大陆/S01E05.mkv 未查询到媒体信息",
        }]

        stage = adapt_task_chain(
            chain,
            now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
        )["items"][0]["stages"][0]

        self.assertEqual(stage["reasonText"], "Symedia 未查询到对应媒体信息")
        self.assertEqual(stage["userReasonText"], stage["reasonText"])
        self.assertIn("/storage/cloud/", stage["technicalReasonText"])

    def test_primary_action_ignores_protected_blocked_stage(self):
        chain = FakeTaskChain().get_chain()
        chain["services"] = {"qb": {"webUrl": "http://qb.local"}}
        chain["items"][0]["steps"] = [
            {
                "key": "library",
                "status": "blocked",
                "evidence": "verified",
                "source": "Symedia",
                "reasonCode": "QUALITY_HIGHER_VERSION_EXISTS",
                "detail": "higher quality version exists",
            },
            {
                "key": "download",
                "status": "blocked",
                "evidence": "verified",
                "source": "qBittorrent",
                "reasonCode": "DOWNLOAD_STALLED",
                "detail": "download stalled",
            },
        ]
        chain["items"][0]["pipelineFacts"] = [
            pipeline_fact(
                "symedia", "protected", reason_code="QUALITY_HIGHER_VERSION_EXISTS",
                reason_text="higher quality version exists",
            ),
            pipeline_fact("qb", "failed", reason_code="QB_DOWNLOAD_FAILED", reason_text="download stalled"),
        ]

        result = adapt_task_chain(
            chain,
            now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
        )["items"][0]

        self.assertEqual(result["healthState"], "action_required")
        self.assertEqual(result["primaryAction"]["kind"], "open_qb")

    def test_unidentified_historical_download_requires_no_daily_action(self):
        chain = FakeTaskChain().get_chain()
        chain["items"][0].update({"tmdbId": "", "confidence": "unlinked", "state": "waiting"})
        chain["items"][0]["activeDownloadTasks"] = 0
        chain["items"][0]["completedDownloadTasks"] = 1
        chain["items"][0]["steps"] = [
            {"key": "download", "status": "done", "evidence": "verified", "source": "qBittorrent"},
            {"key": "library", "status": "waiting", "evidence": "missing", "source": "task-chain"},
        ]

        result = adapt_task_chain(
            chain,
            now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
        )["items"][0]

        self.assertEqual(result["healthState"], "evidence_insufficient")
        self.assertEqual(result["identityState"], "unidentified")
        self.assertEqual(result["userState"], "no_action")

    def test_passive_subscription_waiting_requires_no_daily_action(self):
        chain = FakeTaskChain().get_chain()
        chain["items"][0].update({"state": "waiting", "activeDownloadTasks": 0})
        chain["items"][0]["steps"] = [
            {"key": "subscription", "status": "done", "evidence": "verified", "source": "Torra"},
            {"key": "download", "status": "waiting", "evidence": "missing", "source": "task-chain"},
        ]

        result = adapt_task_chain(
            chain,
            now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
        )["items"][0]

        self.assertEqual(result["healthState"], "waiting")
        self.assertEqual(result["userState"], "no_action")

    def test_active_download_remains_in_progress(self):
        chain = FakeTaskChain().get_chain()
        chain["items"][0].update({"state": "active", "activeDownloadTasks": 1})
        chain["items"][0]["steps"] = [
            {"key": "download", "status": "active", "evidence": "verified", "source": "qBittorrent"},
        ]
        chain["items"][0]["pipelineFacts"] = [pipeline_fact(
            "qb", "active", reason_code="QB_DOWNLOAD_ACTIVE", reason_text="qB 正在下载或排队",
        )]

        result = adapt_task_chain(
            chain,
            now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
        )["items"][0]

        self.assertEqual(result["healthState"], "waiting")
        self.assertEqual(result["userState"], "in_progress")

    def test_summary_and_chains_attach_optional_sanitized_system_issues(self):
        from app.quality_watch_repository import QualityWatchRepository
        from app.secupload_issue_runtime import SecuploadIssueService

        raw_category = "plugin-private-category"
        chain = FakeTaskChain().get_chain()
        chain["services"] = {
            "torra": {
                "connected": True,
                "total": 1,
                "secupload115": {
                    "configured": True,
                    "connected": True,
                    "readable": True,
                    "pluginEnabled": True,
                    "perFileEvidence": False,
                    "activeRuns": 0,
                    "configItems": [{
                        "itemId": raw_category,
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
                        "targetItemId": raw_category,
                        "enabled": True,
                        "nextRunAt": "2026-07-22T18:00:00+08:00",
                    }],
                    "recentRuns": [{
                        "runId": "run-private-1",
                        "taskKey": "retry_pending",
                        "targetItemId": raw_category,
                        "trigger": "schedule",
                        "status": "success",
                        "counts": {"success": 0, "failed": 1},
                        "startedAt": "2026-07-22T12:00:00+08:00",
                        "finishedAt": "2026-07-22T12:00:03+08:00",
                    }],
                    "latestBatch": {
                        "taskKey": "retry_pending",
                        "trigger": "schedule",
                        "status": "failed",
                        "counts": {"success": 0, "failed": 1},
                        "startedAt": "2026-07-22T12:00:00+08:00",
                        "finishedAt": "2026-07-22T12:00:03+08:00",
                    },
                    "nextRunAt": "2026-07-22T18:00:00+08:00",
                    "lastCheckedAt": "2026-07-22T12:00:04+08:00",
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            app = Flask(f"{__name__}-system-issues")
            fake = FakeTaskChain()
            fake.get_chain = lambda: chain
            app.extensions["mcc_task_chain_service"] = fake
            clock = lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc)
            repository = QualityWatchRepository(Path(directory) / "media.sqlite3", clock=clock)

            class SummaryOnlyTorra:
                @staticmethod
                def get_secupload_summary():
                    return chain["services"]["torra"]["secupload115"]

            app.extensions["mcc_secupload_issue"] = SecuploadIssueService(
                SummaryOnlyTorra(), repository, environment={}, clock=clock,
            )
            register_task_chain_v2(app, clock=clock)
            client = app.test_client()

            summary = client.get("/api/v2/tasks/summary")
            listing = client.get("/api/v2/tasks/chains")

            issue = summary.get_json()["systemIssues"][0]
            self.assertEqual(issue["id"], "secupload_failures")
            self.assertEqual(issue["state"], "recovering")
            self.assertEqual(issue["categories"][0]["recentFailedCounts"], [1])
            self.assertTrue(issue["categories"][0]["id"].startswith("category:"))
            self.assertEqual(listing.get_json()["systemIssues"][0]["state"], "recovering")
            serialized = summary.get_data(as_text=True) + listing.get_data(as_text=True)
            for private_value in (raw_category, "run-private-1"):
                self.assertNotIn(private_value, serialized)

    def test_summary_without_issue_service_keeps_optional_system_issues_empty(self):
        app = Flask(f"{__name__}-no-issue-service")
        app.extensions["mcc_task_chain_service"] = FakeTaskChain()
        register_task_chain_v2(app, clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))

        summary = app.test_client().get("/api/v2/tasks/summary").get_json()

        self.assertEqual(summary.get("systemIssues", []), [])

    def test_route_validates_pagination_and_missing_detail(self):
        app = Flask(__name__)
        app.extensions["mcc_task_chain_service"] = FakeTaskChain()
        register_task_chain_v2(app, clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))
        client = app.test_client()

        invalid = client.get("/api/v2/tasks/chains?limit=0")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.get_json()["code"], "TASK_PAGINATION_INVALID")
        missing = client.get("/api/v2/tasks/chains/chain:missing")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.get_json()["code"], "TASK_CHAIN_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
