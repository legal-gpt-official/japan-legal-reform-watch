"""Offline tests for the self-serve email digest (Stripe + Resend are faked)."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import alert_common as ac  # noqa: E402
import build_public_data as bpd  # noqa: E402
import fetch_updates as fu  # noqa: E402
import send_alert_digests as sad  # noqa: E402

TODAY = date(2026, 9, 25)
NOW = datetime(2026, 9, 25, 7, 0, tzinfo=ac.JST)
PRODUCT = "prod_TEST123"
SUBSCRIBER_EMAIL = "reader@example.com"

CONFIG = sad.Config(
    stripe_api_key="rk_test_fake",
    resend_api_key="re_fake",
    product_id=PRODUCT,
    from_email="Japan Legal Reform Watch <alerts@example.org>",
    manage_url="https://billing.stripe.com/p/login/test_portal",
)


def item(item_id, **overrides):
    base = {
        "id": item_id,
        "title_en": f"Title {item_id}",
        "title_ja": "日本語タイトル",
        "area": "Data / Privacy / AI",
        "stage": "Government Announcement",
        "source_name": "個人情報保護委員会 (PPC) 新着情報",
        "source_url": f"https://www.ppc.go.jp/{item_id}",
        "published_at": "2026-09-24",
        "first_seen_at": "2026-09-25",
        "summary_source": "rule_based",
        "summary_en": "Template sentence.",
    }
    base.update(overrides)
    return base


def subscription(sub_id, *, product=PRODUCT, email=SUBSCRIBER_EMAIL):
    return {
        "id": sub_id,
        "customer": {"id": "cus_" + sub_id, "email": email},
        "items": {"data": [{"price": {"id": "price_x", "product": product}}]},
    }


class FakeHttp:
    """Routes Stripe and Resend calls; records every request."""

    def __init__(self, *, subscriptions=None, sessions=None, stripe_status=200, resend_status=200, page_size=None):
        self.subscriptions = subscriptions or {}
        self.sessions = sessions or {}
        self.stripe_status = stripe_status
        self.resend_status = resend_status
        self.page_size = page_size
        self.calls = []

    def __call__(self, method, url, *, headers, body=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        if parsed.netloc == "api.stripe.com":
            if self.stripe_status != 200:
                return self.stripe_status, {"error": {"message": "boom"}}
            if parsed.path == "/v1/subscriptions":
                subs = list(self.subscriptions.get(query["status"][0], []))
                start = 0
                if "starting_after" in query:
                    start = [s["id"] for s in subs].index(query["starting_after"][0]) + 1
                size = self.page_size or len(subs) or 1
                page = subs[start : start + size]
                return 200, {"data": page, "has_more": start + size < len(subs)}
            if parsed.path == "/v1/checkout/sessions":
                session = self.sessions.get(query["subscription"][0])
                return 200, {"data": [session] if session else [], "has_more": False}
        if parsed.netloc == "api.resend.com":
            return self.resend_status, {"id": "email_1"}
        raise AssertionError(f"unexpected request {method} {url}")

    def resend_calls(self):
        return [call for call in self.calls if "api.resend.com" in call["url"]]


def area_session(value, email=SUBSCRIBER_EMAIL):
    return {
        "custom_fields": [{"key": "area", "type": "dropdown", "dropdown": {"value": value}}],
        "customer_details": {"email": email},
    }


class TestChannels(unittest.TestCase):
    def test_channels_cover_every_classifier_area_except_other(self):
        areas = set()
        for table in (
            bpd.AREA_RULES, bpd.UTF8_AREA_RULES, bpd.ADDITIONAL_AREA_RULES,
            bpd.METI_AREA_RULES, bpd.CAA_AREA_RULES, bpd.PPC_AREA_RULES, bpd.JFTC_AREA_RULES,
            bpd.MOJ_AREA_RULES, bpd.MOE_AREA_RULES, bpd.MOF_AREA_RULES, bpd.MIC_AREA_RULES,
            bpd.MLIT_AREA_RULES, bpd.MAFF_AREA_RULES,
        ):
            areas.update(area for area, _ in table)
        areas.update(area for area, _ in bpd.AREA_SOURCE_FALLBACK)
        channel_areas = {channel.area for channel in ac.CHANNELS if channel.area}
        self.assertEqual(channel_areas, areas - {"Other"})

    def test_channel_values_fit_stripe_dropdown_limits(self):
        values = [channel.value for channel in ac.CHANNELS]
        self.assertEqual(len(values), len(set(values)))
        self.assertLessEqual(len(values), 200)
        self.assertEqual(values[0], "all")
        for channel in ac.CHANNELS:
            with self.subTest(channel=channel.value):
                self.assertRegex(channel.value, r"^[a-z0-9]{1,100}$")
                self.assertLessEqual(len(channel.label), 100)

    def test_source_display_names_cover_every_configured_source(self):
        for source in fu.SOURCES:
            with self.subTest(source=source["name"]):
                self.assertIn(source["name"], ac.SOURCE_DISPLAY_NAMES)


class TestSelection(unittest.TestCase):
    def test_new_items_use_lookback_and_skip_processed(self):
        items = [
            item("a", first_seen_at="2026-09-25"),
            item("b", first_seen_at="2026-09-23"),
            item("c", first_seen_at="2026-09-22"),  # outside the 3-day window
            item("d", first_seen_at="2026-09-26"),  # future
            item("e", first_seen_at=None),
            item("f", first_seen_at="2026-09-24"),
        ]
        selected = sad.select_new_items(items, {"f"}, TODAY)
        self.assertEqual([entry["id"] for entry in selected], ["a", "b"])

    def test_deadline_last_day_follows_stage_closing_rules(self):
        self.assertEqual(ac.comment_deadline_last_day("2026-10-16"), date(2026, 10, 16))
        self.assertEqual(ac.comment_deadline_last_day("2026-10-16T00:00:00+09:00"), date(2026, 10, 15))
        self.assertEqual(ac.comment_deadline_last_day("2026-10-16T17:00:00+09:00"), date(2026, 10, 16))
        self.assertEqual(ac.comment_deadline_last_day("2026-10-15T15:00:00Z"), date(2026, 10, 15))
        self.assertIsNone(ac.comment_deadline_last_day("16 Oct"))
        self.assertIsNone(ac.comment_deadline_last_day(None))

    def test_deadlines_only_for_open_consultations_within_horizon(self):
        items = [
            item("open3", stage="Public Comment Open", comment_deadline="2026-09-28"),
            item("closed", stage="Public Comment Closed", comment_deadline="2026-09-28"),
            item("far", stage="Public Comment Open", comment_deadline="2026-10-20"),
            item("past", stage="Public Comment Open", comment_deadline="2026-09-24"),
            item("today", stage="Public Comment Open", comment_deadline="2026-09-25"),
        ]
        deadlines = sad.select_deadlines(items, TODAY)
        self.assertEqual([(entry[0]["id"], entry[2]) for entry in deadlines], [("today", 0), ("open3", 3)])

    def test_email_is_triggered_by_new_items_or_reminder_days_only(self):
        channel = ac.CHANNELS_BY_VALUE["dataprivacyai"]
        five_days = [(item("x", stage="Public Comment Open"), date(2026, 9, 30), 5)]
        self.assertFalse(sad.build_digest(channel, [], five_days).should_send)
        for days in sad.REMINDER_DAYS:
            with self.subTest(days=days):
                reminder = [(item("x", stage="Public Comment Open"), date(2026, 9, 25), days)]
                self.assertTrue(sad.build_digest(channel, [], reminder).should_send)
        self.assertTrue(sad.build_digest(channel, [item("n")], []).should_send)

    def test_digest_filters_by_channel_area(self):
        items = [item("privacy"), item("finance", area="Finance / AML"), item("other", area="Other")]
        privacy = sad.build_digest(ac.CHANNELS_BY_VALUE["dataprivacyai"], items, [])
        everything = sad.build_digest(ac.ALL_AREAS, items, [])
        self.assertEqual([entry["id"] for entry in privacy.new_items], ["privacy"])
        self.assertEqual([entry["id"] for entry in everything.new_items], ["privacy", "finance", "other"])

    def test_state_keeps_only_ids_inside_retention(self):
        items = [item("recent", first_seen_at="2026-09-20"), item("old", first_seen_at="2026-09-01")]
        kept = sad.next_state(items, {"old", "gone"}, ["recent"], TODAY)
        self.assertEqual(kept, ["recent"])


class TestRendering(unittest.TestCase):
    def test_untrusted_fields_are_escaped_and_unsafe_links_dropped(self):
        hostile = item(
            "x",
            title_en='<script>alert("t")</script>',
            title_ja="<img src=x onerror=alert(1)>",
            source_url="javascript:alert(1)",
            summary_source="claude",
            summary_en="<b>bold</b>",
        )
        digest = sad.build_digest(ac.ALL_AREAS, [hostile], [])
        html_body = sad.render_html(digest, TODAY, CONFIG.manage_url)
        text_body = sad.render_text(digest, TODAY, CONFIG.manage_url)
        self.assertNotIn("<script>", html_body)
        self.assertNotIn("<img", html_body)
        self.assertNotIn("<b>bold", html_body)
        self.assertNotIn("javascript:", html_body)
        self.assertIn("&lt;script&gt;", html_body)
        self.assertIn("URL unavailable", text_body)

    def test_digest_carries_newly_detected_caveat_disclaimer_and_portal(self):
        digest = sad.build_digest(ac.ALL_AREAS, [item("a")], [])
        for body in (sad.render_html(digest, TODAY, CONFIG.manage_url), sad.render_text(digest, TODAY, CONFIG.manage_url)):
            self.assertIn("does not mean", body)
            self.assertIn("not legal advice", body)
            self.assertIn("Original Japanese official sources remain", body)
            self.assertIn("https://billing.stripe.com/p/login/test_portal", body)

    def test_rule_based_preview_is_labelled_not_presented_as_summary(self):
        digest = sad.build_digest(ac.ALL_AREAS, [item("a", summary_en="Template sentence.")], [])
        html_body = sad.render_html(digest, TODAY, CONFIG.manage_url)
        self.assertIn("Rule-based preview only", html_body)
        self.assertNotIn("Template sentence.", html_body)

    def test_long_digest_is_capped_with_dashboard_link(self):
        items = [item(f"i{n}") for n in range(sad.MAX_NEW_ITEMS_PER_EMAIL + 4)]
        digest = sad.build_digest(ac.ALL_AREAS, items, [])
        text_body = sad.render_text(digest, TODAY, CONFIG.manage_url)
        self.assertIn("4 more on the dashboard", text_body)
        self.assertNotIn(f"Title i{sad.MAX_NEW_ITEMS_PER_EMAIL}\n", text_body)


class TestConfig(unittest.TestCase):
    def test_missing_names_settings_without_values(self):
        config = sad.Config.from_env({"STRIPE_ALERTS_API_KEY": "rk_live_secret"})
        missing = config.missing()
        self.assertNotIn("STRIPE_ALERTS_API_KEY", missing)
        self.assertIn("RESEND_API_KEY", missing)
        self.assertIn("ALERT_MANAGE_URL", missing)
        self.assertNotIn("rk_live_secret", ",".join(missing))

    def test_manage_url_must_be_stripe_portal(self):
        self.assertTrue(sad.valid_manage_url("https://billing.stripe.com/p/login/abc"))
        for bad in (
            "http://billing.stripe.com/p/login/abc",
            "https://billing.stripe.com.evil.example/p/login/abc",
            "https://evil.example/?https://billing.stripe.com",
            "",
        ):
            with self.subTest(bad=bad):
                self.assertFalse(sad.valid_manage_url(bad))


class TestRun(unittest.TestCase):
    def run_digest(self, http, items, processed=frozenset(), config=CONFIG, dry_run=False):
        written = []
        summary = sad.run(
            config=config,
            items=items,
            processed=set(processed),
            now=NOW,
            http=http,
            sleep=lambda _seconds: None,
            dry_run=dry_run,
            state_writer=written.append,
        )
        return summary, written

    def test_not_configured_makes_no_requests_and_keeps_state(self):
        http = FakeHttp()
        summary, written = self.run_digest(http, [item("a")], config=sad.Config("", "", "", "", ""))
        self.assertEqual(summary["status"], "not_configured")
        self.assertEqual(http.calls, [])
        self.assertEqual(written, [])

    def test_sends_one_recipient_per_request_with_daily_idempotency(self):
        http = FakeHttp(
            subscriptions={
                "active": [
                    subscription("sub_a"),
                    subscription("sub_other_product", product="prod_OTHER", email="other@example.com"),
                    subscription("sub_b", email="second@example.com"),
                ],
                "trialing": [],
                "past_due": [],
            },
            sessions={"sub_a": area_session("dataprivacyai"), "sub_b": area_session("financeaml", "second@example.com")},
            page_size=1,  # exercise pagination
        )
        items = [item("privacy"), item("finance", area="Finance / AML"), item("tax", area="Tax / Stamp Duty")]
        summary, written = self.run_digest(http, items)

        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["subscribers"], 2)
        self.assertEqual(summary["emails_sent"], 2)
        sends = http.resend_calls()
        self.assertEqual(len(sends), 2)
        recipients = []
        for call in sends:
            payload = json.loads(call["body"])
            self.assertEqual(len(payload["to"]), 1)
            recipients.append(payload["to"][0])
            self.assertTrue(call["headers"]["Idempotency-Key"].startswith("jlrw-digest-2026-09-25-sub_"))
            self.assertEqual(call["headers"]["Authorization"], "Bearer re_fake")
        self.assertEqual(sorted(recipients), ["reader@example.com", "second@example.com"])
        privacy_payload = json.loads(sends[0]["body"])
        self.assertIn("Title privacy", privacy_payload["text"])
        self.assertNotIn("Title finance", privacy_payload["text"])
        # Every new item is recorded, including ones no subscriber follows.
        self.assertEqual(written, [["finance", "privacy", "tax"]])
        self.assertNotIn("@", json.dumps(summary))

    def test_nothing_to_report_skips_sending_but_records_items(self):
        http = FakeHttp(
            subscriptions={"active": [subscription("sub_a")], "trialing": [], "past_due": []},
            sessions={"sub_a": area_session("taxstampduty")},
        )
        summary, written = self.run_digest(http, [item("privacy")])
        self.assertEqual(summary["emails_skipped_nothing_to_report"], 1)
        self.assertEqual(http.resend_calls(), [])
        self.assertEqual(written, [["privacy"]])

    def test_unrecognized_area_falls_back_to_all_areas(self):
        http = FakeHttp(
            subscriptions={"active": [subscription("sub_a")], "trialing": [], "past_due": []},
            sessions={"sub_a": area_session("notachannel")},
        )
        summary, _ = self.run_digest(http, [item("finance", area="Finance / AML")])
        self.assertEqual(summary["subscribers_unrecognized_channel"], 1)
        self.assertEqual(summary["emails_sent"], 1)

    def test_stripe_failure_sends_nothing_and_keeps_items_queued(self):
        http = FakeHttp(stripe_status=500)
        summary, written = self.run_digest(http, [item("a")])
        self.assertEqual(summary["status"], "stripe_unavailable")
        self.assertEqual(http.resend_calls(), [])
        self.assertEqual(written, [])

    def test_every_send_failing_keeps_items_queued(self):
        http = FakeHttp(
            subscriptions={"active": [subscription("sub_a")], "trialing": [], "past_due": []},
            sessions={"sub_a": area_session("all")},
            resend_status=401,
        )
        summary, written = self.run_digest(http, [item("a")])
        self.assertEqual(summary["status"], "resend_unavailable")
        self.assertEqual(summary["emails_failed"], 1)
        self.assertEqual(written, [])

    def test_transient_resend_errors_are_retried(self):
        http = FakeHttp(
            subscriptions={"active": [subscription("sub_a")], "trialing": [], "past_due": []},
            sessions={"sub_a": area_session("all")},
            resend_status=503,
        )
        summary, _ = self.run_digest(http, [item("a")])
        self.assertEqual(len(http.resend_calls()), sad.MAX_SEND_ATTEMPTS)
        self.assertEqual(summary["emails_failed"], 1)

    def test_same_day_duplicate_is_not_a_failure(self):
        http = FakeHttp(
            subscriptions={"active": [subscription("sub_a")], "trialing": [], "past_due": []},
            sessions={"sub_a": area_session("all")},
            resend_status=409,
        )
        summary, written = self.run_digest(http, [item("a")])
        self.assertEqual(summary["emails_duplicate"], 1)
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(written, [["a"]])

    def test_dry_run_reads_stripe_but_sends_and_records_nothing(self):
        http = FakeHttp(
            subscriptions={"active": [subscription("sub_a")], "trialing": [], "past_due": []},
            sessions={"sub_a": area_session("all")},
        )
        summary, written = self.run_digest(http, [item("a")], dry_run=True)
        self.assertEqual(summary["status"], "dry_run")
        self.assertEqual(http.resend_calls(), [])
        self.assertEqual(written, [])

    def test_customer_without_usable_email_is_skipped(self):
        http = FakeHttp(
            subscriptions={"active": [subscription("sub_a", email="not-an-email")], "trialing": [], "past_due": []},
            sessions={"sub_a": {"custom_fields": [], "customer_details": {"email": None}}},
        )
        summary, _ = self.run_digest(http, [item("a")])
        self.assertEqual(summary["subscribers_without_email"], 1)
        self.assertEqual(http.resend_calls(), [])


class TestMain(unittest.TestCase):
    def test_main_output_never_contains_subscriber_email_and_writes_state(self):
        http = FakeHttp(
            subscriptions={"active": [subscription("sub_a")], "trialing": [], "past_due": []},
            sessions={"sub_a": area_session("all")},
        )
        env = {
            "STRIPE_ALERTS_API_KEY": "rk_test_fake",
            "RESEND_API_KEY": "re_fake",
            "ALERT_STRIPE_PRODUCT_ID": PRODUCT,
            "ALERT_FROM_EMAIL": "alerts@example.org",
            "ALERT_MANAGE_URL": "https://billing.stripe.com/p/login/test_portal",
        }
        today = datetime.now(ac.JST).date().isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            data_path = Path(tmp) / "legal_updates.json"
            state_path = Path(tmp) / "alert_digest_state.json"
            data_path.write_text(json.dumps([item("a", first_seen_at=today)]), encoding="utf-8")
            out = io.StringIO()
            with mock.patch.dict("os.environ", env, clear=False), mock.patch.object(
                sad, "DATA_PATH", data_path
            ), mock.patch.object(sad, "STATE_PATH", state_path), mock.patch.object(
                sad, "default_http", http
            ), mock.patch.object(sad.time, "sleep", lambda _s: None), contextlib.redirect_stdout(out):
                code = sad.main([])
            state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertNotIn(SUBSCRIBER_EMAIL, out.getvalue())
        self.assertNotIn("rk_test_fake", out.getvalue())
        self.assertNotIn("re_fake", out.getvalue())
        self.assertIn("emails_sent: 1", out.getvalue())
        self.assertEqual(state, {"schema_version": 1, "processed_item_ids": ["a"]})
        self.assertNotIn(SUBSCRIBER_EMAIL, json.dumps(state))

    def test_preview_refuses_public_docs_directory(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = sad.main(["--preview-dir", str(REPO_ROOT / "docs" / "previews")])
        self.assertEqual(code, 2)

    def test_committed_state_file_has_expected_shape(self):
        state = json.loads((REPO_ROOT / "data" / "alert_digest_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["schema_version"], sad.STATE_SCHEMA_VERSION)
        self.assertIsInstance(state["processed_item_ids"], list)
        self.assertNotIn("@", json.dumps(state))


if __name__ == "__main__":
    unittest.main()
