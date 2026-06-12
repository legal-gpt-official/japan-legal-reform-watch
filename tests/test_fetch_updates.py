"""Offline safety tests for scripts/fetch_updates.py (Stage 1).

No network access: these tests cover the SOURCES configuration, id/hash
stability, text normalization, date parsing, and the feed/HTML parsers using
inline fixtures only. The fetch pipeline itself (run/main) is never invoked.
"""

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_updates as fu  # noqa: E402

ALLOWED_SOURCE_TYPES = {
    "public_comment_rss", "regulator_rss", "ministry_rss", "agency_rss",
    "regulator_html", "ministry_html",
}
ALLOWED_HTML_PARSERS = {"ppc_information", "jftc_pressrelease", "moe_press", "mlit_press"}


class TestSourcesConfig(unittest.TestCase):
    def test_sources_have_required_non_empty_keys(self):
        for source in fu.SOURCES:
            with self.subTest(source=source.get("name")):
                for key in ("name", "url", "source_type", "source_language"):
                    self.assertIn(key, source)
                    self.assertTrue(str(source[key]).strip(), f"empty {key}")

    def test_source_types_are_known(self):
        for source in fu.SOURCES:
            self.assertIn(source["source_type"], ALLOWED_SOURCE_TYPES, source["name"])

    def test_urls_are_https_and_unique(self):
        urls = [s["url"] for s in fu.SOURCES]
        for url in urls:
            self.assertTrue(url.startswith("https://"), url)
        self.assertEqual(len(urls), len(set(urls)))

    def test_names_are_unique(self):
        names = [s["name"] for s in fu.SOURCES]
        self.assertEqual(len(names), len(set(names)))

    def test_ministry_expansion_sources_present(self):
        names = " / ".join(s["name"] for s in fu.SOURCES)
        for token in (
            "法務省 (MOJ)",
            "環境省 (MOE)",
            "財務省 (MOF)",
            "総務省 (MIC)",
            "国土交通省 (MLIT)",
            "農林水産省 (MAFF)",
        ):
            self.assertIn(token, names)

    def test_mlit_maff_source_config(self):
        by_name = {source["name"]: source for source in fu.SOURCES}

        mlit = by_name["国土交通省 (MLIT) 報道発表"]
        self.assertEqual(mlit["source_type"], "ministry_html")
        self.assertEqual(mlit["html_parser"], "mlit_press")
        self.assertEqual(mlit["url"], "https://www.mlit.go.jp/report/press/")
        self.assertTrue(mlit.get("follow_meta_refresh"))

        maff = by_name["農林水産省 (MAFF) 報道発表"]
        self.assertEqual(maff["source_type"], "ministry_rss")
        self.assertEqual(maff["url"], "https://www.maff.go.jp/j/press/rss.xml")

    def test_every_source_has_a_ui_display_name(self):
        """docs/app.js maps every source_name to an English-first display label.

        The UI shows English labels while filter values keep the raw
        source_name; forgetting the mapping for a new source would leak a
        Japanese-first label into the English UI.
        """
        app_js = (Path(__file__).resolve().parents[1] / "docs" / "app.js").read_text(encoding="utf-8")
        self.assertIn("SOURCE_DISPLAY_NAMES", app_js)
        for source in fu.SOURCES:
            with self.subTest(source=source["name"]):
                self.assertIn(f'"{source["name"]}":', app_js)

    def test_html_sources_declare_a_known_parser(self):
        for source in fu.SOURCES:
            if str(source["source_type"]).endswith("_html"):
                with self.subTest(source=source["name"]):
                    self.assertIn(source.get("html_parser"), ALLOWED_HTML_PARSERS)

    def test_unsupported_html_parser_raises(self):
        with self.assertRaises(ValueError):
            fu.parse_html_source(b"<html></html>", {"html_parser": "nope", "url": "https://x"})


class TestIdentityAndNormalization(unittest.TestCase):
    def test_make_id_is_stable_and_url_first(self):
        a = fu.make_id("https://example.go.jp/x", "タイトルA", "Source", "2026-06-01")
        b = fu.make_id("https://example.go.jp/x", "タイトルB", "Other Source", "2026-06-02")
        self.assertEqual(a, b)  # same URL -> same id, regardless of metadata
        self.assertTrue(a.startswith("raw-"))
        self.assertEqual(len(a), 4 + 16)

    def test_make_id_meta_fallback_when_no_url(self):
        a = fu.make_id("", "タイトルA", "Source", "2026-06-01")
        b = fu.make_id("", "タイトルA", "Source", "2026-06-01")
        c = fu.make_id("", "タイトルB", "Source", "2026-06-01")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertNotEqual(a, fu.make_id("https://example.go.jp/x", "タイトルA", "Source", "2026-06-01"))

    def test_content_hash_stability_and_sensitivity(self):
        h1 = fu.content_hash("題名", "要約", "2026-06-01")
        h2 = fu.content_hash("題名", "要約", "2026-06-01")
        h3 = fu.content_hash("題名", "要約変更", "2026-06-01")
        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, h3)
        self.assertEqual(len(h1), 64)  # sha256 hex

    def test_clean_text_strips_tags_entities_whitespace(self):
        self.assertEqual(fu.clean_text("<p>A&amp;B  \n C</p>"), "A&B C")
        self.assertEqual(fu.clean_text(""), "")
        self.assertEqual(fu.clean_text("&lt;b&gt;x&lt;/b&gt;"), "x")  # double-unescape strips encoded tags

    def test_build_item_returns_none_without_title_and_url(self):
        self.assertIsNone(fu.build_item({"title": "", "link": ""}, fu.SOURCES[0], "2026-06-10T00:00:00Z"))

    def test_build_item_populates_raw_schema(self):
        entry = {"title": "  改正案 <b>について</b> ", "link": " https://example.go.jp/a ",
                 "summary": "概要&amp;詳細", "published_iso": "2026-06-09T15:00:00Z"}
        source = {"name": "Test Source", "source_type": "ministry_rss", "source_language": "ja"}
        item = fu.build_item(entry, source, "2026-06-10T00:00:00Z")
        self.assertEqual(item["title_ja"], "改正案 について")
        self.assertEqual(item["source_url"], "https://example.go.jp/a")  # verbatim apart from trim
        self.assertEqual(item["raw_summary"], "概要&詳細")
        self.assertEqual(item["published_at"], "2026-06-09T15:00:00Z")
        self.assertEqual(item["source_type"], "ministry_rss")
        for key in ("id", "title_ja", "source_name", "source_url", "published_at",
                    "fetched_at", "source_language", "raw_summary", "raw_content_hash", "source_type"):
            self.assertIn(key, item)


class TestDateParsing(unittest.TestCase):
    def test_normalize_date_formats(self):
        self.assertEqual(fu._normalize_date("Tue, 09 Jun 2026 17:00:00 +0900"), "2026-06-09T08:00:00Z")
        self.assertEqual(fu._normalize_date("2026-06-09T17:00:00+09:00"), "2026-06-09T08:00:00Z")
        # Date-only input is accepted by fromisoformat first, yielding midnight.
        self.assertEqual(fu._normalize_date("2026-06-10"), "2026-06-10T00:00:00")
        self.assertEqual(fu._normalize_date(""), "")
        self.assertEqual(fu._normalize_date("not a date"), "")  # never guessed

    def test_normalize_japanese_era_dates(self):
        self.assertEqual(fu._normalize_japanese_date("令和8年6月10日"), "2026-06-10")
        self.assertEqual(fu._normalize_japanese_date("令和元年5月1日"), "2019-05-01")
        self.assertEqual(fu._normalize_japanese_date("平成31年4月30日"), "2019-04-30")
        self.assertEqual(fu._normalize_japanese_date("令和8年13月1日"), "")  # invalid month
        self.assertEqual(fu._normalize_japanese_date("2026年6月10日"), "")   # no era -> no guess


class TestStdlibFeedParser(unittest.TestCase):
    def test_rss2(self):
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
            "<item><title>改正について</title><link>https://example.go.jp/a</link>"
            "<description>概要</description><pubDate>Tue, 09 Jun 2026 17:00:00 +0900</pubDate></item>"
            "</channel></rss>"
        ).encode("utf-8")
        items = fu._parse_with_stdlib(xml)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "改正について")
        self.assertEqual(items[0]["link"], "https://example.go.jp/a")
        self.assertEqual(items[0]["published_iso"], "2026-06-09T08:00:00Z")

    def test_rdf_rss10(self):
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
            'xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<item><title>省令案の意見募集</title><link>https://example.go.jp/b</link>"
            "<dc:date>2026-06-09T17:00:00+09:00</dc:date></item></rdf:RDF>"
        ).encode("utf-8")
        items = fu._parse_with_stdlib(xml)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["link"], "https://example.go.jp/b")
        self.assertEqual(items[0]["published_iso"], "2026-06-09T08:00:00Z")

    def test_atom(self):
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">'
            '<entry><title>ニュースリリース</title><link rel="alternate" href="https://example.go.jp/c"/>'
            "<updated>2026-06-09T17:00:00+09:00</updated></entry></feed>"
        ).encode("utf-8")
        items = fu._parse_with_stdlib(xml)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["link"], "https://example.go.jp/c")


class TestHtmlParsers(unittest.TestCase):
    def test_ppc_information_parser(self):
        html_text = """
        <ul>
        <li>
        <time datetime="2026-06-01">2026年6月1日</time>
        <div class="news-label-wrap"><span class="news-label">お知らせ</span></div>
        <div class="news-text"><a href="/news/2026/0601.html">個人情報保護法ガイドラインを更新しました</a></div>
        </li>
        </ul>
        """.encode("utf-8")
        source = {"html_parser": "ppc_information", "url": "https://www.ppc.go.jp/information/"}
        items = fu.parse_html_source(html_text, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "個人情報保護法ガイドラインを更新しました")
        self.assertEqual(items[0]["link"], "https://www.ppc.go.jp/news/2026/0601.html")  # urljoin applied
        self.assertEqual(items[0]["published_iso"], "2026-06-01")
        self.assertEqual(items[0]["summary"], "お知らせ")

    def test_moe_press_parser(self):
        html_text = """
        <div class="p-press-release-list__section">
        <details class="p-press-release-list__block" open>
        <summary class="p-press-release-list__head">
        <span class="p-press-release-list__head__content">
        <span class="p-press-release-list__button"></span>
        <span class="p-press-release-list__heading">2026年06月11日発表</span>
        </span>
        </summary>
        <div class="p-press-release-list__body">
        <ul class="p-news-link c-news-link">
        <li class="c-news-link__item">
        <div class="c-news-link__item__col">
        <span class="p-news-link__tag c-tag c-tag--water-soil">水・土壌</span>
        </div>
        <div class="c-news-link__item__col">
        <a href="/press/press_05099.html" class="c-news-link__link">化学物質対策技術の実証事業について</a>
        </div>
        </li>
        <li class="c-news-link__item">
        <div class="c-news-link__item__col">
        <a href="/press/press_05100.html" class="c-news-link__link">タグなし項目の発表</a>
        </div>
        </li>
        </ul>
        </div>
        </details>
        </div>
        """.encode("utf-8")
        source = {"html_parser": "moe_press", "url": "https://www.env.go.jp/press/"}
        items = fu.parse_html_source(html_text, source)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "化学物質対策技術の実証事業について")
        self.assertEqual(items[0]["link"], "https://www.env.go.jp/press/press_05099.html")  # urljoin applied
        self.assertEqual(items[0]["published_iso"], "2026-06-11")  # from the date heading
        self.assertEqual(items[0]["summary"], "水・土壌")  # category tag
        self.assertEqual(items[1]["published_iso"], "2026-06-11")
        self.assertEqual(items[1]["summary"], "報道発表")  # default when no tag

    def test_mlit_press_parser(self):
        html_text = """
        <dl>
        <dt>2026年6月12日</dt>
        <dd><div class="text"><p><a href="/report/press/jidosha10_hh_000345.html">
        後退時の安全性を高めるライト、装備可能に！<br>
        ～道路運送車両の保安基準等の改正について～
        </a></p></div></dd>
        <dd><div class="text"><p><a href="/report/press/houdou202606.html">6月</a></p></div></dd>
        <dt>2026年6月11日</dt>
        <dd><div class="text"><p><a href="/report/press/tetsudo09_hh_000256.html">
        東急田園都市線 列車衝突事故を踏まえた緊急点検の結果（最終報告）
        </a></p></div></dd>
        </dl>
        """.encode("utf-8")
        source = {"html_parser": "mlit_press", "url": "https://www.mlit.go.jp/report/press/houdou202606.html"}
        items = fu.parse_html_source(html_text, source)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "後退時の安全性を高めるライト、装備可能に！ ～道路運送車両の保安基準等の改正について～")
        self.assertEqual(items[0]["link"], "https://www.mlit.go.jp/report/press/jidosha10_hh_000345.html")
        self.assertEqual(items[0]["published_iso"], "2026-06-12")
        self.assertEqual(items[0]["summary"], "報道発表")
        self.assertEqual(items[1]["published_iso"], "2026-06-11")

    def test_jftc_pressrelease_parser(self):
        html_text = """
        <ul class="norcor">
        <li><a href="/houdou/pressrelease/2026/jun/260610.html">(令和8年6月10日) 排除措置命令について</a></li>
        <li><a href="/houdou/pressrelease/2026/jun/260609.html">日付プレフィックスのない発表</a></li>
        </ul>
        """.encode("utf-8")
        source = {
            "html_parser": "jftc_pressrelease",
            "url": "https://www.jftc.go.jp/houdou/pressrelease/shuyohodoR8.html",
        }
        items = fu.parse_html_source(html_text, source)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "排除措置命令について")
        self.assertEqual(items[0]["published_iso"], "2026-06-10")  # era date converted
        self.assertEqual(items[0]["link"], "https://www.jftc.go.jp/houdou/pressrelease/2026/jun/260610.html")
        self.assertEqual(items[1]["published_iso"], "")  # no date -> never guessed
        self.assertEqual(items[1]["title"], "日付プレフィックスのない発表")


if __name__ == "__main__":
    unittest.main()
