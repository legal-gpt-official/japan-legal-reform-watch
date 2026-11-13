"""Official-source fetching for case files: cache reuse, error classes, bounded retries."""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import explainer_sources as src  # noqa: E402
import explainer_state as es  # noqa: E402

PAGE = ("<html><body><main><h1>改正省令の公布について</h1><p>" + "本省令は令和八年十一月一日から施行する。" * 20
        + "</p></main></body></html>").encode("utf-8")
URL = "https://www.meti.go.jp/press/2026/11/example.html"


def http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(response=response)


class Fetcher:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, url):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def state(**source):
    base = {"id": "s1", "url": URL, "kind": "ministry", "status": "not_fetched", "attempts": 0}
    base.update(source)
    return {"schema_version": 1, "dossier_id": "new-reform-2026", "kind": "new", "sources": [base],
            "facts": [{"id": "f1", "category": "effective_date", "status": "confirmed",
                       "statement": "x", "source_ids": ["s1"]}], "unresolved": []}


class ClassifyTests(unittest.TestCase):
    def test_error_classes(self):
        self.assertEqual(src.classify_exception(requests.Timeout()).error_class, "transient_network")
        self.assertEqual(src.classify_exception(requests.ConnectionError()).error_class, "transient_network")
        self.assertEqual(src.classify_exception(http_error(429)).error_class, "http_429")
        self.assertEqual(src.classify_exception(http_error(503)).error_class, "http_5xx")
        self.assertEqual(src.classify_exception(http_error(403)).error_class, "blocked_or_waf")
        self.assertEqual(src.classify_exception(http_error(404)).error_class, "http_4xx")
        self.assertEqual(src.classify_exception(ValueError("source document over size limit")).error_class, "too_large")
        self.assertEqual(src.classify_exception(ValueError("redirect to non-official URL")).error_class, "non_official_url")


class FetchTests(unittest.TestCase):
    def test_transient_error_is_retried_in_process_then_recorded_with_hash(self):
        s = state()
        fetcher = Fetcher(requests.Timeout(), (PAGE, "text/html", URL))
        src.run_fetch(s, fetcher=fetcher, sleep=lambda _: None, log=lambda _: None)
        source = s["sources"][0]
        self.assertEqual(fetcher.calls, 2)
        self.assertEqual(source["status"], "fetched")
        self.assertEqual(source["attempts"], 2)
        self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
        self.assertIn("施行", source["excerpt"])
        self.assertEqual(es.validate_state("new-reform-2026", s)[0], [])

    def test_permanent_error_is_recorded_once_and_not_retried_by_later_slots(self):
        s = state()
        fetcher = Fetcher(http_error(403))
        src.run_fetch(s, fetcher=fetcher, sleep=lambda _: None, log=lambda _: None)
        self.assertEqual((s["sources"][0]["status"], s["sources"][0]["error_class"]), ("failed", "blocked_or_waf"))
        again = Fetcher()
        counters = src.run_fetch(s, fetcher=again, sleep=lambda _: None, log=lambda _: None)
        self.assertEqual(again.calls, 0)
        self.assertEqual(counters["skipped"], 1)

    def test_transient_budget_is_bounded_across_runs(self):
        s = state()
        src.run_fetch(s, fetcher=Fetcher(*[requests.Timeout()] * 3), sleep=lambda _: None, log=lambda _: None)
        self.assertEqual(s["sources"][0]["attempts"], es.MAX_FETCH_ATTEMPTS)
        idle = Fetcher()
        src.run_fetch(s, fetcher=idle, sleep=lambda _: None, log=lambda _: None)
        self.assertEqual(idle.calls, 0, "no fetch after the retry budget is spent")

    def test_recent_source_is_reused_and_changed_content_is_flagged(self):
        s = state()
        src.run_fetch(s, fetcher=Fetcher((PAGE, "text/html", URL)), sleep=lambda _: None, log=lambda _: None)
        idle = Fetcher()
        self.assertEqual(src.run_fetch(s, fetcher=idle, log=lambda _: None)["reused"], 1)
        self.assertEqual(idle.calls, 0)
        changed = PAGE.replace("十一月一日".encode(), "十二月一日".encode())
        messages = []
        src.run_fetch(s, refresh=True, fetcher=Fetcher((changed, "text/html", URL)), log=messages.append)
        self.assertTrue(s["sources"][0]["changed_since_previous"])
        self.assertTrue(any("re-check facts ['f1']" in m for m in messages))

    def test_redirect_to_non_official_host_is_rejected(self):
        s = state()
        src.run_fetch(s, fetcher=Fetcher((PAGE, "text/html", "https://evil.example.com/")), log=lambda _: None)
        self.assertEqual(s["sources"][0]["error_class"], "non_official_url")

    def test_javascript_only_page_and_scanned_pdf_are_not_guessed(self):
        js = b"<html><body><noscript>JavaScript\xe3\x82\x92\xe6\x9c\x89\xe5\x8a\xb9\xe3\x81\xab</noscript></body></html>"
        s = state()
        src.run_fetch(s, fetcher=Fetcher((js, "text/html", URL)), log=lambda _: None)
        self.assertEqual(s["sources"][0]["error_class"], "js_required")
        self.assertNotIn("excerpt", s["sources"][0])

    def test_egov_law_revisions_record_versions(self):
        body = json.dumps({"law_info": {"law_id": "abc", "law_num": "令和八年法律第一号"}, "revisions": [
            {"law_revision_id": "abc_20270401_508AC0000000001", "amendment_law_title": "改正法",
             "amendment_promulgate_date": "2026-05-01", "amendment_enforcement_date": "2027-04-01",
             "amendment_enforcement_comment": "公布の日から起算して一年を超えない範囲内において政令で定める日"},
        ]}).encode("utf-8")
        url = "https://laws.e-gov.go.jp/api/2/law_revisions/abc"
        s = state(url=url, kind="egov_law")
        src.run_fetch(s, fetcher=Fetcher((body, "application/json", url)), log=lambda _: None)
        version = s["sources"][0]["version"]
        self.assertEqual(version["revisions"][0]["amendment_enforcement_date"], "2027-04-01")
        self.assertIn("as_of", version)

    def test_only_official_urls_can_be_added(self):
        with self.assertRaises(SystemExit):
            src.run_fetch(state(), url="https://news.example.com/x", log=lambda _: None)

    def test_should_fetch_refreshes_old_evidence(self):
        now = datetime(2026, 11, 13, tzinfo=timezone.utc)
        old = {"status": "fetched", "fetched_at": (now - timedelta(days=10)).isoformat()}
        self.assertTrue(src.should_fetch(old, refresh=False, force=False, max_age_days=7, now=now)[0])
        fresh = {"status": "fetched", "fetched_at": (now - timedelta(days=1)).isoformat()}
        self.assertFalse(src.should_fetch(fresh, refresh=False, force=False, max_age_days=7, now=now)[0])


if __name__ == "__main__":
    unittest.main()
