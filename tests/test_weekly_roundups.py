"""Offline tests for the weekly roundup pages and their RSS feed."""

from __future__ import annotations

import json
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
import build_public_pages as bpp  # noqa: E402
import weekly_roundups as wr  # noqa: E402

# Wednesday of ISO week 2026-W45; the last completed week is W44 (26 Oct - 1 Nov).
TODAY = date(2026, 11, 4)
W44 = date(2026, 10, 26)
W43 = date(2026, 10, 19)


def item(item_id, **overrides):
    base = {
        "id": item_id,
        "title_en": f"Title {item_id}",
        "title_ja": "日本語タイトル",
        "area": "Finance / AML",
        "stage": "Government Announcement",
        "source_name": "Financial Services Agency (金融庁) 新着情報",
        "source_url": f"https://www.fsa.go.jp/{item_id}",
        "published_at": "2026-10-28",
        "last_checked": "2026-11-03",
        "summary_en": f"The FSA published item {item_id} on 2026-10-28. A second sentence follows.",
        "summary_source": "claude",
        "relevance_score": 10,
    }
    base.update(overrides)
    return base


def week_of(n, day="2026-10-28", **overrides):
    return [item(f"w{day}-{i}", published_at=day, **overrides) for i in range(n)]


class TestWeekBoundaries(unittest.TestCase):
    def test_slug_and_heading_follow_iso_weeks(self):
        self.assertEqual(wr.week_slug(W44), "2026-w44")
        self.assertEqual(wr.week_slug(date(2026, 12, 28)), "2026-w53")
        self.assertEqual(wr.week_slug(date(2027, 1, 4)), "2027-w01")
        self.assertEqual(wr.week_heading(W44),
                         "Japan Legal & Regulatory Roundup — Week 44, 2026 (26 October – 1 November 2026)")
        self.assertEqual(wr.date_range_label(date(2026, 11, 2)), "2–8 November 2026")

    def test_monday_and_sunday_belong_to_the_week(self):
        items = week_of(5, "2026-10-26") + week_of(5, "2026-11-01")
        weeks = wr.build_weeks(items, TODAY)
        self.assertEqual([w.slug for w in weeks], ["2026-w44"])
        self.assertEqual(len(weeks[0].items), 10)

    def test_only_completed_weeks_get_a_page(self):
        items = week_of(6, "2026-11-02") + week_of(6, "2026-10-28")
        self.assertEqual([w.slug for w in wr.build_weeks(items, TODAY)], ["2026-w44"])
        # On Sunday the week is not over yet; on the next Monday it is.
        self.assertEqual(wr.build_weeks(week_of(6, "2026-11-02"), date(2026, 11, 8)), [])
        self.assertEqual(len(wr.build_weeks(week_of(6, "2026-11-02"), date(2026, 11, 9))), 1)

    def test_weeks_before_the_first_week_or_too_thin_are_skipped(self):
        items = week_of(6, "2026-06-28") + week_of(4, "2026-10-21") + week_of(5, "2026-10-28")
        self.assertEqual([w.slug for w in wr.build_weeks(items, TODAY)], ["2026-w44"])

    def test_rule_based_undated_and_untitled_items_are_not_listed(self):
        items = week_of(5) + [
            item("rb", summary_source="rule_based"),
            item("undated", published_at=""),
            item("untitled", title_en=""),
        ]
        week = wr.build_weeks(items, TODAY)[0]
        ids = {i["id"] for i in week.items}
        self.assertEqual(len(ids), 5)
        self.assertEqual(week.total_published, 6)  # the rule-based item counts as published


class TestSelection(unittest.TestCase):
    def test_ranked_by_relevance_then_newest_then_file_order(self):
        items = [
            item("low", relevance_score=1),
            item("older", published_at="2026-10-26", relevance_score=5),
            item("newer", published_at="2026-10-30", relevance_score=5),
            item("top", relevance_score=9),
            item("tie-a", relevance_score=3),
            item("tie-b", relevance_score=3),
        ]
        week = wr.build_weeks(items, TODAY)[0]
        self.assertEqual([i["id"] for i in week.items], ["top", "newer", "older", "tie-a", "tie-b", "low"])

    def test_area_caps_round_robin_other_last_and_page_cap(self):
        areas = [area for area in ac.AREA_LABELS["ja"] if area != "Other"]
        items = [item(f"{a}-{n}", area=a, relevance_score=100 - n) for a in areas for n in range(8)]
        items += [item(f"other-{n}", area="Other", relevance_score=500) for n in range(8)]
        week = wr.build_weeks(items, TODAY)[0]
        selected = week.selected
        self.assertEqual(len(selected), wr.PAGE_MAX)
        per_area = {}
        for row in selected:
            per_area.setdefault(row["area"], []).append(row["id"])
        self.assertEqual(list(per_area)[-1], "Other")
        self.assertLessEqual(len(per_area["Other"]), wr.OTHER_PER_AREA)
        self.assertTrue(all(len(v) <= wr.PER_AREA for v in per_area.values()))
        # Every area gets its top item before any area gets a second.
        self.assertEqual(len(per_area), len(areas) + 1)

    def test_small_week_lists_everything_up_to_the_area_cap(self):
        items = [item(f"a{n}", relevance_score=10 - n) for n in range(7)]
        week = wr.build_weeks(items, TODAY)[0]
        self.assertEqual([i["id"] for i in week.selected], ["a0", "a1", "a2", "a3", "a4"])

    def test_repeated_english_title_keeps_the_higher_ranked_record(self):
        items = week_of(5) + [
            item("egov", title_en="Draft guideline X", relevance_score=50),
            item("ministry", title_en="draft guideline x", relevance_score=40),
        ]
        ids = [i["id"] for i in wr.build_weeks(items, TODAY)[0].selected]
        self.assertIn("egov", ids)
        self.assertNotIn("ministry", ids)

    def test_one_line_is_the_first_sentence_shortened(self):
        self.assertEqual(
            wr.one_line({"summary_en": "Act No. 30 of 2026 was promulgated. It applies from 2027."}),
            "Act No. 30 of 2026 was promulgated.",
        )
        long = "word " * 80 + "end."
        line = wr.one_line({"summary_en": long})
        self.assertLessEqual(len(line), wr.LINE_MAX_CHARS)
        self.assertTrue(line.endswith("…"))

    def test_consultations_are_judged_at_the_week_end_not_the_build_date(self):
        items = week_of(5) + [
            item("open-late", stage="Public Comment Closed", comment_deadline="2026-11-03",
                 published_at="2026-10-10", summary_source="rule_based"),
            item("open-soon", stage="Public Comment Open", comment_deadline="2026-11-02T00:00:00+09:00",
                 published_at="2026-10-20", summary_source="rule_based"),
            item("closed-in-week", stage="Public Comment Closed", comment_deadline="2026-10-30",
                 published_at="2026-10-01"),
            item("after-week", stage="Public Comment Open", comment_deadline="2026-12-01",
                 published_at="2026-11-02"),
            item("results", stage="Public Comment Results Published", comment_deadline="2026-12-01",
                 published_at="2026-10-01"),
            item("no-deadline", stage="Public Comment Open", published_at="2026-10-01"),
        ]
        week = [w for w in wr.build_weeks(items, TODAY) if w.slug == "2026-w44"][0]
        # A deadline of 2026-11-02T00:00 JST leaves 2026-11-01 (the Sunday) as the
        # last full day, so it was no longer open after the week.
        self.assertEqual([i["id"] for i in week.consultations], ["open-late"])
        later = [w for w in wr.build_weeks(items, date(2027, 1, 1)) if w.slug == "2026-w44"][0]
        self.assertEqual(later.consultations, week.consultations)


class TestRendering(unittest.TestCase):
    def setUp(self):
        self.items = week_of(6) + [
            item("hostile", title_en="<script>alert(1)</script>", area='"><img src=x>',
                 source_url="javascript:alert(1)", summary_en="<b>bold</b> claim.", relevance_score=99),
            item("pc", stage="Public Comment Open", comment_deadline="2026-11-20",
                 title_en="Draft & consultation", relevance_score=0),
        ]
        self.weeks = wr.build_weeks(self.items, TODAY)
        self.html = wr.render_week_page(self.weeks[0], None, None)

    def test_untrusted_fields_are_escaped_and_unsafe_urls_are_not_links(self):
        self.assertNotIn("<script>alert", self.html)
        self.assertNotIn("<img src=x", self.html)
        self.assertNotIn("<b>bold", self.html)
        self.assertNotIn("javascript:", self.html)
        self.assertIn("&lt;script&gt;", self.html)
        self.assertIn("Draft &amp; consultation", self.html)

    def test_page_links_item_pages_and_official_sources_with_notices(self):
        self.assertIn('href="../../u/w2026-10-28-0/"', self.html)
        self.assertIn('href="https://www.fsa.go.jp/w2026-10-28-0"', self.html)
        self.assertIn("Original Japanese source", self.html)
        self.assertIn("(AI summary)", self.html)
        self.assertIn("not by legal importance", self.html)
        self.assertIn("legal/disclaimer_en.html", self.html)
        self.assertIn("legal/disclaimer_ja.html", self.html)
        self.assertIn('class="trust-notice"', self.html)
        self.assertIn("Consultations open at week end", self.html)
        self.assertIn("Comments due: 2026-11-20 (end of day, JST)", self.html)
        self.assertIn('<link rel="canonical" href="https://updates.legal-gpt.com/weekly/2026-w44/" />', self.html)
        self.assertIn('property="og:title"', self.html)
        self.assertNotIn("noindex", self.html)
        self.assertNotIn("app.js", self.html)

    def test_page_shows_no_build_or_check_date(self):
        self.assertNotIn("2026-11-03", self.html)  # last_checked
        self.assertNotIn(TODAY.isoformat(), self.html)

    def test_index_and_neighbour_links(self):
        items = self.items + week_of(5, "2026-10-21")
        weeks = wr.build_weeks(items, TODAY)
        self.assertEqual([w.slug for w in weeks], ["2026-w44", "2026-w43"])
        newest = wr.render_week_page(weeks[0], None, weeks[1])
        oldest = wr.render_week_page(weeks[1], weeks[0], None)
        self.assertIn('href="../2026-w43/"', newest)
        self.assertIn('href="../2026-w44/"', oldest)
        index = wr.render_index_page(weeks)
        self.assertLess(index.index('href="./2026-w44/"'), index.index('href="./2026-w43/"'))
        self.assertIn("No weekly roundup has been published yet.", wr.render_index_page([]))

    def test_feed_is_valid_rss_with_stable_dates(self):
        feed = wr.render_feed(self.weeks)
        root = ET.fromstring(feed)
        entries = root.findall("./channel/item")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].findtext("link"), "https://updates.legal-gpt.com/weekly/2026-w44/")
        self.assertEqual(entries[0].findtext("guid"), "https://updates.legal-gpt.com/weekly/2026-w44/")
        self.assertIn("02 Nov 2026", entries[0].findtext("pubDate"))
        self.assertIn("not legal advice", entries[0].findtext("description"))


class TestBuild(unittest.TestCase):
    def run_build(self, tmp_path, items, today=TODAY):
        data = tmp_path / "data.json"
        index = tmp_path / "index.html"
        sitemap = tmp_path / "sitemap.xml"
        data.write_text(json.dumps(items), encoding="utf-8")
        if not index.exists():
            index.write_text(
                f"<html><body>{bpp.STATIC_START}{bpp.STATIC_END}{bpp.AREA_NAV_START}{bpp.AREA_NAV_END}</body></html>",
                encoding="utf-8",
            )
        return bpp.build(data, index, sitemap, today), sitemap

    def test_build_writes_pages_feed_and_sitemap_and_is_idempotent(self):
        items = week_of(5) + week_of(5, "2026-10-21")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            first, sitemap = self.run_build(tmp_path, items)
            second, _ = self.run_build(tmp_path, items)
            locs = {
                el.findtext("{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
                el.findtext("{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod")
                for el in ET.parse(sitemap).getroot()
            }
            self.assertTrue((tmp_path / "weekly" / "index.html").is_file())
            self.assertTrue((tmp_path / "feeds" / "weekly.xml").is_file())
            index_html = (tmp_path / "index.html").read_text(encoding="utf-8")
        self.assertEqual(first["weekly_pages"], 2)
        self.assertEqual(first["weekly_files_changed"], 4)
        self.assertEqual(second["weekly_files_changed"], 0)
        self.assertEqual(second["files_changed"], [])
        self.assertEqual(locs["https://updates.legal-gpt.com/weekly/2026-w44/"], "2026-10-28")
        self.assertEqual(locs["https://updates.legal-gpt.com/weekly/2026-w43/"], "2026-10-21")
        self.assertEqual(locs["https://updates.legal-gpt.com/weekly/"], "2026-10-28")
        self.assertIn('href="./weekly/">Weekly roundup</a>', index_html)

    def test_week_that_stops_qualifying_is_pruned_but_foreign_files_are_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self.run_build(tmp_path, week_of(5) + week_of(5, "2026-10-21"))
            keep = tmp_path / "weekly" / "2026-w01"
            keep.mkdir()
            (keep / "notes.txt").write_text("x", encoding="utf-8")
            result, _ = self.run_build(tmp_path, week_of(5))
            self.assertFalse((tmp_path / "weekly" / "2026-w43").exists())
            self.assertTrue((keep / "notes.txt").exists())
        self.assertEqual(result["weekly_pages_removed"], 1)


class TestWiring(unittest.TestCase):
    def test_directory_name_is_shared_with_the_page_builder(self):
        self.assertEqual(bpp.WEEKLY_DIR_NAME, wr.WEEKLY_DIR_NAME)

    def test_dashboard_links_the_roundup_and_its_feed(self):
        html = (REPO_ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="./weekly/" data-i18n="follow_weekly"', html)
        self.assertIn('href="https://updates.legal-gpt.com/feeds/weekly.xml"', html)
        i18n = (REPO_ROOT / "docs" / "i18n.js").read_text(encoding="utf-8")
        self.assertEqual(i18n.count("follow_weekly:"), 3)

    def test_area_pages_link_the_roundup(self):
        html = (REPO_ROOT / "docs" / "areas" / "finance-aml" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="../../weekly/">Weekly roundup</a>', html)

    def test_daily_workflow_commits_the_weekly_pages(self):
        workflow = (REPO_ROOT / ".github" / "workflows" / "daily-update.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count("docs/public-comments docs/weekly docs/u"), 2)
        self.assertIn("scripts/weekly_roundups.py", workflow)

    def test_robots_does_not_block_the_weekly_pages(self):
        robots = (REPO_ROOT / "docs" / "robots.txt").read_text(encoding="utf-8")
        self.assertNotIn("Disallow: /weekly", robots)


if __name__ == "__main__":
    unittest.main()
