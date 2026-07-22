from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.resource_task_repository import ResourceIdentityConflict, ResourceTaskRepository


NOW = datetime(2026, 7, 22, 6, 0, tzinfo=timezone.utc)


def snapshot(reason_text="下载完成"):
    return {
        "items": [{
            "chainId": "chain:test",
            "mediaKey": "tv:tmdb:100",
            "targetKey": "tv:tmdb:100:season:2",
            "subscriptionId": "subscription:test",
            "mediaType": "tv",
            "tmdbId": "100",
            "title": "测试剧",
            "origin": "subscription",
            "state": "active",
            "healthState": "waiting",
            "observedAt": "2026-07-22T06:00:00Z",
            "freshUntil": "2026-07-22T06:05:00Z",
            "source": "task-chain",
            "reasonCode": "TASK_IN_PROGRESS",
            "reasonText": "正在处理",
            "sourceIds": {"qbHashes": ["hash-1"], "symediaIds": []},
            "stages": [{
                "stage": "download",
                "status": "done",
                "healthState": "normal",
                "evidence": "verified",
                "observedAt": "2026-07-22T05:59:00Z",
                "freshUntil": "2026-07-22T06:05:00Z",
                "source": "qBittorrent",
                "reasonCode": "DOWNLOAD_DONE",
                "reasonText": reason_text,
            }],
        }],
    }


class ResourceTaskRepositoryTests(unittest.TestCase):
    def test_snapshot_is_idempotent_and_redacts_event_text(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ResourceTaskRepository(
                Path(directory) / "media.sqlite3",
                clock=lambda: NOW,
            )
            payload = snapshot("failed https://rss.example/feed?passkey=secret password=plain")

            first = repository.record_snapshot(payload)
            second = repository.record_snapshot(payload)
            events = repository.list_events("chain:test")

            self.assertEqual(first["events"], 1)
            self.assertEqual(second["events"], 0)
            self.assertEqual(repository.get_chain("chain:test")["target_key"], "tv:tmdb:100:season:2")
            self.assertEqual(len(events), 1)
            self.assertNotIn("secret", events[0]["reason_text"])
            self.assertNotIn("plain", events[0]["reason_text"])
            self.assertIn("passkey=***", events[0]["reason_text"])

    def test_identity_upgrade_keeps_one_chain_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ResourceTaskRepository(
                Path(directory) / "media.sqlite3",
                clock=lambda: NOW,
            )
            repository.record_snapshot(snapshot())

            first = repository.record_identity_alias(
                "chain:test",
                "artifact:anonymous:old",
                "artifact:remote-1",
                artifact={"type": "remote_file", "source": "Symedia", "externalId": "remote-1"},
            )
            second = repository.record_identity_alias(
                "chain:test",
                "artifact:anonymous:old",
                "artifact:remote-1",
                artifact={"type": "remote_file", "source": "Symedia", "externalId": "remote-1"},
            )
            events = repository.list_events("chain:test")

            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            self.assertEqual(sum(event["reason_code"] == "ARTIFACT_IDENTITY_UPGRADED" for event in events), 1)
            self.assertEqual(repository.get_chain("chain:test")["chain_id"], "chain:test")

    def test_artifact_cannot_be_silently_moved_to_another_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ResourceTaskRepository(Path(directory) / "media.sqlite3", clock=lambda: NOW)
            repository.record_snapshot(snapshot())
            other = snapshot()
            other["items"][0].update({
                "chainId": "chain:other",
                "mediaKey": "tv:tmdb:200",
                "targetKey": "tv:tmdb:200:season:1",
            })

            result = repository.record_snapshot(other)

            self.assertEqual(result["artifactConflicts"], 1)
            self.assertEqual(repository.get_chain("chain:other")["health_state"], "action_required")
            self.assertEqual(repository.list_events("chain:other")[0]["reason_code"], "ARTIFACT_CHAIN_CONFLICT")
            with self.assertRaises(ResourceIdentityConflict):
                repository.record_identity_alias(
                    "chain:other",
                    "artifact:old",
                    "artifact:hash-1",
                )


if __name__ == "__main__":
    unittest.main()
