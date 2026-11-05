#!/usr/bin/env python3
"""Weekly English roundups of Japanese legal and regulatory updates.

One static, indexable page per completed JST week (Monday to Sunday) at
``docs/weekly/<iso-year>-w<NN>/`` -- for example ``docs/weekly/2026-w45/`` --
plus an index at ``docs/weekly/`` and an RSS feed of the roundups at
``docs/feeds/weekly.xml``. The pages are meant to be shared as they are on
LinkedIn or in a newsletter, and give crawlers a fresh page every week.

Called by ``build_public_pages.build()`` (so the sitemap is written once); it
reads only the published items it is given and makes no API calls.

Selection, all from the published fields:

* a week gets a page only once it is over (its Sunday is before the JST build
  date), from ``FIRST_WEEK`` on, and only with at least ``MIN_WEEK_ITEMS``
  AI-summarized items, so a shared page is complete;
* an item belongs to the week of its official ``published_at`` date and is
  listed only with an AI summary (a rule-based preview is the same template on
  every item); a record detected late joins its week on a later run;
* items are ranked by ``relevance_score``, then newest, then the published
  file's order; at most ``PER_AREA`` per area (``OTHER_PER_AREA`` for
  ``Other``, which is always last) and ``PAGE_MAX`` per page, chosen round-robin
  across areas so a busy area cannot crowd out the others; a record repeating
  a higher-ranked record's English title (the same consultation listed by e-Gov
  and by its ministry) is skipped;
* "Consultations open at week end" lists consultations published by that
  Sunday whose structured deadline falls after it, judged at the week's end
  rather than the build date, so an old page does not change daily.

Output is deterministic for a given input and JST date. Every record field is
untrusted: text is HTML/XML-escaped and only http(s) source URLs are links.
Week slugs are public URLs: never rename one.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.utils import format_datetime
from html import escape
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode
from xml.sax.saxutils import escape as xml_escape

import build_public_pages as bpp
from alert_common import (
    FEEDS_URL,
    JST,
    SITE_URL,
    clean_text,
    comment_deadline_display,
    comment_deadline_last_day,
    safe_http_url,
    source_display_name,
    strict_iso_date,
    summary_is_ai,
)

WEEKLY_DIR_NAME = "weekly"
FEED_FILE_NAME = "weekly.xml"
FIRST_WEEK = date(2026, 6, 29)  # Monday of ISO week 2026-W27
MIN_WEEK_ITEMS = 5
PER_AREA = 5
OTHER_PER_AREA = 3
PAGE_MAX = 40
CONSULTATIONS_MAX = 25
LINE_MAX_CHARS = 200
FEED_MAX_ITEMS = 20
OTHER_AREA = "Other"
OPEN_STAGES = ("Public Comment Open", "Public Comment Closed")
SLUG_PATTERN = re.compile(r"^\d{4}-w\d{2}$")
INDEX_HEADING = "Weekly Japan Legal & Regulatory Roundups"
ROUNDUP_NOTE = (
    "Selected updates with AI summaries, grouped by area and ranked by a keyword relevance heuristic, "
    "not by legal importance. Area labels come from keyword rules. Each title opens the item's page; "
    "each source link opens the original Japanese official source, which remains authoritative."
)
FEED_NOTE = (
    "Monitoring aid only; not legal advice or an official translation. "
    "Original Japanese official sources remain authoritative."
)
# A sentence ends at . ! or ? followed by space and a capital letter or an
# opening bracket/quote; "Act No. 30" and "e.g. the" do not end one.
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"“])")


@dataclass(frozen=True)
class Week:
    monday: date
    items: tuple[Mapping[str, Any], ...]          # every AI-summarized item of the week, ranked
    selected: tuple[Mapping[str, Any], ...]       # the listed ones, grouped by area in display order
    consultations: tuple[Mapping[str, Any], ...]  # open at the week's end, soonest deadline first
    total_published: int                          # every published item of the week, AI or not

    @property
    def sunday(self) -> date:
        return self.monday + timedelta(days=6)

    @property
    def slug(self) -> str:
        return week_slug(self.monday)

    @property
    def path(self) -> str:
        return f"{WEEKLY_DIR_NAME}/{self.slug}/"


# ---------------------------------------------------------------- selection


def week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_slug(monday: date) -> str:
    year, number, _ = monday.isocalendar()
    return f"{year}-w{number:02d}"


def date_range_label(monday: date) -> str:
    sunday = monday + timedelta(days=6)
    if monday.year != sunday.year:
        return f"{monday.day} {monday:%B} {monday.year} – {sunday.day} {sunday:%B} {sunday.year}"
    if monday.month != sunday.month:
        return f"{monday.day} {monday:%B} – {sunday.day} {sunday:%B} {sunday.year}"
    return f"{monday.day}–{sunday.day} {sunday:%B} {sunday.year}"


def week_heading(monday: date) -> str:
    year, number, _ = monday.isocalendar()
    return f"Japan Legal & Regulatory Roundup — Week {number}, {year} ({date_range_label(monday)})"


def _relevance(item: Mapping[str, Any]) -> float:
    value = item.get("relevance_score")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def area_of(item: Mapping[str, Any]) -> str:
    return clean_text(item.get("area")) or OTHER_AREA


def one_line(item: Mapping[str, Any]) -> str:
    """The first sentence of the English AI summary, shortened on a word boundary."""
    text = clean_text(item.get("summary_en"))
    first = SENTENCE_END.split(text, maxsplit=1)[0] if text else ""
    return bpp.shorten(first, LINE_MAX_CHARS)


def select(ranked: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Round-robin across areas (largest first, Other last) up to the caps; grouped by area.

    A record whose English title repeats a higher-ranked one is skipped.
    """
    groups: dict[str, list[Mapping[str, Any]]] = {}
    seen_titles: set[str] = set()
    for item in ranked:
        # The same consultation is often listed by e-Gov and by its ministry
        # under one English title: keep the higher-ranked record only.
        title = clean_text(item.get("title_en")).casefold()
        if title in seen_titles:
            continue
        seen_titles.add(title)
        groups.setdefault(area_of(item), []).append(item)
    order = sorted(groups, key=lambda area: (area == OTHER_AREA, -len(groups[area]), area))
    picked: dict[str, list[Mapping[str, Any]]] = {area: [] for area in order}
    total = 0
    for rank in range(PER_AREA):
        for area in order:
            cap = OTHER_PER_AREA if area == OTHER_AREA else PER_AREA
            if total < PAGE_MAX and rank < min(cap, len(groups[area])):
                picked[area].append(groups[area][rank])
                total += 1
    return [item for area in order for item in picked[area]]


def consultations_open_at(items: Sequence[Mapping[str, Any]], sunday: date) -> list[Mapping[str, Any]]:
    rows = []
    for index, item in enumerate(items):
        published = strict_iso_date(item.get("published_at"))
        last_day = comment_deadline_last_day(item.get("comment_deadline"))
        if (
            item.get("stage") in OPEN_STAGES
            and published is not None
            and published <= sunday
            and last_day is not None
            and last_day > sunday
            and clean_text(item.get("title_en"))
        ):
            rows.append((last_day, index, item))
    rows.sort(key=lambda row: (row[0].toordinal(), row[1]))
    return [item for _, _, item in rows[:CONSULTATIONS_MAX]]


def build_weeks(items: Sequence[Mapping[str, Any]], today: date) -> list[Week]:
    """Every completed week since ``FIRST_WEEK`` with enough AI summaries, newest first."""
    by_week: dict[date, list[tuple[int, Mapping[str, Any], date]]] = {}
    totals: Counter[date] = Counter()
    for index, item in enumerate(items):
        published = strict_iso_date(item.get("published_at"))
        if published is None or not clean_text(item.get("title_en")):
            continue
        monday = week_monday(published)
        if monday < FIRST_WEEK or monday + timedelta(days=6) >= today:
            continue
        totals[monday] += 1
        if summary_is_ai(item):
            by_week.setdefault(monday, []).append((index, item, published))
    weeks = []
    for monday in sorted(by_week, reverse=True):
        rows = by_week[monday]
        if len(rows) < MIN_WEEK_ITEMS:
            continue
        rows.sort(key=lambda row: (-_relevance(row[1]), -row[2].toordinal(), row[0]))
        ranked = tuple(item for _, item, _ in rows)
        weeks.append(Week(
            monday=monday,
            items=ranked,
            selected=tuple(select(ranked)),
            consultations=tuple(consultations_open_at(items, monday + timedelta(days=6))),
            total_published=totals[monday],
        ))
    return weeks


def area_counts(week: Week) -> list[tuple[str, int]]:
    counts = Counter(area_of(item) for item in week.items)
    return sorted(counts.items(), key=lambda row: (row[0] == OTHER_AREA, -row[1], row[0]))


def top_area_names(week: Week, count: int = 3) -> list[str]:
    return [area for area, _ in area_counts(week) if area != OTHER_AREA][:count]


def week_description(week: Week) -> str:
    areas = top_area_names(week)
    focus = f" Most activity: {', '.join(areas)}." if areas else ""
    return (
        f"English triage of {len(week.items):,} Japanese legal and regulatory updates with AI summaries, "
        f"published {date_range_label(week.monday)}, with links to the official Japanese sources.{focus} "
        "Monitoring aid, not legal advice."
    )


def week_lastmod(week: Week) -> date | None:
    dates = [bpp.item_lastmod(item) for item in (*week.selected, *week.consultations)]
    valid = [d for d in dates if d]
    return max(valid) if valid else None


# ---------------------------------------------------------------- rendering


def dashboard_url(params: Mapping[str, str]) -> str:
    return SITE_URL + "?" + urlencode(params)


def render_entry(item: Mapping[str, Any], prefix: str, *, deadline: bool = False) -> str:
    title = escape(clean_text(item.get("title_en")))
    meta = [
        clean_text(item.get("published_at")),
        source_display_name(item.get("source_name")),
        clean_text(item.get("area")) if deadline else "",
        clean_text(item.get("stage")),
    ]
    meta_html = " · ".join(escape(part) for part in meta if part)
    own_page = bpp.item_page_path(item)
    url = safe_http_url(item.get("source_url"))
    lines = ['        <li class="static-latest-item">']
    if own_page:
        lines.append(f'          <a class="static-latest-title" href="{prefix}{own_page}">{title}</a>')
    else:
        lines.append(f'          <span class="static-latest-title">{title}</span>')
    if meta_html:
        lines.append(f'          <span class="static-latest-meta">{meta_html}</span>')
    if deadline:
        due = comment_deadline_display(item.get("comment_deadline"))
        if due:
            lines.append(f'          <span class="static-latest-deadline">Comments due: {escape(due)}</span>')
    elif summary_is_ai(item):
        lines.append(f'          <p class="static-latest-summary">{escape(one_line(item))} <em>(AI summary)</em></p>')
    if url:
        lines.append(
            f'          <a class="static-latest-meta" href="{escape(url)}" target="_blank" '
            'rel="noopener noreferrer">Original Japanese source</a>'
        )
    lines.append("        </li>")
    return "\n".join(lines)


def render_week_page(week: Week, newer: Week | None, older: Week | None) -> str:
    prefix = "../../"
    counts = dict(area_counts(week))
    sources = {source_display_name(item.get("source_name")) for item in week.items} - {""}
    stats = (
        f"{len(week.items):,} updates with AI summaries from {len(sources):,} official sources "
        f"({week.total_published:,} published this week in all); {len(week.selected):,} shown below"
    )
    parts = [
        '    <p class="area-page-intro">Japanese legal and regulatory developments officially published '
        f"{escape(date_range_label(week.monday))} (JST), monitored from government, regulator, Diet, court, "
        f"and self-regulatory sources and summarized in English for triage. {escape(ROUNDUP_NOTE)}</p>",
        f'    <p class="area-page-stats">{escape(stats)}</p>',
    ]
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for item in week.selected:
        groups.setdefault(area_of(item), []).append(item)
    for number, (area, rows) in enumerate(groups.items(), start=1):
        area_page = bpp.AREA_PAGE_BY_AREA.get(area)
        more = counts.get(area, 0) - len(rows)
        parts += [
            f'    <section class="area-page-section" aria-labelledby="area-{number}">',
            f'      <h2 id="area-{number}">{escape(area)}</h2>',
            '        <ol class="static-latest-list">',
            *(render_entry(item, prefix) for item in rows),
            "        </ol>",
        ]
        links = []
        if more > 0:
            links.append(
                f'<a class="area-page-action" href="{escape(dashboard_url({"area": area, "ai": "1", "year": str(week.monday.year)}))}">'
                f"{more:,} more with AI summaries this week — search in the dashboard</a>"
            )
        if area_page is not None:
            links.append(f'<a class="area-page-action" href="{prefix}{bpp.area_page_path(area_page)}">'
                         f"{escape(area)} updates</a>")
        if links:
            parts.append('      <p class="area-page-actions">' + " ".join(links) + "</p>")
        parts.append("    </section>")
    parts += [
        '    <section class="area-page-section" aria-labelledby="consultations-title">',
        '      <h2 id="consultations-title">Consultations open at week end</h2>',
        f'      <p class="static-latest-note">Public comment procedures published by {week.sunday.isoformat()} '
        "whose structured deadline fell after that date, soonest deadline first. The stage shown is the "
        "current one. Confirm the deadline and submission method in the official source.</p>",
    ]
    if week.consultations:
        parts += [
            '        <ol class="static-latest-list">',
            *(render_entry(item, prefix, deadline=True) for item in week.consultations),
            "        </ol>",
        ]
    else:
        parts.append("      <p>No consultation with a structured deadline was open at the end of this week.</p>")
    parts.append("    </section>")
    nav = []
    if older is not None:
        nav.append(f'<a class="area-page-action" href="../{older.slug}/">&larr; Week of {escape(date_range_label(older.monday))}</a>')
    nav.append('<a class="area-page-action" href="../">All weekly roundups</a>')
    if newer is not None:
        nav.append(f'<a class="area-page-action" href="../{newer.slug}/">Week of {escape(date_range_label(newer.monday))} &rarr;</a>')
    parts += [
        '    <p class="area-page-actions">',
        *(f"      {link}" for link in nav),
        f'      <a class="area-page-action" href="{prefix}feeds/{FEED_FILE_NAME}">Weekly roundup RSS feed</a>',
        f'      <a class="area-page-action" href="{prefix}feeds/all.ics">Public comment deadline calendar (.ics)</a>',
        f'      <a class="area-page-action" href="{prefix}">Search every update in the dashboard</a>',
        "    </p>",
    ]
    return bpp.render_page(
        path=week.path,
        title=f"{week_heading(week.monday)} — Japan Legal Reform Watch",
        description=week_description(week),
        heading=week_heading(week.monday),
        breadcrumb=f"Week {week.monday.isocalendar()[1]}, {week.monday.isocalendar()[0]}",
        body="\n".join(parts),
        current="",
        parent=("Weekly roundups", f"{WEEKLY_DIR_NAME}/"),
    )


def render_index_page(weeks: Sequence[Week]) -> str:
    parts = [
        '    <p class="area-page-intro">One page per week of Japanese legal and regulatory developments, '
        "published every Monday (JST) for the week before: selected updates with English AI summaries "
        "grouped by area, and the public comment procedures still open at the week's end. Each page can "
        f"be shared as it is. {escape(ROUNDUP_NOTE)}</p>",
        '    <p class="area-page-actions">',
        f'      <a class="area-page-action" href="../feeds/{FEED_FILE_NAME}">Weekly roundup RSS feed</a>',
        '      <a class="area-page-action" href="../">Search every update in the dashboard</a>',
        "    </p>",
        '    <section class="area-page-section" aria-labelledby="weeks-title">',
        '      <h2 id="weeks-title">Roundups</h2>',
    ]
    if weeks:
        parts.append('        <ol class="static-latest-list">')
        for week in weeks:
            areas = top_area_names(week)
            focus = f" · Most activity: {', '.join(areas)}" if areas else ""
            parts += [
                '        <li class="static-latest-item">',
                f'          <a class="static-latest-title" href="./{week.slug}/">{escape(week_heading(week.monday))}</a>',
                f'          <span class="static-latest-meta">{len(week.items):,} updates with AI summaries'
                f"{escape(focus)}</span>",
                "        </li>",
            ]
        parts.append("        </ol>")
    else:
        parts.append("      <p>No weekly roundup has been published yet.</p>")
    parts.append("    </section>")
    return bpp.render_page(
        path=f"{WEEKLY_DIR_NAME}/",
        title=f"{INDEX_HEADING} — Japan Legal Reform Watch",
        description=(
            "Weekly English roundups of Japanese legal and regulatory updates for foreign companies and "
            "compliance teams, grouped by area with links to the official Japanese sources. Not legal advice."
        ),
        heading=INDEX_HEADING,
        breadcrumb="Weekly roundups",
        body="\n".join(parts),
        current=WEEKLY_DIR_NAME,
    )


def published_on(week: Week) -> date:
    """The Monday after the week: the first day its page can exist (a stable feed date)."""
    return week.monday + timedelta(days=7)


def _rfc822(day: date) -> str:
    return format_datetime(datetime(day.year, day.month, day.day, tzinfo=JST))


def render_feed(weeks: Sequence[Week]) -> str:
    selected = list(weeks[:FEED_MAX_ITEMS])
    self_url = FEEDS_URL + FEED_FILE_NAME
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        f"<title>{xml_escape('Japan Legal Reform Watch — Weekly roundup')}</title>",
        f"<link>{xml_escape(SITE_URL + WEEKLY_DIR_NAME + '/')}</link>",
        f'<atom:link href="{xml_escape(self_url)}" rel="self" type="application/rss+xml" />',
        "<description>"
        + xml_escape("One English roundup per week of Japanese legal and regulatory updates. " + FEED_NOTE)
        + "</description>",
        "<language>en</language>",
    ]
    if selected:
        lines.append(f"<lastBuildDate>{_rfc822(published_on(selected[0]))}</lastBuildDate>")
    for week in selected:
        url = SITE_URL + week.path
        lines += [
            "<item>",
            f"<title>{xml_escape(week_heading(week.monday))}</title>",
            f"<link>{xml_escape(url)}</link>",
            f'<guid isPermaLink="true">{xml_escape(url)}</guid>',
            f"<pubDate>{_rfc822(published_on(week))}</pubDate>",
            f"<description>{xml_escape(week_description(week) + ' ' + FEED_NOTE)}</description>",
            "</item>",
        ]
    lines += ["</channel>", "</rss>", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------ writing


def prune_week_pages(weekly_dir: Path, keep: set[str]) -> int:
    """Remove week directories this module wrote that no longer qualify (only ``index.html`` inside)."""
    if not weekly_dir.is_dir():
        return 0
    removed = 0
    for child in sorted(weekly_dir.iterdir()):
        if (
            child.is_dir()
            and SLUG_PATTERN.match(child.name)
            and child.name not in keep
            and sorted(p.name for p in child.iterdir()) == ["index.html"]
            and (child / "index.html").is_file()
        ):
            (child / "index.html").unlink()
            child.rmdir()
            removed += 1
    return removed


def build(items: Sequence[Mapping[str, Any]], site_dir: Path, today: date) -> tuple[dict[str, Any], list[tuple[str, date | None]]]:
    """Write the week pages, index and feed; return (summary, sitemap entries)."""
    weeks = build_weeks(items, today)
    changed = 0
    entries: list[tuple[str, date | None]] = []
    for position, week in enumerate(weeks):
        newer = weeks[position - 1] if position > 0 else None
        older = weeks[position + 1] if position + 1 < len(weeks) else None
        changed += bpp.write_if_changed(site_dir / week.path / "index.html", render_week_page(week, newer, older))
        entries.append((SITE_URL + week.path, week_lastmod(week)))
    newest = entries[0][1] if entries else None
    changed += bpp.write_if_changed(site_dir / WEEKLY_DIR_NAME / "index.html", render_index_page(weeks))
    changed += bpp.write_if_changed(site_dir / "feeds" / FEED_FILE_NAME, render_feed(weeks))
    removed = prune_week_pages(site_dir / WEEKLY_DIR_NAME, {week.slug for week in weeks})
    summary = {
        "weekly_pages": len(weeks),
        "weekly_files_changed": changed,
        "weekly_pages_removed": removed,
        "weekly_latest": weeks[0].slug if weeks else "",
    }
    return summary, [(SITE_URL + WEEKLY_DIR_NAME + "/", newest), *entries]
