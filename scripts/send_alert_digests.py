#!/usr/bin/env python3
"""Send the paid daily email digest (optional, runs after the public data build).

Self-serve by design: nobody activates, reviews, or reconciles a subscription.

* Subscribers are the Stripe subscriptions for ALERT_STRIPE_PRODUCT_ID that are
  ``active``, ``trialing`` or ``past_due``. The monitoring channel is the value
  the customer chose in the Checkout dropdown (custom field ``area``); the
  recipient is the Stripe customer's email, which the customer can change in the
  Stripe customer portal. Cancelling there removes them on the next run.
* Content is only what the public dashboard already publishes: items first
  detected in the last few days, plus open public comments whose structured
  deadline is 7 days, 3 days, or 0 days away. No new AI output is generated and
  no record is changed.
* Mail goes out through the Resend API, one recipient per request, with an
  Idempotency-Key per subscription per JST day so a same-day rerun cannot send
  twice.

Privacy: subscriber email addresses are read from Stripe at run time and exist
only in memory. They are never written to the repository, the state file, the
preview files, or the logs -- this repository and its Actions logs are public.

State: ``data/alert_digest_state.json`` records only published item ids that
have already been considered, so an item is announced once even though the
lookback window spans several days. It is written only after Stripe was read
successfully, so a Stripe outage defers items to the next run instead of
dropping them.

Exit status is 0 for every external condition (not configured, Stripe or Resend
unavailable, individual send failures) so the daily data commit is never blocked
by mail delivery; problems surface as ``::warning::`` annotations.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlencode, urlsplit

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from alert_common import (  # noqa: E402
    ALL_AREAS,
    CHANNELS,
    CHANNELS_BY_VALUE,
    DASHBOARD_URL,
    DISCLAIMER_TEXT,
    JST,
    NEWLY_DETECTED_NOTE,
    Channel,
    clean_text,
    comment_deadline_display,
    dashboard_channel_url,
    item_in_channel,
    open_comment_deadline,
    safe_http_url,
    source_display_name,
    strict_iso_date,
    summary_is_ai,
    write_text_atomic,
)

REPO_ROOT = SCRIPTS_DIR.parent
DATA_PATH = REPO_ROOT / "docs" / "data" / "legal_updates.json"
STATE_PATH = REPO_ROOT / "data" / "alert_digest_state.json"
STATE_SCHEMA_VERSION = 1

STRIPE_API = "https://api.stripe.com"
RESEND_API = "https://api.resend.com"
USER_AGENT = "jlrw-alert-digest/1"
HTTP_TIMEOUT_SECONDS = 30

# Items first detected within this many days (inclusive of today) are eligible,
# so a skipped or failed run is caught up by the next one. The state file keeps
# them from being announced twice.
LOOKBACK_DAYS = 3
# Processed ids older than this are pruned; it must exceed LOOKBACK_DAYS.
STATE_RETENTION_DAYS = 14
DEADLINE_HORIZON_DAYS = 7
# A consultation triggers an email on these days-remaining values only; on other
# days it is listed if an email is being sent anyway, but never causes one.
REMINDER_DAYS = (7, 3, 0)
MAX_NEW_ITEMS_PER_EMAIL = 25
MAX_DEADLINES_PER_EMAIL = 20
DELIVERABLE_STATUSES = ("active", "trialing", "past_due")
AREA_FIELD_KEY = "area"
# Resend's default limit is a few requests per second per team.
SEND_INTERVAL_SECONDS = 0.6
MAX_SEND_ATTEMPTS = 3

EMAIL_RE = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[^@\s<>\"',;]+$")

HttpFunc = Callable[..., tuple[int, Any]]


class StripeUnavailable(Exception):
    """Stripe could not be read; nothing may be sent or recorded this run."""


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    stripe_api_key: str
    resend_api_key: str
    product_id: str
    from_email: str
    manage_url: str
    reply_to: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        return cls(
            stripe_api_key=env.get("STRIPE_ALERTS_API_KEY", "").strip(),
            resend_api_key=env.get("RESEND_API_KEY", "").strip(),
            product_id=env.get("ALERT_STRIPE_PRODUCT_ID", "").strip(),
            from_email=env.get("ALERT_FROM_EMAIL", "").strip(),
            manage_url=env.get("ALERT_MANAGE_URL", "").strip(),
            reply_to=env.get("ALERT_REPLY_TO", "").strip(),
        )

    def missing(self) -> list[str]:
        """Names of absent or malformed settings (names only, never values)."""
        problems = []
        if not self.stripe_api_key:
            problems.append("STRIPE_ALERTS_API_KEY")
        if not self.resend_api_key:
            problems.append("RESEND_API_KEY")
        if not re.fullmatch(r"prod_[A-Za-z0-9]+", self.product_id):
            problems.append("ALERT_STRIPE_PRODUCT_ID")
        if not self.from_email or "@" not in self.from_email:
            problems.append("ALERT_FROM_EMAIL")
        if not valid_manage_url(self.manage_url):
            problems.append("ALERT_MANAGE_URL")
        return problems


def valid_manage_url(value: str) -> bool:
    url = safe_http_url(value)
    if not url:
        return False
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname == "billing.stripe.com" and parsed.port is None


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def default_http(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    body: bytes | None = None,
    timeout: float = HTTP_TIMEOUT_SECONDS,
) -> tuple[int, Any]:
    """Minimal JSON HTTP call. Error bodies are returned, never printed."""
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        status = exc.code
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    return status, payload


# --------------------------------------------------------------------------
# Stripe (read-only)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Subscriber:
    subscription_id: str
    email: str
    channel: Channel
    channel_recognized: bool


def _stripe_get(http: HttpFunc, api_key: str, path: str, params: Sequence[tuple[str, str]]) -> Mapping[str, Any]:
    url = f"{STRIPE_API}{path}?{urlencode(list(params))}"
    try:
        status, payload = http(
            "GET",
            url,
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT},
        )
    except (OSError, ValueError) as exc:
        raise StripeUnavailable(type(exc).__name__) from exc
    if status != 200 or not isinstance(payload, dict):
        raise StripeUnavailable(f"http_{status}")
    return payload


def _stripe_list(
    http: HttpFunc, api_key: str, path: str, params: Sequence[tuple[str, str]]
) -> Iterable[Mapping[str, Any]]:
    starting_after = ""
    for _ in range(1000):  # hard stop: 100k objects
        page_params = list(params) + [("limit", "100")]
        if starting_after:
            page_params.append(("starting_after", starting_after))
        payload = _stripe_get(http, api_key, path, page_params)
        data = payload.get("data")
        if not isinstance(data, list):
            raise StripeUnavailable("unexpected_list_shape")
        for obj in data:
            if isinstance(obj, dict):
                yield obj
        if not payload.get("has_more") or not data:
            return
        last = data[-1]
        starting_after = last.get("id", "") if isinstance(last, dict) else ""
        if not starting_after:
            raise StripeUnavailable("unexpected_list_shape")
    raise StripeUnavailable("pagination_limit")


def _subscription_has_product(subscription: Mapping[str, Any], product_id: str) -> bool:
    items = subscription.get("items")
    data = items.get("data") if isinstance(items, dict) else None
    if not isinstance(data, list):
        return False
    for item in data:
        price = item.get("price") if isinstance(item, dict) else None
        product = price.get("product") if isinstance(price, dict) else None
        if isinstance(product, dict):
            product = product.get("id")
        if product == product_id:
            return True
    return False


def channel_from_session(session: Mapping[str, Any] | None) -> tuple[Channel, bool]:
    fields = session.get("custom_fields") if isinstance(session, dict) else None
    if isinstance(fields, list):
        for entry in fields:
            if not isinstance(entry, dict) or entry.get("key") != AREA_FIELD_KEY:
                continue
            dropdown = entry.get("dropdown")
            value = dropdown.get("value") if isinstance(dropdown, dict) else None
            if isinstance(value, str) and value in CHANNELS_BY_VALUE:
                return CHANNELS_BY_VALUE[value], True
    # A subscription without a recognizable choice still receives mail rather
    # than silently nothing; the run reports how many fell back.
    return ALL_AREAS, False


def _customer_email(subscription: Mapping[str, Any], session: Mapping[str, Any] | None) -> str:
    customer = subscription.get("customer")
    if isinstance(customer, dict) and not customer.get("deleted"):
        email = clean_text(customer.get("email"))
        if EMAIL_RE.match(email):
            return email
    details = session.get("customer_details") if isinstance(session, dict) else None
    email = clean_text(details.get("email")) if isinstance(details, dict) else ""
    return email if EMAIL_RE.match(email) else ""


def list_subscribers(http: HttpFunc, config: Config) -> tuple[list[Subscriber], int]:
    """Return deliverable subscribers and the number skipped for having no usable email."""
    subscribers: list[Subscriber] = []
    seen: set[str] = set()
    no_email = 0
    for status in DELIVERABLE_STATUSES:
        for subscription in _stripe_list(
            http,
            config.stripe_api_key,
            "/v1/subscriptions",
            [("status", status), ("expand[]", "data.customer")],
        ):
            sub_id = subscription.get("id")
            if not isinstance(sub_id, str) or sub_id in seen:
                continue
            if not _subscription_has_product(subscription, config.product_id):
                continue
            seen.add(sub_id)
            sessions = _stripe_get(
                http,
                config.stripe_api_key,
                "/v1/checkout/sessions",
                [("subscription", sub_id), ("limit", "1")],
            ).get("data")
            session = sessions[0] if isinstance(sessions, list) and sessions else None
            email = _customer_email(subscription, session)
            if not email:
                no_email += 1
                continue
            channel, recognized = channel_from_session(session)
            subscribers.append(Subscriber(sub_id, email, channel, recognized))
    return subscribers, no_email


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


def load_items(path: Path = DATA_PATH) -> list[Mapping[str, Any]]:
    items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError("published data must be a JSON array")
    return [item for item in items if isinstance(item, dict)]


def load_state(path: Path = STATE_PATH) -> set[str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    if not isinstance(raw, dict) or raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("alert digest state has an unexpected shape")
    ids = raw.get("processed_item_ids")
    if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids):
        raise ValueError("alert digest state has an unexpected shape")
    return set(ids)


def next_state(
    items: Sequence[Mapping[str, Any]], processed: set[str], newly_processed: Iterable[str], today: date
) -> list[str]:
    """Keep only ids that can still fall inside a future lookback window."""
    cutoff = today - timedelta(days=STATE_RETENTION_DAYS)
    first_seen = {
        item.get("id"): strict_iso_date(item.get("first_seen_at"))
        for item in items
        if isinstance(item.get("id"), str)
    }
    keep = set()
    for item_id in processed | set(newly_processed):
        seen = first_seen.get(item_id)
        if seen is not None and seen >= cutoff:
            keep.add(item_id)
    return sorted(keep)


def write_state(ids: list[str], path: Path = STATE_PATH) -> None:
    payload = {"schema_version": STATE_SCHEMA_VERSION, "processed_item_ids": ids}
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def select_new_items(
    items: Sequence[Mapping[str, Any]], processed: set[str], today: date
) -> list[Mapping[str, Any]]:
    earliest = today - timedelta(days=LOOKBACK_DAYS - 1)
    selected = []
    for item in items:
        item_id = item.get("id")
        seen = strict_iso_date(item.get("first_seen_at"))
        if not isinstance(item_id, str) or item_id in processed or seen is None:
            continue
        if earliest <= seen <= today:
            selected.append(item)
    return selected  # published array order = the build's relevance ranking


def select_deadlines(items: Sequence[Mapping[str, Any]], today: date) -> list[tuple[Mapping[str, Any], date, int]]:
    upcoming = []
    for item in items:
        deadline = open_comment_deadline(item, today)
        if deadline and deadline[1] <= DEADLINE_HORIZON_DAYS:
            upcoming.append((item, deadline[0], deadline[1]))
    upcoming.sort(key=lambda entry: (entry[2], clean_text(entry[0].get("title_en"))))
    return upcoming


@dataclass
class Digest:
    channel: Channel
    new_items: list[Mapping[str, Any]] = field(default_factory=list)
    deadlines: list[tuple[Mapping[str, Any], date, int]] = field(default_factory=list)

    @property
    def should_send(self) -> bool:
        return bool(self.new_items) or any(days in REMINDER_DAYS for _, _, days in self.deadlines)


def build_digest(
    channel: Channel,
    new_items: Sequence[Mapping[str, Any]],
    deadlines: Sequence[tuple[Mapping[str, Any], date, int]],
) -> Digest:
    return Digest(
        channel=channel,
        new_items=[item for item in new_items if item_in_channel(item, channel)],
        deadlines=[entry for entry in deadlines if item_in_channel(entry[0], channel)],
    )


# --------------------------------------------------------------------------
# Rendering (every record field is untrusted)
# --------------------------------------------------------------------------


def _days_label(days: int) -> str:
    if days == 0:
        return "closes today"
    if days == 1:
        return "1 day left"
    return f"{days} days left"


def render_subject(digest: Digest, today: date) -> str:
    return f"Japan Legal Reform Watch — {digest.channel.label} — {today.strftime('%d %b %Y')}"


def render_text(digest: Digest, today: date, manage_url: str) -> str:
    lines = [
        "Japan Legal Reform Watch by LegalOS — daily digest",
        f"Channel: {digest.channel.label}",
        f"Date: {today.isoformat()} (JST)",
        "",
    ]
    shown = digest.new_items[:MAX_NEW_ITEMS_PER_EMAIL]
    lines.append(f"NEWLY DETECTED ({len(digest.new_items)})")
    lines.append(NEWLY_DETECTED_NOTE)
    lines.append("")
    if not shown:
        lines.extend(("No newly detected updates in this channel since the last digest.", ""))
    for item in shown:
        lines.append(f"- {clean_text(item.get('title_en')) or 'Untitled update'}")
        title_ja = clean_text(item.get("title_ja"))
        if title_ja:
            lines.append(f"  Original title: {title_ja}")
        meta = " | ".join(
            part
            for part in (
                clean_text(item.get("stage")),
                source_display_name(item.get("source_name")),
                f"Published {clean_text(item.get('published_at'))}" if strict_iso_date(item.get("published_at")) else "",
            )
            if part
        )
        if meta:
            lines.append(f"  {meta}")
        if summary_is_ai(item):
            lines.append(f"  AI summary: {clean_text(item.get('summary_en'))}")
        source_url = safe_http_url(item.get("source_url"))
        lines.append(f"  Official source: {source_url or 'URL unavailable'}")
        lines.append("")
    if len(digest.new_items) > len(shown):
        lines.append(f"{len(digest.new_items) - len(shown)} more on the dashboard: {dashboard_channel_url(digest.channel, newly_detected=True)}")
        lines.append("")

    lines.append(f"PUBLIC COMMENT DEADLINES — NEXT {DEADLINE_HORIZON_DAYS} DAYS ({len(digest.deadlines)})")
    if not digest.deadlines:
        lines.append("No open public comments with a structured deadline in this window.")
    for item, _, days in digest.deadlines[:MAX_DEADLINES_PER_EMAIL]:
        lines.append(f"- {clean_text(item.get('title_en')) or 'Untitled consultation'}")
        lines.append(f"  Closes {comment_deadline_display(item.get('comment_deadline'))} — {_days_label(days)}")
        source_url = safe_http_url(item.get("source_url"))
        lines.append(f"  Official source: {source_url or 'URL unavailable'}")
    lines.extend(
        (
            "",
            f"Dashboard: {dashboard_channel_url(digest.channel)}",
            f"Manage or cancel your subscription: {manage_url}",
            "",
            DISCLAIMER_TEXT,
            "",
        )
    )
    return "\n".join(lines)


_E = html.escape


def _link(url: str, label: str) -> str:
    safe = safe_http_url(url)
    if not safe:
        return _E(label + " (URL unavailable)")
    return f'<a href="{_E(safe, quote=True)}" style="color:#174f78;">{_E(label)}</a>'


def render_html(digest: Digest, today: date, manage_url: str) -> str:
    shown = digest.new_items[:MAX_NEW_ITEMS_PER_EMAIL]
    blocks = []
    for item in shown:
        title = _E(clean_text(item.get("title_en")) or "Untitled update")
        title_ja = clean_text(item.get("title_ja"))
        meta = " &middot; ".join(
            _E(part)
            for part in (
                clean_text(item.get("stage")),
                source_display_name(item.get("source_name")),
                f"Published {clean_text(item.get('published_at'))}" if strict_iso_date(item.get("published_at")) else "",
            )
            if part
        )
        summary = (
            f'<p style="margin:6px 0;"><strong>AI summary:</strong> {_E(clean_text(item.get("summary_en")))}</p>'
            if summary_is_ai(item)
            else '<p style="margin:6px 0;color:#5e6b76;">Rule-based preview only &mdash; review the original Japanese source.</p>'
        )
        blocks.append(
            '<div style="padding:14px 0;border-top:1px solid #d8dee4;">'
            f'<p style="margin:0 0 4px;font-weight:700;color:#0a2540;">{title}</p>'
            + (f'<p style="margin:0 0 4px;color:#5e6b76;font-size:13px;">{_E(title_ja)}</p>' if title_ja else "")
            + (f'<p style="margin:0 0 4px;color:#5e6b76;font-size:12px;">{meta}</p>' if meta else "")
            + summary
            + f'<p style="margin:6px 0 0;font-size:13px;">{_link(item.get("source_url"), "Open original Japanese official source")}</p>'
            "</div>"
        )
    if not shown:
        blocks.append('<p style="color:#5e6b76;">No newly detected updates in this channel since the last digest.</p>')
    if len(digest.new_items) > len(shown):
        blocks.append(
            f'<p>{_link(dashboard_channel_url(digest.channel, newly_detected=True), f"{len(digest.new_items) - len(shown)} more on the dashboard")}</p>'
        )

    deadline_rows = []
    for item, _, days in digest.deadlines[:MAX_DEADLINES_PER_EMAIL]:
        deadline_rows.append(
            '<div style="padding:10px 0;border-top:1px solid #d8dee4;">'
            f'<p style="margin:0 0 4px;font-weight:700;color:#0a2540;">{_E(clean_text(item.get("title_en")) or "Untitled consultation")}</p>'
            f'<p style="margin:0 0 4px;font-size:13px;">Closes {_E(comment_deadline_display(item.get("comment_deadline")))} &mdash; <strong>{_E(_days_label(days))}</strong></p>'
            f'<p style="margin:0;font-size:13px;">{_link(item.get("source_url"), "Open original Japanese official source")}</p>'
            "</div>"
        )
    if not deadline_rows:
        deadline_rows.append('<p style="color:#5e6b76;">No open public comments with a structured deadline in this window.</p>')

    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_E(render_subject(digest, today))}</title></head>"
        '<body style="margin:0;background:#f1f3f5;color:#243241;font-family:Arial,sans-serif;line-height:1.5;">'
        '<div style="max-width:680px;margin:0 auto;padding:24px;background:#ffffff;border-top:4px solid #b08a3e;">'
        '<p style="margin:0;color:#8a6a2f;font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;">Daily digest</p>'
        '<h1 style="margin:4px 0 4px;color:#0a2540;font-family:Georgia,serif;font-size:22px;">Japan Legal Reform Watch</h1>'
        f'<p style="margin:0 0 16px;color:#5e6b76;font-size:13px;">{_E(digest.channel.label)} &middot; {_E(today.isoformat())} (JST)</p>'
        f'<h2 style="margin:18px 0 4px;color:#0a2540;font-size:17px;">Newly detected ({len(digest.new_items)})</h2>'
        f'<p style="margin:0 0 8px;color:#5e6b76;font-size:12px;">{_E(NEWLY_DETECTED_NOTE)}</p>'
        + "".join(blocks)
        + f'<h2 style="margin:24px 0 4px;color:#0a2540;font-size:17px;">Public comment deadlines &mdash; next {DEADLINE_HORIZON_DAYS} days ({len(digest.deadlines)})</h2>'
        + "".join(deadline_rows)
        + '<p style="margin:24px 0 6px;font-size:13px;">'
        + _link(dashboard_channel_url(digest.channel), "Open this channel on the dashboard")
        + " &middot; "
        + _link(manage_url, "Manage or cancel your subscription")
        + "</p>"
        f'<p style="margin:16px 0 0;padding-top:12px;border-top:1px solid #d8dee4;color:#5e6b76;font-size:11px;">{_E(DISCLAIMER_TEXT)}</p>'
        "</div></body></html>\n"
    )


# --------------------------------------------------------------------------
# Resend
# --------------------------------------------------------------------------


def send_email(
    http: HttpFunc,
    config: Config,
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str,
    idempotency_key: str,
    channel: Channel,
    sleep: Callable[[float], None],
) -> str:
    """Return 'sent', 'duplicate', or 'failed'. Never raises for HTTP problems."""
    payload: dict[str, Any] = {
        "from": config.from_email,
        "to": [to],  # exactly one recipient: subscribers never see each other
        "subject": subject,
        "html": html_body,
        "text": text_body,
        "tags": [{"name": "channel", "value": channel.value}],
    }
    if config.reply_to:
        payload["reply_to"] = config.reply_to
    body = json.dumps(payload).encode("utf-8")
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
            # Same key already used today: the recipient already has a digest.
            return "duplicate"
        if status in (0, 429) or status >= 500:
            if attempt < MAX_SEND_ATTEMPTS:
                sleep(2.0 * attempt)
                continue
        return "failed"
    return "failed"


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _print_summary(summary: Mapping[str, Any]) -> None:
    for key, value in summary.items():
        print(f"{key}: {value}")


def write_previews(
    preview_dir: Path, digests: Sequence[Digest], today: date, manage_url: str
) -> list[Path]:
    preview_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for digest in digests:
        path = preview_dir / f"digest-{today.isoformat()}-{digest.channel.value}.html"
        path.write_text(render_html(digest, today, manage_url), encoding="utf-8")
        written.append(path)
    return written


def run(
    *,
    config: Config,
    items: Sequence[Mapping[str, Any]],
    processed: set[str],
    now: datetime,
    http: HttpFunc = default_http,
    sleep: Callable[[float], None] = time.sleep,
    dry_run: bool = False,
    state_writer: Callable[[list[str]], None] | None = None,
) -> dict[str, Any]:
    today = now.astimezone(JST).date()
    new_items = select_new_items(items, processed, today)
    deadlines = select_deadlines(items, today)
    summary: dict[str, Any] = {
        "status": "",
        "new_items": len(new_items),
        "upcoming_deadlines": len(deadlines),
        "subscribers": 0,
        "subscribers_unrecognized_channel": 0,
        "subscribers_without_email": 0,
        "emails_sent": 0,
        "emails_duplicate": 0,
        "emails_failed": 0,
        "emails_skipped_nothing_to_report": 0,
        "state_updated": False,
    }

    missing = config.missing()
    if missing:
        summary["status"] = "not_configured"
        summary["missing_settings"] = ",".join(missing)
        return summary

    try:
        subscribers, no_email = list_subscribers(http, config)
    except StripeUnavailable as exc:
        summary["status"] = "stripe_unavailable"
        summary["stripe_error"] = str(exc)
        return summary

    summary["subscribers"] = len(subscribers)
    summary["subscribers_without_email"] = no_email
    summary["subscribers_unrecognized_channel"] = sum(1 for sub in subscribers if not sub.channel_recognized)

    digests: dict[str, Digest] = {}
    rendered: dict[str, tuple[str, str, str]] = {}
    for index, subscriber in enumerate(subscribers):
        channel = subscriber.channel
        if channel.value not in digests:
            digests[channel.value] = build_digest(channel, new_items, deadlines)
            digest = digests[channel.value]
            rendered[channel.value] = (
                render_subject(digest, today),
                render_html(digest, today, config.manage_url),
                render_text(digest, today, config.manage_url),
            )
        if not digests[channel.value].should_send:
            summary["emails_skipped_nothing_to_report"] += 1
            continue
        if dry_run:
            continue
        subject, html_body, text_body = rendered[channel.value]
        if index:
            sleep(SEND_INTERVAL_SECONDS)
        outcome = send_email(
            http,
            config,
            to=subscriber.email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            idempotency_key=f"jlrw-digest-{today.isoformat()}-{subscriber.subscription_id}",
            channel=channel,
            sleep=sleep,
        )
        summary[f"emails_{outcome}"] += 1

    if dry_run:
        summary["status"] = "dry_run"
        return summary
    if summary["emails_failed"] and not (summary["emails_sent"] or summary["emails_duplicate"]):
        # Every attempt failed (e.g. a revoked Resend key or an unverified
        # sending domain). Keep the items queued so the next run can deliver
        # them instead of recording them as announced.
        summary["status"] = "resend_unavailable"
        return summary
    if state_writer is not None:
        state_writer(next_state(items, processed, (item["id"] for item in new_items), today))
        summary["state_updated"] = True
    summary["status"] = "ok"
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read Stripe and count what would be sent; send nothing and keep the state file unchanged.",
    )
    parser.add_argument(
        "--preview-dir",
        type=Path,
        help="Render one HTML preview per channel into this directory (outside docs/) and exit. No network access.",
    )
    parser.add_argument(
        "--as-of",
        help="Preview only: render as if today (JST) were this YYYY-MM-DD date.",
    )
    args = parser.parse_args(argv)

    now = datetime.now(JST)
    items = load_items(DATA_PATH)
    processed = load_state(STATE_PATH)

    if args.as_of and not args.preview_dir:
        print("error: --as-of is only valid with --preview-dir.", file=sys.stderr)
        return 2
    if args.preview_dir:
        resolved = args.preview_dir.resolve()
        docs_root = (REPO_ROOT / "docs").resolve()
        if resolved == docs_root or docs_root in resolved.parents:
            print("error: previews must not be written inside the public docs directory.", file=sys.stderr)
            return 2
        today = strict_iso_date(args.as_of) if args.as_of else now.date()
        if today is None:
            print("error: --as-of must be a YYYY-MM-DD date.", file=sys.stderr)
            return 2
        new_items = select_new_items(items, processed, today)
        deadlines = select_deadlines(items, today)
        digests = [build_digest(channel, new_items, deadlines) for channel in CHANNELS]
        paths = write_previews(resolved, digests, today, "https://billing.stripe.com/p/login/preview")
        print(f"previews_written: {len(paths)}")
        print(f"preview_dir: {resolved}")
        return 0

    summary = run(
        config=Config.from_env(os.environ),
        items=items,
        processed=processed,
        now=now,
        http=default_http,
        dry_run=args.dry_run,
        state_writer=lambda ids: write_state(ids, STATE_PATH),
    )
    _print_summary(summary)
    if summary["status"] == "not_configured":
        print(f"::notice::Email alerts are not configured (missing: {summary['missing_settings']}); nothing was sent.")
    elif summary["status"] == "stripe_unavailable":
        print(f"::warning::Email alerts skipped: Stripe could not be read ({summary['stripe_error']}). Items stay queued for the next run.")
    elif summary["status"] == "resend_unavailable":
        print("::warning::Email alerts could not be sent (every Resend request failed). Items stay queued for the next run.")
    if summary.get("emails_failed"):
        print(f"::warning::{summary['emails_failed']} alert email(s) could not be delivered this run.")
    if summary.get("subscribers_unrecognized_channel"):
        print(
            f"::warning::{summary['subscribers_unrecognized_channel']} subscription(s) had no recognizable area choice and received the All areas digest."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
