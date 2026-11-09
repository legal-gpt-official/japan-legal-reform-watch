"""Offline tests for bounded primary-source extraction and summary-cache isolation."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import official_source_evidence as evidence  # noqa: E402
import summarize_updates as su  # noqa: E402


class TestURLSafety(unittest.TestCase):
    def test_official_urls_only(self):
        for url in (
            "https://www.meti.go.jp/press/2026.html",
            "https://public-comment.e-gov.go.jp/pcm/detail?id=1",
            "https://www.caa.go.jp/notice/entry/1/",
            "https://www.jpx.co.jp/rules-participants/a.pdf",
        ):
            self.assertTrue(evidence.safe_official_url(url), url)
        for url in (
            "http://www.meti.go.jp/a",
            "https://evil.go.jp.example.com/a",
            "https://127.0.0.1/x", "https://localhost/a",
            "https://www.meti.go.jp@evil.example.com/a",
            "https://www.meti.go.jp:444/hidden",
            "file:///etc/passwd", "https://www.meti.go.jp.evil.test/",
        ):
            self.assertFalse(evidence.safe_official_url(url), url)

    def test_redirect_to_arbitrary_host_is_rejected_before_second_request(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status_code = 302
        response.headers = {"Location": "https://evil.example.com/private"}
        with mock.patch.object(evidence.requests, "get", return_value=response) as getter:
            with self.assertRaisesRegex(ValueError, "non-official"):
                evidence.fetch_bounded("https://www.meti.go.jp/press/a.html", 100)
        getter.assert_called_once()

    def test_response_beyond_limit_is_rejected(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.headers = {"Content-Type": "text/html"}
        response.iter_content.return_value = [b"1234", b"567890"]
        with mock.patch.object(evidence.requests, "get", return_value=response):
            with self.assertRaisesRegex(ValueError, "size limit"):
                evidence.fetch_bounded("https://www.meti.go.jp/a", 5)


class TestExtract(unittest.TestCase):
    def test_html_text_omits_navigation_and_finds_relevant_pdf(self):
        html = """<html><body><nav>UNRELATED MENU</nav><main>
        <h1>制度の改正に関するお知らせ</h1>
        <p>本改正では、申請に必要となる手続を変更するものです。</p>
        <p>2027年1月1日から新しい届出様式を使用します。</p>
        <a href="/files/old-new.pdf">新旧対照表（PDF）</a>
        <a href="/files/random.pdf">交通案内</a>
        </main></body></html>"""
        text, pdfs = evidence.html_documents(html.encode(), "https://www.meti.go.jp/press/a.html")
        self.assertIn("申請に必要となる手続", text)
        self.assertNotIn("UNRELATED MENU", text)
        self.assertEqual(pdfs, ["https://www.meti.go.jp/files/old-new.pdf"])

    def test_navigation_inside_main_does_not_consume_source_excerpt(self):
        navigation = "".join(
            f'<li><a href="/menu/{n}">各種制度の紹介や手続についての案内{n}</a></li>'
            for n in range(200)
        )
        substantive = ("今回の省令案では申請書の提出先を地方局から本省へ変更し、"
                       "施行期日を2027年4月1日とする。" * 9)
        html = (f"<html><body><main><ul>{navigation}</ul>"
                f"<h1>省令案の概要</h1><p>{substantive}</p>"
                f'<a href="/amendment.pdf">新旧対照表</a>'
                f"</main></body></html>")
        excerpt, links = evidence.html_documents(
            html.encode("utf-8"), "https://www.meti.go.jp/press/test.html"
        )
        self.assertIn("提出先を地方局から本省へ変更", excerpt)
        self.assertNotIn("各種制度の紹介", excerpt)
        self.assertEqual(links, ["https://www.meti.go.jp/amendment.pdf"])

    def test_keep_substantive_paragraph_with_inline_source_link(self):
        html = ('<html><main><p>この改正では'
                '<a href="/law">対象となる事業者</a>の届出期限を変更します。</p>'
                '</main></html>')
        excerpt, _ = evidence.html_documents(
            html.encode("utf-8"), "https://www.meti.go.jp/press/test.html"
        )
        self.assertIn("届出期限を変更", excerpt)

    def test_egov_download_links_without_pdf_suffix_are_selected(self):
        html = """<html><main>
        <h1>電気工事士法施行令の一部を改正する政令案</h1>
        <p>施行令を改正する意見募集について、改正概要を公表します。</p>
        <a href="/pcm/download?seqNo=0000323737">意見公募要領</a>
        <a href="/pcm/download?seqNo=0000323739">概要</a>
        <a href="/pcm/download?seqNo=0000323740">案文・理由</a>
        <a href="/pcm/download?seqNo=0000323741">新旧対照表</a>
        </main></html>"""
        url = "https://public-comment.e-gov.go.jp/servlet/Public?id=595320122"
        excerpt, pdfs = evidence.html_documents(html.encode(), url)
        self.assertIn("施行令を改正", excerpt)
        self.assertEqual(pdfs, [
            "https://public-comment.e-gov.go.jp/pcm/download?seqNo=0000323739",
            "https://public-comment.e-gov.go.jp/pcm/download?seqNo=0000323741",
        ])
        self.assertTrue(evidence.is_pdf_attachment_url(pdfs[0]))
        self.assertFalse(evidence.is_pdf_attachment_url(
            "https://public-comment.e-gov.go.jp/servlet/Public?id=595320122"))
        self.assertFalse(evidence.is_pdf_attachment_url(
            "https://evil.example.com/pcm/download?seqNo=123"))

    def test_egov_pdf_mime_is_checked_not_assumed(self):
        url = "https://public-comment.e-gov.go.jp/pcm/download?seqNo=0000323739"
        sample = "この政令案では受験手数料を14400円から17000円に改めます。" * 9
        with mock.patch.object(evidence, "fetch_bounded", return_value=(
                b"%PDF test", "application/pdf", url)) as fetch, \
             mock.patch.object(evidence, "pdf_text", return_value=sample):
            docs = evidence.gather(url)
        fetch.assert_called_once_with(url, evidence.MAX_PDF_BYTES)
        self.assertEqual(docs[0]["format"], "pdf")
        self.assertIn("17000円", docs[0]["excerpt"])

    def test_large_consumer_affairs_order_pdf_has_bounded_exception(self):
        order = "https://www.caa.go.jp/notice/assets/representation_cms210_261106_01.pdf"
        self.assertGreater(evidence.pdf_fetch_limit(order), 14_700_000)
        self.assertEqual(evidence.pdf_fetch_limit("https://www.caa.go.jp/other.pdf"),
                         evidence.MAX_PDF_BYTES)
        self.assertEqual(evidence.pdf_fetch_limit("https://evil.example.com/notice/assets/representation_1.pdf"),
                         evidence.MAX_PDF_BYTES)

    def test_gather_keeps_page_and_pdf_provenance(self):
        html = """<html><main><h1>省令案</h1><p>""".encode("utf-8")
        html += ("手続の対象が変わり、施行日は2027年4月1日です。" * 15).encode()
        html += """</p><a href="/a.pdf">新旧対照表</a></main></html>""".encode("utf-8")
        async_values = [
            (html, "text/html; charset=utf-8", "https://www.caa.go.jp/a"),
            (b"%PDF FAKED", "application/pdf", "https://www.caa.go.jp/a.pdf"),
        ]
        with mock.patch.object(evidence, "fetch_bounded", side_effect=async_values), \
             mock.patch.object(evidence, "pdf_text", return_value="改正前の手続では届出が必要です。" * 22):
            docs = evidence.gather("https://www.caa.go.jp/a")
        self.assertEqual(len(docs), 2)
        self.assertEqual(docs[0]["format"], "html")
        self.assertEqual(docs[1]["format"], "pdf")
        self.assertIn("施行日", docs[0]["excerpt"])
        self.assertTrue(all(d["url"].startswith("https://www.caa.go.jp/") for d in docs))

    def test_long_pdf_keeps_operational_amendment_beyond_contents_page(self):
        cover = ("改正案の概要。新たな対象事業者の基準は検討中です。" * 45)
        contents = ("目次 各章の見出し 第１条 第２条 第３条 " * 85)
        change = ("改正内容：申請の期限を2027年4月1日に変更する。"
                  "施行期日及び経過措置を別途規定する。" * 40)
        appendix = ("届出義務の対象となる事業者及び許可基準の詳細。" * 50)
        text = evidence.select_pdf_page_excerpts([cover, contents, change, appendix])
        self.assertLessEqual(len(text), evidence.MAX_PDF_CHARS)
        self.assertIn("[PDF page 1; excerpt]", text)
        self.assertIn("[PDF page 3; excerpt]", text)
        self.assertIn("2027年4月1日", text)
        self.assertNotIn("[PDF page 2; excerpt]", text)

    def test_short_pdf_keeps_full_content_without_page_selection(self):
        self.assertEqual(
            evidence.select_pdf_page_excerpts(["第１条 改正内容", "附則 施行日"]),
            "第１条 改正内容 附則 施行日"
        )

    def test_invalid_pdf_without_text_never_accepted(self):
        self.assertEqual(evidence.pdf_text(b"<html>bad</html>"), "")

    def test_title_relevance_filters_unrelated_index_content(self):
        docs = [{"url": "https://www.meti.go.jp/a", "format": "html",
                 "excerpt": "電気工事士法施行令の改正案に関する概要を説明します。" * 8}]
        self.assertTrue(evidence.related_to_title("電気工事士法施行令の改正案", docs))
        self.assertFalse(evidence.related_to_title("医療保険に関する行政処分", docs))
        self.assertFalse(evidence.related_to_title("", docs))

    def test_old_extraction_version_is_refetched(self):
        raw = {"source_url": "https://www.meti.go.jp/a", "raw_content_hash": "abc"}
        old = {"source_url": raw["source_url"], "raw_hash": "abc",
               "documents": [{"url": raw["source_url"], "format": "html",
                              "excerpt": "navigation " * 40}]}
        self.assertFalse(evidence.eligible_cached(raw, old))
        self.assertTrue(evidence.eligible_cached(
            raw, {**old, "extraction_version": evidence.EXTRACTION_VERSION}
        ))

    def test_failed_source_is_retried_only_after_cooldown_or_source_change(self):
        from datetime import datetime, timedelta, timezone
        raw = {"source_url": "https://www.meti.go.jp/a", "raw_content_hash": "abc"}
        now = datetime.now(timezone.utc)
        failed = {
            "source_url": raw["source_url"], "raw_hash": "abc",
            "status": "no_evidence", "documents": [],
            "extraction_version": evidence.EXTRACTION_VERSION,
            "checked_at": now.isoformat(timespec="seconds"),
        }
        self.assertTrue(evidence.recently_failed(raw, failed))
        self.assertFalse(evidence.recently_failed({**raw, "raw_content_hash": "changed"}, failed))
        old = dict(failed, checked_at=(now - timedelta(days=4)).isoformat(timespec="seconds"))
        self.assertFalse(evidence.recently_failed(raw, old))

    def test_source_diversification_does_not_discard_or_duplicate_candidates(self):
        items = [
            {"id": f"parliament-{n}", "source_name": "Diet"} for n in range(8)
        ] + [
            {"id": "fsa", "source_name": "FSA"},
            {"id": "meti", "source_name": "METI"},
            {"id": "caa", "source_name": "CAA"},
        ]
        ranked = evidence.diversify_sources(items)
        self.assertEqual(len(ranked), len(items))
        self.assertEqual({x["id"] for x in ranked}, {x["id"] for x in items})
        self.assertEqual([x["id"] for x in ranked[:5]],
                         ["parliament-0", "parliament-1", "fsa", "meti", "caa"])
        self.assertEqual([x["id"] for x in ranked[5:]],
                         [f"parliament-{n}" for n in range(2, 8)])

    def test_source_diversification_keeps_outside_window_at_tail(self):
        items = [{"id": str(i), "source_name": "Same"} for i in range(6)]
        ranked = evidence.diversify_sources(items, window=4, per_source=1)
        self.assertEqual([r["id"] for r in ranked], ["0", "1", "2", "3", "4", "5"])

    def test_public_entries_cached_and_not_retried_on_same_source_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            raw = tmp / "raw.json"
            public = tmp / "public.json"
            cache = tmp / "cache.json"
            raw.write_text(json.dumps([{
                "id": "raw-test", "source_url": "https://www.meti.go.jp/a",
                "raw_content_hash": "abc",
            }]), encoding="utf-8")
            public.write_text(json.dumps([{
                "id": "raw-test", "published_at": "2026-11-09", "relevance_score": 10,
                "title_ja": "具体的な改正内容について",
            }]), encoding="utf-8")
            cache.write_text("{}\n", encoding="utf-8")
            doc = [{"url": "https://www.meti.go.jp/a", "format": "html", "excerpt": "具体的な改正内容" * 20}]
            with mock.patch.object(evidence, "RAW_PATH", raw), \
                 mock.patch.object(evidence, "PUBLIC_PATH", public), \
                 mock.patch.object(evidence, "CACHE_PATH", cache), \
                 mock.patch.object(evidence, "gather", return_value=doc) as getter:
                first = evidence.collect(2)
                second = evidence.collect(2)
            self.assertEqual(first["enriched"], 1)
            self.assertEqual(second["cached"], 1)
            getter.assert_called_once()
            entry = json.loads(cache.read_text(encoding="utf-8"))["raw-test"]
            self.assertEqual(entry["raw_hash"], "abc")
            self.assertEqual(entry["documents"], doc)


class TestSummaryIntegration(unittest.TestCase):
    def test_source_evidence_attached_to_both_languages(self):
        item = {"id": "raw-test", "title_ja": "政令改正の公表", "source_url": "https://www.meti.go.jp/a"}
        raw = {"raw_summary": "", "official_documents": [{
            "url": "https://www.meti.go.jp/a", "format": "html",
            "excerpt": "省令に基づく事業者の手続について2027年から新しい様式を使用します。" * 9,
        }]}
        self.assertEqual(su.source_evidence(item, raw), "descriptive_text")
        for payload in (su.english_source_payload(item, raw), su.japanese_source_payload(item, raw)):
            self.assertIn("official_source_documents", payload)
            self.assertEqual(payload["official_source_documents"], raw["official_documents"])
        self.assertIn("official_source_documents", su.build_user_content(item, raw))

    def test_stale_or_unsafe_evidence_is_not_loaded(self):
        raw = {
            "raw-test": {"source_url": "https://www.meti.go.jp/a", "raw_content_hash": "abc"},
        }
        long_excerpt = "省令についての具体的な改正内容です。" * 20
        bad_source = {"raw-test": {
            "source_url": "https://www.meti.go.jp/a", "raw_hash": "old",
            "documents": [{"url": "https://www.meti.go.jp/a", "format": "html", "excerpt": long_excerpt}],
        }}
        self.assertEqual(su.attach_official_documents(raw, bad_source), 0)
        self.assertNotIn("official_documents", raw["raw-test"])
        bad_source["raw-test"]["raw_hash"] = "abc"
        bad_source["raw-test"]["documents"][0]["url"] = "https://evil.example.com/"
        self.assertEqual(su.attach_official_documents(raw, bad_source), 0)
        bad_source["raw-test"]["documents"][0]["url"] = "https://www.meti.go.jp/a"
        self.assertEqual(su.attach_official_documents(raw, bad_source), 1)
        self.assertEqual(raw["raw-test"]["official_documents"][0]["excerpt"], long_excerpt)

    def test_only_successful_enrichment_invalidates_existing_cache(self):
        item = {"id": "raw-test", "title_ja": "一部改正", "source_url": "https://www.meti.go.jp/a"}
        old = {"raw_content_hash": "abc", "raw_summary": "一部改正"}
        old_key = su.cache_key(item, {item["id"]: old})
        again = su.cache_key(item, {item["id"]: {**old, "official_documents": []}})
        enriched = {**old, "official_documents": [{
            "url": "https://www.meti.go.jp/a", "format": "html",
            "excerpt": "新制度に関する具体的な説明" * 20,
        }]}
        changed = su.cache_key(item, {item["id"]: enriched})
        self.assertEqual(old_key, again)
        self.assertNotEqual(old_key, changed)
        self.assertEqual(changed, su.cache_key(item, {item["id"]: enriched}))


if __name__ == "__main__":
    unittest.main()
