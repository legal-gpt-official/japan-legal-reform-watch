"""Offline tests for the crawlable static page parts and sitemap."""

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

TODAY = date(2026, 10, 7)
DOCS = REPO_ROOT / "docs"
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def item(item_id, **overrides):
    base = {
        "id": item_id,
        "title_en": f"Title {item_id}",
        "title_ja": "日本語タイトル",
        "area": "Finance / AML",
        "stage": "Government Announcement",
        "source_name": "Financial Services Agency (金融庁) 新着情報",
        "source_url": f"https://www.fsa.go.jp/{item_id}",
        "published_at": "2026-10-01",
        "last_checked": "2026-10-06",
        "summary_en": "Template sentence shared by every rule-based item.",
        "summary_source": "rule_based",
    }
    base.update(overrides)
    return base


def page(block=""):
    return f"<html><body><div id=\"cards\">\n{bpp.STATIC_START}{block}{bpp.STATIC_END}\n</div></body></html>"


class TestLatestItems(unittest.TestCase):
    def test_newest_first_and_skips_undated_future_and_untitled(self):
        items = [
            item("old", published_at="2026-09-01"),
            item("new", published_at="2026-10-05"),
            item("undated", published_at=""),
            item("future", published_at="2026-10-08"),
            item("untitled", title_en=""),
        ]
        ids = [i["id"] for i in bpp.latest_items(items, TODAY, 10)]
        self.assertEqual(ids, ["new", "old"])

    def test_same_day_keeps_relevance_order_and_count_is_capped(self):
        items = [item(f"i{n}") for n in range(5)]
        ids = [i["id"] for i in bpp.latest_items(items, TODAY, 3)]
        self.assertEqual(ids, ["i0", "i1", "i2"])


class TestStaticBlock(unittest.TestCase):
    def test_untrusted_fields_are_escaped_and_unsafe_urls_are_not_links(self):
        hostile = item(
            "x",
            title_en='<script>alert(1)</script>',
            source_url="javascript:alert(1)",
            area='"><img src=x>',
        )
        html = bpp.render_static_item(hostile)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("javascript:", html)
        self.assertNotIn("<a ", html)
        self.assertIn("&lt;script&gt;", html)

    def test_source_link_opens_the_official_source(self):
        html = bpp.render_static_item(item("a"))
        self.assertIn('href="https://www.fsa.go.jp/a"', html)
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertIn("Financial Services Agency (FSA)", html)

    def test_only_ai_summaries_are_shown(self):
        self.assertNotIn("Template sentence", bpp.render_static_item(item("a")))
        ai = bpp.render_static_item(item("b", summary_source="claude", summary_en="An AI summary."))
        self.assertIn("An AI summary.", ai)
        self.assertIn("(AI summary)", ai)

    def test_long_summary_is_shortened_on_a_word_boundary(self):
        text = "word " * 200
        out = bpp.shorten(text.strip(), 50)
        self.assertLessEqual(len(out), 50)
        self.assertTrue(out.endswith("…"))
        self.assertTrue(out[:-1].endswith("word"))

    def test_block_carries_the_monitoring_aid_notice(self):
        block = bpp.render_static_latest([item("a")], date(2026, 10, 6))
        self.assertIn("not legal advice", block)
        self.assertIn("remains authoritative", block)
        self.assertIn("checked 2026-10-06", block)

    def test_replace_requires_exactly_one_marker_pair(self):
        self.assertIn("NEW", bpp.replace_static_block(page("old"), "NEW"))
        self.assertNotIn("old", bpp.replace_static_block(page("old"), "NEW"))
        for broken in ("<html></html>", page() + page(), bpp.STATIC_END + bpp.STATIC_START):
            with self.subTest(broken=broken[:30]):
                with self.assertRaises(ValueError):
                    bpp.replace_static_block(broken, "NEW")


class TestBuild(unittest.TestCase):
    def test_build_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data = tmp_path / "data.json"
            index = tmp_path / "index.html"
            sitemap = tmp_path / "sitemap.xml"
            data.write_text(json.dumps([item("a"), item("b")]), encoding="utf-8")
            index.write_text(page(), encoding="utf-8")
            first = bpp.build(data, index, sitemap, TODAY)
            second = bpp.build(data, index, sitemap, TODAY)
            html = index.read_text(encoding="utf-8")
        self.assertEqual(first["static_latest_items"], 2)
        self.assertTrue(first["index_changed"] and first["sitemap_changed"])
        self.assertFalse(second["index_changed"] or second["sitemap_changed"])
        self.assertEqual(html.count(bpp.STATIC_START), 1)

    def test_sitemap_lists_site_pages_with_latest_checked_date(self):
        root = ET.fromstring(bpp.render_sitemap(date(2026, 10, 6)))
        urls = [u.find(SITEMAP_NS + "loc").text for u in root.findall(SITEMAP_NS + "url")]
        self.assertEqual(urls[0], ac.SITE_URL)
        self.assertIn(ac.SITE_URL + "legal/disclaimer_en.html", urls)
        self.assertNotIn("thank-you", "".join(urls))
        self.assertEqual(root.find(SITEMAP_NS + "url").find(SITEMAP_NS + "lastmod").text, "2026-10-06")


class TestPublishedSite(unittest.TestCase):
    def test_site_url_is_the_custom_domain_from_cname(self):
        cname = (DOCS / "CNAME").read_text(encoding="utf-8").strip()
        self.assertEqual(ac.SITE_URL, f"https://{cname}/")
        self.assertEqual(ac.DASHBOARD_URL, ac.SITE_URL)

    def test_index_head_points_at_the_canonical_site(self):
        html = (DOCS / "index.html").read_text(encoding="utf-8")
        self.assertIn(f'<link rel="canonical" href="{ac.SITE_URL}" />', html)
        self.assertIn(f'href="{ac.SITE_URL}feeds/all.xml"', html)
        self.assertIn('<meta property="og:url" content="' + ac.SITE_URL + '" />', html)
        self.assertEqual(html.count(bpp.STATIC_START), 1)
        self.assertEqual(html.count(bpp.STATIC_END), 1)
        # The static block sits inside the cards container app.js replaces.
        self.assertLess(html.index('id="cards"'), html.index(bpp.STATIC_START))
        self.assertLess(html.index(bpp.STATIC_END), html.index('id="load-more-wrap"'))

    def test_robots_allows_crawling_and_names_the_sitemap(self):
        robots = (DOCS / "robots.txt").read_text(encoding="utf-8")
        self.assertIn(f"Sitemap: {ac.SITE_URL}sitemap.xml", robots)
        # The browser data must stay crawlable or rendered pages come out empty.
        self.assertNotIn("Disallow: /data", robots)
        self.assertTrue((DOCS / "sitemap.xml").is_file())

    def test_follow_strip_links_the_free_feeds(self):
        html = (DOCS / "index.html").read_text(encoding="utf-8")
        for snippet in ('href="./feeds/all.xml"', 'href="./feeds/all.ics"', 'id="follow-by-area"'):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, html)

    def test_daily_workflow_builds_and_commits_the_pages(self):
        workflow = (REPO_ROOT / ".github" / "workflows" / "daily-update.yml").read_text(encoding="utf-8")
        self.assertIn("python scripts/build_public_pages.py", workflow)
        self.assertLess(
            workflow.index("python scripts/build_public_feeds.py"),
            workflow.index("python scripts/build_public_pages.py"),
        )
        self.assertLess(workflow.index("python scripts/build_public_pages.py"), workflow.index("name: Check data changes"))
        self.assertEqual(workflow.count("docs/feeds docs/index.html docs/sitemap.xml"), 2)


if __name__ == "__main__":
    unittest.main()
