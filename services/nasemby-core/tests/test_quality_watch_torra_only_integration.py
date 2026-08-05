from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask

from app.private_rss_api_runtime import register_private_rss
from app.private_rss_repository import PrivateRssRepository
from app.quality_watch_bridge_runtime import QualityWatchBridgeRuntime
from app.quality_watch_repository import QualityWatchRepository, make_unit_key
from app.quality_watch_runtime import QualityWatchRuntime
from app.quality_watch_scheduler import (
    QualityWatchScheduler,
    QualityWatchSchedulerDependencies,
)
from app.rss_subscription_match_runtime import (
    RssAnalysisDependencies,
    RssSubscriptionMatchRuntime,
)
from app.subscription_automation_api_runtime import register_subscription_automation
from app.subscription_automation_runtime import (
    SubscriptionAutomationDependencies,
    SubscriptionAutomationService,
)
from app.torra_subscription_keys import torra_public_subscription_key


NOW = datetime(2026, 8, 6, 2, 0, tzinfo=timezone.utc)
REMOTE_ID = "torra-readonly-rage"
CANONICAL_KEY = f"torra:{REMOTE_ID}"


class FakeTorra:
    def __init__(self):
        self.rows = [{
            "id": REMOTE_ID,
            "name": "狂怒追缉",
            "media_type": "tv",
            "tmdb_id": 303,
            "season_number": 1,
            "category": "anime",
            "downloaded_episode_files": {
                "1": ["Rage.Pursuit.S01E01.1080p.WEB-DL.mkv"],
            },
            "is_running": False,
            "is_mutating": False,
        }]
        self.rules = [{
            "id": "anime-rule",
            "name": "Anime rule",
            "media_type": "tv",
            "category": ["tv::anime"],
            "videoFormat": {
                "blacklist": [],
                "whitelist": [],
                "screen_1080p": {"name": "1080p", "pattern": "1080p", "score": 5},
                "screen_2160p": {"name": "2160p", "pattern": "2160p", "score": 10},
            },
            "videoFormat_weight": 2,
            "file_extension": {
                "blacklist": [],
                "whitelist": [],
                "mkv": {"name": "MKV", "pattern": r"\.mkv$", "score": 2},
            },
            "file_extension_weight": 1,
            "custom_attributes": [
                {"name": "WEB-DL", "pattern": "WEB[ ._-]*DL", "score": 3},
            ],
            "custom_weight": 1,
            "file_size_score": 0,
            "file_size_weight": 0,
            "always_override_weight": 0,
            "version_control_enabled": True,
            "version_control_entries": [{
                "kind": "local",
                "version": {
                    "name": "MKV version",
                    "include_conditions": [{
                        "attribute": "file_extension",
                        "values": ["mkv"],
                        "match_mode": "any",
                    }],
                    "exclude_conditions": [],
                },
            }],
        }]
        self.submissions = []

    @staticmethod
    def is_configured():
        return True

    def list_subscriptions(self):
        return list(self.rows)

    def list_meta_weight_rules(self):
        return list(self.rules)

    def submit_analysis(self, subscription_id):
        self.submissions.append(subscription_id)
        return f"job-{len(self.submissions)}"


class FakeQb:
    @staticmethod
    def summary():
        return {"configured": True, "connected": True, "tasks": []}


def symedia_snapshot():
    target = "tv:tmdb:303:season:1:episode:1"
    artifact = "artifact:symedia:rage-e01"
    return {
        "items": [{
            "title": "狂怒追缉",
            "mediaType": "tv",
            "tmdbId": "303",
            "seasonNumber": 1,
            "episodeNumber": 1,
            "targetKey": target,
            "identityState": "linked",
            "sourceIds": {
                "subscriptionId": "",
                "torraId": REMOTE_ID,
                "symediaIds": ["rage-e01"],
            },
            "artifactKeys": [artifact],
            "evidenceOwnership": [{
                "artifactKey": artifact,
                "ownerTargetKey": target,
                "matchMethod": "artifact_exact",
            }],
            "episodeEvidence": [{
                "seasonNumber": 1,
                "episodeStart": 1,
                "episodeEnd": 1,
                "stage": "library",
                "artifactKey": artifact,
                "status": "done",
                "ownerTargetKey": target,
                "parentTargetKey": "tv:tmdb:303:season:1",
            }],
            "pipelineFacts": [{
                "stage": "symedia",
                "state": "succeeded",
                "scope": "file",
                "evidence": "verified",
                "eventAt": "2026-08-06T02:03:00Z",
                "sourceRef": "rage-e01",
                "units": [{
                    "unitKey": "rage-e01",
                    "sourceRef": "rage-e01",
                    "eventAt": "2026-08-06T02:03:00Z",
                }],
            }],
        }],
    }


class QualityWatchTorraOnlyIntegrationTests(unittest.TestCase):
    def test_torra_only_bridge_scheduler_rss_and_public_panels_share_one_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [NOW]
            database = Path(directory) / "media.sqlite3"
            watch = QualityWatchRepository(database, clock=lambda: now[0])
            rss = PrivateRssRepository(database)
            torra = FakeTorra()
            qb = FakeQb()
            config = {
                "torra_quality_watch_enabled": True,
                "torra_quality_lifecycle_mode": "follow_rss",
                "torra_quality_default_window_hours": 48,
                "torra_quality_min_interval_minutes": 60,
                "torra_quality_hourly_limit": 4,
                "torra_quality_daily_limit": 30,
                "torra_quality_scheduler_batch_size": 2,
            }
            environment = {
                "MCC_TORRA_QUALITY_WATCH_ENABLED": "true",
                "MCC_TORRA_REWASH_DOWNLOAD_ENABLED": "false",
            }
            quality = QualityWatchRuntime(
                watch,
                config_loader=lambda: config,
                clock=lambda: now[0],
            )
            bridge = QualityWatchBridgeRuntime(
                watch,
                quality,
                subscription_loader=lambda: {"items": []},
                torra_subscription_loader=torra.list_subscriptions,
                config_loader=lambda: config,
                clock=lambda: now[0],
            )
            bridge.set_mode("shadow")
            bridge.set_mode("apply")
            now[0] += timedelta(minutes=5)

            first_bridge = bridge.process_snapshot(symedia_snapshot())
            repeated_bridge = bridge.process_snapshot(symedia_snapshot())
            canonical_unit = make_unit_key(CANONICAL_KEY, "tv", 1, 1)
            units = watch.list_watch_units(CANONICAL_KEY)

            self.assertEqual((first_bridge["applied"], repeated_bridge["applied"]), (1, 1))
            self.assertEqual([unit["unit_key"] for unit in units], [canonical_unit])
            self.assertEqual(units[0]["state"], "observing_upgrade")

            rss_runtime = RssSubscriptionMatchRuntime(
                rss,
                watch,
                lambda: {"items": []},
                clock=lambda: now[0],
                analysis=RssAnalysisDependencies(
                    environment,
                    torra,
                    qb,
                    lambda: config,
                ),
            )
            scheduler = QualityWatchScheduler(
                watch,
                QualityWatchSchedulerDependencies(
                    environment,
                    torra,
                    qb,
                    lambda: {"items": []},
                    lambda: config,
                    rss_runtime=rss_runtime,
                ),
                clock=lambda: now[0],
            )

            scheduled = scheduler.run_once()
            unit_after_schedule = watch.get_watch_unit(canonical_unit)

            self.assertEqual((scheduled["selected"], scheduled["processed"]), (0, []))
            self.assertEqual(unit_after_schedule["state"], "observing_upgrade")
            self.assertNotEqual(
                unit_after_schedule.get("last_result", {}).get("reason"),
                "subscription_missing",
            )
            self.assertEqual(torra.submissions, [])

            source = rss.save_source({
                "name": "测试站",
                "feedUrl": "https://tracker.example/rss",
            })
            inserted = rss.upsert_items(source["id"], [{
                "fingerprint": "rage-upgrade-e01",
                "title": "Rage.Pursuit.S01E01.2160p.WEB-DL.mkv",
                "published_at": "2026-08-06T02:10:00Z",
                "media_type": "tv",
                "season_number": 1,
                "episode_start": 1,
                "episode_end": 1,
                "tmdb_id": "303",
                "identity_status": "identified",
            }], on_insert=rss_runtime.match_inserted_rows)
            matches = rss.list_matches()["items"]
            evaluated = rss_runtime.evaluate_matches([match["id"] for match in matches])
            repeated = rss.upsert_items(source["id"], [{
                "fingerprint": "rage-upgrade-e01",
                "title": "Rage.Pursuit.S01E01.2160p.WEB-DL.mkv",
                "published_at": "2026-08-06T02:10:00Z",
                "media_type": "tv",
                "season_number": 1,
                "episode_start": 1,
                "episode_end": 1,
                "tmdb_id": "303",
                "identity_status": "identified",
            }], on_insert=rss_runtime.match_inserted_rows)

            self.assertEqual((inserted["inserted"], repeated["inserted"]), (1, 0))
            self.assertEqual(len(evaluated), 1)
            self.assertEqual(evaluated[0]["evaluationStatus"], "scored")
            self.assertEqual(evaluated[0]["decision"], "current_best")
            self.assertGreater(evaluated[0]["candidateScore"], evaluated[0]["baselineScore"])
            internal_match = rss.get_match_internal(evaluated[0]["id"])
            self.assertEqual(internal_match["subscription_key"], CANONICAL_KEY)
            self.assertEqual(internal_match["unit_key"], canonical_unit)
            self.assertEqual(rss.list_matches()["total"], 1)

            service = SubscriptionAutomationService(SubscriptionAutomationDependencies(
                environment,
                watch,
                torra,
                qb,
                lambda: config,
                lambda value: value,
                lambda: {"items": []},
                lambda _key, _updater: None,
                rss_runtime=rss_runtime,
                bridge_runtime=bridge,
                clock=lambda: now[0],
            ))
            app = Flask(__name__)
            app.extensions["mcc_quality_watch_repository"] = watch
            register_private_rss(
                app,
                database,
                environment=environment,
                repository=rss,
                subscription_loader=lambda: {"items": []},
                config_loader=lambda: config,
                match_runtime=rss_runtime,
            )
            register_subscription_automation(app, service)
            client = app.test_client()
            public_key = torra_public_subscription_key(REMOTE_ID)

            quality_response = client.get(
                f"/api/v2/subscriptions/{public_key}/quality-watch"
            )
            rss_response = client.get("/api/v2/rss-matches")
            public_quality = quality_response.get_json()
            public_match = rss_response.get_json()["items"][0]

            self.assertEqual((quality_response.status_code, rss_response.status_code), (200, 200))
            self.assertEqual(public_quality["subscriptionId"], public_key)
            self.assertTrue(public_quality["units"][0]["id"].startswith(public_key))
            self.assertEqual(public_match["subscriptionId"], public_key)
            self.assertTrue(public_match["unitId"].startswith(public_key))
            self.assertNotIn(REMOTE_ID, quality_response.get_data(as_text=True))
            self.assertNotIn(REMOTE_ID, rss_response.get_data(as_text=True))
            self.assertEqual(len(watch.list_watch_units(CANONICAL_KEY)), 1)
            self.assertEqual(rss.list_matches()["total"], 1)


if __name__ == "__main__":
    unittest.main()
