#!/usr/bin/env python3
"""Generate a review-required email digest draft from a dashboard filter URL.

This is an operator tool, not an email sender. It reads the same generated
public JSON used by the dashboard, applies the supported URL filters, limits
the result to a delivery window, and writes Markdown and HTML drafts. All
record fields remain untrusted: HTML is escaped and source links are restricted
to HTTP(S).
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, urlsplit


JST = timezone(timedelta(hours=9))
DEFAULT_MAX_ITEMS = 10
MAX_FILTER_URL_LENGTH = 4_096
MAX_ITEMS_LIMIT = 50
VALID_SORTS = {"relevance", "published", "checked", "detected"}

# Keep these values aligned with docs/app.js. The URL uses compact slugs, while
# the underlying public JSON retains the exact source_name values.
SOURCE_SLUGS = {
    "egov": "e-Gov Public Comment (意見募集案件一覧)",
    "shugiin-bills": "House of Representatives (衆議院) 議案情報",
    "egov-laws": "e-Gov Law Search (法令更新一覧)",
    "jpx-comments": "Japan Exchange Group (JPX) Public Comments",
    "jpx-rules": "Tokyo Stock Exchange (JPX) Rule Revisions",
    "pmda": "Pharmaceuticals and Medical Devices Agency (PMDA) Safety Updates",
    "jsda-comments": "Japan Securities Dealers Association (JSDA) Public Comments",
    "jsda-results": "Japan Securities Dealers Association (JSDA) Public Comment Results",
    "courts-supreme": "Courts in Japan (裁判所) Recent Supreme Court Decisions",
    "sesc": "Securities and Exchange Surveillance Commission (SESC) Enforcement Updates",
    "fsa": "Financial Services Agency (金融庁) 新着情報",
    "mhlw": "Ministry of Health, Labour and Welfare (厚生労働省) 新着情報",
    "digital-agency": "Digital Agency (デジタル庁) 新着・更新",
    "meti": "経済産業省 (METI) ニュースリリース",
    "caa": "消費者庁 (CAA) 新着情報",
    "ppc": "個人情報保護委員会 (PPC) 新着情報",
    "jftc": "公正取引委員会 (JFTC) 報道発表",
    "moj": "法務省 (MOJ) 新着情報",
    "moe": "環境省 (MOE) 報道発表",
    "mof": "財務省 (MOF) 新着情報",
    "nta": "国税庁 (NTA) 新着・通達",
    "mic": "総務省 (MIC) 新着情報",
    "mlit": "国土交通省 (MLIT) 報道発表",
    "maff": "農林水産省 (MAFF) 報道発表",
}

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


@dataclass(frozen=True)
class FilterSpec:
    period: str
    search: str = ""
    area: str = ""
    stage: str = ""
    source: str = ""
    impact: str = ""
    sort: str = "published"
    ai_only: bool = False
    newly_detected_only: bool = False


@dataclass(frozen=True)
class DigestResult:
    filters: FilterSpec
    since: date
    until: date
    date_field: str
    total_in_period: int
    filter_match_count: int
    window_match_count: int
    items: tuple[Mapping[str, Any], ...]
    dashboard_url: str

    @property
    def omitted_count(self) -> int:
        return max(0, self.window_match_count - len(self.items))


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read valid JSON: {path}") from exc


def _strict_iso_date(value: Any) -> date | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _finite_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("\x00", " ").split())


def _markdown_text(value: Any) -> str:
    # Escaping HTML metacharacters prevents record fields from becoming active
    # markup in Markdown renderers that allow raw HTML. Escaping link/formatting
    # delimiters also prevents an untrusted title from creating a Markdown link.
    escaped = html.escape(_clean_text(value), quote=False)
    return re.sub(r"([\\`*_\[\]()])", r"\\\1", escaped)


def _safe_http_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return candidate


def _safe_dashboard_url(value: Any) -> str:
    candidate = _safe_http_url(value)
    if not candidate:
        return ""
    parsed = urlsplit(candidate)
    if parsed.hostname != "legal-gpt-official.github.io":
        return ""
    if not (
        parsed.path == "/japan-legal-reform-watch/"
        or parsed.path == "/japan-legal-reform-watch/index.html"
    ):
        return ""
    return candidate


def _parse_filter_params(value: str) -> dict[str, str]:
    if not isinstance(value, str) or len(value) > MAX_FILTER_URL_LENGTH:
        raise ValueError("Dashboard URL is missing or too long.")
    candidate = value.strip()
    if not candidate:
        raise ValueError("Dashboard URL is required.")
    parsed = urlsplit(candidate)
    query = parsed.query if (parsed.scheme or "?" in candidate) else candidate.lstrip("?")
    raw = parse_qs(query, keep_blank_values=False, strict_parsing=False)
    return {key: values[0] for key, values in raw.items() if values}


def _load_manifest(repo_root: Path) -> tuple[Path, Mapping[str, Any]]:
    manifest_path = repo_root / "docs" / "data" / "legal_updates_manifest.json"
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("periods"), list):
        raise ValueError("Archive manifest has an unexpected shape.")
    if not isinstance(manifest.get("latest_period"), str):
        raise ValueError("Archive manifest is missing latest_period.")
    return manifest_path, manifest


def _resolve_period(params: Mapping[str, str], manifest: Mapping[str, Any]) -> str:
    available = {
        entry.get("value")
        for entry in manifest.get("periods", [])
        if isinstance(entry, dict) and isinstance(entry.get("value"), str)
    }
    requested = params.get("year", "")
    if requested == "all":
        return "all"
    if requested in available:
        return requested
    return str(manifest["latest_period"])


def _load_period_items(repo_root: Path, period: str, manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if period == "all":
        data_path = repo_root / "docs" / "data" / "legal_updates.json"
        expected = manifest.get("total_items")
    else:
        entry = next(
            (
                value
                for value in manifest.get("periods", [])
                if isinstance(value, dict) and value.get("value") == period
            ),
            None,
        )
        if not entry or entry.get("file") != f"./data/archive/{period}.json":
            raise ValueError(f"Archive period is unavailable: {period}")
        data_path = repo_root / "docs" / "data" / "archive" / f"{period}.json"
        expected = entry.get("count")

    items = _read_json(data_path)
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError(f"Dataset must be an array of objects: {data_path}")
    if isinstance(expected, int) and len(items) != expected:
        raise ValueError(f"Dataset count does not match the manifest: {period}")
    return items


def _filter_spec(params: Mapping[str, str], period: str, items: Sequence[Mapping[str, Any]]) -> FilterSpec:
    areas = {item.get("area") for item in items if isinstance(item.get("area"), str)}
    stages = {item.get("stage") for item in items if isinstance(item.get("stage"), str)}
    impacts = {item.get("impact_level") for item in items if isinstance(item.get("impact_level"), str)}
    source = SOURCE_SLUGS.get(params.get("source", "").strip().lower(), "")
    sort_value = params.get("sort", "published")
    return FilterSpec(
        period=period,
        search=params.get("q", "").strip(),
        area=params.get("area", "") if params.get("area", "") in areas else "",
        stage=params.get("stage", "") if params.get("stage", "") in stages else "",
        source=source,
        impact=params.get("impact", "") if params.get("impact", "") in impacts else "",
        sort=sort_value if sort_value in VALID_SORTS else "published",
        ai_only=params.get("ai") == "1",
        newly_detected_only=params.get("new") == "7",
    )


def _is_newly_detected(item: Mapping[str, Any], today: date) -> bool:
    first_seen = _strict_iso_date(item.get("first_seen_at"))
    if first_seen is None:
        return False
    age = (today - first_seen).days
    return 0 <= age < 7


def _search_haystack(item: Mapping[str, Any]) -> str:
    translations = item.get("translations")
    zh = translations.get("zh-Hans", {}) if isinstance(translations, dict) else {}
    if not isinstance(zh, dict):
        zh = {}
    values = (
        item.get("title_en"),
        item.get("title_ja"),
        item.get("summary_en"),
        zh.get("title"),
        zh.get("summary"),
        zh.get("business_impact"),
        zh.get("recommended_action"),
    )
    return " ".join(value for value in values if isinstance(value, str)).lower()


def apply_dashboard_filters(
    items: Sequence[Mapping[str, Any]], filters: FilterSpec, *, today: date
) -> list[Mapping[str, Any]]:
    query = filters.search.lower()
    matched: list[Mapping[str, Any]] = []
    for item in items:
        if filters.area and item.get("area") != filters.area:
            continue
        if filters.stage and item.get("stage") != filters.stage:
            continue
        if filters.source and item.get("source_name") != filters.source:
            continue
        if filters.impact and item.get("impact_level") != filters.impact:
            continue
        if filters.ai_only and item.get("summary_source") != "claude":
            continue
        if filters.newly_detected_only and not _is_newly_detected(item, today):
            continue
        if query and query not in _search_haystack(item):
            continue
        matched.append(item)
    return matched


def _date_rank(value: Any) -> int:
    parsed = _strict_iso_date(value)
    return parsed.toordinal() if parsed else 0


def sort_updates(items: Sequence[Mapping[str, Any]], sort_value: str) -> list[Mapping[str, Any]]:
    indexed = list(enumerate(items))
    if sort_value == "relevance":
        return [item for _, item in indexed]

    def key(pair: tuple[int, Mapping[str, Any]]) -> tuple[float, ...]:
        index, item = pair
        relevance = _finite_number(item.get("relevance_score"))
        published = _date_rank(item.get("published_at"))
        if sort_value == "checked":
            return (-_date_rank(item.get("last_checked")), -published, -relevance, index)
        if sort_value == "detected":
            return (-_date_rank(item.get("first_seen_at")), -published, -relevance, index)
        return (-published, -relevance, index)

    return [item for _, item in sorted(indexed, key=key)]


def _in_delivery_window(item: Mapping[str, Any], since: date, until: date, date_field: str) -> bool:
    field = "first_seen_at" if date_field == "first_seen" else "published_at"
    value = _strict_iso_date(item.get(field))
    return value is not None and since <= value <= until


def build_digest(
    *,
    repo_root: Path,
    dashboard_url: str,
    since: date,
    until: date,
    date_field: str = "first_seen",
    max_items: int = DEFAULT_MAX_ITEMS,
) -> DigestResult:
    if since > until:
        raise ValueError("The delivery-window start date must not be after the end date.")
    if date_field not in {"first_seen", "published"}:
        raise ValueError("date_field must be 'first_seen' or 'published'.")
    if not 1 <= max_items <= MAX_ITEMS_LIMIT:
        raise ValueError(f"max_items must be between 1 and {MAX_ITEMS_LIMIT}.")

    params = _parse_filter_params(dashboard_url)
    _, manifest = _load_manifest(repo_root)
    period = _resolve_period(params, manifest)
    all_items = _load_period_items(repo_root, period, manifest)
    filters = _filter_spec(params, period, all_items)
    filtered = apply_dashboard_filters(all_items, filters, today=until)
    windowed = [
        item for item in filtered if _in_delivery_window(item, since, until, date_field)
    ]
    sorted_items = sort_updates(windowed, filters.sort)
    return DigestResult(
        filters=filters,
        since=since,
        until=until,
        date_field=date_field,
        total_in_period=len(all_items),
        filter_match_count=len(filtered),
        window_match_count=len(sorted_items),
        items=tuple(sorted_items[:max_items]),
        dashboard_url=dashboard_url,
    )


def _criteria_text(filters: FilterSpec) -> str:
    parts: list[str] = []
    if filters.search:
        parts.append(f'Search: "{_clean_text(filters.search)}"')
    if filters.area:
        parts.append(f"Area: {filters.area}")
    if filters.stage:
        parts.append(f"Stage: {filters.stage}")
    if filters.source:
        parts.append(f"Source: {SOURCE_DISPLAY_NAMES.get(filters.source, filters.source)}")
    if filters.impact:
        parts.append(f"Impact: {filters.impact}")
    if filters.ai_only:
        parts.append("AI summaries only")
    if filters.newly_detected_only:
        parts.append("Newly detected (7 days)")
    base = " · ".join(parts) if parts else "Latest updates / no additional filters"
    period = "All years" if filters.period == "all" else filters.period
    return f"{base} · Period: {period}"


def _date_field_label(value: str) -> str:
    return "First detected by this dashboard" if value == "first_seen" else "Published"


def _summary_type(item: Mapping[str, Any]) -> str:
    return "AI Summary" if item.get("summary_source") == "claude" else "Rule-based Preview"


def _subject(frequency: str, until: date) -> str:
    label = "Daily" if frequency == "daily" else "Weekly"
    return f"[JLRW] {label} regulatory alert — {until.strftime('%d %b %Y')}"


def render_markdown(result: DigestResult, *, frequency: str, digest_title: str) -> str:
    subject = _subject(frequency, result.until)
    date_label = _date_field_label(result.date_field)
    lines = [
        "# DRAFT — HUMAN REVIEW REQUIRED",
        "",
        "Do not send this draft until every item and official source link has been reviewed.",
        "",
        f"**Subject:** {_markdown_text(subject)}",
        "",
        f"# {_markdown_text(digest_title)}",
        "",
        f"**Monitoring criteria:** {_markdown_text(_criteria_text(result.filters))}",
        f"**Coverage window ({date_label.lower()}):** {result.since.isoformat()} to {result.until.isoformat()}",
        f"**Matches:** {len(result.items)} shown of {result.window_match_count} in this delivery window",
        "",
    ]
    dashboard_url = _safe_dashboard_url(result.dashboard_url)
    if dashboard_url:
        lines.extend((f"Dashboard view: {dashboard_url}", ""))

    if not result.items:
        lines.extend(("No matching updates were identified for this delivery window.", ""))

    for index, item in enumerate(result.items, start=1):
        title = _markdown_text(item.get("title_en") or "Untitled update")
        source_name = _markdown_text(
            SOURCE_DISPLAY_NAMES.get(_clean_text(item.get("source_name")), _clean_text(item.get("source_name")))
        )
        source_url = _safe_http_url(item.get("source_url"))
        lines.extend(
            (
                f"## {index}. {title}",
                "",
                f"- Original Japanese title: {_markdown_text(item.get('title_ja')) or 'Not provided'}",
                f"- Area / stage / impact: {_markdown_text(item.get('area')) or 'Unspecified'} / {_markdown_text(item.get('stage')) or 'Unspecified'} / {_markdown_text(item.get('impact_level')) or 'Unspecified'}",
                f"- Published: {_markdown_text(item.get('published_at')) or 'Undated'}",
                f"- First detected: {_markdown_text(item.get('first_seen_at')) or 'Unknown'}",
                f"- Source: {source_name or 'Unspecified'}",
                f"- Summary type: {_summary_type(item)}",
                "",
                f"**Summary:** {_markdown_text(item.get('summary_en')) or 'No English summary is available.'}",
                "",
                f"**Potential business relevance:** {_markdown_text(item.get('business_impact_en')) or 'Review the official source to assess relevance.'}",
                "",
                f"**Suggested review:** {_markdown_text(item.get('recommended_action_en')) or 'Review the original Japanese official source.'}",
                "",
                f"Official Japanese source: {source_url or 'URL unavailable — verify manually'}",
                "",
            )
        )

    if result.omitted_count:
        lines.extend(
            (
                f"**Additional matches not included in this draft:** {result.omitted_count}",
                "Use the dashboard view above to review the remaining matches before delivery.",
                "",
            )
        )

    lines.extend(
        (
            "---",
            "",
            "This alert is a monitoring aid for general informational purposes only. It is not legal advice, an official translation, or a comprehensive statement of Japanese legal and regulatory developments. AI summaries and rule-based previews may contain errors or omissions. Original Japanese official sources remain authoritative and should be reviewed before action is taken.",
            "",
        )
    )
    return "\n".join(lines)


def render_html(result: DigestResult, *, frequency: str, digest_title: str) -> str:
    subject = _subject(frequency, result.until)
    date_label = _date_field_label(result.date_field)
    dashboard_url = _safe_dashboard_url(result.dashboard_url)
    item_blocks: list[str] = []
    for index, item in enumerate(result.items, start=1):
        title = html.escape(_clean_text(item.get("title_en")) or "Untitled update")
        source_value = _clean_text(item.get("source_name"))
        source_name = html.escape(SOURCE_DISPLAY_NAMES.get(source_value, source_value) or "Unspecified")
        source_url = _safe_http_url(item.get("source_url"))
        source_link = (
            f'<a href="{html.escape(source_url, quote=True)}" target="_blank" rel="noopener noreferrer">Open original Japanese official source</a>'
            if source_url
            else "URL unavailable — verify manually"
        )
        item_blocks.append(
            f"""
            <section class="update">
              <p class="item-number">Update {index}</p>
              <h2>{title}</h2>
              <dl>
                <dt>Original Japanese title</dt><dd>{html.escape(_clean_text(item.get('title_ja')) or 'Not provided')}</dd>
                <dt>Area / stage / impact</dt><dd>{html.escape(_clean_text(item.get('area')) or 'Unspecified')} / {html.escape(_clean_text(item.get('stage')) or 'Unspecified')} / {html.escape(_clean_text(item.get('impact_level')) or 'Unspecified')}</dd>
                <dt>Published</dt><dd>{html.escape(_clean_text(item.get('published_at')) or 'Undated')}</dd>
                <dt>First detected</dt><dd>{html.escape(_clean_text(item.get('first_seen_at')) or 'Unknown')}</dd>
                <dt>Source</dt><dd>{source_name}</dd>
                <dt>Summary type</dt><dd>{html.escape(_summary_type(item))}</dd>
              </dl>
              <h3>Summary</h3>
              <p>{html.escape(_clean_text(item.get('summary_en')) or 'No English summary is available.')}</p>
              <h3>Potential business relevance</h3>
              <p>{html.escape(_clean_text(item.get('business_impact_en')) or 'Review the official source to assess relevance.')}</p>
              <h3>Suggested review</h3>
              <p>{html.escape(_clean_text(item.get('recommended_action_en')) or 'Review the original Japanese official source.')}</p>
              <p class="source-link">{source_link}</p>
            </section>"""
        )

    if not item_blocks:
        item_blocks.append('<p class="empty">No matching updates were identified for this delivery window.</p>')

    dashboard_link = (
        f'<p><a href="{html.escape(dashboard_url, quote=True)}" target="_blank" rel="noopener noreferrer">Open the monitored dashboard view</a></p>'
        if dashboard_url
        else ""
    )
    omitted = (
        f'<div class="omitted"><strong>Additional matches not included in this draft: {result.omitted_count}</strong><br>Review the remaining dashboard matches before delivery.</div>'
        if result.omitted_count
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <title>{html.escape(subject)}</title>
  <style>
    body {{ margin: 0; color: #243241; background: #f1f3f5; font-family: Arial, sans-serif; line-height: 1.55; }}
    main {{ width: min(720px, calc(100% - 32px)); margin: 24px auto; padding: 28px; background: #fff; border-top: 4px solid #b08a3e; box-sizing: border-box; }}
    h1, h2 {{ color: #0a2540; font-family: Georgia, serif; }}
    h1 {{ margin: 0 0 12px; font-size: 26px; }}
    h2 {{ margin: 2px 0 14px; font-size: 20px; }}
    h3 {{ margin: 16px 0 4px; color: #0a2540; font-size: 13px; }}
    p {{ margin: 6px 0; }}
    a {{ color: #174f78; }}
    .draft {{ padding: 12px 14px; color: #6f321c; background: #fff3eb; border: 1px solid #e1bda9; font-weight: 700; }}
    .meta {{ padding: 14px; background: #f4f7f9; border-left: 3px solid #8da3b8; }}
    .update {{ margin-top: 22px; padding-top: 20px; border-top: 1px solid #d8dee4; }}
    .item-number {{ margin: 0; color: #8a6a2f; font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
    dl {{ display: grid; grid-template-columns: 150px 1fr; gap: 4px 12px; margin: 0; font-size: 12px; }}
    dt {{ color: #5e6b76; font-weight: 700; }} dd {{ margin: 0; }}
    .source-link {{ margin-top: 14px; font-weight: 700; }}
    .omitted {{ margin-top: 22px; padding: 12px 14px; background: #f8f4e9; border: 1px solid #dfd1ac; }}
    .trust {{ margin-top: 24px; padding-top: 16px; color: #5e6b76; border-top: 1px solid #d8dee4; font-size: 11px; }}
    .empty {{ padding: 18px 0; }}
    @media (max-width: 560px) {{ main {{ margin: 0; width: 100%; padding: 20px; }} dl {{ grid-template-columns: 1fr; }} dt {{ margin-top: 5px; }} }}
  </style>
</head>
<body>
  <main>
    <div class="draft">DRAFT — HUMAN REVIEW REQUIRED<br><span>Do not send until every item and official source link has been reviewed.</span></div>
    <p><strong>Subject:</strong> {html.escape(subject)}</p>
    <h1>{html.escape(_clean_text(digest_title))}</h1>
    <div class="meta">
      <p><strong>Monitoring criteria:</strong> {html.escape(_criteria_text(result.filters))}</p>
      <p><strong>Coverage window ({html.escape(date_label.lower())}):</strong> {result.since.isoformat()} to {result.until.isoformat()}</p>
      <p><strong>Matches:</strong> {len(result.items)} shown of {result.window_match_count} in this delivery window</p>
    </div>
    {dashboard_link}
    {''.join(item_blocks)}
    {omitted}
    <p class="trust">This alert is a monitoring aid for general informational purposes only. It is not legal advice, an official translation, or a comprehensive statement of Japanese legal and regulatory developments. AI summaries and rule-based previews may contain errors or omissions. Original Japanese official sources remain authoritative and should be reviewed before action is taken.</p>
  </main>
</body>
</html>
"""


def write_digest_files(
    result: DigestResult,
    *,
    output_dir: Path,
    file_prefix: str,
    frequency: str,
    digest_title: str,
    public_docs_root: Path | None = None,
) -> tuple[Path, Path]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", file_prefix):
        raise ValueError("file_prefix must be 1-80 safe filename characters.")
    resolved_output = output_dir.resolve()
    if public_docs_root is not None:
        resolved_docs = public_docs_root.resolve()
        if resolved_output == resolved_docs or resolved_docs in resolved_output.parents:
            raise ValueError("Alert drafts must not be written inside the public docs directory.")
    resolved_output.mkdir(parents=True, exist_ok=True)
    stem = f"{file_prefix}-{result.until.isoformat()}"
    markdown_path = resolved_output / f"{stem}.md"
    html_path = resolved_output / f"{stem}.html"
    markdown_path.write_text(
        render_markdown(result, frequency=frequency, digest_title=digest_title),
        encoding="utf-8",
    )
    html_path.write_text(
        render_html(result, frequency=frequency, digest_title=digest_title),
        encoding="utf-8",
    )
    return markdown_path, html_path


def _parse_cli_date(value: str) -> date:
    parsed = _strict_iso_date(value)
    if parsed is None:
        raise argparse.ArgumentTypeError("Use an ISO date in YYYY-MM-DD form.")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate review-required Markdown and HTML alert digest drafts."
    )
    parser.add_argument("--dashboard-url", required=True, help="Saved dashboard/filter URL.")
    parser.add_argument("--since", type=_parse_cli_date, help="Inclusive delivery-window start date.")
    parser.add_argument("--until", type=_parse_cli_date, help="Inclusive delivery-window end date.")
    parser.add_argument(
        "--date-field",
        choices=("first_seen", "published"),
        default="first_seen",
        help="Date used for delivery-window selection (default: first_seen).",
    )
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--frequency", choices=("daily", "weekly"), default="weekly")
    parser.add_argument("--digest-title", default="Japan Regulatory Alert")
    parser.add_argument("--output-dir", type=Path, help="Write .md and .html drafts here.")
    parser.add_argument("--file-prefix", default="jlrw-alert-draft")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    today = datetime.now(JST).date()
    until = args.until or today
    since = args.since or (until - timedelta(days=6))
    if until > today:
        print("error: --until must not be in the future (Asia/Tokyo).", file=sys.stderr)
        return 2
    try:
        result = build_digest(
            repo_root=args.repo_root.resolve(),
            dashboard_url=args.dashboard_url,
            since=since,
            until=until,
            date_field=args.date_field,
            max_items=args.max_items,
        )
        if args.output_dir:
            markdown_path, html_path = write_digest_files(
                result,
                output_dir=args.output_dir.resolve(),
                file_prefix=args.file_prefix,
                frequency=args.frequency,
                digest_title=args.digest_title,
                public_docs_root=args.repo_root.resolve() / "docs",
            )
            print(f"Markdown draft: {markdown_path}")
            print(f"HTML draft: {html_path}")
        else:
            print(render_markdown(result, frequency=args.frequency, digest_title=args.digest_title))
        print(
            f"Review required: {len(result.items)} shown / "
            f"{result.window_match_count} delivery-window matches / "
            f"{result.filter_match_count} filter matches.",
            file=sys.stderr,
        )
        return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
