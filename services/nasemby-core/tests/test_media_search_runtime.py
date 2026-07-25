from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from flask import Flask

from app.media_search_runtime import _read_tmdb_candidates, register_media_search
from app.task_chain_runtime import _orphan_qb_item
from app.task_chain_v2_runtime import adapt_task_chain


class FakeWorkbench:
    def snapshot(self, *, limit=None, offset=0, media_type="", query=""):
        items = [{
            "id": "subscription-secret-id",
            "title": "雀骨",
            "mediaType": "tv",
            "tmdbId": "202",
            "seasonNumber": 1,
            "posterUrl": "https://image.tmdb.org/t/p/w342/poster.jpg",
            "updatedAt": "2026-07-25T01:00:00Z",
            "origin": "manual",
            "torra": {
                "status": "linked",
                "remoteId": "torra-secret-id",
            },
            "qb": {"hashes": ["hash-secret"]},
            "cloud115": {"ids": ["115-secret-id"]},
            "library": {"detail": "/volume/media/雀骨"},
        }]
        wanted = str(query or "").casefold()
        if wanted:
            items = [
                item for item in items
                if wanted in item["title"].casefold() or wanted in item["tmdbId"]
            ]
        return {"items": items}


class FakeTasks:
    def full_snapshot(self):
        common = {
            "title": "雀骨",
            "mediaType": "tv",
            "tmdbId": "202",
            "posterUrl": "https://image.tmdb.org/t/p/w342/task.jpg",
            "userState": "completed",
            "primaryAction": {
                "kind": "none",
                "label": "",
                "available": False,
                "reason": "token=must-not-leak",
            },
            "embyIndexed": True,
            "embyEvidenceScope": "episode",
            "activeDownloadTasks": 0,
            "completedDownloadTasks": 13,
            "sourceIds": {
                "qbHashes": ["hash-secret"],
                "symediaIds": ["symedia-secret"],
            },
            "artifactKeys": ["artifact:secret"],
        }
        return {
            "items": [
                {
                    **common,
                    "chainId": "chain:secret-one",
                    "updatedAt": "2026-07-25T02:00:00Z",
                    "stages": [
                        {
                            "stage": "download",
                            "status": "done",
                            "healthState": "normal",
                            "evidence": "verified",
                            "observedAt": "2026-07-25T01:20:00Z",
                            "detail": "/downloads/雀骨",
                        },
                        {
                            "stage": "cloud115",
                            "status": "done",
                            "healthState": "normal",
                            "evidence": "verified",
                            "observedAt": "2026-07-25T01:30:00Z",
                        },
                        {
                            "stage": "library",
                            "status": "done",
                            "healthState": "normal",
                            "evidence": "verified",
                            "observedAt": "2026-07-25T01:40:00Z",
                        },
                    ],
                    "episodeEvidence": [
                        {
                            "stage": "download",
                            "status": "done",
                            "seasonNumber": 1,
                            "episodeStart": 13,
                            "episodeEnd": 13,
                            "artifactKey": "artifact:secret",
                            "source": "qBittorrent",
                            "observedAt": "2026-07-25T01:20:00Z",
                        },
                        {
                            "stage": "library",
                            "status": "done",
                            "seasonNumber": 1,
                            "episodeStart": 13,
                            "episodeEnd": 13,
                            "path": "/volume/media/雀骨/S01E13.mkv",
                            "source": "Symedia",
                            "observedAt": "2026-07-25T01:40:00Z",
                        },
                    ],
                },
                {
                    **common,
                    "chainId": "chain:secret-two",
                    "updatedAt": "2026-07-24T02:00:00Z",
                    "completedDownloadTasks": 0,
                    "stages": [],
                    "episodeEvidence": [{
                        "stage": "download",
                        "status": "done",
                        "seasonNumber": 1,
                        "episodeStart": 99,
                        "episodeEnd": 99,
                        "artifactKey": "artifact:torra-range",
                        "source": "Torra",
                        "observedAt": "",
                    }],
                },
            ],
        }


class FakeCalendar:
    def __init__(self, payload=None):
        self.payload = payload or {
            "calendar": {
                "entries": [{
                    "key": "subscription-secret-id",
                    "title": "雀骨",
                    "mediaType": "tv",
                    "tmdbId": "202",
                    "date": "2026-07-26",
                    "airAt": "2026-07-26T20:00:00+08:00",
                    "episodeLabel": "S01E14",
                    "status": "upcoming",
                    "inLibrary": False,
                    "chainId": "chain:secret-one",
                }],
            },
        }
        self.snapshot_calls = 0

    def cached_snapshot(self, year, month, media_type):
        return self.payload

    def snapshot(self, year, month, media_type):
        self.snapshot_calls += 1
        raise AssertionError("媒体搜索不得主动生成整月日历")


class EmptyCalendarCache(FakeCalendar):
    def __init__(self):
        super().__init__(payload={})

    def cached_snapshot(self, year, month, media_type):
        return None


class LegacySlowCalendar:
    def __init__(self):
        self.snapshot_calls = 0

    def snapshot(self, year, month, media_type):
        self.snapshot_calls += 1
        return {
            "calendar": {"entries": []},
        }


class FakeEmby:
    def is_configured(self):
        return True

    def get_tmdb_library_index(self):
        return {"movies": set(), "series": {"202", "999"}}


class FakeRssRepository:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def search_items(self, **options):
        self.calls.append(options)
        return {"items": list(self.items), "total": len(self.items)}


class FakeRss:
    def __init__(self, items):
        self.repository = FakeRssRepository(items)


class MediaSearchRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.extensions["mcc_subscription_workbench"] = FakeWorkbench()
        self.app.extensions["mcc_task_chain_v2_service"] = FakeTasks()
        self.app.extensions["mcc_calendar_timeline"] = FakeCalendar()
        self.app.extensions["mcc_emby_client"] = FakeEmby()
        register_media_search(
            self.app,
            clock=lambda: datetime(2026, 7, 25, 3, 0, tzinfo=timezone.utc),
            tmdb_reader=lambda _query, _target, _limit: [],
        )
        self.client = self.app.test_client()

    def test_search_aggregates_and_deduplicates_by_media_key(self):
        response = self.client.get("/api/v2/search?q=雀骨&limit=20")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["page"], {"total": 1, "limit": 20})
        self.assertEqual(len(payload["items"]), 1)
        item = payload["items"][0]
        self.assertEqual(item["mediaKey"], "tv:202")
        self.assertEqual(item["title"], "雀骨")
        self.assertEqual(item["sources"], ["calendar", "emby", "subscription", "task"])
        self.assertEqual(item["subscriptionStatus"], "following")
        self.assertEqual(item["embyStatus"], "available")

        by_key = self.client.get("/api/v2/search?q=tv:202").get_json()
        self.assertEqual(by_key["items"][0]["mediaKey"], "tv:202")
        self.assertEqual(by_key["items"][0]["subscriptionStatus"], "following")
        self.assertEqual(self.app.extensions["mcc_calendar_timeline"].snapshot_calls, 0)

    def test_search_skips_calendar_without_complete_cache_and_never_calls_live_snapshot(self):
        legacy = LegacySlowCalendar()
        self.app.extensions["mcc_calendar_timeline"] = legacy

        legacy_response = self.client.get("/api/v2/search?q=雀骨")

        self.assertEqual(legacy_response.status_code, 200)
        self.assertEqual(legacy.snapshot_calls, 0)
        self.assertNotIn("calendar", legacy_response.get_json()["items"][0]["sources"])

        self.app.extensions["mcc_calendar_timeline"] = EmptyCalendarCache()
        empty_response = self.client.get("/api/v2/search?q=雀骨")

        self.assertEqual(empty_response.status_code, 200)
        self.assertNotIn("calendar", empty_response.get_json()["items"][0]["sources"])

    def test_media_overview_returns_verified_lifecycle_and_deep_links(self):
        response = self.client.get("/api/v2/media/tv:202")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["media"]["mediaKey"], "tv:202")
        self.assertEqual(payload["subscription"]["status"], "following")
        self.assertEqual(payload["subscription"]["torraStatus"], "linked")
        self.assertEqual(payload["download"]["status"], "completed")
        self.assertEqual(payload["download"]["completedTasks"], 13)
        self.assertEqual(payload["download"]["latestEpisode"]["label"], "S01E13")
        self.assertEqual(payload["cloud115"]["status"], "completed")
        self.assertEqual(payload["library"]["status"], "completed")
        self.assertEqual(payload["library"]["latestEpisode"]["label"], "S01E13")
        self.assertEqual(payload["emby"], {"status": "available", "evidenceScope": "episode"})
        self.assertEqual(payload["playback"], {"status": "available", "directLinkAvailable": False})
        self.assertEqual(
            payload["resultText"],
            "追更中 · Torra 已同步 · 已下载 13 个 · 已进入 115 · 已入库 · Emby 可看",
        )
        self.assertEqual(payload["primaryAction"]["kind"], "view_subscription")
        self.assertIn("tmdbId=202", payload["links"]["tasks"])
        self.assertIn("q=%E9%9B%80%E9%AA%A8", payload["links"]["calendar"])
        self.assertIn("tmdbId=202", payload["links"]["subscription"])

        legacy_key = self.client.get("/api/v2/media/tv:tmdb:202")
        self.assertEqual(legacy_key.status_code, 200)
        self.assertEqual(legacy_key.get_json()["media"]["mediaKey"], "tv:202")

    def test_empty_and_unknown_search_return_no_results(self):
        empty = self.client.get("/api/v2/search?q=%20%20")
        missing = self.client.get("/api/v2/search?q=不存在的作品")
        emby_only = self.client.get("/api/v2/search?q=tv:999")
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.get_json()["items"], [])
        self.assertEqual(empty.get_json()["page"]["total"], 0)
        self.assertEqual(missing.status_code, 200)
        self.assertEqual(missing.get_json()["items"], [])
        self.assertEqual(emby_only.status_code, 200)
        self.assertEqual(len(emby_only.get_json()["items"]), 1)
        self.assertEqual(emby_only.get_json()["items"][0]["mediaKey"], "tv:999")
        self.assertEqual(emby_only.get_json()["items"][0]["sources"], ["emby"])
        self.assertEqual(emby_only.get_json()["items"][0]["embyStatus"], "available")

    def test_identified_rss_items_form_one_locatable_result_per_media_key(self):
        rss = FakeRss([
            {
                "id": "rss-secret-one",
                "title": "Archive Show S01E01",
                "mediaType": "tv",
                "tmdbId": "303",
                "identityStatus": "identified",
                "sourceName": "Feed A",
            },
            {
                "id": "rss-secret-two",
                "title": "Archive Show S01E02",
                "mediaType": "tv",
                "tmdbId": "303",
                "identityStatus": "identified",
                "sourceName": "Feed B",
            },
            {
                "id": "rss-unidentified",
                "title": "Archive Show Unknown",
                "mediaType": "tv",
                "tmdbId": "404",
                "identityStatus": "unidentified",
                "sourceName": "Feed C",
            },
        ])
        self.app.extensions["mcc_private_rss"] = rss

        response = self.client.get("/api/v2/search?q=Archive%20Show&limit=20")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["page"]["total"], 1)
        self.assertEqual(payload["items"][0]["mediaKey"], "tv:303")
        self.assertEqual(payload["items"][0]["sources"], ["rss"])
        self.assertIn("/rss-library?", payload["items"][0]["links"]["rss"])
        self.assertIn("identityStatus=identified", payload["items"][0]["links"]["rss"])
        self.assertEqual(rss.repository.calls[0]["identity_status"], "identified")

    def test_tmdb_fallback_forms_result_and_external_failure_degrades_to_empty(self):
        calls = []

        def reader(query, target, limit):
            calls.append((query, target, limit))
            return [{
                "title": "Localized Film",
                "mediaType": "movie",
                "tmdbId": "707",
                "year": "2026",
                "posterUrl": "https://image.tmdb.org/t/p/w342/remote.jpg",
            }]

        service = self.app.extensions["mcc_media_search"]
        service.tmdb_reader = reader

        response = self.client.get("/api/v2/search?q=Original%20Alias&limit=5")

        self.assertEqual(response.status_code, 200)
        item = response.get_json()["items"][0]
        self.assertEqual(item["mediaKey"], "movie:707")
        self.assertEqual(item["sources"], ["tmdb"])
        self.assertEqual(item["year"], "2026")
        self.assertEqual(calls, [("Original Alias", None, 5)])

        def failing_reader(_query, _target, _limit):
            raise RuntimeError("api_key=must-not-leak")

        service.tmdb_reader = failing_reader
        failed = self.client.get("/api/v2/search?q=Only%20Remote")
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.get_json()["items"], [])
        self.assertNotIn("must-not-leak", json.dumps(failed.get_json()))

    def test_unlinked_local_task_is_searchable_and_opens_task(self):
        self.app.extensions["mcc_task_chain_v2_service"].full_snapshot = lambda: {
            "items": [{
                "chainId": "chain:public-bleach",
                "mediaKey": "tv:title:bleach-sennen-kessen-hen",
                "title": "BLEACH Sennen Kessen-hen Kashin-tan",
                "mediaType": "tv",
                "tmdbId": "",
                "userState": "action_required",
                "healthState": "action_required",
                "executionState": "confirmed_failed",
                "freshUntil": "2026-07-26T00:00:00Z",
                "primaryAction": {
                    "kind": "view_details",
                    "label": "View details",
                    "available": True,
                },
                "stages": [{
                    "stage": "library",
                    "status": "blocked",
                    "healthState": "action_required",
                    "evidence": "verified",
                    "freshUntil": "2026-07-26T00:00:00Z",
                }],
            }],
        }

        response = self.client.get("/api/v2/search?q=BLEACH")

        self.assertEqual(response.status_code, 200)
        item = response.get_json()["items"][0]
        self.assertEqual(item["tmdbId"], "")
        self.assertEqual(item["chainId"], "chain:public-bleach")
        self.assertEqual(item["sources"], ["task"])
        self.assertEqual(item["userState"], "action_required")
        self.assertEqual(item["links"]["overview"], item["links"]["tasks"])
        self.assertIn("chainId=chain%3Apublic-bleach", item["links"]["tasks"])
        self.assertEqual(item["links"]["api"], "")

    def test_unknown_media_type_qb_orphan_is_searchable_by_public_chain(self):
        orphan = _orphan_qb_item({
            "hash": "private-orphan-hash",
            "name": "Raw.Orphan.Show.S01E01",
            "status": "downloading",
            "state": "downloading",
            "progress": 0.5,
            "addedOn": 1753412400,
        }, {
            "confidence": "unlinked",
            "artifactKey": "artifact:private-orphan-hash",
            "ownerTargetKey": "",
            "matchMethod": "none",
        }, {})
        snapshot = adapt_task_chain(
            {"items": [orphan], "services": {}},
            now=datetime(2026, 7, 25, 3, 0, tzinfo=timezone.utc),
        )
        self.app.extensions["mcc_task_chain_v2_service"].full_snapshot = lambda: snapshot

        response = self.client.get("/api/v2/search?q=Raw%20Orphan")

        self.assertEqual(response.status_code, 200)
        item = response.get_json()["items"][0]
        self.assertEqual(item["mediaType"], "unknown")
        self.assertEqual(item["tmdbId"], "")
        self.assertTrue(item["chainId"].startswith("chain:"))
        self.assertEqual(item["links"]["overview"], item["links"]["tasks"])
        self.assertIn(f"chainId={item['chainId'].replace(':', '%3A')}", item["links"]["tasks"])
        self.assertNotIn("private-orphan-hash", json.dumps(item))

    def test_current_task_with_expired_download_stage_does_not_report_counts(self):
        self.app.extensions["mcc_task_chain_v2_service"].full_snapshot = lambda: {
            "items": [{
                "chainId": "chain:expired-download",
                "mediaKey": "tv:tmdb:202",
                "title": "雀骨",
                "mediaType": "tv",
                "tmdbId": "202",
                "userState": "no_action",
                "healthState": "evidence_insufficient",
                "freshUntil": "2026-07-26T00:00:00Z",
                "activeDownloadTasks": 2,
                "completedDownloadTasks": 13,
                "embyIndexed": False,
                "stages": [{
                    "stage": "download",
                    "status": "active",
                    "healthState": "evidence_insufficient",
                    "evidence": "verified",
                    "freshUntil": "2026-07-24T00:00:00Z",
                }],
            }],
        }

        lifecycle = self.client.get("/api/v2/media/tv:202").get_json()

        self.assertEqual(lifecycle["download"]["status"], "unknown")
        self.assertEqual(lifecycle["download"]["activeTasks"], 0)
        self.assertEqual(lifecycle["download"]["completedTasks"], 0)
        self.assertNotIn("正在下载", lifecycle["resultText"])
        self.assertNotIn("已下载", lifecycle["resultText"])

    def test_tmdb_reader_uses_existing_read_path_without_cache_writes(self):
        config = {
            "api_key": "secret-v3-key",
            "api_token": "",
            "api_base_url": "https://api.themoviedb.org/3",
            "image_base_url": "https://image.tmdb.org/t/p",
        }
        with patch("app.discover_runtime.load_tmdb_config", return_value=config), patch(
            "app.discover_runtime.tmdb_credentials_available",
            return_value=True,
        ), patch(
            "app.discover_runtime.http_json",
            return_value={
                "results": [{
                    "id": 808,
                    "media_type": "tv",
                    "name": "Remote Series",
                    "first_air_date": "2026-07-01",
                    "poster_path": "/remote.jpg",
                }],
            },
        ) as http_json, patch(
            "app.discover_runtime.tmdb_image",
            return_value="https://image.tmdb.org/t/p/w342/remote.jpg",
        ), patch("app.discover_runtime.set_discover_cache") as cache_write, patch(
            "app.discover_runtime._write_tmdb_match_cache",
        ) as match_cache_write:
            rows = _read_tmdb_candidates("Remote Series", None, 10)

        self.assertEqual(rows[0]["mediaType"], "tv")
        self.assertEqual(rows[0]["tmdbId"], "808")
        self.assertNotIn("secret-v3-key", json.dumps(rows))
        self.assertEqual(http_json.call_args.kwargs["timeout"], 12)
        cache_write.assert_not_called()
        match_cache_write.assert_not_called()

    def test_protected_unverified_and_expired_blocked_stages_are_not_actionable(self):
        payload = FakeTasks().full_snapshot()
        protected = dict(payload["items"][0])
        protected.update({
            "userState": "action_required",
            "healthState": "action_required",
            "executionState": "action_required",
            "activeDownloadTasks": 0,
            "completedDownloadTasks": 0,
            "embyIndexed": False,
            "primaryAction": {
                "kind": "retry_stage",
                "label": "Retry",
                "available": True,
            },
        })
        protected["stages"] = [
            {
                "stage": "download",
                "status": "blocked",
                "healthState": "protected",
                "evidence": "verified",
                "freshUntil": "2026-07-26T00:00:00Z",
            },
            {
                "stage": "download",
                "status": "blocked",
                "healthState": "action_required",
                "evidence": "inferred",
                "freshUntil": "2026-07-26T00:00:00Z",
            },
            {
                "stage": "download",
                "status": "blocked",
                "healthState": "action_required",
                "evidence": "verified",
                "freshUntil": "2026-07-24T00:00:00Z",
            },
        ]
        payload["items"] = [protected]
        self.app.extensions["mcc_task_chain_v2_service"].full_snapshot = lambda: payload
        self.app.extensions["mcc_emby_client"].get_tmdb_library_index = lambda: {
            "movies": set(),
            "series": set(),
        }

        response = self.client.get("/api/v2/media/tv:202")

        self.assertEqual(response.status_code, 200)
        lifecycle = response.get_json()
        self.assertEqual(lifecycle["userState"], "no_action")
        self.assertEqual(lifecycle["download"]["status"], "unknown")
        self.assertEqual(lifecycle["primaryAction"]["kind"], "view_subscription")

    def test_current_verified_action_required_stage_remains_actionable(self):
        payload = FakeTasks().full_snapshot()
        blocked = dict(payload["items"][0])
        blocked.update({
            "userState": "action_required",
            "healthState": "action_required",
            "executionState": "confirmed_failed",
            "activeDownloadTasks": 0,
            "completedDownloadTasks": 0,
            "primaryAction": {
                "kind": "retry_stage",
                "label": "Retry",
                "available": True,
            },
        })
        blocked["stages"] = [{
            "stage": "download",
            "status": "blocked",
            "healthState": "action_required",
            "evidence": "verified",
            "freshUntil": "2026-07-26T00:00:00Z",
        }]
        payload["items"] = [blocked]
        self.app.extensions["mcc_task_chain_v2_service"].full_snapshot = lambda: payload

        response = self.client.get("/api/v2/media/tv:202")

        self.assertEqual(response.status_code, 200)
        lifecycle = response.get_json()
        self.assertEqual(lifecycle["userState"], "action_required")
        self.assertEqual(lifecycle["download"]["status"], "action_required")
        self.assertEqual(lifecycle["primaryAction"]["kind"], "retry_stage")

    def test_expired_task_stages_do_not_contribute_lifecycle_status_time_or_episode(self):
        payload = FakeTasks().full_snapshot()
        expired = dict(payload["items"][0])
        expired.update({
            "userState": "no_action",
            "activeDownloadTasks": 2,
            "completedDownloadTasks": 13,
            "embyIndexed": True,
            "freshUntil": "2026-07-24T00:00:00Z",
        })
        expired["stages"] = [{
            **stage,
            "healthState": "evidence_insufficient",
            "freshUntil": "2026-07-24T00:00:00Z",
        } for stage in expired["stages"]]
        payload["items"] = [expired]
        self.app.extensions["mcc_task_chain_v2_service"].full_snapshot = lambda: payload
        self.app.extensions["mcc_emby_client"].get_tmdb_library_index = lambda: {
            "movies": set(),
            "series": set(),
        }

        response = self.client.get("/api/v2/media/tv:202")

        self.assertEqual(response.status_code, 200)
        lifecycle = response.get_json()
        self.assertEqual(lifecycle["download"]["status"], "unknown")
        self.assertEqual(lifecycle["download"]["observedAt"], "")
        self.assertNotIn("latestEpisode", lifecycle["download"])
        self.assertEqual(lifecycle["cloud115"], {"status": "unknown", "observedAt": ""})
        self.assertEqual(lifecycle["library"]["status"], "unknown")
        self.assertEqual(lifecycle["library"]["observedAt"], "")
        self.assertNotIn("latestEpisode", lifecycle["library"])
        self.assertNotIn("已下载", lifecycle["resultText"])
        self.assertNotIn("已进入 115", lifecycle["resultText"])
        self.assertNotIn("已入库", lifecycle["resultText"])
        self.assertEqual(lifecycle["emby"], {"status": "unknown", "evidenceScope": "none"})

    def test_invalid_key_missing_media_and_invalid_limits_are_stable(self):
        invalid_key = self.client.get("/api/v2/media/series:202")
        missing = self.client.get("/api/v2/media/tv:998")
        invalid_limit = self.client.get("/api/v2/search?q=雀骨&limit=21")
        long_query = self.client.get(f"/api/v2/search?q={'a' * 201}")
        self.assertEqual(invalid_key.status_code, 400)
        self.assertEqual(invalid_key.get_json()["code"], "MEDIA_KEY_INVALID")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.get_json()["code"], "MEDIA_NOT_FOUND")
        self.assertEqual(invalid_limit.status_code, 400)
        self.assertEqual(invalid_limit.get_json()["code"], "MEDIA_SEARCH_LIMIT_INVALID")
        self.assertEqual(long_query.status_code, 400)
        self.assertEqual(long_query.get_json()["code"], "MEDIA_SEARCH_QUERY_INVALID")

    def test_public_payload_does_not_expose_paths_hashes_tokens_or_raw_ids(self):
        search = self.client.get("/api/v2/search?q=雀骨").get_json()
        detail = self.client.get("/api/v2/media/tv:202").get_json()
        serialized = json.dumps({"search": search, "detail": detail}, ensure_ascii=False)
        for secret in (
            "hash-secret",
            "artifact:secret",
            "torra-secret-id",
            "115-secret-id",
            "symedia-secret",
            "subscription-secret-id",
            "/volume/media",
            "/downloads/",
            "token=must-not-leak",
        ):
            self.assertNotIn(secret, serialized)


if __name__ == "__main__":
    unittest.main()
