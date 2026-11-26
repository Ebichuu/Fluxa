from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from app.domain.quality_policies import (
    compare_quality_candidates,
    evaluate_candidate_quality,
    parse_release_quality_specs,
)
from app.domain.rss_auto_download import (
    extract_magnet_hash,
    extract_torrent_info_hash,
    run_rss_auto_rule,
)
from app.storage import FluxaStore

MIB = 1024 * 1024


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

    def test_donghua_hq_rejects_low_bitrate_4k_and_insufficient_evidence_picks_hq_1080p(self):
        """国漫高质量验收：低码 4K/HDR/DV 不胜过优质 1080p；无体积/码率的 4K 标签标明依据不足；720p 直接跳过。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = FluxaStore(Path(tmp) / "test.sqlite3")
            settings = store.get_settings()
            rule = store.save_rss_auto_rule({
                "id": "rule-donghua",
                "siteId": "audiences",
                "siteName": "观众",
                "officialGroup": "ADWeb",
                "qualityPolicyId": "donghua_hq",
                "url": "https://audiences.me/rss?group=adweb",
                "category": "03-国漫",
                "savePath": "/downloads/donghua",
                "baselineInitialized": True,
                "enabled": True,
            })

            t_4k_low, _ = _make_torrent("Fanren.EP125.2160p.HDR.DV.WEB-DL.H265-ADWeb.mkv")
            t_1080_hq, h_1080_hq = _make_torrent("Fanren.EP125.1080p.WEB-DL.H265.ASS-ADWeb.mkv")
            t_720, _ = _make_torrent("Fanren.EP125.720p.WEB-DL.H264-ADWeb.mkv")
            t_4k_noinfo, _ = _make_torrent("Swallowed.Star.EP150.2160p.HDR.DV.WEB-DL.H265-ADWeb.mkv")

            feed = [
                # Low-bitrate 4K/HDR/DV of EP125 (~620 MiB single ep, 6.2 Mbps -> demoted Tier 2)
                {
                    "title": "凡人修仙传.EP125.2026.2160p.WEB-DL.H265.HDR.DV.AAC-ADWeb",
                    "description": "Video Bit rate : 6.2 Mb/s Duration : 20 min",
                    "sizeBytes": int(620 * MIB),
                    "downloadUrl": "https://pt/4k-low.torrent",
                    "detailUrl": "https://pt/101",
                },
                # High-quality 1080p of SAME EP125 (10.5 Mbps + 特效字幕 -> Tier 3, MUST beat low-bitrate 4K!)
                {
                    "title": "凡人修仙传.EP125.2026.1080p.WEB-DL.H265.10bit.特效字幕.DDP5.1-ADWeb",
                    "description": "Video Bit rate : 10.5 Mb/s Duration : 20 min",
                    "sizeBytes": int(1450 * MIB),
                    "downloadUrl": "https://pt/1080-hq.torrent",
                    "detailUrl": "https://pt/102",
                },
                # 720p low quality -> MUST be quality_rejected
                {
                    "title": "凡人修仙传.EP125.2026.720p.WEB-DL.H264.AAC-ADWeb",
                    "sizeBytes": int(350 * MIB),
                    "downloadUrl": "https://pt/720.torrent",
                    "detailUrl": "https://pt/103",
                },
                # 4K HDR DV with ZERO size and ZERO bitrate info -> MUST be insufficient_evidence
                {
                    "title": "吞噬星空.EP150.2026.2160p.WEB-DL.H265.HDR.DV.AAC-ADWeb",
                    "description": "",
                    "sizeBytes": 0,
                    "downloadUrl": "https://pt/4k-noinfo.torrent",
                    "detailUrl": "https://pt/104",
                },
            ]
            dl_map = {
                "https://pt/4k-low.torrent": t_4k_low,
                "https://pt/1080-hq.torrent": t_1080_hq,
                "https://pt/720.torrent": t_720,
                "https://pt/4k-noinfo.torrent": t_4k_noinfo,
            }
            qb = _FakeQb()
            res = run_rss_auto_rule(
                rule["id"],
                store=store,
                settings=settings,
                adapters={"qbittorrent": qb},
                feed_fetcher=lambda *_: feed,
                torrent_downloader=lambda u: dl_map[u],
                sleep_fn=lambda _: None,
            )

            self.assertEqual(res["added"], 1)
            self.assertEqual(res["qualitySkipped"], 2)  # 720p rejected + low-bitrate 4K outperformed
            self.assertEqual(res["insufficientSkipped"], 1)  # 4K with no size/bitrate
            self.assertEqual(len(qb.added_calls), 1)
            self.assertIn(h_1080_hq, qb.tasks)

            by_title = {i["title"]: i for i in store.list_rss_auto_items(rule_id=rule["id"])}
            self.assertEqual(by_title["凡人修仙传.EP125.2026.1080p.WEB-DL.H265.10bit.特效字幕.DDP5.1-ADWeb"]["status"], "confirmed_in_qb")
            self.assertEqual(by_title["凡人修仙传.EP125.2026.2160p.WEB-DL.H265.HDR.DV.AAC-ADWeb"]["status"], "quality_outperformed")
            self.assertIn("低码 4K", by_title["凡人修仙传.EP125.2026.2160p.WEB-DL.H265.HDR.DV.AAC-ADWeb"]["errorText"])
            self.assertEqual(by_title["凡人修仙传.EP125.2026.720p.WEB-DL.H264.AAC-ADWeb"]["status"], "quality_rejected")
            self.assertEqual(by_title["吞噬星空.EP150.2026.2160p.WEB-DL.H265.HDR.DV.AAC-ADWeb"]["status"], "insufficient_evidence")
            self.assertIn("依据不足", by_title["吞噬星空.EP150.2026.2160p.WEB-DL.H265.HDR.DV.AAC-ADWeb"]["errorText"])

    def test_anime_web_prefers_effects_sub_keeps_distinct_episodes_and_no_cross_codec_size_rank(self):
        """日漫追更验收：同集优先特效字幕；不同集数/不同作品互不淘汰；不同编码不按体积粗排。"""
        # Direct cross-codec check: H.265 520 MiB vs H.264 1300 MiB (same tier, no effects sub, same AAC) -> tie, NOT ranked by file size!
        s_hevc = parse_release_quality_specs(title="Frieren.S01E05.1080p.WEB-DL.H265.AAC-ADWeb", size_bytes=int(520 * MIB))
        s_avc = parse_release_quality_specs(title="Frieren.S01E05.1080p.WEB-DL.H264.AAC-ADWeb", size_bytes=int(1300 * MIB))
        cmp_codec = compare_quality_candidates(
            {"specs": s_hevc, "eval": evaluate_candidate_quality(s_hevc, "anime_web")},
            {"specs": s_avc, "eval": evaluate_candidate_quality(s_avc, "anime_web")},
            "anime_web",
        )
        self.assertEqual(cmp_codec["winner"], "tie")
        self.assertIn("不同编码", cmp_codec["reason"])

        with tempfile.TemporaryDirectory() as tmp:
            store = FluxaStore(Path(tmp) / "test.sqlite3")
            settings = store.get_settings()
            rule = store.save_rss_auto_rule({
                "id": "rule-anime-web",
                "siteId": "audiences",
                "siteName": "观众",
                "officialGroup": "ADWeb",
                "qualityPolicyId": "anime_web",
                "url": "https://audiences.me/rss?cat=anime",
                "category": "02-日漫",
                "savePath": "/downloads/anime",
                "baselineInitialized": True,
                "enabled": True,
            })

            t_e5_plain, _ = _make_torrent("Frieren.S01E05.plain.mkv")
            t_e5_ass, h_e5_ass = _make_torrent("Frieren.S01E05.ass.mkv")
            t_e6, h_e6 = _make_torrent("Frieren.S01E06.mkv")
            t_other, h_other = _make_torrent("Dandadan.S01E05.mkv")

            feed = [
                {"title": "葬送的芙莉莲.S01E05.1080p.WEB-DL.H265.AAC-ADWeb", "sizeBytes": int(600 * MIB), "downloadUrl": "https://pt/e5p.torrent", "detailUrl": "https://pt/201"},
                {"title": "葬送的芙莉莲.S01E05.1080p.WEB-DL.H265.简繁特效字幕.ASS.AAC-ADWeb", "sizeBytes": int(620 * MIB), "downloadUrl": "https://pt/e5a.torrent", "detailUrl": "https://pt/202"},
                # Different episode E06 -> MUST NOT be eliminated by E05!
                {"title": "葬送的芙莉莲.S01E06.1080p.WEB-DL.H265.AAC-ADWeb", "sizeBytes": int(610 * MIB), "downloadUrl": "https://pt/e6.torrent", "detailUrl": "https://pt/203"},
                # Different work E05 -> MUST NOT be eliminated!
                {"title": "胆大党.S01E05.1080p.WEB-DL.H265.AAC-ADWeb", "sizeBytes": int(580 * MIB), "downloadUrl": "https://pt/other.torrent", "detailUrl": "https://pt/204"},
            ]
            dl_map = {
                "https://pt/e5p.torrent": t_e5_plain,
                "https://pt/e5a.torrent": t_e5_ass,
                "https://pt/e6.torrent": t_e6,
                "https://pt/other.torrent": t_other,
            }
            qb = _FakeQb()
            res = run_rss_auto_rule(
                rule["id"],
                store=store,
                settings=settings,
                adapters={"qbittorrent": qb},
                feed_fetcher=lambda *_: feed,
                torrent_downloader=lambda u: dl_map[u],
                sleep_fn=lambda _: None,
            )

            self.assertEqual(res["added"], 3)
            self.assertEqual(res["qualitySkipped"], 1)
            self.assertEqual(set(qb.tasks.keys()), {h_e5_ass, h_e6, h_other})

    def test_anime_bd_rejects_webdl_accepts_bdrip_and_blocks_later_downgrade(self):
        """日漫BD收藏验收：拒绝 WEB-DL；接受优质 1080p BDRip；已有更优版本后跳过后续低档版本。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = FluxaStore(Path(tmp) / "test.sqlite3")
            settings = store.get_settings()
            rule = store.save_rss_auto_rule({
                "id": "rule-anime-bd",
                "siteId": "ourbits",
                "siteName": "我堡",
                "officialGroup": "OurBits",
                "qualityPolicyId": "anime_bd",
                "url": "https://ourbits.club/rss?cat=bd",
                "category": "02-日漫BD",
                "savePath": "/downloads/anime-bd",
                "baselineInitialized": True,
                "enabled": True,
            })

            t_web, _ = _make_torrent("Frieren.WEB.mkv")
            t_bd_hq, h_bd_hq = _make_torrent("Frieren.BD.HQ.mkv")
            t_bd_lo, _ = _make_torrent("Frieren.BD.LO.mkv")

            # Cycle 1: WEB-DL rejected, high-quality 1080p BDRip confirmed in qB
            feed1 = [
                {"title": "葬送的芙莉莲.S01.Complete.1080p.WEB-DL.H265.AAC-OurBits", "sizeBytes": int(18000 * MIB), "downloadUrl": "https://pt/web.torrent", "detailUrl": "https://pt/301"},
                {"title": "葬送的芙莉莲.S01.Complete.1080p.BDRip.Ma10p.HEVC.FLAC.特效字幕.Chapters-OurBits", "sizeBytes": int(28000 * MIB), "downloadUrl": "https://pt/bdhq.torrent", "detailUrl": "https://pt/302"},
            ]
            qb = _FakeQb()
            res1 = run_rss_auto_rule(
                rule["id"],
                store=store,
                settings=settings,
                adapters={"qbittorrent": qb},
                feed_fetcher=lambda *_: feed1,
                torrent_downloader=lambda u: t_bd_hq if "bdhq" in u else t_web,
                sleep_fn=lambda _: None,
            )
            self.assertEqual(res1["added"], 1)
            self.assertEqual(res1["qualitySkipped"], 1)
            self.assertIn(h_bd_hq, qb.tasks)

            # Cycle 2: A lower-tier BDRip (8bit, AAC, no effects sub) for the SAME work arrives -> MUST skip!
            feed2 = [
                *feed1,
                {"title": "葬送的芙莉莲.S01.Complete.1080p.BDRip.8bit.HEVC.AAC-OurBits", "sizeBytes": int(14000 * MIB), "downloadUrl": "https://pt/bdlo.torrent", "detailUrl": "https://pt/303"},
            ]
            res2 = run_rss_auto_rule(
                rule["id"],
                store=store,
                settings=settings,
                adapters={"qbittorrent": qb},
                feed_fetcher=lambda *_: feed2,
                torrent_downloader=lambda _: t_bd_lo,
                sleep_fn=lambda _: None,
            )
            self.assertEqual(res2["added"], 0)
            self.assertEqual(res2["qualitySkipped"], 1)
            self.assertEqual(len(qb.tasks), 1)  # Existing qB task untouched!

            items = store.list_rss_auto_items(rule_id=rule["id"])
            by_title = {i["title"]: i for i in items}
            self.assertEqual(by_title["葬送的芙莉莲.S01.Complete.1080p.WEB-DL.H265.AAC-OurBits"]["status"], "quality_rejected")
            self.assertEqual(by_title["葬送的芙莉莲.S01.Complete.1080p.BDRip.Ma10p.HEVC.FLAC.特效字幕.Chapters-OurBits"]["status"], "confirmed_in_qb")
            self.assertEqual(by_title["葬送的芙莉莲.S01.Complete.1080p.BDRip.8bit.HEVC.AAC-OurBits"]["status"], "quality_existing_sufficient")


if __name__ == "__main__":
    unittest.main()
