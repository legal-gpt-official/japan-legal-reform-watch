"""Shared vocabulary for the self-serve email alerts and the free public feeds.

The paid digest (send_alert_digests.py), the free RSS/ICS feeds
(build_public_feeds.py), and the one-time Stripe setup (setup_alert_billing.py)
must agree on exactly one list of monitoring channels. A subscriber chooses a
channel at Stripe Checkout; the value Stripe stores is the channel ``value``
below, so renaming a value strands every subscriber who chose it. Add channels
freely; never rename or remove a value that is live.

Every record field is untrusted third-party data. The helpers here are the
single place that decides what may become a link or plain text.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
DASHBOARD_URL = "https://legal-gpt-official.github.io/japan-legal-reform-watch/"
FEEDS_URL = DASHBOARD_URL + "feeds/"


@dataclass(frozen=True)
class Channel:
    # Stripe dropdown option values must be alphanumeric (<= 100 chars).
    value: str
    # Shown to the customer at checkout (<= 100 chars) and in emails.
    label: str
    # The published ``area`` this channel follows; None follows every area.
    area: str | None


ALL_AREAS = Channel("all", "All areas", None)

CHANNELS: tuple[Channel, ...] = (
    ALL_AREAS,
    Channel("dataprivacyai", "Data / Privacy / AI", "Data / Privacy / AI"),
    Channel("financeaml", "Finance / AML", "Finance / AML"),
    Channel("antitrustfairtrade", "Antitrust / Fair Trade", "Antitrust / Fair Trade"),
    Channel("consumeradvertising", "Consumer / Advertising", "Consumer / Advertising"),
    Channel("laboremployment", "Labor / Employment", "Labor / Employment"),
    Channel("corporategovernance", "Corporate / Governance", "Corporate / Governance"),
    Channel("economicsecurityfdi", "Economic Security / FDI", "Economic Security / FDI"),
    Channel("taxstampduty", "Tax / Stamp Duty", "Tax / Stamp Duty"),
    Channel(
        "healthcarepharma",
        "Healthcare / Pharmaceuticals",
        "Healthcare / Pharmaceuticals",
    ),
    Channel("energyenvironment", "Energy / Environment", "Energy / Environment"),
    Channel("foodagriculture", "Food / Agriculture", "Food / Agriculture"),
    Channel(
        "transportinfrastructure",
        "Transport / Infrastructure",
        "Transport / Infrastructure",
    ),
    Channel("realestatelanduse", "Real Estate / Land Use", "Real Estate / Land Use"),
    Channel(
        "publicsafetydisaster",
        "Public Safety / Disaster Management",
        "Public Safety / Disaster Management",
    ),
)

CHANNELS_BY_VALUE: dict[str, Channel] = {channel.value: channel for channel in CHANNELS}

# Keep aligned with docs/app.js SOURCE_DISPLAY_NAMES; a test enforces that every
# configured fetch source has an entry here.
SOURCE_DISPLAY_NAMES = {
    "e-Gov Public Comment (意見募集案件一覧)": "e-Gov Public Comment",
    "House of Representatives (衆議院) 議案情報": "House of Representatives — Diet Bills",
    "e-Gov Law Search (法令更新一覧)": "e-Gov Law Search — Updated Laws",
    "Japan Exchange Group (JPX) Public Comments": "Japan Exchange Group (JPX) — Public Comments",
    "Tokyo Stock Exchange (JPX) Rule Revisions": "Tokyo Stock Exchange (JPX) — Rule Revisions",
    "Pharmaceuticals and Medical Devices Agency (PMDA) Safety Updates": "Pharmaceuticals and Medical Devices Agency (PMDA) — Safety Updates",
    "Japan Securities Dealers Association (JSDA) Public Comments": "Japan Securities Dealers Association (JSDA) — Public Comments",
    "Japan Securities Dealers Association (JSDA) Public Comment Results": "Japan Securities Dealers Association (JSDA) — Public Comment Results",
    "Courts in Japan (裁判所) Recent Supreme Court Decisions": "Courts in Japan — Recent Supreme Court Decisions",
    "Securities and Exchange Surveillance Commission (SESC) Enforcement Updates": "Securities and Exchange Surveillance Commission (SESC) — Enforcement Updates",
    "Financial Services Agency (金融庁) 新着情報": "Financial Services Agency (FSA)",
    "経済産業省 (METI) ニュースリリース": "Ministry of Economy, Trade and Industry (METI)",
    "Ministry of Health, Labour and Welfare (厚生労働省) 新着情報": "Ministry of Health, Labour and Welfare (MHLW)",
    "Digital Agency (デジタル庁) 新着・更新": "Digital Agency",
    "消費者庁 (CAA) 新着情報": "Consumer Affairs Agency (CAA)",
    "個人情報保護委員会 (PPC) 新着情報": "Personal Information Protection Commission (PPC)",
    "公正取引委員会 (JFTC) 報道発表": "Japan Fair Trade Commission (JFTC)",
    "法務省 (MOJ) 新着情報": "Ministry of Justice (MOJ)",
    "環境省 (MOE) 報道発表": "Ministry of the Environment (MOE)",
    "財務省 (MOF) 新着情報": "Ministry of Finance (MOF)",
    "国税庁 (NTA) 新着・通達": "National Tax Agency (NTA)",
    "総務省 (MIC) 新着情報": "Ministry of Internal Affairs and Communications (MIC)",
    "国土交通省 (MLIT) 報道発表": "Ministry of Land, Infrastructure, Transport and Tourism (MLIT)",
    "農林水産省 (MAFF) 報道発表": "Ministry of Agriculture, Forestry and Fisheries (MAFF)",
}

DISCLAIMER_TEXT = (
    "This is a monitoring aid for general informational purposes only. It is not "
    "legal advice, an official translation, or a comprehensive statement of Japanese "
    "legal and regulatory developments. AI summaries and rule-based previews may "
    "contain errors or omissions. Original Japanese official sources remain "
    "authoritative and should be reviewed before action is taken."
)

NEWLY_DETECTED_NOTE = (
    "“Newly detected” means first detected by this dashboard. It does not mean "
    "a new law, a new regulation, or the date of enactment, amendment, or first "
    "government publication."
)


def write_text_atomic(path: Path, text: str, *, attempts: int = 6, delay: float = 0.05) -> None:
    """Write via a temp file and move it into place.

    The move retries briefly because on Windows an on-access scanner can hold a
    freshly written temp file for a few milliseconds (the same reason every
    pipeline writer has an atomic_replace). Linux, where CI runs, never retries.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    for attempt in range(attempts):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))


def clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("\x00", " ").split())


def strict_iso_date(value: Any) -> date | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def safe_http_url(value: Any) -> str:
    """Return the URL only when it is an absolute http(s) URL without credentials."""
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate or any(ch in candidate for ch in "\r\n\t\x00 "):
        return ""
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    return candidate


def source_display_name(source_name: Any) -> str:
    value = clean_text(source_name)
    return SOURCE_DISPLAY_NAMES.get(value, value)


def item_in_channel(item: Mapping[str, Any], channel: Channel) -> bool:
    return channel.area is None or item.get("area") == channel.area


def dashboard_channel_url(channel: Channel, *, newly_detected: bool = False) -> str:
    params: dict[str, str] = {}
    if channel.area:
        params["area"] = channel.area
    params["sort"] = "detected"
    if newly_detected:
        params["new"] = "7"
    return DASHBOARD_URL + "?" + urlencode(params)


def comment_deadline_last_day(value: Any) -> date | None:
    """Return the last JST calendar day on which a comment can still be filed.

    Mirrors public_comment_deadlines.resolve_public_comment_stage: a date-only
    deadline stays open through the end of that JST day, while an explicit
    date-time closes at that instant (so ``2026-10-16T00:00:00+09:00`` leaves
    2026-10-15 as the last full day).
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if len(text) == 10:
        return strict_iso_date(text)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    closes_at = parsed.astimezone(JST)
    return (closes_at - timedelta(microseconds=1)).date()


def comment_deadline_display(value: Any) -> str:
    """Human-readable deadline that never overstates precision."""
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip()
    if len(text) == 10:
        parsed_date = strict_iso_date(text)
        return f"{parsed_date.isoformat()} (end of day, JST)" if parsed_date else ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")


def open_comment_deadline(item: Mapping[str, Any], today: date) -> tuple[date, int] | None:
    """(last filing day, days remaining) for an open consultation, else None."""
    if item.get("stage") != "Public Comment Open":
        return None
    last_day = comment_deadline_last_day(item.get("comment_deadline"))
    if last_day is None or last_day < today:
        return None
    return last_day, (last_day - today).days


def summary_is_ai(item: Mapping[str, Any]) -> bool:
    return item.get("summary_source") == "claude" and bool(clean_text(item.get("summary_en")))
