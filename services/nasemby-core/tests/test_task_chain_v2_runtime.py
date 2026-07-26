from __future__ import annotations

import unittest
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask

from app.resource_identity_runtime import chain_id
from app.resource_task_repository import ResourceTaskRepository
from app.task_chain_v2_runtime import TaskChainV2Service, adapt_task_chain, register_task_chain_v2


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
        item = adapt_task_chain(FakeTaskChain().get_chain(), now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))["items"][0]
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

    def test_filtered_snapshot_persists_full_chain_before_response_filter(self):
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
            expected_chain_id = chain_id("tv:tmdb:101", "tv:tmdb:101:season:2")

            self.assertEqual(payload["items"], [])
            self.assertEqual(payload["ledger"]["chains"], 1)
            self.assertEqual(repository.get_chain(expected_chain_id)["health_state"], "action_required")

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
        self.assertNotIn("episodeEvidence", listing["items"][0])
        self.assertIn("stageSummary", listing["items"][0])

        detail = client.get(f"/api/v2/tasks/chains/{chain_id_value}").get_json()
        self.assertEqual(detail["item"]["chainId"], chain_id_value)
        self.assertTrue(detail["item"]["stages"])
        self.assertTrue(detail["item"]["artifactKeys"])
        self.assertEqual(detail["item"]["episodeEvidence"][0]["episodeStart"], 3)
        self.assertEqual(fake.calls, 1)

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
        chain["items"][0].update({"state": "completed", "confidence": "strong"})
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
        listing = client.get("/api/v2/tasks/chains?userState=completed&completedDate=2026-07-22").get_json()
        self.assertEqual(listing["page"]["total"], 1)
        self.assertEqual(listing["items"][0]["resultText"], "已下载 13 个 · 已入库 · Emby 已识别")
        self.assertEqual(listing["items"][0]["completedAt"], "2026-07-22T02:00:00Z")
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
