"""Offline safety tests for scripts/fetch_updates.py (Stage 1).

No network access: these tests cover the SOURCES configuration, id/hash
stability, text normalization, date parsing, and the feed/HTML parsers using
inline fixtures only. Pipeline tests mock network and file writes.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_updates as fu  # noqa: E402

ALLOWED_SOURCE_TYPES = {
    "public_comment_rss", "regulator_rss", "ministry_rss", "agency_rss",
    "regulator_html", "ministry_html", "legislature_html", "agency_html", "public_comment_html",
    "pmda_safety_html", "public_comment_results_html", "court_html", "enforcement_html",
    "law_api",
}
ALLOWED_HTML_PARSERS = {
    "ppc_information", "jftc_pressrelease", "moe_press", "mlit_press", "meti_press_index",
    "nta_updates",
    "shugiin_current_bills",
    "jpx_public_comments", "jpx_rule_revisions", "pmda_safety_updates",
    "jsda_public_comments", "jsda_public_comment_results", "courts_recent_supreme",
    "sesc_current_year",
}


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

    def test_meti_source_config_uses_html_parser(self):
        by_name = {source["name"]: source for source in fu.SOURCES}
        meti = by_name["経済産業省 (METI) ニュースリリース"]
        self.assertEqual(meti["key"], "meti")  # key + name preserved for continuity
        self.assertEqual(meti["source_type"], "ministry_html")
        self.assertEqual(meti["html_parser"], "meti_press_index")
        self.assertEqual(meti["url"], "https://www.meti.go.jp/press/index.html")
        # The old failing Atom feed must be gone.
        self.assertNotIn("ml_index_release_atom", meti["url"])

    def test_nta_source_config_uses_current_shift_jis_page(self):
        nta = next(source for source in fu.SOURCES if source["key"] == "nta")
        self.assertEqual(nta["source_type"], "agency_html")
        self.assertEqual(nta["html_parser"], "nta_updates")
        self.assertEqual(nta["encoding"], "shift_jis")
        self.assertEqual(nta["history_days"], 550)
        self.assertEqual(nta["max_items"], 200)
        self.assertEqual(nta["url"], "https://www.nta.go.jp/information/news/news.htm")

    def test_legislative_expansion_sources_present(self):
        by_key = {source["key"]: source for source in fu.SOURCES}
        diet = by_key["shugiin-bills"]
        self.assertEqual(diet["html_parser"], "shugiin_current_bills")
        self.assertEqual(diet["encoding"], "shift_jis")
        self.assertFalse(diet["dedupe_by_url"])
        laws = by_key["egov-laws"]
        self.assertEqual(laws["entry_fetcher"], "egov_updated_laws")
        self.assertEqual(laws["lookback_days"], 7)
        self.assertFalse(laws["dedupe_by_url"])

    def test_jpx_pmda_expansion_sources_present(self):
        by_key = {source["key"]: source for source in fu.SOURCES}
        self.assertEqual(by_key["jpx-comments"]["html_parser"], "jpx_public_comments")
        self.assertEqual(by_key["jpx-comments"]["source_type"], "public_comment_html")
        self.assertEqual(by_key["jpx-rules"]["html_parser"], "jpx_rule_revisions")
        self.assertEqual(by_key["pmda"]["html_parser"], "pmda_safety_updates")
        self.assertEqual(by_key["pmda"]["history_days"], 550)
        self.assertFalse(by_key["pmda"]["dedupe_by_url"])

    def test_jsda_and_courts_expansion_sources_present(self):
        by_key = {source["key"]: source for source in fu.SOURCES}
        self.assertEqual(by_key["jsda-comments"]["html_parser"], "jsda_public_comments")
        self.assertEqual(by_key["jsda-comments"]["source_type"], "public_comment_html")
        self.assertEqual(by_key["jsda-results"]["html_parser"], "jsda_public_comment_results")
        self.assertEqual(by_key["jsda-results"]["source_type"], "public_comment_results_html")
        self.assertEqual(by_key["courts-supreme"]["html_parser"], "courts_recent_supreme")
        self.assertEqual(by_key["courts-supreme"]["source_type"], "court_html")
        self.assertIn("filter%5Brecent%5D=1", by_key["courts-supreme"]["url"])

    def test_sesc_source_uses_stable_index_and_current_year_fetcher(self):
        sesc = next(source for source in fu.SOURCES if source["key"] == "sesc")
        self.assertEqual(sesc["url"], "https://www.fsa.go.jp/sesc/news/news.html")
        self.assertEqual(sesc["entry_fetcher"], "sesc_current_year")
        self.assertEqual(sesc["html_parser"], "sesc_current_year")
        self.assertEqual(sesc["source_type"], "enforcement_html")

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

    def test_make_id_event_identity_tracks_status_without_changing_source_url(self):
        url = "https://www.shugiin.go.jp/internet/itdb_gian.nsf/html/gian/keika/ABC.htm"
        submitted = fu.make_id(url, "法律案", "Diet", "", "diet:閣法:221:1:審議中")
        enacted = fu.make_id(url, "法律案", "Diet", "", "diet:閣法:221:1:成立")
        self.assertNotEqual(submitted, enacted)
        self.assertEqual(
            submitted,
            fu.make_id(url, "変更タイトル", "Diet", "2026-01-01", "diet:閣法:221:1:審議中"),
        )

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

    def test_build_item_stores_structured_egov_comment_deadline(self):
        entry = {
            "title": "省令案に関する意見募集について",
            "link": "https://public-comment.e-gov.go.jp/servlet/Public?id=1",
            "summary": (
                "案の公示日：2026/07/01 "
                "受付締切日時：2026/07/18 23:59 "
                "カテゴリー：環境保全 問合せ先：担当課"
            ),
            "published_iso": "2026-07-01T00:00:00Z",
        }
        source = {
            "name": "e-Gov Public Comment",
            "source_type": "public_comment_rss",
            "source_language": "ja",
        }

        item = fu.build_item(entry, source, "2026-07-01T01:00:00Z")

        self.assertEqual(item["comment_deadline"], "2026-07-18T23:59:00+09:00")

    def test_build_item_does_not_extract_deadline_from_other_source_or_prose(self):
        entry = {
            "title": "省令案について",
            "link": "https://example.go.jp/item",
            "summary": "回答期限は2026年7月18日です。",
            "published_iso": "2026-07-01",
        }
        other_source = {
            "name": "Other Official Source",
            "source_type": "ministry_rss",
            "source_language": "ja",
        }

        item = fu.build_item(entry, other_source, "2026-07-01T01:00:00Z")

        self.assertNotIn("comment_deadline", item)


class TestFirstSeenAtRawMerge(unittest.TestCase):
    def test_run_marks_only_actually_appended_items_as_first_seen(self):
        source = {
            "key": "egov",
            "name": "Test Source",
            "url": "https://example.go.jp/feed.xml",
            "source_type": "ministry_rss",
            "source_language": "ja",
        }
        legacy_entry = {
            "title": "Legacy title",
            "link": "https://example.go.jp/a",
            "summary": "",
            "published_iso": "2026-06-01",
        }
        seen_entry = {
            "title": "Previously detected title",
            "link": "https://example.go.jp/b",
            "summary": "",
            "published_iso": "2026-06-02",
        }
        new_entry = {
            "title": "New detected title",
            "link": "https://example.go.jp/c",
            "summary": "",
            "published_iso": "2026-06-18",
        }
        legacy_item = fu.build_item(legacy_entry, source, "2026-06-01T00:00:00Z")
        seen_item = fu.build_item(seen_entry, source, "2026-06-02T00:00:00Z")
        seen_item["first_seen_at"] = "2026-06-03"
        saved_raw = {}
        saved_report = {}

        def capture_raw(_path, data):
            saved_raw["data"] = data

        def capture_report(_path, data):
            saved_report["data"] = data

        with mock.patch.object(fu, "SOURCES", [source]), \
                mock.patch.object(fu, "load_existing", return_value=[legacy_item, seen_item]), \
                mock.patch.object(fu, "save_json", side_effect=capture_raw), \
                mock.patch.object(fu, "save_json_document", side_effect=capture_report), \
                mock.patch.object(fu, "http_get", return_value=b"fixture"), \
                mock.patch.object(
                    fu,
                    "parse_source_entries",
                    return_value=[legacy_entry, seen_entry, new_entry, new_entry],
                ):
            exit_code = fu.run(timeout=1, dry_run=False, first_seen_date="2026-06-18")

        self.assertEqual(exit_code, 0)
        merged = saved_raw["data"]
        self.assertEqual(len(merged), 3)
        self.assertNotIn("first_seen_at", merged[0])
        self.assertEqual(merged[1]["first_seen_at"], "2026-06-03")
        self.assertEqual(merged[2]["first_seen_at"], "2026-06-18")

        report_row = saved_report["data"]["sources"][0]
        self.assertEqual(report_row["fetched_count"], 3)
        self.assertEqual(report_row["new_count"], 1)
        self.assertEqual(report_row["latest_published_at"], "2026-06-18")


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
    def test_sesc_year_resolver_requires_exact_current_year(self):
        index = """
        <ul><li><a href="/sesc/news/c_2026/c_2026.html">令和8年（2026年）の情報</a></li>
        <li><a href="/sesc/news/c_2025/c_2025.html">令和7年（2025年）の情報</a></li></ul>
        """
        self.assertEqual(
            fu._find_sesc_year_url(index, "https://www.fsa.go.jp/sesc/news/news.html", "2026"),
            "https://www.fsa.go.jp/sesc/news/c_2026/c_2026.html",
        )
        self.assertEqual(fu._find_sesc_year_url(index, "https://www.fsa.go.jp/", "2027"), "")

    def test_sesc_parser_keeps_enforcement_and_public_comment_but_drops_reports(self):
        html_text = """
        <ul>
        <li>令和8年6月26日　<a href="/sesc/news/c_2026/2026/20260626-2.html">
        内部者取引に対する課徴金納付命令の勧告について</a></li>
        <li>令和8年7月1日　<a href="/sesc/news/c_2026/2026/20260701-1.html">
        「証券モニタリングに関する基本指針」の一部改正案に対するパブリックコメントの結果について</a></li>
        <li>令和8年7月31日　<a href="/sesc/news/c_2026/2026/20260731-2.html">
        「令和8事務年度 証券モニタリング基本方針」について</a></li>
        <li>令和8年6月23日　<a href="/sesc/reports/n_2025/n_2025.html">
        令和7年度証券取引等監視委員会の活動状況の公表について</a></li>
        <li>令和8年6月23日　<a href="/sesc/jirei/torichou/20260623.html">
        金融商品取引法における課徴金事例集の公表について</a></li>
        </ul>
        """.encode("utf-8")
        source = {
            "html_parser": "sesc_current_year",
            "url": "https://www.fsa.go.jp/sesc/news/c_2026/c_2026.html",
        }
        items = fu.parse_html_source(html_text, source)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["published_iso"], "2026-06-26")
        self.assertEqual(items[0]["stage_hint"], "Enforcement Action")
        self.assertNotIn("stage_hint", items[1])
        self.assertIn("20260701-1.html", items[1]["link"])
        self.assertNotIn("stage_hint", items[2])
        self.assertIn("20260731-2.html", items[2]["link"])

    def test_jsda_public_comment_parser_carries_structured_period(self):
        html_text = """
        <table><tr><th>案　件　名</th><th>募集期間</th></tr>
        <tr><td><a href="./files/20260715_rule.pdf">「店頭有価証券規則」の一部改正案について</a></td>
        <td>2026年7月15日 ～ 2026年8月14日</td></tr></table>
        """.encode("utf-8")
        source = {
            "html_parser": "jsda_public_comments",
            "url": "https://www.jsda.or.jp/about/public/bosyu/index.html",
        }
        items = fu.parse_html_source(html_text, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published_iso"], "2026-07-15")
        self.assertEqual(items[0]["comment_deadline"], "2026-08-14")
        self.assertEqual(
            items[0]["link"],
            "https://www.jsda.or.jp/about/public/bosyu/files/20260715_rule.pdf",
        )

    def test_jsda_public_comment_results_parser_sets_controlled_stage(self):
        html_text = """
        <table><tr><th>公表日</th><th>案件名</th><th>募集期間</th></tr>
        <tr><td>2026年 8月1日</td><td>「店頭有価証券規則」の一部改正について
        【資料】<a href="./files/20260801_result.pdf">パブリックコメントの結果について</a></td>
        <td>2026年7月1日～2026年7月31日</td></tr></table>
        """.encode("utf-8")
        source = {
            "html_parser": "jsda_public_comment_results",
            "url": "https://www.jsda.or.jp/about/public/kekka/index.html",
        }
        items = fu.parse_html_source(html_text, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published_iso"], "2026-08-01")
        self.assertEqual(items[0]["stage_hint"], "Public Comment Results Published")
        self.assertEqual(
            items[0]["link"],
            "https://www.jsda.or.jp/about/public/kekka/files/20260801_result.pdf",
        )

    def test_courts_recent_supreme_parser_uses_detail_page_and_decision_date(self):
        html_text = """
        <table class="search-result-table"><tr>
        <th><a href="./../96798/detail2/index.html">最高裁判例</a></th>
        <td><p>令和6(オ)720 損害賠償請求本訴、同反訴事件</p>
        <p>令和8年7月16日 最高裁判所第一小法廷 判決 破棄差戻</p></td>
        <td><a href="./../../assets/hanrei/hanrei-pdf-96798.pdf">全文</a></td>
        </tr></table>
        """.encode("utf-8")
        source = {
            "html_parser": "courts_recent_supreme",
            "url": "https://www.courts.go.jp/hanrei/search2/index.html?courtCaseType=1&filter%5Brecent%5D=1",
        }
        items = fu.parse_html_source(html_text, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published_iso"], "2026-07-16")
        self.assertEqual(items[0]["stage_hint"], "Court Decision")
        self.assertEqual(items[0]["link"], "https://www.courts.go.jp/hanrei/96798/detail2/index.html")

    def test_jpx_public_comment_parser_carries_structured_deadline(self):
        html_text = """
        <table><tr><th>募集開始日</th><th>募集終了日</th><th>法人</th><th>案件名</th></tr>
        <tr><td>2026/07/22</td><td>2026/08/21</td><td>東証</td>
        <td><a href="/rules-participants/public-comment/detail/d6/20260722-01.html">
        ベンチャーファンドの上場制度の見直しについて</a></td></tr></table>
        """.encode("utf-8")
        source = {
            "html_parser": "jpx_public_comments",
            "url": "https://www.jpx.co.jp/rules-participants/public-comment/index.html",
        }
        items = fu.parse_html_source(html_text, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published_iso"], "2026-07-22")
        self.assertEqual(items[0]["comment_deadline"], "2026-08-21")
        self.assertEqual(
            items[0]["link"],
            "https://www.jpx.co.jp/rules-participants/public-comment/detail/d6/20260722-01.html",
        )

        configured = next(s for s in fu.SOURCES if s["key"] == "jpx-comments")
        raw = fu.build_item(items[0], configured, "2026-08-01T00:00:00Z")
        self.assertEqual(raw["comment_deadline"], "2026-08-21")

    def test_jpx_rule_revisions_parser_prefers_official_overview_pdf(self):
        html_text = """
        <table><tr><th>公表日</th><th>内容</th><th>概要</th><th>新旧<br>対照表</th></tr>
        <tr><td>2026/07/21</td><td>有価証券上場規程の一部改正について</td>
        <td><a href="/rules/revise/overview.pdf"><img alt="PDF"></a></td>
        <td><a href="/rules/revise/tracked.pdf"><img alt="PDF"></a></td></tr></table>
        """.encode("utf-8")
        source = {
            "html_parser": "jpx_rule_revisions",
            "url": "https://www.jpx.co.jp/rules-participants/rules/revise/index.html",
        }
        items = fu.parse_html_source(html_text, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published_iso"], "2026-07-21")
        self.assertEqual(items[0]["link"], "https://www.jpx.co.jp/rules/revise/overview.pdf")

    def test_pmda_parser_keeps_safety_rows_and_excludes_review_events(self):
        html_text = """
        <ul>
          <li><a href="/safety/revision/1.html"><p class="date">2026年7月14日</p>
          <p class="category category01">安全</p><p class="title">使用上の注意の改訂指示通知を掲載しました</p></a></li>
          <li><a href="/review/event/1.html"><p class="date">2026年7月10日</p>
          <p class="category category02">審査</p><p class="title">PMDAシンポジウムを開催します</p></a></li>
          <li><a href="/safety/old/1.html"><p class="date">2020年1月1日</p>
          <p class="category category01">安全</p><p class="title">過去の安全対策情報</p></a></li>
        </ul>
        """.encode("utf-8")
        source = {
            "html_parser": "pmda_safety_updates",
            "url": "https://www.pmda.go.jp/safety/0001.html",
        }
        items = fu.parse_html_source(html_text, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published_iso"], "2026-07-14")
        self.assertEqual(items[0]["link"], "https://www.pmda.go.jp/safety/revision/1.html")
        self.assertIn("pmda:2026-07-14:", items[0]["identity_key"])
        self.assertEqual(items[0]["stage_hint"], "Government Announcement")

    def test_shugiin_current_bills_parser_preserves_official_url_and_status_event(self):
        html_text = """
        <table>
        <caption class="txt04b">閣法の一覧</caption>
        <tr><th>提出回次</th><th>番号</th><th>議案件名</th><th>審議状況</th></tr>
        <tr valign="top">
          <td><span>221</span></td><td><span>7</span></td>
          <td><span>金融機能の強化のための特別措置法等の一部を改正する法律案</span></td>
          <td><span>成立</span></td>
          <td><a href="./keika/ABC123.htm" title="経過">経過</a></td>
          <td><a href="./honbun/g22109007.htm" title="本文">本文</a></td>
        </tr>
        </table>
        """.encode("utf-8")
        source = {
            "html_parser": "shugiin_current_bills",
            "url": "https://www.shugiin.go.jp/internet/itdb_gian.nsf/html/gian/menu.htm",
        }
        items = fu.parse_html_source(html_text, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["stage_hint"], "Enacted")
        self.assertEqual(items[0]["published_iso"], "")
        self.assertEqual(items[0]["identity_key"], "diet:閣法:221:7:成立")
        self.assertEqual(
            items[0]["link"],
            "https://www.shugiin.go.jp/internet/itdb_gian.nsf/html/gian/keika/ABC123.htm",
        )

    def test_shugiin_terminal_unenacted_bill_is_not_marked_currently_submitted(self):
        self.assertEqual(fu._diet_stage_hint("未了"), "Government Announcement")
        self.assertEqual(fu._diet_stage_hint("衆議院で審議中"), "Bill Submitted")

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

    def test_meti_press_index_parser(self):
        html_text = """
        <main>
        <h2>最新ニュースリリース</h2>
        <div class="news">
          <h3>2026年6月23日</h3>
          <ul>
            <li><a href="/press/2026/06/20260623001/20260623001.html">中小企業向け支援策の公募を開始します</a></li>
            <li><a href="/press/2026/06/20260623002/20260623002.html">エネルギー基本計画改定案に関する意見公募について</a></li>
          </ul>
          <h3>2026年6月20日</h3>
          <ul>
            <li><a href="/press/2026/06/20260620005/20260620005.html">産業構造審議会の報告書を取りまとめました</a></li>
          </ul>
        </div>
        <nav>
          <a href="/press/index.html">バックナンバー</a>
          <a href="/">トップページ</a>
        </nav>
        </main>
        """.encode("utf-8")
        source = {"html_parser": "meti_press_index", "url": "https://www.meti.go.jp/press/index.html"}
        items = fu.parse_html_source(html_text, source)

        self.assertEqual(len(items), 3)  # 3 press links; nav / back-number links excluded (not 0)
        self.assertEqual(items[0]["title"], "中小企業向け支援策の公募を開始します")
        self.assertEqual(
            items[0]["link"],
            "https://www.meti.go.jp/press/2026/06/20260623001/20260623001.html",  # urljoin applied
        )
        self.assertEqual(items[0]["published_iso"], "2026-06-23")  # 2026年6月23日 -> ISO
        self.assertEqual(items[1]["published_iso"], "2026-06-23")
        self.assertEqual(items[2]["published_iso"], "2026-06-20")  # second date group
        for item in items:
            self.assertTrue(item["title"])           # non-empty title
            self.assertTrue(item["published_iso"])   # non-empty date
            self.assertTrue(item["link"].startswith("https://www.meti.go.jp/press/"))

    def test_meti_press_index_parser_handles_time_element(self):
        # Per-item <time datetime> layout (robust to no date heading).
        html_text = """
        <ul>
        <li><time datetime="2026-06-18">2026年6月18日</time>
        <a href="/press/2026/06/20260618010/20260618010.html">省エネ法に基づく定期報告について</a></li>
        </ul>
        """.encode("utf-8")
        source = {"html_parser": "meti_press_index", "url": "https://www.meti.go.jp/press/index.html"}
        items = fu.parse_html_source(html_text, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published_iso"], "2026-06-18")

    def test_meti_build_item_from_parser_output(self):
        # Parser output flows through build_item with the preserved source identity.
        html_text = (
            "<h3>2026年6月23日</h3><ul><li>"
            '<a href="/press/2026/06/20260623001/20260623001.html">支援策の公募について</a>'
            "</li></ul>"
        ).encode("utf-8")
        meti = next(s for s in fu.SOURCES if s["key"] == "meti")
        entries = fu.parse_html_source(html_text, meti)
        self.assertTrue(entries)  # parser does not return zero items
        item = fu.build_item(entries[0], meti, "2026-06-23T00:00:00Z")
        self.assertEqual(item["source_name"], "経済産業省 (METI) ニュースリリース")
        self.assertEqual(item["source_type"], "ministry_html")
        self.assertTrue(item["title_ja"])
        self.assertEqual(item["published_at"], "2026-06-23")
        self.assertTrue(item["source_url"].startswith("https://www.meti.go.jp/press/"))

    def test_nta_updates_parser_keeps_current_tables_and_recent_dates_only(self):
        html_text = """
        <table class="index_news">
          <tr><th>令和8年8月5日</th><td><a href="/information/other/data.htm">税制改正に関する情報</a></td></tr>
          <tr><th>令和8年8月4日</th><td><a href="/laws/tsutatsu/kobetsu/shotoku/260804.htm">所得税法基本通達の一部改正について</a></td></tr>
          <tr><th>令和8年8月4日</th><td><a href="/information/other/data.htm">重複リンク</a></td></tr>
          <tr><th>令和4年1月1日</th><td><a href="/old.htm">古い情報</a></td></tr>
          <tr><th>令和99年1月1日</th><td><a href="/future.htm">未来の情報</a></td></tr>
          <tr><th>日付なし</th><td><a href="/invalid.htm">不正な日付</a></td></tr>
        </table>
        <table class="index_news">
          <tr><th>令和8年8月6日</th><td><a href="laws/tsutatsu/260806.htm">法令解釈通達の改正</a></td></tr>
        </table>
        <table class="index_news info-item">
          <tr><th>令和8年8月7日</th><td><a href="/archive.htm">折り畳みアーカイブ</a></td></tr>
        </table>
        """.encode("shift_jis")
        source = {
            "html_parser": "nta_updates",
            "url": "https://www.nta.go.jp/information/news/news.htm",
            "encoding": "shift_jis",
            "history_days": 550,
        }

        with mock.patch.object(fu, "current_jst_date", return_value="2026-08-07"):
            items = fu.parse_html_source(html_text, source)

        self.assertEqual([item["published_iso"] for item in items], ["2026-08-06", "2026-08-05", "2026-08-04"])
        self.assertEqual(items[0]["title"], "法令解釈通達の改正")
        self.assertEqual(items[0]["link"], "https://www.nta.go.jp/information/news/laws/tsutatsu/260806.htm")
        self.assertEqual(items[0]["summary"], "国税庁新着情報・通達等")
        self.assertNotIn("折り畳みアーカイブ", [item["title"] for item in items])
        self.assertNotIn("古い情報", [item["title"] for item in items])


class TestEgovUpdatedLawsAdapter(unittest.TestCase):
    def test_parser_uses_structured_promulgation_and_enforcement_dates(self):
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <DataRoot><Result><Code>0</Code><Message/></Result><ApplData><Date>20260805</Date>
        <LawNameListInfo>
          <LawTypeName>\xe6\xb3\x95\xe5\xbe\x8b</LawTypeName><LawNo>\xe6\x98\xad\xe5\x92\x8c\xe4\xb8\x80\xe5\xb9\xb4\xe6\xb3\x95\xe5\xbe\x8b\xe7\xac\xac\xe4\xb8\x80\xe5\x8f\xb7</LawNo>
          <LawName>\xe4\xbc\x9a\xe7\xa4\xbe\xe6\xb3\x95</LawName><PromulgationDate>19260426</PromulgationDate>
          <AmendName>\xe4\xbc\x9a\xe7\xa4\xbe\xe6\xb3\x95\xe3\x81\xae\xe4\xb8\x80\xe9\x83\xa8\xe3\x82\x92\xe6\x94\xb9\xe6\xad\xa3\xe3\x81\x99\xe3\x82\x8b\xe6\xb3\x95\xe5\xbe\x8b</AmendName>
          <AmendNo>\xe4\xbb\xa4\xe5\x92\x8c\xe5\x85\xab\xe5\xb9\xb4\xe6\xb3\x95\xe5\xbe\x8b\xe7\xac\xac\xe4\xba\x8c\xe5\x8f\xb7</AmendNo><AmendPromulgationDate>20260723</AmendPromulgationDate>
          <EnforcementDate>20270101</EnforcementDate><LawId>001</LawId>
          <LawUrl>https://elaws.e-gov.go.jp/document?lawid=001_20270101</LawUrl>
        </LawNameListInfo></ApplData></DataRoot>"""
        items = fu._parse_egov_updated_laws_xml(xml, "20260805", "2026-08-06")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "会社法の一部を改正する法律")
        self.assertEqual(items[0]["published_iso"], "2026-07-23")
        self.assertEqual(items[0]["stage_hint"], "Scheduled to Take Effect")
        self.assertEqual(
            items[0]["identity_key"],
            "law:https://elaws.e-gov.go.jp/document?lawid=001_20270101:Scheduled to Take Effect",
        )
        self.assertIn("施行日: 2027-01-01", items[0]["summary"])

    def test_update_dates_exclude_today_and_are_newest_first(self):
        self.assertEqual(
            fu._egov_update_dates(3, "2026-08-06"),
            ["20260805", "20260804", "20260803"],
        )

    def test_adapter_skips_no_update_404_dates(self):
        source = next(source for source in fu.SOURCES if source["key"] == "egov-laws")
        empty_xml = b"<DataRoot><Result><Code>0</Code></Result><ApplData><Date>20260805</Date></ApplData></DataRoot>"

        class NotFound(Exception):
            pass

        not_found = NotFound("404")
        with mock.patch.object(fu, "_egov_update_dates", return_value=["20260805", "20260804"]), \
                mock.patch.object(fu, "_http_status_from_exception", side_effect=lambda exc: 404), \
                mock.patch.object(fu, "http_get", side_effect=[empty_xml, not_found]):
            entries, effective_url = fu.fetch_source_entries(source, 1)
        self.assertEqual(entries, [])
        self.assertEqual(effective_url, source["url"])


class TestSescCurrentYearAdapter(unittest.TestCase):
    def test_adapter_resolves_and_fetches_exact_current_year_page(self):
        source = next(source for source in fu.SOURCES if source["key"] == "sesc")
        index_html = b'<a href="/sesc/news/c_2026/c_2026.html">2026</a>'
        year_html = """
        <ul><li>令和8年6月12日　<a href="/sesc/news/c_2026/2026/20260612-1.html">
        虚偽記載に係る課徴金納付命令勧告について</a></li></ul>
        """.encode("utf-8")
        with mock.patch.object(fu, "current_jst_date", return_value="2026-08-06"), \
                mock.patch.object(fu, "http_get", side_effect=[index_html, year_html]) as get:
            entries, effective_url = fu.fetch_source_entries(source, 1)
        self.assertEqual(len(entries), 1)
        self.assertEqual(effective_url, "https://www.fsa.go.jp/sesc/news/c_2026/c_2026.html")
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].args[0], source["url"])
        self.assertEqual(get.call_args_list[1].args[0], effective_url)


class TestHttpFetchRobustness(unittest.TestCase):
    def test_meti_source_has_robust_fetch_settings(self):
        meti = next(s for s in fu.SOURCES if s["key"] == "meti")
        self.assertEqual(meti["url"], "https://www.meti.go.jp/press/index.html")
        self.assertEqual(meti["html_parser"], "meti_press_index")
        # Escalating read timeouts, kept modest now that METI is warning-only.
        self.assertEqual(meti["timeouts"], (20, 35, 50))
        self.assertEqual(meti["backoff"], (3, 6))
        self.assertTrue(meti["urllib_fallback"])
        self.assertIn("Mozilla", meti["user_agent"])  # browser-like (still identifying) UA
        # METI is a warning-only / non-gating source.
        self.assertFalse(meti["gate_required"])
        self.assertEqual(meti.get("health_severity"), "warning")

    def test_default_timeout_is_unchanged_for_other_sources(self):
        self.assertEqual(fu.DEFAULT_TIMEOUT_SECONDS, 20)
        fsa = next(s for s in fu.SOURCES if s["key"] == "fsa")
        self.assertNotIn("timeouts", fsa)  # other sources keep the single default timeout

    def test_http_get_retries_transient_then_succeeds(self):
        calls = {"n": 0}

        def fake_once(url, timeout, prefer_urllib, accept_html, user_agent):
            calls["n"] += 1
            if calls["n"] < 3:
                raise TimeoutError("read timed out")
            return b"OK"

        with mock.patch.object(fu, "_http_get_once", side_effect=fake_once), \
                mock.patch.object(fu.time, "sleep", return_value=None):
            content = fu.http_get("https://x", 20, timeouts=(1, 1, 1), backoff=(0, 0, 0))
        self.assertEqual(content, b"OK")
        self.assertEqual(calls["n"], 3)

    def test_http_get_uses_escalating_per_attempt_timeouts(self):
        used: list[int] = []

        def fake_once(url, timeout, prefer_urllib, accept_html, user_agent):
            used.append(timeout)
            if len(used) < 3:
                raise TimeoutError("t")
            return b"OK"

        with mock.patch.object(fu, "_http_get_once", side_effect=fake_once), \
                mock.patch.object(fu.time, "sleep", return_value=None):
            fu.http_get("https://x", 20, timeouts=(30, 45, 60), backoff=(0, 0, 0))
        self.assertEqual(used, [30, 45, 60])

    def test_http_get_falls_back_to_urllib_after_requests_timeout(self):
        attempts: list[bool] = []

        def fake_once(url, timeout, prefer_urllib, accept_html, user_agent):
            attempts.append(prefer_urllib)
            if not prefer_urllib:
                raise TimeoutError("requests read timeout")  # requests path times out
            return b"<html>via urllib</html>"  # urllib path succeeds

        with mock.patch.object(fu, "_http_get_once", side_effect=fake_once), \
                mock.patch.object(fu.time, "sleep", return_value=None):
            content = fu.http_get(
                "https://www.meti.go.jp/press/index.html", 20,
                timeouts=(1, 1, 1), backoff=(0, 0, 0), urllib_fallback=True,
            )
        self.assertEqual(content, b"<html>via urllib</html>")
        self.assertFalse(attempts[0])  # first attempt: requests
        self.assertTrue(attempts[1])   # second attempt: switched to urllib

    def test_http_get_does_not_switch_to_urllib_when_disabled(self):
        attempts: list[bool] = []

        def fake_once(url, timeout, prefer_urllib, accept_html, user_agent):
            attempts.append(prefer_urllib)
            raise TimeoutError("t")

        with mock.patch.object(fu, "_http_get_once", side_effect=fake_once), \
                mock.patch.object(fu.time, "sleep", return_value=None):
            with self.assertRaises(TimeoutError):
                fu.http_get("https://x", 20, timeouts=(1, 1, 1), backoff=(0, 0, 0), urllib_fallback=False)
        self.assertTrue(all(used is False for used in attempts))  # never switched

    def test_run_meti_urllib_fallback_is_reported_success(self):
        meti = next(s for s in fu.SOURCES if s["key"] == "meti")
        fixture = (
            "<h3>2026年6月23日</h3><ul><li>"
            '<a href="/press/2026/06/20260623001/20260623001.html">支援策の公募について</a>'
            "</li></ul>"
        ).encode("utf-8")

        def fake_once(url, timeout, prefer_urllib, accept_html, user_agent):
            if not prefer_urllib:
                raise TimeoutError("requests read timeout")  # requests fails
            return fixture  # urllib succeeds

        saved_report = {}
        with mock.patch.object(fu, "SOURCES", [meti]), \
                mock.patch.object(fu, "load_existing", return_value=[]), \
                mock.patch.object(fu, "save_json", side_effect=lambda _p, _d: None), \
                mock.patch.object(fu, "save_json_document", side_effect=lambda _p, d: saved_report.setdefault("data", d)), \
                mock.patch.object(fu, "_http_get_once", side_effect=fake_once), \
                mock.patch.object(fu.time, "sleep", return_value=None):
            rc = fu.run(timeout=1, dry_run=False, first_seen_date="2026-06-23")

        self.assertEqual(rc, 0)
        row = next(r for r in saved_report["data"]["sources"] if r["source_key"] == "meti")
        self.assertEqual(row["status"], "success")  # urllib fallback -> METI counts as success
        self.assertGreater(row["fetched_count"], 0)


if __name__ == "__main__":
    unittest.main()
