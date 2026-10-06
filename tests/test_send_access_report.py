"""Offline tests for the owner-only access report (scripts/send_access_report.py)."""

from __future__ import annotations

import json
import re
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import send_access_report as sar  # noqa: E402

ACCOUNT = "a" * 32
SITE = "b" * 32
RECIPIENT = "owner@example.com"
TOKEN = "cf-secret-token"
RESEND_KEY = "re_secret_key"

FULL_ENV = {
    "CLOUDFLARE_API_TOKEN": TOKEN,
    "CLOUDFLARE_ACCOUNT_ID": ACCOUNT,
    "CLOUDFLARE_WEB_ANALYTICS_SITE_TAG": SITE,
    "RESEND_API_KEY": RESEND_KEY,
    "ALERT_FROM_EMAIL": "Japan Legal Reform Watch <alerts@example.com>",
    "ACCESS_REPORT_TO": RECIPIENT,
}

# 2026-10-05 00:30 JST is a Monday; 2026-10-01 00:30 JST is the 1st (Thursday).
MONDAY = datetime(2026, 10, 4, 15, 30, tzinfo=timezone.utc)
FIRST = datetime(2026, 9, 30, 15, 30, tzinfo=timezone.utc)
TUESDAY = datetime(2026, 10, 5, 23, 50, tzinfo=timezone.utc)


def rows_payload(rows):
    return {"data": {"viewer": {"accounts": [{"rumPageloadEventsAdaptiveGroups": rows}]}}, "errors": None}


class FakeCloudflareAndResend:
    """Answers every totals chunk with 10 views / 4 visits; records each call."""

    def __init__(self, *, resend_status=200, breakdown_error_for=None, totals_status=200):
        self.calls = []
        self.resend_status = resend_status
        self.breakdown_error_for = breakdown_error_for
        self.totals_status = totals_status

    def __call__(self, method, url, *, headers, body=None, timeout=None):
        payload = json.loads(body.decode("utf-8"))
        self.calls.append((method, url, dict(headers), payload))
        if url.startswith(sar.RESEND_API):
            return self.resend_status, {}
        query = payload["query"]
        if "JlrwAccessTotals" in query:
            return self.totals_status, rows_payload([{"count": 10, "sum": {"visits": 4}}])
        dimension = re.search(r"dimensions \{ (\w+) \}", query).group(1)
        if dimension == self.breakdown_error_for:
            return 200, {"data": None, "errors": [{"message": f"unknown field {dimension} at 12:3"}]}
        return 200, rows_payload(
            [
                {"count": 7, "sum": {"visits": 3}, "dimensions": {dimension: "<script>alert(1)</script>"}},
                {"count": 3, "sum": {"visits": 1}, "dimensions": {dimension: ""}},
            ]
        )

    def resend_calls(self):
        return [call for call in self.calls if call[1].startswith(sar.RESEND_API)]

    def graphql_calls(self):
        return [call for call in self.calls if call[1] == sar.CLOUDFLARE_GRAPHQL]


def run_report(fake, env=FULL_ENV, now=TUESDAY, **kwargs):
    lines = []
    code = sar.run(
        config=sar.Config.from_env(env), http=fake, now=now, sleep=lambda _s: None, out=lines.append, **kwargs
    )
    return code, lines


class PeriodTests(unittest.TestCase):
    def test_jst_today_uses_tokyo_date(self):
        self.assertEqual(sar.jst_today(datetime(2026, 10, 5, 23, 50, tzinfo=timezone.utc)), date(2026, 10, 6))
        with self.assertRaises(ValueError):
            sar.jst_today(datetime(2026, 10, 5))

    def test_jst_midnight_is_previous_utc_afternoon(self):
        self.assertEqual(sar.jst_midnight_utc(date(2026, 10, 6)), "2026-10-05T15:00:00Z")

    def test_daily_pair(self):
        current, previous = sar.period_pair("daily", date(2026, 10, 6))
        self.assertEqual((current.start, current.end), (date(2026, 10, 5), date(2026, 10, 6)))
        self.assertEqual((previous.start, previous.end), (date(2026, 10, 4), date(2026, 10, 5)))

    def test_weekly_pair_is_previous_monday_to_sunday(self):
        current, previous = sar.period_pair("weekly", date(2026, 10, 5))  # Monday
        self.assertEqual((current.start, current.end), (date(2026, 9, 28), date(2026, 10, 5)))
        self.assertEqual(current.start.weekday(), 0)
        self.assertEqual((previous.start, previous.end), (date(2026, 9, 21), date(2026, 9, 28)))
        # A forced weekly report mid-week still reports the last complete week.
        self.assertEqual(sar.period_pair("weekly", date(2026, 10, 8))[0], current)

    def test_monthly_pair_handles_year_boundary_and_lengths(self):
        current, previous = sar.period_pair("monthly", date(2026, 1, 1))
        self.assertEqual((current.start, current.end), (date(2025, 12, 1), date(2026, 1, 1)))
        self.assertEqual((previous.start, previous.end), (date(2025, 11, 1), date(2025, 12, 1)))
        march, february = sar.period_pair("monthly", date(2028, 4, 1))
        self.assertEqual(march.days, 31)
        self.assertEqual(february.days, 29)

    def test_today_is_manual_only_and_compares_with_yesterday(self):
        current, previous = sar.period_pair("today", date(2026, 10, 6))
        self.assertEqual((current.start, current.end), (date(2026, 10, 6), date(2026, 10, 7)))
        self.assertEqual((previous.start, previous.end), (date(2026, 10, 5), date(2026, 10, 6)))
        self.assertEqual(sar.parse_kinds("today", date(2026, 10, 6)), ["today"])
        self.assertNotIn("today", sar.parse_kinds("all", date(2026, 6, 1)))
        self.assertNotIn("today", sar.scheduled_kinds(date(2026, 6, 1)))
        self.assertIn("送信時点", sar.period_label(current))

    def test_scheduled_kinds(self):
        self.assertEqual(sar.scheduled_kinds(date(2026, 10, 6)), ["daily"])
        self.assertEqual(sar.scheduled_kinds(date(2026, 10, 5)), ["daily", "weekly"])
        self.assertEqual(sar.scheduled_kinds(date(2026, 10, 1)), ["daily", "monthly"])
        self.assertEqual(sar.scheduled_kinds(date(2026, 6, 1)), ["daily", "weekly", "monthly"])

    def test_parse_kinds(self):
        self.assertEqual(sar.parse_kinds("all", date(2026, 10, 6)), ["daily", "weekly", "monthly"])
        self.assertEqual(sar.parse_kinds("monthly,daily", date(2026, 10, 6)), ["daily", "monthly"])
        self.assertEqual(sar.parse_kinds("", date(2026, 10, 5)), ["daily", "weekly"])
        with self.assertRaises(ValueError):
            sar.parse_kinds("yearly", date(2026, 10, 6))

    def test_chunks_cover_range_without_overlap(self):
        parts = sar.chunks(date(2026, 9, 1), date(2026, 10, 1))
        self.assertEqual(parts[0][0], date(2026, 9, 1))
        self.assertEqual(parts[-1][1], date(2026, 10, 1))
        for (_, end), (start, _) in zip(parts, parts[1:]):
            self.assertEqual(end, start)
        self.assertTrue(all((end - start).days <= sar.CHUNK_DAYS for start, end in parts))


class ConfigTests(unittest.TestCase):
    def test_full_config_is_complete(self):
        self.assertEqual(sar.Config.from_env(FULL_ENV).missing(), [])

    def test_missing_names_only(self):
        missing = sar.Config.from_env({"ACCESS_REPORT_TO": "not-an-email"}).missing()
        self.assertIn("ACCESS_REPORT_TO", missing)
        self.assertIn("CLOUDFLARE_WEB_ANALYTICS_SITE_TAG", missing)
        self.assertNotIn("not-an-email", " ".join(missing))

    def test_not_configured_warns_and_exits_zero_without_calls(self):
        fake = FakeCloudflareAndResend()
        code, lines = run_report(fake, env={})
        self.assertEqual(code, 0)
        self.assertIn("status=not_configured", lines)
        self.assertEqual(fake.calls, [])


class RunTests(unittest.TestCase):
    def test_daily_send(self):
        fake = FakeCloudflareAndResend()
        code, lines = run_report(fake)
        self.assertEqual(code, 0)
        self.assertIn("status=sent", lines)
        (resend,) = fake.resend_calls()
        _, _, headers, payload = resend
        self.assertEqual(payload["to"], [RECIPIENT])
        self.assertEqual(headers["Idempotency-Key"], "jlrw-access-report-2026-10-06")
        self.assertIn("日次", payload["subject"])
        self.assertNotIn("週次", payload["subject"])
        self.assertIn("2026年10月5日（月）", payload["text"])

    def test_graphql_request_shape(self):
        fake = FakeCloudflareAndResend()
        run_report(fake)
        _, _, headers, payload = fake.graphql_calls()[0]
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(
            payload["variables"],
            {"accountTag": ACCOUNT, "siteTag": SITE, "start": "2026-10-04T15:00:00Z", "end": "2026-10-05T15:00:00Z"},
        )
        self.assertIn("datetime_lt: $end", payload["query"])

    def test_monthly_totals_sum_across_chunks(self):
        fake = FakeCloudflareAndResend()
        stats = sar.collect(fake, sar.Config.from_env(FULL_ENV), ["monthly"], date(2026, 10, 1))
        chunk_count = len(sar.chunks(date(2026, 9, 1), date(2026, 10, 1)))
        self.assertEqual(stats[0].page_views, 10 * chunk_count)
        self.assertEqual(stats[0].visits, 4 * chunk_count)
        pages = stats[0].breakdowns[0]
        self.assertEqual(pages.rows[0][1:], (7 * chunk_count, 3 * chunk_count))

    def test_monday_on_the_first_sends_all_three_in_one_email(self):
        fake = FakeCloudflareAndResend()
        code, _ = run_report(fake, now=datetime(2026, 5, 31, 23, 50, tzinfo=timezone.utc))  # 2026-06-01 JST
        self.assertEqual(code, 0)
        (resend,) = fake.resend_calls()
        self.assertIn("日次・週次・月次", resend[3]["subject"])

    def test_duplicate_is_success(self):
        code, lines = run_report(FakeCloudflareAndResend(resend_status=409))
        self.assertEqual(code, 0)
        self.assertIn("status=duplicate", lines)

    def test_resend_failure_fails_run_after_retries(self):
        fake = FakeCloudflareAndResend(resend_status=500)
        code, _ = run_report(fake)
        self.assertEqual(code, 1)
        self.assertEqual(len(fake.resend_calls()), sar.MAX_SEND_ATTEMPTS)

    def test_cloudflare_failure_fails_run_and_sends_nothing(self):
        fake = FakeCloudflareAndResend(totals_status=403)
        code, lines = run_report(fake)
        self.assertEqual(code, 1)
        self.assertEqual(fake.resend_calls(), [])
        self.assertTrue(any("check_token_permission" in line for line in lines))

    def test_breakdown_failure_keeps_report_and_hides_digits(self):
        fake = FakeCloudflareAndResend(breakdown_error_for="refererHost")
        code, lines = run_report(fake)
        self.assertEqual(code, 0)
        warning = next(line for line in lines if "unavailable" in line)
        self.assertNotRegex(warning, r"\d")
        (resend,) = fake.resend_calls()
        self.assertIn("取得できませんでした", resend[3]["text"])

    def test_manual_suffix_changes_key(self):
        fake = FakeCloudflareAndResend()
        run_report(fake, key_suffix="manual-123-1")
        self.assertEqual(fake.resend_calls()[0][2]["Idempotency-Key"], "jlrw-access-report-2026-10-06-manual-123-1")

    def test_dry_run_needs_no_mail_settings_and_sends_nothing(self):
        env = {k: v for k, v in FULL_ENV.items() if k.startswith("CLOUDFLARE_")}
        fake = FakeCloudflareAndResend()
        code, lines = run_report(fake, env=env, dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("status=dry_run", lines)
        self.assertEqual(fake.resend_calls(), [])

    def test_log_never_contains_figures_address_or_keys(self):
        fake = FakeCloudflareAndResend(breakdown_error_for="countryName")
        _, lines = run_report(fake, periods="all")
        log = "\n".join(lines)
        for secret in (RECIPIENT, TOKEN, RESEND_KEY, ACCOUNT, SITE):
            self.assertNotIn(secret, log)
        figure_lines = [line for line in lines if not line.startswith("report_date=")]
        self.assertFalse(any(re.search(r"\d", line) for line in figure_lines), figure_lines)


class RenderTests(unittest.TestCase):
    def setUp(self):
        fake = FakeCloudflareAndResend()
        self.stats = sar.collect(fake, sar.Config.from_env(FULL_ENV), ["daily"], date(2026, 10, 6))

    def test_html_escapes_visitor_supplied_labels(self):
        body = sar.render_html(self.stats, date(2026, 10, 6))
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_empty_referrer_is_labelled_direct(self):
        text = sar.render_text(self.stats, date(2026, 10, 6))
        self.assertIn("（直接アクセス・不明）", text)

    def test_text_states_visits_are_not_unique_users(self):
        text = sar.render_text(self.stats, date(2026, 10, 6))
        self.assertIn("ユニークユーザー数ではありません", text)
        self.assertIn("推定値", text)

    def test_change_text(self):
        self.assertEqual(sar.change_text(12, 10), "+2（+20.0%）")
        self.assertEqual(sar.change_text(5, 10), "-5（-50.0%）")
        self.assertEqual(sar.change_text(10, 10), "±0（0.0%）")
        self.assertEqual(sar.change_text(0, 0), "±0")
        self.assertIn("増減率なし", sar.change_text(3, 0))


class ListSitesTests(unittest.TestCase):
    def test_falls_back_to_tags_when_hostname_dimension_is_rejected(self):
        calls = []

        def http(method, url, *, headers, body=None, timeout=None):
            query = json.loads(body.decode("utf-8"))["query"]
            calls.append(query)
            if "requestHost" in query:
                return 200, {"data": None, "errors": [{"message": "unknown field requestHost"}]}
            return 200, rows_payload([{"count": 1, "dimensions": {"siteTag": SITE}}])

        lines = []
        code = sar.run_list_sites(config=sar.Config.from_env(FULL_ENV), http=http, now=TUESDAY, out=lines.append)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertIn(f"site_tag={SITE} host=unknown", lines)


class SiteIntegrationTests(unittest.TestCase):
    ANALYTICS_JS = (REPO_ROOT / "docs" / "analytics.js").read_text(encoding="utf-8")
    WORKFLOW = (REPO_ROOT / ".github" / "workflows" / "access-report.yml").read_text(encoding="utf-8")

    def test_loader_uses_only_the_cloudflare_beacon_host(self):
        hosts = set(re.findall(r"https?://([^/\"']+)", self.ANALYTICS_JS))
        self.assertEqual(hosts, {"static.cloudflareinsights.com"})
        self.assertIn("spa: false", self.ANALYTICS_JS)
        self.assertNotIn("document.cookie", self.ANALYTICS_JS)

    def test_token_is_empty_or_well_formed(self):
        token = re.search(r'CLOUDFLARE_WEB_ANALYTICS_TOKEN = "([^"]*)"', self.ANALYTICS_JS).group(1)
        self.assertRegex(token, r"^(|[0-9a-f]{32})$")

    def test_pages_load_the_loader_and_no_other_third_party_script(self):
        for path, prefix in (("docs/index.html", ""), ("docs/alerts/thank-you.html", "../")):
            page = (REPO_ROOT / path).read_text(encoding="utf-8")
            self.assertRegex(page, re.escape(f'<script src="{prefix}analytics.js?v=') + r"[\w-]+")
            self.assertNotRegex(page, r"<script[^>]+src=\"https?://")

    def test_workflow_is_read_only_and_scheduled_for_morning_jst(self):
        self.assertIn('cron: "50 23 * * *"', self.WORKFLOW)
        self.assertIn("contents: read", self.WORKFLOW)
        self.assertNotIn("contents: write", self.WORKFLOW)
        self.assertNotIn("git push", self.WORKFLOW)
        self.assertIn("ACCESS_REPORT_TO: ${{ secrets.ACCESS_REPORT_TO }}", self.WORKFLOW)
        self.assertIn("CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}", self.WORKFLOW)


if __name__ == "__main__":
    unittest.main()
