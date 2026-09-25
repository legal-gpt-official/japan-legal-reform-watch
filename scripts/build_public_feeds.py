#!/usr/bin/env python3
"""Build the free public RSS feeds and public-comment deadline calendars.

For every monitoring channel in alert_common.CHANNELS this writes:

* ``docs/feeds/<channel>.xml`` -- RSS 2.0 of the most recently detected updates;
* ``docs/feeds/<channel>.ics`` -- iCalendar of open public comments with a
  structured deadline, one all-day event on the last day a comment can be filed.

These are the free tier: static files on GitHub Pages, no sign-up, no personal
data. They are derived only from ``docs/data/legal_updates.json`` and change
nothing in it. Output is deterministic for a given input and JST date (no
wall-clock timestamps), so an unchanged corpus produces no diff.

Every record field is untrusted: text is XML/iCalendar-escaped and only http(s)
source URLs are emitted as links.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from xml.sax.saxutils import escape as xml_escape

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from alert_common import (  # noqa: E402
    CHANNELS,
    FEEDS_URL,
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
FEEDS_DIR = REPO_ROOT / "docs" / "feeds"
MAX_FEED_ITEMS = 50
FEED_NOTE = (
    "Monitoring aid only; not legal advice or an official translation. "
    "Original Japanese official sources remain authoritative."
)


def _feed_date(item: Mapping[str, Any]) -> date | None:
    """Detection date where known (what a feed reader should sort by), else publication date."""
    return strict_iso_date(item.get("first_seen_at")) or strict_iso_date(item.get("published_at"))


def feed_items(items: Sequence[Mapping[str, Any]], channel: Channel) -> list[Mapping[str, Any]]:
    dated = [
        (index, item, _feed_date(item))
        for index, item in enumerate(items)
        if item_in_channel(item, channel) and _feed_date(item) is not None
    ]
    # Newest first; ties keep the build's relevance order.
    dated.sort(key=lambda entry: (-entry[2].toordinal(), entry[0]))
    return [item for _, item, _ in dated[:MAX_FEED_ITEMS]]


def _rfc822(day: date) -> str:
    return format_datetime(datetime(day.year, day.month, day.day, tzinfo=JST))


def _item_description(item: Mapping[str, Any]) -> str:
    parts = []
    if summary_is_ai(item):
        parts.append("AI summary: " + clean_text(item.get("summary_en")))
    else:
        parts.append("Rule-based preview only; review the original Japanese source.")
    title_ja = clean_text(item.get("title_ja"))
    if title_ja:
        parts.append("Original title: " + title_ja)
    meta = " | ".join(
        value
        for value in (
            clean_text(item.get("stage")),
            source_display_name(item.get("source_name")),
            ("Published " + clean_text(item.get("published_at"))) if strict_iso_date(item.get("published_at")) else "",
            ("First detected " + clean_text(item.get("first_seen_at"))) if strict_iso_date(item.get("first_seen_at")) else "",
        )
        if value
    )
    if meta:
        parts.append(meta)
    parts.append(FEED_NOTE)
    return "\n".join(parts)


def render_rss(items: Sequence[Mapping[str, Any]], channel: Channel) -> str:
    selected = feed_items(items, channel)
    newest = _feed_date(selected[0]) if selected else None
    self_url = f"{FEEDS_URL}{channel.value}.xml"
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        f"<title>{xml_escape('Japan Legal Reform Watch — ' + channel.label)}</title>",
        f"<link>{xml_escape(dashboard_channel_url(channel))}</link>",
        f'<atom:link href="{xml_escape(self_url)}" rel="self" type="application/rss+xml" />',
        "<description>"
        + xml_escape(
            "English monitoring summaries of Japanese legal and regulatory updates, newest first by the date "
            "this dashboard first detected them. " + NEWLY_DETECTED_NOTE + " " + FEED_NOTE
        )
        + "</description>",
        "<language>en</language>",
    ]
    if newest:
        lines.append(f"<lastBuildDate>{_rfc822(newest)}</lastBuildDate>")
    for item in selected:
        item_id = clean_text(item.get("id"))
        link = safe_http_url(item.get("source_url"))
        lines.append("<item>")
        lines.append(f"<title>{xml_escape(clean_text(item.get('title_en')) or 'Untitled update')}</title>")
        if link:
            lines.append(f"<link>{xml_escape(link)}</link>")
        lines.append(f'<guid isPermaLink="false">{xml_escape("jlrw-" + item_id)}</guid>')
        lines.append(f"<pubDate>{_rfc822(_feed_date(item))}</pubDate>")
        for category in (clean_text(item.get("area")), clean_text(item.get("stage"))):
            if category:
                lines.append(f"<category>{xml_escape(category)}</category>")
        lines.append(f"<description>{xml_escape(_item_description(item))}</description>")
        lines.append("</item>")
    lines.extend(("</channel>", "</rss>", ""))
    return "\n".join(lines)


def _ics_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _fold(line: str) -> list[str]:
    """RFC 5545 line folding at 75 octets without splitting a UTF-8 character."""
    out = []
    current = ""
    limit = 75
    for char in line:
        if len((current + char).encode("utf-8")) > limit:
            out.append(current)
            current = " " + char
            limit = 75
        else:
            current += char
    out.append(current)
    return out


def _consultation_title(item: Mapping[str, Any]) -> str:
    title = clean_text(item.get("title_en")) or "Untitled consultation"
    # Rule-based titles already start with the stage; the event label says it once.
    prefix = "Public Comment: "
    return title[len(prefix):] if title.startswith(prefix) and len(title) > len(prefix) else title


def render_ics(items: Sequence[Mapping[str, Any]], channel: Channel, today: date) -> str:
    events = []
    for index, item in enumerate(items):
        if not item_in_channel(item, channel):
            continue
        deadline = open_comment_deadline(item, today)
        if not deadline:
            continue
        events.append((deadline[0], index, item))
    events.sort(key=lambda entry: (entry[0], entry[1]))

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LegalOS//Japan Legal Reform Watch//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:" + _ics_text(f"JLRW public comment deadlines — {channel.label}"),
        "X-WR-TIMEZONE:Asia/Tokyo",
    ]
    for last_day, _, item in events:
        item_id = clean_text(item.get("id"))
        stamp_day = strict_iso_date(item.get("last_checked")) or strict_iso_date(item.get("published_at")) or last_day
        link = safe_http_url(item.get("source_url"))
        description = "\n".join(
            part
            for part in (
                "Closes " + comment_deadline_display(item.get("comment_deadline")) + ".",
                ("Original title: " + clean_text(item.get("title_ja"))) if clean_text(item.get("title_ja")) else "",
                source_display_name(item.get("source_name")),
                ("Official source: " + link) if link else "",
                FEED_NOTE,
            )
            if part
        )
        lines.extend(
            (
                "BEGIN:VEVENT",
                "UID:" + _ics_text(f"jlrw-{item_id}-comment-deadline@legal-gpt-official.github.io"),
                "DTSTAMP:" + stamp_day.strftime("%Y%m%d") + "T000000Z",
                "DTSTART;VALUE=DATE:" + last_day.strftime("%Y%m%d"),
                "DTEND;VALUE=DATE:" + (last_day + timedelta(days=1)).strftime("%Y%m%d"),
                "SUMMARY:" + _ics_text("Public comment closes: " + _consultation_title(item)),
                "DESCRIPTION:" + _ics_text(description),
                "TRANSP:TRANSPARENT",
            )
        )
        if link:
            lines.append("URL:" + _ics_text(link))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    folded = [segment for line in lines for segment in _fold(line)]
    return "\r\n".join(folded) + "\r\n"


def build_feeds(items: Sequence[Mapping[str, Any]], out_dir: Path, today: date) -> dict[str, int]:
    written = {"rss_files": 0, "ics_files": 0, "ics_events_all": 0}
    for channel in CHANNELS:
        write_text_atomic(out_dir / f"{channel.value}.xml", render_rss(items, channel))
        ics = render_ics(items, channel, today)
        write_text_atomic(out_dir / f"{channel.value}.ics", ics)
        written["rss_files"] += 1
        written["ics_files"] += 1
        if channel.area is None:
            written["ics_events_all"] = ics.count("BEGIN:VEVENT")
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out-dir", type=Path, default=FEEDS_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    items = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        print("error: published data must be a JSON array", file=sys.stderr)
        return 1
    items = [item for item in items if isinstance(item, dict)]
    summary = build_feeds(items, args.out_dir, datetime.now(JST).date())
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
