from __future__ import annotations

import unittest
from pathlib import Path

from app.private_rss_parser import parse_private_feed


MTEAM_FIXTURE = Path(__file__).with_name("fixtures") / "mteam_rss_sanitized.xml"
HDHOME_FIXTURE = Path(__file__).with_name("fixtures") / "hdhome_rss_sanitized.xml"
ZMPT_FIXTURE = Path(__file__).with_name("fixtures") / "zmpt_rss_sanitized.xml"
QINGWA_FIXTURE = Path(__file__).with_name("fixtures") / "qingwa_rss_sanitized.xml"


RSS_SAMPLE = b'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Private Tracker</title><item>
<title><![CDATA[Test Show S02E03-E04 2160p WEB-DL HDR HEVC]]></title>
<guid>item-1</guid><pubDate>Fri, 18 Jul 2026 01:00:00 GMT</pubDate>
<link>https://tracker.example/details.php?id=1&amp;passkey=secret</link>
<enclosure url="https://tracker.example/download.php?id=1&amp;passkey=secret" length="123456" type="application/x-bittorrent" />
<category>TV</category></item></channel></rss>'''

RSS_IDENTITY_SAMPLE = b'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="https://example.invalid/media"><channel><title>Identity</title>
<item><title>Known Movie 2026</title><guid>identity-1</guid>
<media:tmdb_id>12345</media:tmdb_id>
<description><![CDATA[TMDB: 12345<br><a href="https://www.imdb.com/title/tt1234567/">IMDb</a>]]></description>
</item></channel></rss>'''

RSS_IDENTITY_CONFLICT_SAMPLE = b'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Conflict</title><item>
<title>Conflicting Movie 2026</title><guid>identity-2</guid>
<description><![CDATA[TMDB: 12345 / https://www.themoviedb.org/movie/67890]]></description>
</item></channel></rss>'''


class PrivateRssParserTests(unittest.TestCase):
    def test_parses_identity_episode_and_version_summary(self):
        parsed = parse_private_feed(RSS_SAMPLE)
        self.assertEqual(parsed["title"], "Private Tracker")
        item = parsed["items"][0]
        self.assertEqual(item["media_type"], "tv")
        self.assertEqual(item["season_number"], 2)
        self.assertEqual(item["episode_start"], 3)
        self.assertEqual(item["episode_end"], 4)
        self.assertIn("2160P", item["version_summary"])
        self.assertIn("HDR", item["version_summary"])
        self.assertEqual(item["size_bytes"], 123456)

        interlaced = parse_private_feed(RSS_SAMPLE.replace(b"2160p", b"1080i"))["items"][0]
        self.assertIn("1080I", interlaced["version_summary"])

        mteam = parse_private_feed(MTEAM_FIXTURE.read_bytes())
        self.assertEqual(mteam["title"], "M-Team - TP")
        self.assertEqual(len(mteam["items"]), 5)
        self.assertEqual(mteam["items"][0]["media_type"], "movie")
        self.assertIn("1080I", mteam["items"][0]["version_summary"])
        self.assertEqual((mteam["items"][1]["season_number"], mteam["items"][1]["episode_start"]), (1, 14))
        self.assertTrue(all(item["detail_url"].startswith("https://tracker.example/") for item in mteam["items"]))
        self.assertTrue(all(item["download_url"].startswith("https://tracker.example/") for item in mteam["items"]))

        hdhome = parse_private_feed(HDHOME_FIXTURE.read_bytes())
        self.assertEqual(hdhome["title"], "HDHome Torrents")
        self.assertEqual(len(hdhome["items"]), 5)
        self.assertEqual([item["media_type"] for item in hdhome["items"]], ["movie", "movie", "movie", "tv", "movie"])
        self.assertEqual(
            (
                hdhome["items"][3]["season_number"],
                hdhome["items"][3]["episode_start"],
                hdhome["items"][3]["episode_end"],
            ),
            (1, None, None),
        )
        self.assertIn("1080I", hdhome["items"][1]["version_summary"])
        self.assertIn("2160P", hdhome["items"][2]["version_summary"])
        self.assertGreater(hdhome["items"][0]["size_bytes"], 20 * 1024 * 1024 * 1024)
        self.assertTrue(all(item["detail_url"].startswith("https://tracker.example/") for item in hdhome["items"]))
        self.assertTrue(all(item["download_url"].startswith("https://tracker.example/") for item in hdhome["items"]))

        zmpt = parse_private_feed(ZMPT_FIXTURE.read_bytes())
        self.assertEqual(zmpt["title"], "织梦 Torrents")
        self.assertEqual(len(zmpt["items"]), 5)
        self.assertTrue(all(item["media_type"] == "tv" for item in zmpt["items"]))
        self.assertEqual([item["season_number"] for item in zmpt["items"]], [1, 1, 1, 2, 1])
        self.assertTrue(all(item["episode_start"] is None and item["episode_end"] is None for item in zmpt["items"]))
        self.assertIn("2160P", zmpt["items"][0]["version_summary"])
        self.assertIn("H.265", zmpt["items"][1]["version_summary"])
        self.assertGreater(zmpt["items"][0]["size_bytes"], 100 * 1024 * 1024 * 1024)
        self.assertTrue(all(item["detail_url"].startswith("https://tracker.example/") for item in zmpt["items"]))
        self.assertTrue(all(item["download_url"].startswith("https://tracker.example/") for item in zmpt["items"]))

        qingwa = parse_private_feed(QINGWA_FIXTURE.read_bytes())
        self.assertEqual(qingwa["title"], "青蛙 Torrents")
        self.assertEqual(len(qingwa["items"]), 5)
        self.assertEqual([item["media_type"] for item in qingwa["items"]], ["tv", "movie", "tv", "tv", "tv"])
        self.assertEqual([item["season_number"] for item in qingwa["items"]], [1, None, 1, 2, 1])
        self.assertEqual([item["episode_start"] for item in qingwa["items"]], [None, None, 3, 3, None])
        self.assertIn("HDR", qingwa["items"][4]["version_summary"])
        self.assertIn("ATMOS", qingwa["items"][4]["version_summary"])
        self.assertTrue(all(item["detail_url"].startswith("https://tracker.example/") for item in qingwa["items"]))
        self.assertTrue(all(item["download_url"].startswith("https://tracker.example/") for item in qingwa["items"]))

    def test_rejects_oversized_payload(self):
        with self.assertRaises(ValueError):
            parse_private_feed(b"x" * (2 * 1024 * 1024 + 1))

    def test_extracts_explicit_media_identity_and_marks_conflicts(self):
        identified = parse_private_feed(RSS_IDENTITY_SAMPLE)["items"][0]
        self.assertEqual(identified["tmdb_id"], "12345")
        self.assertEqual(identified["imdb_id"], "tt1234567")
        self.assertEqual(identified["identity_status"], "identified")
        self.assertIn("rss_field", identified["identity_source"])
        self.assertEqual(identified["identity_confidence"], "strong")

        conflict = parse_private_feed(RSS_IDENTITY_CONFLICT_SAMPLE)["items"][0]
        self.assertEqual(conflict["identity_status"], "conflict")
        self.assertEqual(conflict["tmdb_id"], "")
        self.assertEqual(conflict["imdb_id"], "")
        self.assertIn("rss_description", conflict["identity_source"])


if __name__ == "__main__":
    unittest.main()
