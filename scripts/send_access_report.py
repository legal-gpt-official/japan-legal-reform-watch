#!/usr/bin/env python3
"""Email the site owner a dashboard access report (administrator-facing only).

Reads Cloudflare Web Analytics through the GraphQL Analytics API and sends one
Japanese-language email through Resend to a single owner address:

* every day: the previous JST day, compared with the day before it;
* on Mondays (JST): also the previous Monday-Sunday week, against the week before;
* on the 1st (JST): also the previous calendar month, against the month before.

Periods are JST calendar days converted to UTC instants, so "yesterday" means
the owner's yesterday rather than Cloudflare's UTC day.

Figures are Cloudflare's: ``count`` (page views) and ``sum.visits`` (a page view
whose referrer is not this site). Both are sample-based estimates, and visits
are NOT unique visitors -- the email says so.

Privacy: this repository and its Actions logs are public. The recipient address,
the API token, and every traffic figure stay out of stdout and the log; only
status words, period labels and field-free error categories are printed.

Exit status: 0 when sent, when the report was already sent today (Resend 409),
or when the feature is not configured yet (a ``::warning::``); 1 when Cloudflare
or Resend fails, so a broken report shows up as a failed scheduled run.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from alert_common import DASHBOARD_URL, JST, write_text_atomic  # noqa: E402
from send_alert_digests import EMAIL_RE, default_http  # noqa: E402

CLOUDFLARE_GRAPHQL = "https://api.cloudflare.com/client/v4/graphql"
CLOUDFLARE_DASHBOARD = "https://dash.cloudflare.com/?to=/:account/web-analytics"
RESEND_API = "https://api.resend.com"
USER_AGENT = "jlrw-access-report/1"

# Cloudflare caps the time range of one adaptive-dataset query; a week per
# request stays well inside it, and the counts are additive across chunks.
CHUNK_DAYS = 7
GROUP_LIMIT = 1000
TOP_N = 5
MAX_SEND_ATTEMPTS = 3

HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
WEEKDAYS_JA = ("月", "火", "水", "木", "金", "土", "日")
PERIOD_KINDS = ("daily", "weekly", "monthly")

HttpFunc = Callable[..., tuple[int, Any]]


class CloudflareError(Exception):
    """Cloudflare could not answer; the message is a safe category, never a body."""


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    api_token: str
    account_id: str
    site_tag: str
    resend_api_key: str
    from_email: str
    to_email: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        return cls(
            api_token=env.get("CLOUDFLARE_API_TOKEN", "").strip(),
            account_id=env.get("CLOUDFLARE_ACCOUNT_ID", "").strip().lower(),
            site_tag=env.get("CLOUDFLARE_WEB_ANALYTICS_SITE_TAG", "").strip().lower(),
            resend_api_key=env.get("RESEND_API_KEY", "").strip(),
            from_email=env.get("ALERT_FROM_EMAIL", "").strip(),
            to_email=env.get("ACCESS_REPORT_TO", "").strip(),
        )

    def missing_for_cloudflare(self) -> list[str]:
        """Names of absent or malformed settings (names only, never values)."""
        problems = []
        if not self.api_token:
            problems.append("CLOUDFLARE_API_TOKEN")
        if not HEX32_RE.fullmatch(self.account_id):
            problems.append("CLOUDFLARE_ACCOUNT_ID")
        return problems

    def missing(self) -> list[str]:
        problems = self.missing_for_cloudflare()
        if not HEX32_RE.fullmatch(self.site_tag):
            problems.append("CLOUDFLARE_WEB_ANALYTICS_SITE_TAG")
        if not self.resend_api_key:
            problems.append("RESEND_API_KEY")
        if not self.from_email or "@" not in self.from_email:
            problems.append("ALERT_FROM_EMAIL")
        if not EMAIL_RE.fullmatch(self.to_email):
            problems.append("ACCESS_REPORT_TO")
        return problems


# --------------------------------------------------------------------------
# Periods
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Period:
    kind: str
    start: date  # inclusive JST day
    end: date  # exclusive JST day

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    def utc_bounds(self) -> tuple[str, str]:
        return jst_midnight_utc(self.start), jst_midnight_utc(self.end)


def jst_midnight_utc(day: date) -> str:
    instant = datetime.combine(day, dt_time(0, 0), tzinfo=JST).astimezone(timezone.utc)
    return instant.strftime("%Y-%m-%dT%H:%M:%SZ")


def jst_today(now: datetime | None = None) -> date:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return current.astimezone(JST).date()


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _previous_month_start(first_of_month: date) -> date:
    return _month_start(first_of_month - timedelta(days=1))


def period_pair(kind: str, today: date) -> tuple[Period, Period]:
    """(reported period, comparison period), both ending before ``today``."""
    if kind == "daily":
        current = Period(kind, today - timedelta(days=1), today)
        return current, Period(kind, current.start - timedelta(days=1), current.start)
    if kind == "weekly":
        # The most recent complete Monday-Sunday week before today.
        end = today - timedelta(days=today.weekday())
        current = Period(kind, end - timedelta(days=7), end)
        return current, Period(kind, current.start - timedelta(days=7), current.start)
    if kind == "monthly":
        end = _month_start(today)
        current = Period(kind, _previous_month_start(end), end)
        return current, Period(kind, _previous_month_start(current.start), current.start)
    raise ValueError(f"unknown period kind: {kind}")


def scheduled_kinds(today: date) -> list[str]:
    kinds = ["daily"]
    if today.weekday() == 0:
        kinds.append("weekly")
    if today.day == 1:
        kinds.append("monthly")
    return kinds


def parse_kinds(value: str, today: date) -> list[str]:
    value = (value or "auto").strip().lower()
    if value == "auto":
        return scheduled_kinds(today)
    if value == "all":
        return list(PERIOD_KINDS)
    kinds = [part.strip() for part in value.split(",") if part.strip()]
    unknown = [kind for kind in kinds if kind not in PERIOD_KINDS]
    if unknown or not kinds:
        raise ValueError("periods must be auto, all, or a comma list of daily/weekly/monthly")
    return [kind for kind in PERIOD_KINDS if kind in kinds]


def chunks(start: date, end: date, size_days: int = CHUNK_DAYS) -> list[tuple[date, date]]:
    out = []
    cursor = start
    while cursor < end:
        stop = min(cursor + timedelta(days=size_days), end)
        out.append((cursor, stop))
        cursor = stop
    return out


# --------------------------------------------------------------------------
# Cloudflare GraphQL
# --------------------------------------------------------------------------

_FILTER = "filter: { siteTag: $siteTag, datetime_geq: $start, datetime_lt: $end }"
_VARIABLES = "$accountTag: String!, $siteTag: String!, $start: Time!, $end: Time!"

TOTALS_QUERY = (
    f"query JlrwAccessTotals({_VARIABLES}) {{ viewer {{ accounts(filter: {{ accountTag: $accountTag }}) {{ "
    f"rumPageloadEventsAdaptiveGroups(limit: 1, {_FILTER}) {{ count sum {{ visits }} }} }} }} }}"
)

# Breakdown dimension -> email heading. Each is its own request, so a dimension
# Cloudflare rejects costs only that table, never the totals.
BREAKDOWNS = (
    ("requestPath", "閲覧の多いページ"),
    ("refererHost", "流入元"),
    ("countryName", "国・地域"),
)


def breakdown_query(dimension: str) -> str:
    if dimension not in {name for name, _ in BREAKDOWNS}:
        raise ValueError("unknown breakdown dimension")
    return (
        f"query JlrwAccessBreakdown({_VARIABLES}) {{ viewer {{ accounts(filter: {{ accountTag: $accountTag }}) {{ "
        f"rumPageloadEventsAdaptiveGroups(limit: {GROUP_LIMIT}, {_FILTER}, orderBy: [count_DESC]) "
        f"{{ count sum {{ visits }} dimensions {{ {dimension} }} }} }} }} }}"
    )


def sites_query(dimensions: str) -> str:
    return (
        "query JlrwAccessSites($accountTag: String!, $start: Time!, $end: Time!) { viewer { "
        "accounts(filter: { accountTag: $accountTag }) { rumPageloadEventsAdaptiveGroups(limit: 100, "
        "filter: { datetime_geq: $start, datetime_lt: $end }, orderBy: [count_DESC]) "
        f"{{ count dimensions {{ {dimensions} }} }} }} }} }}"
    )


def graphql(http: HttpFunc, config: Config, query: str, variables: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return the dataset rows for the single account, or raise CloudflareError."""
    body = json.dumps({"query": query, "variables": dict(variables)}).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {config.api_token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    try:
        status, payload = http("POST", CLOUDFLARE_GRAPHQL, headers=headers, body=body)
    except (OSError, ValueError) as exc:
        raise CloudflareError("network_error") from exc
    if status in (401, 403):
        raise CloudflareError(f"http_{status}_check_token_permission")
    if not 200 <= status < 300 or not isinstance(payload, Mapping):
        raise CloudflareError(f"http_{status}")
    errors = payload.get("errors")
    if errors:
        raise CloudflareError("graphql_error: " + _safe_error_text(errors))
    try:
        accounts = payload["data"]["viewer"]["accounts"]
    except (KeyError, TypeError) as exc:
        raise CloudflareError("unexpected_response_shape") from exc
    if not isinstance(accounts, list) or len(accounts) != 1:
        raise CloudflareError("account_not_visible_to_token")
    rows = accounts[0].get("rumPageloadEventsAdaptiveGroups") if isinstance(accounts[0], Mapping) else None
    if not isinstance(rows, list):
        raise CloudflareError("unexpected_response_shape")
    return [row for row in rows if isinstance(row, Mapping)]


def _safe_error_text(errors: Any) -> str:
    """First GraphQL error message, trimmed. Schema errors carry no secrets or figures."""
    first = errors[0] if isinstance(errors, list) and errors else {}
    message = first.get("message") if isinstance(first, Mapping) else None
    text = re.sub(r"\s+", " ", str(message or "unknown"))
    text = re.sub(r"\d", "#", text)  # never echo a figure into the public log
    return text[:160]


def _row_numbers(row: Mapping[str, Any]) -> tuple[int, int]:
    def as_int(value: Any) -> int:
        try:
            return max(int(round(float(value))), 0)
        except (TypeError, ValueError):
            return 0

    total = row.get("sum")
    visits = total.get("visits") if isinstance(total, Mapping) else 0
    return as_int(row.get("count")), as_int(visits)


@dataclass
class Breakdown:
    heading: str
    rows: list[tuple[str, int, int]] = field(default_factory=list)  # label, page views, visits
    error: str = ""


@dataclass
class PeriodStats:
    period: Period
    page_views: int
    visits: int
    previous_page_views: int
    previous_visits: int
    breakdowns: list[Breakdown] = field(default_factory=list)


def _variables(config: Config, start: date, end: date) -> dict[str, str]:
    return {
        "accountTag": config.account_id,
        "siteTag": config.site_tag,
        "start": jst_midnight_utc(start),
        "end": jst_midnight_utc(end),
    }


def fetch_totals(http: HttpFunc, config: Config, period: Period) -> tuple[int, int]:
    page_views = visits = 0
    for start, end in chunks(period.start, period.end):
        for row in graphql(http, config, TOTALS_QUERY, _variables(config, start, end)):
            row_views, row_visits = _row_numbers(row)
            page_views += row_views
            visits += row_visits
    return page_views, visits


def fetch_breakdown(http: HttpFunc, config: Config, period: Period, dimension: str, heading: str) -> Breakdown:
    merged: dict[str, list[int]] = {}
    try:
        for start, end in chunks(period.start, period.end):
            for row in graphql(http, config, breakdown_query(dimension), _variables(config, start, end)):
                dims = row.get("dimensions")
                label = str(dims.get(dimension) or "") if isinstance(dims, Mapping) else ""
                row_views, row_visits = _row_numbers(row)
                bucket = merged.setdefault(label, [0, 0])
                bucket[0] += row_views
                bucket[1] += row_visits
    except CloudflareError as exc:
        return Breakdown(heading, error=str(exc))
    ranked = sorted(merged.items(), key=lambda item: (-item[1][0], -item[1][1], item[0]))
    return Breakdown(heading, [(label, views, visits) for label, (views, visits) in ranked[:TOP_N]])


def collect(http: HttpFunc, config: Config, kinds: Sequence[str], today: date) -> list[PeriodStats]:
    stats = []
    for kind in kinds:
        current, previous = period_pair(kind, today)
        page_views, visits = fetch_totals(http, config, current)
        previous_views, previous_visits = fetch_totals(http, config, previous)
        breakdowns = [fetch_breakdown(http, config, current, name, heading) for name, heading in BREAKDOWNS]
        stats.append(PeriodStats(current, page_views, visits, previous_views, previous_visits, breakdowns))
    return stats


def list_sites(http: HttpFunc, config: Config, today: date) -> list[tuple[str, str]]:
    """(siteTag, hostname) pairs that received page views in the last 7 JST days."""
    seen: dict[str, str] = {}
    variables = {
        "accountTag": config.account_id,
        "start": jst_midnight_utc(today - timedelta(days=7)),
        "end": jst_midnight_utc(today + timedelta(days=1)),
    }
    try:
        rows = graphql(http, config, sites_query("siteTag requestHost"), variables)
    except CloudflareError as exc:
        if not str(exc).startswith("graphql_error"):
            raise
        # The hostname only helps tell sites apart; fall back to tags alone.
        rows = graphql(http, config, sites_query("siteTag"), variables)
    for row in rows:
        dims = row.get("dimensions")
        if not isinstance(dims, Mapping):
            continue
        tag = str(dims.get("siteTag") or "").lower()
        host = str(dims.get("requestHost") or "")
        if not re.fullmatch(r"[A-Za-z0-9.-]{0,253}", host):
            host = ""
        if HEX32_RE.fullmatch(tag) and (tag not in seen or not seen[tag]):
            seen[tag] = host
    return sorted(seen.items(), key=lambda item: (item[1], item[0]))


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

KIND_TITLES = {"daily": "日次", "weekly": "週次", "monthly": "月次"}
COMPARISON_LABELS = {"daily": "前日比", "weekly": "前週比", "monthly": "前月比"}


def _day_label(day: date) -> str:
    return f"{day.year}年{day.month}月{day.day}日（{WEEKDAYS_JA[day.weekday()]}）"


def period_label(period: Period) -> str:
    last = period.end - timedelta(days=1)
    if period.kind == "daily":
        return _day_label(period.start)
    if period.kind == "monthly":
        return f"{period.start.year}年{period.start.month}月（{period.start.month}/1〜{last.month}/{last.day}）"
    return f"{_day_label(period.start)}〜{_day_label(last)}"


def change_text(current: int, previous: int) -> str:
    diff = current - previous
    sign = "+" if diff > 0 else ""
    if previous == 0:
        return f"{sign}{diff:,}（前期間 0 のため増減率なし）" if diff else "±0"
    if diff == 0:
        return "±0（0.0%）"
    return f"{sign}{diff:,}（{sign}{diff / previous * 100:.1f}%）"


def breakdown_label(heading: str, label: str) -> str:
    if label:
        return label
    return "（直接アクセス・不明）" if heading == "流入元" else "（不明）"


NOTES = (
    "「訪問数」は Cloudflare の Visits（他サイトからの流入または直接アクセスで始まった閲覧）で、ユニークユーザー数ではありません。",
    "Cloudflare Web Analytics はサンプリングに基づく推定値です。ダッシュボードの表示と数件ずれることがあります。",
    "期間はすべて日本時間（JST）の 0:00〜24:00 で区切っています。",
    "JavaScript を無効にした閲覧や、一部の広告ブロッカー利用者の閲覧は計測されません。",
)


def render_subject(stats: Sequence[PeriodStats], today: date) -> str:
    kinds = "・".join(KIND_TITLES[item.period.kind] for item in stats)
    return f"[JLRW] アクセスレポート（{kinds}）{today.isoformat()}"


def render_text(stats: Sequence[PeriodStats], today: date) -> str:
    lines = [f"Japan Legal Reform Watch アクセスレポート（{today.isoformat()} 送信）", ""]
    for item in stats:
        comparison = COMPARISON_LABELS[item.period.kind]
        lines += [
            f"■ {KIND_TITLES[item.period.kind]}: {period_label(item.period)}",
            f"  訪問数     {item.visits:,}（{comparison} {change_text(item.visits, item.previous_visits)}）",
            f"  ページビュー {item.page_views:,}（{comparison} {change_text(item.page_views, item.previous_page_views)}）",
        ]
        for breakdown in item.breakdowns:
            lines.append(f"  {breakdown.heading}:")
            if breakdown.error:
                lines.append("    取得できませんでした")
            elif not breakdown.rows:
                lines.append("    データなし")
            for label, views, visits in breakdown.rows:
                lines.append(f"    - {breakdown_label(breakdown.heading, label)}: {views:,} PV / {visits:,} 訪問")
        lines.append("")
    lines.append("注記")
    lines += [f"- {note}" for note in NOTES]
    lines += ["", f"ダッシュボード: {DASHBOARD_URL}", f"Cloudflare Web Analytics: {CLOUDFLARE_DASHBOARD}"]
    return "\n".join(lines) + "\n"


def render_html(stats: Sequence[PeriodStats], today: date) -> str:
    esc = html.escape
    cell = "padding:4px 10px;border-bottom:1px solid #e5e7eb;"
    parts = [
        '<div style="font-family:-apple-system,Segoe UI,Hiragino Sans,Meiryo,sans-serif;color:#1f2937;max-width:640px;">',
        f'<h2 style="font-size:18px;margin:0 0 12px;">Japan Legal Reform Watch アクセスレポート</h2>',
        f'<p style="color:#6b7280;margin:0 0 16px;">{esc(today.isoformat())} 送信</p>',
    ]
    for item in stats:
        comparison = COMPARISON_LABELS[item.period.kind]
        parts += [
            f'<h3 style="font-size:16px;margin:20px 0 8px;">{esc(KIND_TITLES[item.period.kind])}: {esc(period_label(item.period))}</h3>',
            '<table style="border-collapse:collapse;font-size:14px;">',
            f'<tr><td style="{cell}">訪問数</td><td style="{cell}text-align:right;"><strong>{item.visits:,}</strong></td>'
            f'<td style="{cell}color:#6b7280;">{esc(comparison)} {esc(change_text(item.visits, item.previous_visits))}</td></tr>',
            f'<tr><td style="{cell}">ページビュー</td><td style="{cell}text-align:right;"><strong>{item.page_views:,}</strong></td>'
            f'<td style="{cell}color:#6b7280;">{esc(comparison)} {esc(change_text(item.page_views, item.previous_page_views))}</td></tr>',
            "</table>",
        ]
        for breakdown in item.breakdowns:
            parts.append(f'<p style="margin:12px 0 4px;font-weight:600;">{esc(breakdown.heading)}</p>')
            if breakdown.error or not breakdown.rows:
                message = "取得できませんでした" if breakdown.error else "データなし"
                parts.append(f'<p style="margin:0;color:#6b7280;font-size:13px;">{message}</p>')
                continue
            parts.append('<table style="border-collapse:collapse;font-size:13px;">')
            for label, views, visits in breakdown.rows:
                # Paths and referrer hosts come from visitors' browsers: escape, never link.
                parts.append(
                    f'<tr><td style="{cell}">{esc(breakdown_label(breakdown.heading, label))}</td>'
                    f'<td style="{cell}text-align:right;">{views:,} PV</td>'
                    f'<td style="{cell}text-align:right;">{visits:,} 訪問</td></tr>'
                )
            parts.append("</table>")
    parts.append('<ul style="margin:20px 0 0;padding-left:18px;color:#6b7280;font-size:12px;">')
    parts += [f"<li>{esc(note)}</li>" for note in NOTES]
    parts.append("</ul>")
    parts.append(
        f'<p style="font-size:12px;color:#6b7280;"><a href="{esc(DASHBOARD_URL)}">ダッシュボード</a> ・ '
        f'<a href="{esc(CLOUDFLARE_DASHBOARD)}">Cloudflare Web Analytics</a></p></div>'
    )
    return "".join(parts)


# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------


def send_email(
    http: HttpFunc,
    config: Config,
    *,
    subject: str,
    html_body: str,
    text_body: str,
    idempotency_key: str,
    sleep: Callable[[float], None],
) -> str:
    """Return 'sent', 'duplicate', or 'failed'. Never raises for HTTP problems."""
    body = json.dumps(
        {
            "from": config.from_email,
            "to": [config.to_email],
            "subject": subject,
            "html": html_body,
            "text": text_body,
            "tags": [{"name": "kind", "value": "access_report"}],
        }
    ).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {config.resend_api_key}",
        "Content-Type": "application/json",
        "Idempotency-Key": idempotency_key,
        "User-Agent": USER_AGENT,
    }
    for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
        try:
            status, _ = http("POST", f"{RESEND_API}/emails", headers=headers, body=body)
        except (OSError, ValueError):
            status = 0
        if 200 <= status < 300:
            return "sent"
        if status == 409:
            return "duplicate"
        if (status in (0, 429) or status >= 500) and attempt < MAX_SEND_ATTEMPTS:
            sleep(2.0 * attempt)
            continue
        return "failed"
    return "failed"


def idempotency_key(today: date, suffix: str = "") -> str:
    key = f"jlrw-access-report-{today.isoformat()}"
    suffix = re.sub(r"[^A-Za-z0-9-]", "", suffix or "")[:40]
    return f"{key}-{suffix}" if suffix else key


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def run(
    *,
    config: Config,
    http: HttpFunc = default_http,
    now: datetime | None = None,
    periods: str = "auto",
    dry_run: bool = False,
    preview_dir: Path | None = None,
    key_suffix: str = "",
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> int:
    today = jst_today(now)
    kinds = parse_kinds(periods, today)
    out(f"report_date={today.isoformat()}")
    out("periods=" + ",".join(kinds))

    # A dry run only reads Cloudflare, so it needs no mail settings.
    missing = [
        name for name in config.missing()
        if not dry_run or name.startswith("CLOUDFLARE_")
    ]
    if missing:
        out("status=not_configured")
        out("missing_settings=" + ",".join(missing))
        out("::warning::Access report not sent: missing " + ", ".join(missing))
        return 0

    try:
        stats = collect(http, config, kinds, today)
    except CloudflareError as exc:
        out("status=cloudflare_error")
        out(f"::error::Cloudflare Web Analytics query failed: {exc}")
        return 1

    for item in stats:
        for breakdown in item.breakdowns:
            if breakdown.error:
                out(f"::warning::{item.period.kind} breakdown '{breakdown.heading}' unavailable: {breakdown.error}")

    subject = render_subject(stats, today)
    text_body = render_text(stats, today)
    html_body = render_html(stats, today)
    if preview_dir is not None:
        preview_dir.mkdir(parents=True, exist_ok=True)
        write_text_atomic(preview_dir / "access_report.txt", subject + "\n\n" + text_body)
        write_text_atomic(preview_dir / "access_report.html", html_body)
        out("preview_written=true")
    if dry_run:
        out("status=dry_run")
        return 0

    result = send_email(
        http,
        config,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        idempotency_key=idempotency_key(today, key_suffix),
        sleep=sleep,
    )
    out(f"status={result}")
    if result == "failed":
        out("::error::Access report email could not be sent through Resend")
        return 1
    return 0


def run_list_sites(*, config: Config, http: HttpFunc = default_http, now: datetime | None = None,
                   out: Callable[[str], None] = print) -> int:
    missing = config.missing_for_cloudflare()
    if missing:
        out("status=not_configured")
        out("missing_settings=" + ",".join(missing))
        return 1
    try:
        sites = list_sites(http, config, jst_today(now))
    except CloudflareError as exc:
        out(f"::error::Cloudflare Web Analytics query failed: {exc}")
        return 1
    if not sites:
        out("No page views in the last 7 days yet. Open the dashboard once, wait a few minutes, and retry.")
    # A site tag and hostname are identifiers, not traffic figures or secrets.
    for tag, host in sites:
        out(f"site_tag={tag} host={host or 'unknown'}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--periods", default="auto", help="auto (default), all, or e.g. daily,weekly")
    parser.add_argument("--dry-run", action="store_true", help="query Cloudflare but do not send")
    parser.add_argument("--preview-dir", type=Path, help="write the rendered email here (local use only)")
    parser.add_argument("--idempotency-key-suffix", default="", help="distinguishes a manual resend from the daily one")
    parser.add_argument("--list-sites", action="store_true", help="print site tags that received page views recently")
    args = parser.parse_args(argv)
    config = Config.from_env(os.environ)
    if args.list_sites:
        return run_list_sites(config=config)
    try:
        return run(
            config=config,
            periods=args.periods,
            dry_run=args.dry_run,
            preview_dir=args.preview_dir,
            key_suffix=args.idempotency_key_suffix,
        )
    except ValueError as exc:
        print(f"::error::{exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
