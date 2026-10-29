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
    return (
        f"<html><body><div id=\"cards\">\n{bpp.STATIC_START}{block}{bpp.STATIC_END}\n</div>"
        f"{bpp.AREA_NAV_START}{bpp.AREA_NAV_END}</body></html>"
    )


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


class TestLocalizedOrdinaryDetail(unittest.TestCase):
    def test_japanese_and_chinese_summaries_follow_dashboard_language(self):
        translated = item(
            "localized",
            summary_ja="日本語の要約",
            business_impact_ja="日本語の影響",
            recommended_action_ja="日本語の対応",
            translations={"zh-Hans": {
                "title": "中文标题", "summary": "中文摘要",
                "business_impact": "中文影响", "recommended_action": "中文建议"
            }},
        )
        html = bpp.render_item_page(translated)
        self.assertIn('id="localized-summary-ja"', html)
        self.assertIn('id="localized-summary-zh-Hans"', html)
        self.assertIn("日本語の要約", html)
        self.assertIn("中文摘要", html)
        self.assertIn('id="item-english-summary"', html)
        self.assertIn('new URLSearchParams(location.search).get("lang")', html)
        self.assertIn('document.addEventListener("DOMContentLoaded"', html)
        self.assertIn('document.documentElement.lang=l', html)

    def test_missing_or_partial_translation_is_not_misrepresented(self):
        partial = item("partial", summary_ja="一部のみ", translations={
            "zh-Hans": {"title": "中文标题", "summary": "摘要"}
        })
        html = bpp.render_item_page(partial)
        self.assertNotIn('id="localized-summary-ja"', html)
        self.assertNotIn('id="localized-summary-zh-Hans"', html)
        self.assertIn('id="item-english-summary"', html)

    def test_untrusted_localized_text_is_escaped(self):
        hostile = item(
            "hostile", summary_ja="<img src=x onerror=alert(1)>",
            business_impact_ja="影響", recommended_action_ja="対応",
        )
        html = bpp.render_item_page(hostile)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        self.assertNotIn('<img src=x onerror=alert(1)>', html)


class TestBuild(unittest.TestCase):
    def test_build_writes_every_page_and_is_idempotent(self):
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
            pages = sorted(str(p.relative_to(tmp_path).as_posix()) for p in tmp_path.rglob("index.html"))
        self.assertEqual(first["pages_written"], len(bpp.AREA_PAGES) + 1)
        self.assertIn("index", first["files_changed"])
        self.assertIn("sitemap", first["files_changed"])
        self.assertEqual(second["files_changed"], [])
        self.assertEqual(html.count(bpp.STATIC_START), 1)
        self.assertIn('href="./areas/finance-aml/"', html)
        expected = sorted(
            ["index.html", "public-comments/index.html", "u/a/index.html", "u/b/index.html"]
            + [f"areas/{area.slug}/index.html" for area in bpp.AREA_PAGES]
        )
        self.assertEqual(pages, expected)
        self.assertEqual(first["item_pages_changed"], 2)
        self.assertEqual(second["item_pages_changed"], 0)

    def test_sitemap_lists_given_pages_and_dates(self):
        root = ET.fromstring(bpp.render_sitemap([(ac.SITE_URL, date(2026, 10, 6)), (ac.SITE_URL + "x/", None)]))
        rows = root.findall(SITEMAP_NS + "url")
        self.assertEqual([u.find(SITEMAP_NS + "loc").text for u in rows], [ac.SITE_URL, ac.SITE_URL + "x/"])
        self.assertEqual(rows[0].find(SITEMAP_NS + "lastmod").text, "2026-10-06")
        self.assertIsNone(rows[1].find(SITEMAP_NS + "lastmod"))


class TestAreaPages(unittest.TestCase):
    def test_every_area_channel_has_exactly_one_page(self):
        areas = sorted(c.value for c in ac.CHANNELS if c.area)
        self.assertEqual(sorted(a.channel for a in bpp.AREA_PAGES), areas)

    def test_slugs_are_unique_and_url_safe(self):
        slugs = [a.slug for a in bpp.AREA_PAGES]
        self.assertEqual(len(slugs), len(set(slugs)))
        for slug in slugs:
            with self.subTest(slug=slug):
                self.assertRegex(slug, r"^[a-z0-9]+(-[a-z0-9]+)*$")

    def test_area_page_lists_only_that_area_and_carries_the_notices(self):
        items = [
            item("fin", title_en="Finance item"),
            item("ene", title_en="Energy item", area="Energy / Environment"),
        ]
        finance = next(a for a in bpp.AREA_PAGES if a.slug == "finance-aml")
        html = bpp.render_area_page(finance, items, TODAY)
        self.assertIn("Finance item", html)
        self.assertNotIn("Energy item", html)
        self.assertIn(f'<link rel="canonical" href="{ac.SITE_URL}areas/finance-aml/" />', html)
        self.assertIn("<h1 class=\"area-page-title\">Japan Financial Regulation &amp; AML Updates</h1>", html)
        self.assertIn("not legal advice", html)
        self.assertIn("remains authoritative", html)
        self.assertIn("keyword rules", html)
        self.assertIn('href="../../legal/disclaimer_en.html"', html)
        self.assertIn('href="../../feeds/financeaml.xml"', html)
        self.assertIn('href="../../style.css?v=' + bpp.STYLE_CACHE_BUSTER + '"', html)
        self.assertIn('<span aria-current="page">Finance / AML</span>', html)

    def test_area_page_escapes_untrusted_fields(self):
        hostile = item("x", title_en="<script>alert(1)</script>", source_name="<b>src</b>")
        finance = next(a for a in bpp.AREA_PAGES if a.slug == "finance-aml")
        html = bpp.render_area_page(finance, [hostile], TODAY)
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<b>src</b>", html)

    def test_open_comments_soonest_first_and_closed_excluded(self):
        items = [
            item("late", stage="Public Comment Open", comment_deadline="2026-11-30"),
            item("soon", stage="Public Comment Open", comment_deadline="2026-10-10"),
            item("past", stage="Public Comment Open", comment_deadline="2026-10-01"),
            item("none", stage="Public Comment Open"),
            item("closed", stage="Public Comment Closed", comment_deadline="2026-12-01"),
        ]
        self.assertEqual([i["id"] for i in bpp.open_comments(items, TODAY)], ["soon", "late"])
        html = bpp.render_public_comments_page(items, TODAY)
        self.assertIn("Comments due: 2026-10-10 (end of day, JST)", html)
        self.assertNotIn("fsa.go.jp/past", html)
        self.assertIn(f'<link rel="canonical" href="{ac.SITE_URL}public-comments/" />', html)
        self.assertIn('href="../legal/disclaimer_en.html"', html)

    def test_top_sources_are_counted_and_stably_ordered(self):
        items = [item("a"), item("b"), item("c", source_name="B source"), item("d", source_name="A source")]
        self.assertEqual(
            bpp.top_sources(items, 3),
            [("Financial Services Agency (FSA)", 2), ("A source", 1), ("B source", 1)],
        )


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
        self.assertEqual(html.count(bpp.AREA_NAV_START), 1)
        self.assertIn('style.css?v=' + bpp.STYLE_CACHE_BUSTER, html)

    def test_published_area_pages_are_in_the_sitemap(self):
        sitemap = (DOCS / "sitemap.xml").read_text(encoding="utf-8")
        for area in bpp.AREA_PAGES:
            with self.subTest(slug=area.slug):
                self.assertTrue((DOCS / "areas" / area.slug / "index.html").is_file())
                self.assertIn(f"<loc>{ac.SITE_URL}areas/{area.slug}/</loc>", sitemap)
        self.assertIn(f"<loc>{ac.SITE_URL}public-comments/</loc>", sitemap)

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
        self.assertEqual(
            workflow.count("docs/feeds docs/index.html docs/sitemap.xml docs/areas docs/public-comments docs/u"), 2
        )

    def test_every_published_item_has_a_page_and_only_indexable_ones_are_in_the_sitemap(self):
        items = bpp.load_items(DOCS / "data" / "legal_updates.json")
        sitemap = (DOCS / "sitemap.xml").read_text(encoding="utf-8")
        for record in bpp.page_items(items):
            path = bpp.item_page_path(record)
            self.assertTrue((DOCS / path / "index.html").is_file(), path)
            listed = f"<loc>{ac.SITE_URL}{path}</loc>" in sitemap
            page_html = (DOCS / path / "index.html").read_text(encoding="utf-8")
            self.assertEqual(listed, 'name="robots" content="noindex' not in page_html, path)
        published = {bpp.item_page_id(record) for record in items}
        on_disk = {child.name for child in (DOCS / bpp.ITEM_DIR_NAME).iterdir()}
        self.assertEqual(on_disk - published, set(), "pages of unpublished items should be pruned")


AI_ITEM = dict(
    summary_source="claude",
    title_ja="下水道法施行令の一部を改正する政令（案）",
    summary_en="Draft amendment published for comment.",
    business_impact_en="Operators may be affected.",
    recommended_action_en="Review the consultation documents.",
    impact_level="Medium",
)

RICH_ITEM = dict(
    AI_ITEM,
    confidence="medium",
    summary_en=(
        "Ministerial Ordinance No. 89 of 2026 partially amends the Industrial Safety and Health Regulations. "
        "The listing records a promulgation date of 2026-04-28 and an effective date of 2026-10-01, and names the "
        "Ordinance on Prevention of Hazards Due to Specified Chemical Substances as an amended instrument."
    ),
    business_impact_en=(
        "Employers that handle specified chemical substances should check whether the amended requirements change "
        "their workplace practices, record keeping and the training they give to workers in affected processes."
    ),
)


class TestSearchValue(unittest.TestCase):
    def rich(self, item_id="raw-r", **overrides):
        return item(item_id, **{**RICH_ITEM, **overrides})

    def test_rich_item_with_more_than_a_title_qualifies(self):
        self.assertTrue(bpp.item_search_value(self.rich(), None, "title_and_structured_metadata"))
        self.assertTrue(bpp.item_search_value(self.rich(), None, "descriptive_text"))

    def test_each_requirement_is_needed(self):
        cases = {
            "title only": (self.rich(), "title_only"),
            "unknown evidence": (self.rich(), None),
            "low confidence": (self.rich(confidence="low"), "descriptive_text"),
            "rule based": (self.rich(summary_source="rule_based"), "descriptive_text"),
            "short summary": (self.rich(summary_en="Ordinance No. 89 of 2026 took effect on 2026-10-01."), "descriptive_text"),
            "short impact": (self.rich(business_impact_en="Employers may be affected."), "descriptive_text"),
            "no facts": (self.rich(summary_en="x" * 300), "descriptive_text"),
            "admits unknown impact": (
                self.rich(business_impact_en=RICH_ITEM["business_impact_en"] + " The text does not describe the changes."),
                "descriptive_text",
            ),
            "hedged summary": (
                self.rich(summary_en=RICH_ITEM["summary_en"] + " The provisions are not specified in the listing."),
                "descriptive_text",
            ),
        }
        for name, (record, evidence) in cases.items():
            self.assertFalse(bpp.item_search_value(record, None, evidence), name)

    def test_published_explainer_always_qualifies(self):
        self.assertTrue(bpp.item_search_value(item("raw-x"), object(), None))

    def test_duplicate_summaries_keep_only_the_earliest_page(self):
        first = self.rich("raw-b", published_at="2026-01-01")
        later = self.rich("raw-a", published_at="2026-02-01")
        other = self.rich("raw-c", published_at="2026-03-01", summary_en="Different. " + RICH_ITEM["summary_en"])
        evidence = {"raw-a": "descriptive_text", "raw-b": "descriptive_text", "raw-c": "descriptive_text"}
        chosen = bpp.select_indexable([later, other, first], {}, evidence)
        self.assertEqual(chosen, {"raw-b", "raw-c"})
        self.assertEqual(chosen, bpp.select_indexable([first, other, later], {}, evidence))

    def test_robots_txt_does_not_block_crawling_of_noindex_pages(self):
        robots = (DOCS / "robots.txt").read_text(encoding="utf-8")
        self.assertNotRegex(robots, r"(?im)^\s*disallow:\s*\S")
        self.assertIn("Allow: /", robots)


class TestItemPages(unittest.TestCase):
    def test_ai_item_page_shows_summary_attribution_and_notices(self):
        html = bpp.render_item_page(item("raw-abc", **AI_ITEM), indexable=True)
        self.assertIn(f'<link rel="canonical" href="{ac.SITE_URL}u/raw-abc/" />', html)
        self.assertNotIn("noindex", html)
        self.assertIn('<h1 class="area-page-title">Title raw-abc</h1>', html)
        self.assertIn('lang="ja">下水道法施行令の一部を改正する政令（案）</p>', html)
        self.assertIn('href="https://www.fsa.go.jp/raw-abc" target="_blank" rel="noopener noreferrer"', html)
        self.assertIn("Source: Financial Services Agency (FSA)", html)
        self.assertIn("Published: 2026-10-01", html)
        self.assertIn("Stage: Government Announcement", html)
        for text in AI_ITEM.values():
            if text.endswith("."):
                self.assertIn(text, html)
        self.assertIn("not legal advice", html)
        self.assertIn("remains authoritative", html)
        self.assertIn('href="../../legal/disclaimer_en.html"', html)
        self.assertIn('href="../../legal/disclaimer_ja.html"', html)
        # Breadcrumb goes through the item's area page.
        self.assertIn('<a href="../../areas/finance-aml/">Finance / AML</a>', html)
        self.assertIn('href="../../style.css?v=' + bpp.STYLE_CACHE_BUSTER + '"', html)
        self.assertIn('src="../../analytics.js?v=' + bpp.ANALYTICS_CACHE_BUSTER + '"', html)
        self.assertNotIn("app.js", html)
        self.assertNotIn("i18n.js", html)

    def test_rule_based_item_page_is_noindex_and_omits_the_template(self):
        html = bpp.render_item_page(item("raw-rule"))
        self.assertIn('<meta name="robots" content="noindex, follow" />', html)
        self.assertNotIn("Template sentence", html)
        self.assertIn("not yet available", html)
        self.assertFalse(bpp.item_search_value(item("raw-rule"), None, "descriptive_text"))
        self.assertTrue(bpp.item_search_value(item("raw-ai", **RICH_ITEM), None, "descriptive_text"))

    def test_a_thin_ai_page_is_noindex_unless_it_is_told_to_be_indexable(self):
        html = bpp.render_item_page(item("raw-abc", **AI_ITEM))
        self.assertIn('<meta name="robots" content="noindex, follow" />', html)
        self.assertIn("Draft amendment published for comment.", html)

    def test_untrusted_fields_are_escaped_and_unsafe_urls_are_not_links(self):
        hostile = item(
            "raw-x",
            title_en="<script>alert(1)</script>",
            title_ja="<img src=x onerror=alert(1)>",
            source_name="<b>src</b>",
            source_url="javascript:alert(1)",
            stage='"><svg onload=alert(1)>',
            **{k: v for k, v in AI_ITEM.items() if k not in ("title_ja", "business_impact_en")},
            business_impact_en="</script><script>alert(2)</script>",
        )
        html = bpp.render_item_page(hostile)
        for raw in ("<script>alert", "<img src=x", "<b>src</b>", "javascript:", "<svg", "</script><script>"):
            self.assertNotIn(raw, html)
        self.assertNotIn("View the original Japanese source", html)

    def test_unsafe_or_duplicate_ids_get_no_page(self):
        for bad in ("../etc", "a/b", ".hidden", "", "x" * 81, "a b", None, 5):
            with self.subTest(bad=bad):
                self.assertEqual(bpp.item_page_path(item("x") | {"id": bad}), "")
        self.assertEqual(bpp.item_page_path(item("raw-1", title_en="")), "")
        rows = bpp.page_items([item("raw-1"), item("raw-1", title_en="Second"), item("../x")])
        self.assertEqual([r["title_en"] for r in rows], ["Title raw-1"])

    def test_page_ignores_last_checked_so_quiet_days_rewrite_nothing(self):
        a = bpp.render_item_page(item("raw-1", **AI_ITEM))
        b = bpp.render_item_page(item("raw-1", last_checked="2026-12-31", **AI_ITEM))
        self.assertEqual(a, b)
        self.assertEqual(
            bpp.item_lastmod(item("raw-1", summarized_at="2026-10-17T22:55:49Z", last_checked="2026-12-31")),
            date(2026, 10, 17),
        )

    def test_listings_link_to_the_item_page_and_keep_the_source_link(self):
        html = bpp.render_static_item(item("raw-a"), prefix="../../")
        self.assertIn('class="static-latest-title" href="../../u/raw-a/"', html)
        self.assertIn('href="https://www.fsa.go.jp/raw-a" target="_blank" rel="noopener noreferrer"', html)
        block = bpp.render_static_latest([item("raw-a")], None)
        self.assertIn('href="./u/raw-a/"', block)

    def test_build_lists_only_rich_pages_in_the_sitemap_and_prunes_stale_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data = tmp_path / "data.json"
            index = tmp_path / "index.html"
            sitemap = tmp_path / "sitemap.xml"
            index.write_text(page(), encoding="utf-8")
            stale = tmp_path / "u" / "raw-gone"
            stale.mkdir(parents=True)
            (stale / "index.html").write_text("old", encoding="utf-8")
            foreign = tmp_path / "u" / "raw-keep"
            foreign.mkdir()
            (foreign / "index.html").write_text("x", encoding="utf-8")
            (foreign / "notes.txt").write_text("not ours", encoding="utf-8")
            data.write_text(
                json.dumps([
                    item("raw-ai", summarized_at="2026-10-05T01:00:00Z", **RICH_ITEM),
                    item("raw-thin", **AI_ITEM),
                    item("raw-rule"),
                ]),
                encoding="utf-8",
            )
            raw = tmp_path / "raw.json"
            long_text = "The ordinance text describes the amended provisions in some detail. " * 3
            raw.write_text(
                json.dumps([
                    {"id": "raw-ai", "title_ja": "下水道法施行令の一部を改正する政令（案）", "raw_summary": long_text},
                    {"id": "raw-thin", "title_ja": "下水道法施行令の一部を改正する政令（案）", "raw_summary": ""},
                ]),
                encoding="utf-8",
            )
            result = bpp.build(data, index, sitemap, TODAY, raw_path=raw)
            root = ET.fromstring(sitemap.read_text(encoding="utf-8"))
            self.assertFalse(stale.exists())
            self.assertTrue(foreign.exists())
            self.assertTrue((tmp_path / "u" / "raw-rule" / "index.html").is_file())
        rows = {u.find(SITEMAP_NS + "loc").text: u.find(SITEMAP_NS + "lastmod") for u in root.findall(SITEMAP_NS + "url")}
        self.assertIn(ac.SITE_URL + "u/raw-ai/", rows)
        self.assertEqual(rows[ac.SITE_URL + "u/raw-ai/"].text, "2026-10-05")
        self.assertNotIn(ac.SITE_URL + "u/raw-rule/", rows)
        self.assertNotIn(ac.SITE_URL + "u/raw-thin/", rows)
        self.assertEqual(result["item_pages"], 3)
        self.assertEqual(result["item_pages_indexable"], 1)
        self.assertEqual(result["item_pages_removed"], 1)


if __name__ == "__main__":
    unittest.main()
