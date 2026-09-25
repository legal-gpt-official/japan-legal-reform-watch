"""Offline tests for the free RSS feeds and deadline calendars."""

from __future__ import annotations

import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import alert_common as ac  # noqa: E402
import build_public_feeds as bpf  # noqa: E402

TODAY = date(2026, 9, 25)


def item(item_id, **overrides):
    base = {
        "id": item_id,
        "title_en": f"Title {item_id}",
        "title_ja": "日本語タイトル",
        "area": "Finance / AML",
        "stage": "Government Announcement",
        "source_name": "Financial Services Agency (金融庁) 新着情報",
        "source_url": f"https://www.fsa.go.jp/{item_id}",
        "published_at": "2026-09-20",
        "first_seen_at": "2026-09-21",
        "last_checked": "2026-09-24",
    }
    base.update(overrides)
    return base


class TestRss(unittest.TestCase):
    def test_rss_is_valid_escaped_and_filtered_by_channel(self):
        items = [
            item("a", title_en="A & <B>", source_url="javascript:alert(1)"),
            item("b", area="Tax / Stamp Duty"),
        ]
        rss = bpf.render_rss(items, ac.CHANNELS_BY_VALUE["financeaml"])
        root = ET.fromstring(rss.encode("utf-8"))
        entries = root.findall("./channel/item")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].findtext("title"), "A & <B>")
        self.assertIsNone(entries[0].find("link"), "unsafe URL must not become a link")
        self.assertNotIn("<B>", rss)
        self.assertIn("not legal advice", entries[0].findtext("description"))

    def test_rss_orders_by_detection_date_and_is_deterministic(self):
        items = [
            item("old", first_seen_at="2026-09-01"),
            item("new", first_seen_at="2026-09-22"),
            item("legacy", first_seen_at=None, published_at="2026-09-10"),
            item("undated", first_seen_at=None, published_at=""),
        ]
        rss = bpf.render_rss(items, ac.ALL_AREAS)
        self.assertEqual(rss, bpf.render_rss(items, ac.ALL_AREAS))
        guids = [node.text for node in ET.fromstring(rss.encode("utf-8")).iter("guid")]
        self.assertEqual(guids, ["jlrw-new", "jlrw-legacy", "jlrw-old"])
        self.assertIn("<lastBuildDate>Tue, 22 Sep 2026 00:00:00 +0900</lastBuildDate>", rss)

    def test_rss_is_capped(self):
        items = [item(f"i{n}") for n in range(bpf.MAX_FEED_ITEMS + 5)]
        root = ET.fromstring(bpf.render_rss(items, ac.ALL_AREAS).encode("utf-8"))
        self.assertEqual(len(root.findall("./channel/item")), bpf.MAX_FEED_ITEMS)


class TestIcs(unittest.TestCase):
    def test_calendar_lists_only_open_upcoming_deadlines(self):
        items = [
            item("open", stage="Public Comment Open", comment_deadline="2026-10-16T00:00:00+09:00"),
            item("closed", stage="Public Comment Closed", comment_deadline="2026-10-16"),
            item("past", stage="Public Comment Open", comment_deadline="2026-09-20"),
            item("none", stage="Public Comment Open"),
        ]
        ics = bpf.render_ics(items, ac.ALL_AREAS, TODAY)
        self.assertEqual(ics.count("BEGIN:VEVENT"), 1)
        self.assertIn("UID:jlrw-open-comment-deadline@legal-gpt-official.github.io", ics)
        # Closing at 00:00 on the 16th leaves the 15th as the last filing day.
        self.assertIn("DTSTART;VALUE=DATE:20261015", ics)
        self.assertIn("DTEND;VALUE=DATE:20261016", ics)

    def test_calendar_escapes_text_folds_lines_and_uses_crlf(self):
        items = [
            item(
                "x",
                stage="Public Comment Open",
                comment_deadline="2026-10-01",
                title_en="Public Comment: Rules; draft, with a \\ backslash " + "long " * 30,
            )
        ]
        ics = bpf.render_ics(items, ac.ALL_AREAS, TODAY)
        self.assertTrue(ics.endswith("\r\n"))
        self.assertNotIn("\n", ics.replace("\r\n", ""))
        for line in ics.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75, line)
        unfolded = ics.replace("\r\n ", "")
        self.assertIn("SUMMARY:Public comment closes: Rules\\; draft\\, with a \\\\ backslash", unfolded)
        self.assertNotIn("closes: Public Comment:", unfolded)

    def test_multibyte_text_is_never_split_mid_character(self):
        line = "DESCRIPTION:" + "日本語" * 40
        for segment in bpf._fold(line):
            segment.encode("utf-8")  # would raise on a split surrogate
            self.assertLessEqual(len(segment.encode("utf-8")), 75)
        self.assertEqual("".join(seg[1:] if i else seg for i, seg in enumerate(bpf._fold(line))), line)


class TestBuild(unittest.TestCase):
    def test_build_writes_one_rss_and_one_ics_per_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = bpf.build_feeds([item("a")], Path(tmp), TODAY)
            names = sorted(path.name for path in Path(tmp).iterdir())
        expected = sorted([f"{c.value}.xml" for c in ac.CHANNELS] + [f"{c.value}.ics" for c in ac.CHANNELS])
        self.assertEqual(names, expected)
        self.assertEqual(summary["rss_files"], len(ac.CHANNELS))

    def test_published_feeds_exist_for_every_channel(self):
        feeds_dir = REPO_ROOT / "docs" / "feeds"
        for channel in ac.CHANNELS:
            with self.subTest(channel=channel.value):
                self.assertTrue((feeds_dir / f"{channel.value}.xml").is_file())
                self.assertTrue((feeds_dir / f"{channel.value}.ics").is_file())


if __name__ == "__main__":
    unittest.main()
