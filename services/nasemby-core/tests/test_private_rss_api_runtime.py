from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.main import create_app
from app.private_rss_repository import PrivateRssRepository
from app.quality_watch_repository import QualityWatchRepository
from app.subscription_repository import SubscriptionRepository
from app.torra_subscription_keys import torra_public_subscription_key


class FakeCollector:
    def fetch_source(self, source_id, persist=False):
        return {"status": "success", "items": 2, "title": "测试 RSS"}


class FakeManualMatchRuntime:
    def __init__(self, repository):
        self.repository = repository
        self.calls = []

    def create_manual_match(self, item_id, subscription_id, unit_id):
        self.calls.append((item_id, subscription_id, unit_id))
        existing = self.repository.get_match_for_item_unit(item_id, unit_id)
        match = existing or self.repository.create_match(
            item_id,
            subscription_id,
            unit_id,
            {"identity": {"basis": "title"}, "matchSource": "manual"},
        )
        return {
            "status": "existing" if existing else "created",
            "match": match,
        }


class PrivateRssApiRuntimeTests(unittest.TestCase):
    def test_rss_items_api_exposes_local_media_metadata_without_sensitive_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "media_control_center.sqlite3"
            subscriptions = SubscriptionRepository(database)
            subscriptions.upsert_item({
                "title": "资源卡片中文名",
                "media_type": "tv",
                "tmdb_id": "778899",
                "target_season": 1,
                "year": "2026",
                "poster_url": "https://image.example/poster.jpg?token=poster-secret",
                "private_note": "subscription-payload-secret",
            }, "tv:metadata-api")
            repository = PrivateRssRepository(database)
            source = repository.save_source({
                "name": "测试站",
                "feedUrl": "https://tracker.example/rss?passkey=source-secret",
            })
            repository.upsert_items(source["id"], [{
                "fingerprint": "metadata-api",
                "title": "Release.Name.S01E01.2160p",
                "description": "❁ 片 名: 资源卡片中文名 ❁ 年 代: 2026",
                "media_type": "tv",
                "season_number": 1,
                "episode_start": 1,
                "episode_end": 1,
                "tmdb_id": "778899",
                "identity_status": "identified",
                "download_url": "https://tracker.example/download?passkey=item-secret",
            }])
            app = create_app(
                access_environment={"NASEMBY_CORE_WRITE_ENABLED": "false", "MCC_PRIVATE_RSS_ENABLED": "false"},
                private_rss_repository=repository,
                private_rss_collector=FakeCollector(),
                quality_watch_repository=QualityWatchRepository(database),
            )

            response = app.test_client().get(
                "/api/v2/rss-items?query=资源卡片中文名&tmdbId=778899"
                "&mediaType=tv&seasonNumber=1&episodeNumber=1"
            )
            payload = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["total"], 1)
            self.assertEqual(payload["items"][0]["mediaTitle"], "资源卡片中文名")
            self.assertEqual(payload["items"][0]["sourceTitle"], "资源卡片中文名")
            self.assertEqual(payload["items"][0]["mediaYear"], "2026")
            self.assertEqual(payload["items"][0]["posterUrl"], "https://image.example/poster.jpg")
            response_text = response.get_data(as_text=True)
            for forbidden in (
                "poster-secret", "subscription-payload-secret", "source-secret", "item-secret",
                "download_url", "downloadUrl", "feedUrl", "passkey", "token=",
            ):
                self.assertNotIn(forbidden, response_text)

    def test_cleanup_preview_confirm_and_audit_are_safe_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "media_control_center.sqlite3"
            repository = PrivateRssRepository(database)
            source = repository.save_source({
                "name": "测试站",
                "feedUrl": "https://tracker.example/rss?passkey=source-secret",
            })
            repository.upsert_items(source["id"], [{
                "fingerprint": "cleanup-api",
                "title": "待整理条目 S01E03",
                "download_url": "https://tracker.example/download?passkey=item-secret",
                "detail_url": "https://tracker.example/detail?token=detail-secret",
            }])
            item = repository.search_items(limit=10)["items"][0]
            match = repository.create_match(
                item["id"], "torra:raw-subscription-secret", "torra:raw-subscription-secret:s1:e3", {},
            )
            repository.save_match_evaluation([match["id"]], {
                "status": "blocked",
                "reason": "subscription_missing",
            })
            app = create_app(
                access_environment={"NASEMBY_CORE_WRITE_ENABLED": "true", "MCC_PRIVATE_RSS_ENABLED": "false"},
                private_rss_repository=repository,
                private_rss_collector=FakeCollector(),
                quality_watch_repository=QualityWatchRepository(database),
            )
            client = app.test_client()

            preview_response = client.post(
                "/api/v2/rss-match-cleanups/previews",
                json={"matchIds": [match["id"]]},
            )
            preview = preview_response.get_json()
            preview_detail = client.get(preview_response.headers["Location"])
            apply_body = {
                "previewId": preview["id"],
                "fingerprint": preview["fingerprint"],
                "matchIds": [match["id"]],
                "idempotencyKey": "cleanup-api-once",
            }
            applied = client.post("/api/v2/rss-match-cleanups", json=apply_body)
            replay = client.post("/api/v2/rss-match-cleanups", json=apply_body)
            audit = client.get("/api/v2/rss-match-cleanups?limit=10")

            self.assertEqual(preview_response.status_code, 201)
            self.assertEqual(preview_detail.status_code, 200)
            self.assertEqual(preview_detail.get_json()["id"], preview["id"])
            self.assertEqual(preview["itemCount"], 1)
            self.assertEqual(applied.status_code, 200)
            self.assertEqual(applied.get_json(), replay.get_json())
            self.assertEqual(audit.status_code, 200)
            self.assertEqual(audit.get_json()["items"][0]["archivedCount"], 1)
            response_text = "\n".join((
                preview_response.get_data(as_text=True),
                preview_detail.get_data(as_text=True),
                applied.get_data(as_text=True),
                audit.get_data(as_text=True),
            ))
            for forbidden in (
                "source-secret", "item-secret", "detail-secret", "raw-subscription-secret",
                "download_url", "downloadUrl", "feedUrl", "passkey", "token=", "https://",
            ):
                self.assertNotIn(forbidden, response_text)

    def test_manual_match_post_uses_create_semantics_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "media_control_center.sqlite3"
            environment = {"NASEMBY_CORE_WRITE_ENABLED": "true", "MCC_PRIVATE_RSS_ENABLED": "false"}
            repository = PrivateRssRepository(database)
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [{"fingerprint": "idempotent-match", "title": "测试条目"}])
            item_id = repository.search_items()["items"][0]["id"]
            app = create_app(
                access_environment=environment,
                private_rss_repository=repository,
                private_rss_collector=FakeCollector(),
                quality_watch_repository=QualityWatchRepository(database),
            )
            runtime = FakeManualMatchRuntime(repository)
            app.extensions["mcc_private_rss"].match_runtime = runtime
            client = app.test_client()
            public_key = torra_public_subscription_key("subscription-1")
            body = {
                "rssItemId": item_id,
                "subscriptionId": public_key,
                "unitId": f"{public_key}:s1:e1",
            }

            created = client.post("/api/v2/rss-matches", json=body)
            environment["NASEMBY_CORE_WRITE_ENABLED"] = "false"
            existing = client.post("/api/v2/rss-matches", json=body)

            self.assertEqual(created.status_code, 201)
            self.assertEqual(existing.status_code, 200)
            self.assertEqual(created.headers["Location"], f"/api/v2/rss-matches/{created.get_json()['id']}")
            self.assertEqual(existing.get_json(), created.get_json())
            self.assertEqual(runtime.calls, [
                (item_id, public_key, f"{public_key}:s1:e1"),
                (item_id, public_key, f"{public_key}:s1:e1"),
            ])
            repository.save_match_evaluation([created.get_json()["id"]], {
                "status": "scored",
                "decision": "best_available",
                "reason": "version_fields_unconfirmed",
                "candidateScore": 28,
                "candidateSummary": {
                    "versionSummary": "测试条目 2160p WEB-DL",
                    "versionState": "unconfirmed",
                    "privateField": "must-not-leak",
                },
            })
            presented = client.get(created.headers["Location"]).get_json()
            self.assertEqual(
                presented["candidateSummary"]["versionState"],
                "unconfirmed",
            )
            self.assertNotIn("privateField", presented["candidateSummary"])

    def test_manual_match_location_resolves_to_same_public_dto_without_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "media_control_center.sqlite3"
            repository = PrivateRssRepository(database)
            source = repository.save_source({
                "name": "测试站",
                "feedUrl": "https://tracker.example/rss?passkey=source-secret",
            })
            repository.upsert_items(source["id"], [{
                "fingerprint": "manual-match",
                "title": "测试条目 S01E01",
                "published_at": "2026-07-30T16:00:00Z",
                "download_url": "https://tracker.example/download?passkey=item-secret",
                "detail_url": "https://tracker.example/details?token=detail-secret",
            }])
            item_id = repository.search_items()["items"][0]["id"]
            app = create_app(
                access_environment={"NASEMBY_CORE_WRITE_ENABLED": "false", "MCC_PRIVATE_RSS_ENABLED": "false"},
                private_rss_repository=repository,
                private_rss_collector=FakeCollector(),
                quality_watch_repository=QualityWatchRepository(database),
            )
            app.extensions["mcc_private_rss"].match_runtime = FakeManualMatchRuntime(repository)
            client = app.test_client()

            public_key = torra_public_subscription_key("subscription-1")
            created = client.post("/api/v2/rss-matches", json={
                "rssItemId": item_id,
                "subscriptionId": public_key,
                "unitId": f"{public_key}:s1:e1",
            })
            detail = client.get(created.headers["Location"])
            legacy_list = client.get("/api/v2/rss-matches?limit=10")
            grouped = client.get("/api/v2/rss-matches?view=groups&limit=10")
            artifact_grouped = client.get("/api/v2/rss-matches?view=artifact-groups&limit=10")
            artifact_grouped_by_item = client.get(
                f"/api/v2/rss-matches?view=artifact-groups&itemId={item_id}&limit=2"
            )
            grouped_scoreable = client.get("/api/v2/rss-matches?view=groups&groupScope=scoreable&limit=10")
            grouped_cleanup = client.get("/api/v2/rss-matches?view=groups&groupScope=cleanup&limit=10")
            grouped_monitoring = client.get("/api/v2/rss-matches?view=groups&groupState=monitoring_rss&limit=10")
            grouped_upgrade = client.get("/api/v2/rss-matches?view=groups&groupState=upgrade_available&limit=10")
            grouped_subscription = client.get(
                f"/api/v2/rss-matches?view=groups&subscriptionId={public_key}"
                "&mediaType=tv&seasonNumber=1&episodeNumber=1"
            )
            grouped_match = client.get(
                f"/api/v2/rss-matches?view=groups&matchId={created.get_json()['id']}"
            )
            published_today = client.get("/api/v2/rss-items?publishedDate=2026-07-31&window=all")
            invalid_published_date = client.get("/api/v2/rss-items?publishedDate=2026-7-31")
            invalid_view = client.get("/api/v2/rss-matches?view=unknown")
            invalid_group_state = client.get("/api/v2/rss-matches?view=groups&groupState=unknown")
            invalid_group_scope = client.get("/api/v2/rss-matches?view=groups&groupScope=unknown")

            self.assertEqual(created.status_code, 201)
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(legacy_list.status_code, 200)
            self.assertIn("items", legacy_list.get_json())
            self.assertNotIn("groups", legacy_list.get_json())
            self.assertEqual(grouped.status_code, 200)
            self.assertEqual(artifact_grouped.status_code, 200)
            self.assertEqual(artifact_grouped_by_item.status_code, 200)
            self.assertEqual(artifact_grouped_by_item.get_json()["total"], 1)
            self.assertEqual(
                artifact_grouped_by_item.get_json()["groups"][0]["candidates"][0]["itemId"],
                item_id,
            )
            self.assertNotIn("items", grouped.get_json())
            self.assertEqual(grouped.get_json()["total"], 1)
            self.assertEqual(len(grouped.get_json()["groups"]), 1)
            self.assertEqual(grouped.get_json()["groups"][0]["candidateCount"], 1)
            self.assertEqual(grouped.get_json()["groups"][0]["baselineState"], "baseline_missing")
            self.assertEqual(grouped.get_json()["groups"][0]["nextAction"], "preview_baseline_initialization")
            self.assertEqual(grouped.get_json()["groups"][0]["blockerCode"], "baseline_missing")
            artifact = artifact_grouped.get_json()["groups"][0]
            self.assertTrue(artifact["id"].startswith("rss-artifact:"))
            self.assertEqual(len(artifact["coveredUnits"]), 1)
            self.assertFalse(artifact["winsAllCoveredUnits"])
            self.assertNotIn("artifactKey", artifact)
            self.assertNotIn("artifactKey", artifact["representativeMatch"])
            self.assertNotIn("artifactKey", artifact["unitResults"][0]["match"])
            self.assertEqual(grouped.get_json()["counts"]["total"], 1)
            self.assertEqual(grouped.get_json()["counts"]["scoreableTotal"], 1)
            self.assertEqual(grouped.get_json()["counts"]["needsCleanup"], 0)
            self.assertEqual(grouped.get_json()["counts"]["monitoringRss"], 1)
            self.assertEqual(grouped_scoreable.get_json()["total"], 1)
            self.assertEqual(grouped_cleanup.get_json()["total"], 0)
            self.assertEqual(grouped_monitoring.get_json()["total"], 1)
            self.assertEqual(grouped_upgrade.get_json()["total"], 0)
            self.assertEqual(grouped_subscription.get_json()["total"], 1)
            self.assertEqual(grouped_match.get_json()["groups"][0]["candidates"][0]["id"], created.get_json()["id"])
            self.assertEqual(published_today.get_json()["total"], 1)
            self.assertEqual(invalid_published_date.status_code, 422)
            self.assertEqual(invalid_published_date.get_json()["code"], "RSS_QUERY_INVALID")
            self.assertEqual(
                grouped.get_json()["groups"][0]["candidates"][0],
                detail.get_json(),
            )
            self.assertEqual(invalid_view.status_code, 422)
            self.assertEqual(invalid_view.get_json()["code"], "RSS_MATCH_QUERY_INVALID")
            self.assertEqual(invalid_group_state.status_code, 422)
            self.assertEqual(invalid_group_state.get_json()["code"], "RSS_MATCH_QUERY_INVALID")
            self.assertEqual(invalid_group_scope.status_code, 422)
            self.assertEqual(invalid_group_scope.get_json()["code"], "RSS_MATCH_QUERY_INVALID")
            self.assertEqual(detail.get_json(), created.get_json())
            self.assertEqual(detail.get_json(), repository.get_match(created.get_json()["id"]))
            self.assertEqual(set(detail.get_json()), {
                "id", "itemId", "subscriptionId", "unitId", "status", "reason",
                "triggerActionId", "torraLinked", "targetKey", "artifactKey",
                "ruleId", "ruleHash", "candidateScore", "baselineScore",
                "evaluationStatus", "decision", "evaluationReason",
                "evaluationActionId", "downloadActionId", "candidateSummary",
                "baselineSummary", "bestCandidate", "evaluatedAt",
                "archiveState", "archivedAt", "archiveReasonCode", "archiveRunId", "version",
                "createdAt", "updatedAt",
            })
            response_text = detail.get_data(as_text=True)
            for forbidden in (
                "source-secret", "item-secret", "detail-secret", "download_url", "downloadUrl",
                "detail_url", "detailUrl", "feed_url", "feedUrl", "passkey",
            ):
                self.assertNotIn(forbidden, response_text)

    def test_legacy_torra_match_has_one_public_projection_across_list_detail_and_create(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "media_control_center.sqlite3"
            repository = PrivateRssRepository(database)
            source = repository.save_source({
                "name": "测试站",
                "feedUrl": "https://tracker.example/rss",
            })
            repository.upsert_items(source["id"], [{
                "fingerprint": "legacy-torra-match",
                "title": "历史匹配 S01E01",
            }])
            item_id = repository.search_items()["items"][0]["id"]
            remote_id = "legacy-torra-subscription-secret"
            raw_key = f"torra:{remote_id}"
            raw_unit_key = f"{raw_key}:s1:e1"
            stored = repository.create_match(
                item_id,
                raw_key,
                raw_unit_key,
                {
                    "identity": {
                        "basis": "title-alias",
                        "tmdbId": "202",
                        "alias": (
                            "安全别名 https://tracker.example/item?token=url-secret "
                            f"C:\\media\\private.mkv token=reason-secret {remote_id}"
                        ),
                        "rawId": remote_id,
                    },
                    "mediaType": "tv",
                    "year": {"item": "2026", "subscription": 2026, "url": "https://secret.example"},
                    "season": {"item": "1", "unit": 1, "path": "/srv/private"},
                    "episode": {"start": "1", "end": 1, "unit": 1, "token": "episode-secret"},
                    "matchSource": "manual",
                    "externalJobId": "job-secret",
                    "rawSubscriptionId": remote_id,
                    "path": "/srv/private/rss.json",
                    "url": "https://secret.example/rss",
                    "token": "reason-secret",
                },
            )
            stored = repository.update_match(stored["id"], "triggered", "local-action-123")
            app = create_app(
                access_environment={"NASEMBY_CORE_WRITE_ENABLED": "false"},
                private_rss_repository=repository,
                private_rss_collector=FakeCollector(),
                quality_watch_repository=QualityWatchRepository(database),
            )
            app.extensions["mcc_private_rss"].match_runtime = FakeManualMatchRuntime(repository)
            client = app.test_client()

            listed = client.get("/api/v2/rss-matches?status=triggered").get_json()["items"][0]
            detail = client.get(f"/api/v2/rss-matches/{stored['id']}").get_json()
            existing = client.post("/api/v2/rss-matches", json={
                "rssItemId": item_id,
                "subscriptionId": raw_key,
                "unitId": raw_unit_key,
            })

            public_key = torra_public_subscription_key(remote_id)
            expected_unit_key = f"{public_key}:s1:e1"
            self.assertEqual(existing.status_code, 200)
            self.assertEqual(listed, detail)
            self.assertEqual(existing.get_json(), detail)
            self.assertEqual(detail["subscriptionId"], public_key)
            self.assertEqual(detail["unitId"], expected_unit_key)
            self.assertEqual(detail["triggerActionId"], "local-action-123")
            self.assertEqual(detail["reason"], {
                "identity": {
                    "basis": "title-alias",
                    "tmdbId": "202",
                    "alias": detail["reason"]["identity"]["alias"],
                },
                "mediaType": "tv",
                "year": {"item": 2026, "subscription": 2026},
                "season": {"item": 1, "unit": 1},
                "episode": {"start": 1, "end": 1, "unit": 1},
                "matchSource": "manual",
            })
            self.assertIn("安全别名", detail["reason"]["identity"]["alias"])
            self.assertNotIn("externalJobId", detail)
            self.assertNotIn("external_job_id", detail)
            response_text = existing.get_data(as_text=True)
            for forbidden in (
                remote_id,
                "tracker.example",
                "secret.example",
                "C:\\media\\private.mkv",
                "/srv/private",
                "url-secret",
                "reason-secret",
                "episode-secret",
                "job-secret",
                "rawSubscriptionId",
                "externalJobId",
            ):
                self.assertNotIn(forbidden, response_text)
            self.assertEqual(repository.get_match(stored["id"])["subscriptionId"], raw_key)
            self.assertEqual(repository.get_match(stored["id"])["unitId"], raw_unit_key)
            self.assertEqual(
                repository.get_match(stored["id"])["reason"]["rawSubscriptionId"],
                remote_id,
            )

    def test_rss_match_detail_returns_consistent_not_found_error(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "media_control_center.sqlite3"
            app = create_app(
                access_environment={},
                private_rss_repository=PrivateRssRepository(database),
                private_rss_collector=FakeCollector(),
                quality_watch_repository=QualityWatchRepository(database),
            )

            response = app.test_client().get("/api/v2/rss-matches/missing-match")

            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.get_json()["code"], "RSS_MATCH_NOT_FOUND")
            self.assertEqual(response.get_json()["error"], "RSS 匹配不存在")
            self.assertEqual(response.get_json()["request_id"], response.headers["X-Request-ID"])

    def test_crud_is_local_and_collection_test_has_separate_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            action_repository = QualityWatchRepository(Path(directory) / "media_control_center.sqlite3")
            environment = {"NASEMBY_CORE_WRITE_ENABLED": "true", "MCC_PRIVATE_RSS_ENABLED": "false"}
            app = create_app(
                access_environment=environment,
                private_rss_repository=repository,
                private_rss_collector=FakeCollector(),
                quality_watch_repository=action_repository,
            )
            client = app.test_client()
            self.assertIn("mcc_rss_subscription_match_runtime", app.extensions)
            created = client.post("/api/v2/rss-sources", json={
                "name": "测试站",
                "feedUrl": "https://tracker.example/rss?passkey=secret-value",
                "intervalMinutes": 30,
                "retentionDays": 7,
            })
            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.get_json()["intervalMinutes"], 30)
            source_id = created.get_json()["id"]
            self.assertNotIn("secret-value", created.get_data(as_text=True))
            detail = client.get(created.headers["Location"])
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.get_json()["id"], source_id)
            self.assertNotIn("secret-value", detail.get_data(as_text=True))
            self.assertEqual(client.get("/api/v2/rss-sources").get_json()["summary"]["sources"], 1)
            repository.upsert_items(source_id, [{"fingerprint": "match-one", "title": "测试条目"}])
            item_id = repository.search_items()["items"][0]["id"]
            item_detail = client.get(f"/api/v2/rss-items/{item_id}")
            self.assertEqual(item_detail.status_code, 200)
            self.assertEqual(item_detail.get_json()["identityStatus"], "unidentified")
            self.assertEqual(item_detail.get_json()["followState"], "unlinked")
            self.assertNotIn("download_url", item_detail.get_data(as_text=True))
            self.assertNotIn("detail_url", item_detail.get_data(as_text=True))
            self.assertEqual(client.get("/api/v2/rss-items?identityStatus=unidentified").get_json()["total"], 1)
            self.assertEqual(client.get("/api/v2/rss-items?reviewState=needs_review").get_json()["total"], 1)
            self.assertEqual(client.get("/api/v2/rss-items?reviewState=follow_needs_review").get_json()["total"], 0)
            self.assertEqual(client.get("/api/v2/rss-items?reviewState=unlinked").get_json()["total"], 1)
            self.assertEqual(client.get("/api/v2/rss-items?followState=linked").get_json()["total"], 0)
            self.assertEqual(client.get("/api/v2/rss-items?followState=unlinked").get_json()["total"], 1)
            self.assertEqual(client.get("/api/v2/rss-items?identityStatus=invalid").status_code, 422)
            self.assertEqual(client.get("/api/v2/rss-items?reviewState=invalid").status_code, 422)
            self.assertEqual(client.get("/api/v2/rss-items?followState=invalid").status_code, 422)
            self.assertEqual(client.post("/api/v2/rss-items/identity-backfills", json={"limit": 201}).status_code, 422)
            repository.upsert_items(source_id, [{
                "fingerprint": "history-imdb",
                "title": "History Movie 2024",
                "description": "https://www.imdb.com/title/tt7654321/",
                "media_type": "movie",
            }])
            backfill = client.post("/api/v2/rss-items/identity-backfills", json={"limit": 50})
            self.assertEqual(backfill.status_code, 200)
            self.assertEqual(backfill.get_json()["identified"], 1)
            self.assertEqual(repository.search_items(query="History Movie")["items"][0]["imdbId"], "tt7654321")
            backfill_summary = client.get("/api/v2/rss-sources").get_json()["summary"]
            self.assertTrue(backfill_summary["identityBackfillRan"])
            self.assertEqual(backfill_summary["lastIdentityBackfillIdentified"], 1)
            self.assertGreaterEqual(backfill_summary["lastIdentityBackfillRemaining"], 1)
            matcher = client.post("/api/v2/rss-items/match-runs", json={"limit": 200})
            self.assertEqual(matcher.status_code, 200)
            self.assertGreaterEqual(matcher.get_json()["scanned"], 1)
            matcher_summary = client.get("/api/v2/rss-sources").get_json()["summary"]
            self.assertTrue(matcher_summary["matcherRan"])
            self.assertEqual(matcher_summary["lastMatchStatus"], "success")
            repository.create_match(item_id, "tv:202:s1", "tv:202:s1:s1:e1", {"identity": {"basis": "title"}})
            listed_matches = client.get("/api/v2/rss-matches?status=candidate").get_json()
            self.assertEqual(listed_matches["total"], 1)
            self.assertEqual(listed_matches["items"][0]["unitId"], "tv:202:s1:s1:e1")
            disabled = client.post(f"/api/v2/rss-sources/{source_id}/tests")
            self.assertEqual(disabled.status_code, 503)
            environment["MCC_PRIVATE_RSS_ENABLED"] = "true"
            accepted = client.post(f"/api/v2/rss-sources/{source_id}/tests")
            self.assertEqual(accepted.status_code, 202)
            self.assertTrue(accepted.headers["Location"].startswith("/api/v2/automation-actions/"))
            action = client.get(accepted.headers["Location"])
            self.assertEqual(action.get_json()["status"], "succeeded")
            self.assertEqual(action.get_json()["result"]["items"], 2)
            deleted = client.delete(f"/api/v2/rss-sources/{source_id}")
            self.assertEqual(deleted.status_code, 204)

    def test_rss_items_accept_target_identity_and_scope_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            app = create_app(
                access_environment={"NASEMBY_CORE_WRITE_ENABLED": "true", "MCC_PRIVATE_RSS_ENABLED": "false"},
                private_rss_repository=repository,
                private_rss_collector=FakeCollector(),
                quality_watch_repository=QualityWatchRepository(Path(directory) / "media_control_center.sqlite3"),
            )
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [{
                "fingerprint": "target", "title": "Unrelated English Name S01E01", "tmdb_id": "279323",
                "identity_status": "identified", "media_type": "tv", "season_number": 1,
            }])

            response = app.test_client().get(
                "/api/v2/rss-items?query=%E9%AC%BC%E8%B0%9C%E4%B8%9C%E5%AE%AB"
                "&tmdbId=279323&mediaType=tv&seasonNumber=1&year=2026"
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["total"], 1)
            self.assertEqual(response.get_json()["items"][0]["matchMethod"], "tmdb_exact")

            self.assertEqual(app.test_client().get("/api/v2/rss-items?mediaType=invalid").status_code, 422)
            self.assertEqual(app.test_client().get("/api/v2/rss-items?seasonNumber=bad").status_code, 422)


if __name__ == "__main__":
    unittest.main()
