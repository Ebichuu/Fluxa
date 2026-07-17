from __future__ import annotations

import unittest

from app.private_rss_parser import parse_private_feed


RSS_SAMPLE = b'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Private Tracker</title><item>
<title><![CDATA[Test Show S02E03-E04 2160p WEB-DL HDR HEVC]]></title>
<guid>item-1</guid><pubDate>Fri, 18 Jul 2026 01:00:00 GMT</pubDate>
<link>https://tracker.example/details.php?id=1&amp;passkey=secret</link>
<enclosure url="https://tracker.example/download.php?id=1&amp;passkey=secret" length="123456" type="application/x-bittorrent" />
<category>TV</category></item></channel></rss>'''


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

    def test_rejects_oversized_payload(self):
        with self.assertRaises(ValueError):
            parse_private_feed(b"x" * (2 * 1024 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
