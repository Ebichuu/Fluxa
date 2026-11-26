from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from app.domain.rss_auto_download import (
    extract_magnet_hash,
    extract_torrent_info_hash,
    run_rss_auto_rule,
)
from app.storage import FluxaStore


def _make_torrent(name: str) -> tuple[bytes, str]:
    name_b = name.encode("utf-8")
    info_dict = b"d6:lengthi1024e4:name" + str(len(name_b)).encode() + b":" + name_b + b"12:piece lengthi16384e6:pieces20:" + (b"0" * 20) + b"e"
    torrent = b"d8:announce15:http://pt.local4:info" + info_dict + b"e"
    expected_hash = hashlib.sha1(info_dict).hexdigest().lower()
    return torrent, expected_hash


class _FakeQb:
    configured = True

    def __init__(self, initial_hashes=None, readback_succeeds=True):
        self.tasks = {h: {"hash": h, "name": f"existing-{h[:6]}"} for h in (initial_hashes or [])}
        self.added_calls = []
        self.readback_succeeds = readback_succeeds

    def list_torrents(self):
        return list(self.tasks.values())

    def add_torrent(self, **kwargs):
        self.added_calls.append(kwargs)
        if kwargs.get("torrent_bytes"):
            h = extract_torrent_info_hash(kwargs["torrent_bytes"])
            if self.readback_succeeds and h:
                self.tasks[h] = {"hash": h, "name": kwargs.get("filename", "")}
        return {"ok": True, "message": "已提交"}

    def get_torrent_by_hash(self, torrent_hash: str):
        return self.tasks.get(torrent_hash.lower())


class RssAutoDownloadTests(unittest.TestCase):
    def test_bencode_info_hash_and_magnet_hash(self):
        raw, expected = _make_torrent("Show.S01E01-ADWeb.mkv")
        self.assertEqual(extract_torrent_info_hash(raw), expected)
        self.assertEqual(
            extract_magnet_hash(f"magnet:?xt=urn:btih:{expected.upper()}&dn=test"),
            expected,
        )

    def test_baseline_no_history_flood_then_new_item_dedup_and_qb_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FluxaStore(Path(tmp) / "test.sqlite3")
            settings = store.get_settings()

            t_old, h_old = _make_torrent("Old.Episode.S01E01-ADWeb.mkv")
            t_new, h_new = _make_torrent("New.Episode.S01E02-ADWeb.mkv")
            t_dup_qb, h_dup_qb = _make_torrent("AlreadyInQb.S01E03-ADWeb.mkv")

            rule = store.save_rss_auto_rule({
                "id": "rule-adweb",
                "siteId": "audiences",
                "siteName": "观众",
                "officialGroup": "ADWeb",
                "url": "https://audiences.me/torrentrss.php?passkey=secret",
                "category": "02-剧集",
                "savePath": "/downloads/tv",
                "enabled": True,
                "pollIntervalMinutes": 5,
            })

            qb = _FakeQb(initial_hashes=[h_dup_qb])
            adapters = {"qbittorrent": qb}

            # 1. First run MUST establish baseline ONLY — zero torrents added to qB!
            feed_v1 = [{"title": "Old.Episode.S01E01-ADWeb", "downloadUrl": "https://pt/old.torrent", "detailUrl": "https://pt/1"}]
            res1 = run_rss_auto_rule(
                rule["id"],
                store=store,
                settings=settings,
                adapters=adapters,
                feed_fetcher=lambda *_: feed_v1,
                torrent_downloader=lambda u: t_old,
                sleep_fn=lambda _: None,
            )
            self.assertTrue(res1["baselineInit"])
            self.assertEqual(res1["added"], 0)
            self.assertEqual(len(qb.added_calls), 0)

            # 2. Second run introduces 1 brand-new torrent + 1 torrent whose hash is ALREADY in qB
            feed_v2 = [
                {"title": "AlreadyInQb.S01E03-ADWeb", "downloadUrl": "https://pt/dup.torrent", "detailUrl": "https://pt/3"},
                {"title": "New.Episode.S01E02-ADWeb", "downloadUrl": "https://pt/new.torrent", "detailUrl": "https://pt/2"},
                {"title": "Old.Episode.S01E01-ADWeb", "downloadUrl": "https://pt/old.torrent", "detailUrl": "https://pt/1"},
            ]
            dl_map = {"https://pt/new.torrent": t_new, "https://pt/dup.torrent": t_dup_qb, "https://pt/old.torrent": t_old}
            res2 = run_rss_auto_rule(
                rule["id"],
                store=store,
                settings=settings,
                adapters=adapters,
                feed_fetcher=lambda *_: feed_v2,
                torrent_downloader=lambda u: dl_map[u],
                sleep_fn=lambda _: None,
            )
            self.assertTrue(res2["ok"])
            self.assertEqual(res2["added"], 1)
            self.assertEqual(res2["skipped"], 1)
            self.assertEqual(len(qb.added_calls), 1)
            self.assertEqual(qb.added_calls[0]["category"], "02-剧集")
            self.assertEqual(qb.added_calls[0]["save_path"], "/downloads/tv")

            items = store.list_rss_auto_items(rule_id=rule["id"])
            by_title = {i["title"]: i for i in items}
            self.assertEqual(by_title["New.Episode.S01E02-ADWeb"]["status"], "confirmed_in_qb")
            self.assertEqual(by_title["New.Episode.S01E02-ADWeb"]["torrentHash"], h_new)
            self.assertEqual(by_title["AlreadyInQb.S01E03-ADWeb"]["status"], "duplicate_in_qb")
            self.assertEqual(by_title["Old.Episode.S01E01-ADWeb"]["status"], "baseline_skipped")

    def test_submitted_but_missing_on_readback_is_unconfirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FluxaStore(Path(tmp) / "test.sqlite3")
            settings = store.get_settings()
            t_new, _ = _make_torrent("Unconfirmed.S01E01-ADWeb.mkv")
            rule = store.save_rss_auto_rule({
                "id": "rule-unconfirmed",
                "siteId": "audiences",
                "siteName": "观众",
                "officialGroup": "ADWeb",
                "url": "https://audiences.me/rss",
                "baselineInitialized": True,
                "enabled": True,
            })
            qb = _FakeQb(readback_succeeds=False)
            res = run_rss_auto_rule(
                rule["id"],
                store=store,
                settings=settings,
                adapters={"qbittorrent": qb},
                feed_fetcher=lambda *_: [{"title": "Unconfirmed.S01E01-ADWeb", "downloadUrl": "https://pt/u.torrent", "detailUrl": "https://pt/u"}],
                torrent_downloader=lambda _: t_new,
                sleep_fn=lambda _: None,
            )
            self.assertEqual(res["added"], 0)
            self.assertEqual(res["unconfirmed"], 1)
            items = store.list_rss_auto_items(rule_id=rule["id"])
            self.assertEqual(items[0]["status"], "submitted_unconfirmed")


if __name__ == "__main__":
    unittest.main()
