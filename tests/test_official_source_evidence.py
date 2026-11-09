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

    def test_gather_keeps_page_and_pdf_provenance(self):
        html = b"""<html><main><h1>省令案</h1><p>"""
        html += ("手続の対象が変わり、施行日は2027年4月1日です。" * 15).encode()
        html += b"""</p><a href="/a.pdf">新旧対照表</a></main></html>"""
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

    def test_invalid_pdf_without_text_never_accepted(self):
        self.assertEqual(evidence.pdf_text(b"<html>bad</html>"), "")

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
