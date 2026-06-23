#!/usr/bin/env python3
"""
fetch_updates.py — Japan Legal Reform Watch by LegalOS
Stage 1 ingestion: fetch RAW items from Japanese public-sector feeds.

What this script does
---------------------
- Fetches a small set of Japanese government / regulator RSS (or RDF/Atom) feeds.
- Normalizes each entry into a common raw schema.
- De-duplicates by `id` and by `source_url`.
- Appends only NEW items to data/raw_items.json (existing items are preserved).
- Logs errors / counts to logs/fetch.log and prints a console summary.

What this script deliberately does NOT do (later stages)
--------------------------------------------------------
- No English summarization.
- No Claude / LLM API calls.
- No modification of docs/data/legal_updates.json (the published dashboard data).
- No GitHub Actions / scheduling.

Security posture
----------------
ALL fetched data is treated as UNTRUSTED external input. In particular,
`source_url`, `title_ja`, and `raw_summary` come from third-party feeds. This
script never executes, renders, or trusts them — it only stores them (with light
text normalization for readability) for a separate, later processing stage.
`source_url` is stored verbatim (only surrounding whitespace is trimmed).

Requirements
------------
Python 3.11+. Optional but recommended: `requests` and `feedparser`
(see requirements.txt). The script falls back to the standard library
(urllib + xml.etree) if either package is unavailable.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Optional third-party deps — preferred, but not required.
try:
    import requests  # type: ignore
except ImportError:  # pragma: no cover
    requests = None

try:
    import feedparser  # type: ignore
except ImportError:  # pragma: no cover
    feedparser = None


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# Fetch targets. Keep this curated and prefer RSS /
# RDF / Atom feeds that are easy to retrieve. All values here are operator-chosen
# and trusted; the *content* returned by these URLs is treated as untrusted.
SOURCES = [
    {
        "name": "e-Gov Public Comment (意見募集案件一覧)",
        "key": "egov",
        "url": "https://public-comment.e-gov.go.jp/rss/pcm_list.xml",
        "source_type": "public_comment_rss",
        "source_language": "ja",
    },
    {
        "name": "Financial Services Agency (金融庁) 新着情報",
        "key": "fsa",
        "url": "https://www.fsa.go.jp/fsaNewsListAll_rss2.xml",
        "source_type": "regulator_rss",
        "source_language": "ja",
    },
    {
        # The Atom feed (ml_index_release_atom.xml) has been failing; the official
        # press-release index HTML is stable, so METI uses a lightweight HTML parser.
        # www.meti.go.jp is slow from CI and frequently ReadTimeouts at 20s, so this
        # source gets escalating per-attempt timeouts, longer backoff, a urllib
        # fallback when requests times out, and a browser-like (still identifying) UA.
        "name": "経済産業省 (METI) ニュースリリース",
        "key": "meti",
        "url": "https://www.meti.go.jp/press/index.html",
        "source_type": "ministry_html",
        "source_language": "ja",
        "html_parser": "meti_press_index",
        "timeouts": (30, 45, 60),
        "backoff": (3, 8, 15),
        "urllib_fallback": True,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "JapanLegalReformWatch/0.1 (+https://github.com/legalos/japan-legal-reform-watch)"
        ),
    },
    {
        "name": "Ministry of Health, Labour and Welfare (厚生労働省) 新着情報",
        "key": "mhlw",
        "url": "https://www.mhlw.go.jp/stf/news.rdf",
        "source_type": "ministry_rss",
        "source_language": "ja",
    },
    {
        "name": "Digital Agency (デジタル庁) 新着・更新",
        "key": "digital-agency",
        "url": "https://www.digital.go.jp/rss/news.xml",
        "source_type": "agency_rss",
        "source_language": "ja",
    },
    {
        "name": "消費者庁 (CAA) 新着情報",
        "key": "caa",
        "url": "https://www.caa.go.jp/news.rss",
        "source_type": "agency_rss",
        "source_language": "ja",
    },
    {
        "name": "個人情報保護委員会 (PPC) 新着情報",
        "key": "ppc",
        "url": "https://www.ppc.go.jp/information/",
        "source_type": "regulator_html",
        "source_language": "ja",
        "html_parser": "ppc_information",
    },
    {
        "name": "公正取引委員会 (JFTC) 報道発表",
        "key": "jftc",
        "url": "https://www.jftc.go.jp/houdou/pressrelease/shuyohodoR8.html",
        "source_type": "regulator_html",
        "source_language": "ja",
        "html_parser": "jftc_pressrelease",
        "prefer_urllib": True,
        "user_agent": "JapanLegalReformWatch/0.1",
    },
    {
        "name": "法務省 (MOJ) 新着情報",
        "key": "moj",
        "url": "https://www.moj.go.jp/news.xml",
        "source_type": "ministry_rss",
        "source_language": "ja",
    },
    {
        "name": "環境省 (MOE) 報道発表",
        "key": "moe",
        "url": "https://www.env.go.jp/press/",
        "source_type": "ministry_html",
        "source_language": "ja",
        "html_parser": "moe_press",
    },
    {
        "name": "財務省 (MOF) 新着情報",
        "key": "mof",
        "url": "https://www.mof.go.jp/news.rss",
        "source_type": "ministry_rss",
        "source_language": "ja",
    },
    {
        "name": "総務省 (MIC) 新着情報",
        "key": "mic",
        "url": "https://www.soumu.go.jp/news.rdf",  # Shift_JIS feed; parsers honor the XML declaration
        "source_type": "ministry_rss",
        "source_language": "ja",
    },
    {
        "name": "国土交通省 (MLIT) 報道発表",
        "key": "mlit",
        "url": "https://www.mlit.go.jp/report/press/",
        "source_type": "ministry_html",
        "source_language": "ja",
        "html_parser": "mlit_press",
        "follow_meta_refresh": True,
    },
    {
        "name": "農林水産省 (MAFF) 報道発表",
        "key": "maff",
        "url": "https://www.maff.go.jp/j/press/rss.xml",
        "source_type": "ministry_rss",
        "source_language": "ja",
    },
]

USER_AGENT = (
    "JapanLegalReformWatch/0.1 (+https://github.com/legalos/japan-legal-reform-watch; "
    "non-commercial ingestion bot; respects robots/ToS)"
)
DEFAULT_TIMEOUT_SECONDS = 20
MAX_ITEMS_PER_SOURCE = 100  # safety cap so one large feed cannot dominate a run
JST = ZoneInfo("Asia/Tokyo")
HTTP_MAX_ATTEMPTS = 3
HTTP_RETRY_BACKOFF_SECONDS = (2, 5)

# Paths are resolved relative to the repository root (this file lives in scripts/).
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
RAW_ITEMS_PATH = DATA_DIR / "raw_items.json"
LOG_DIR = REPO_ROOT / "logs"
LOG_PATH = LOG_DIR / "fetch.log"
SOURCE_FETCH_REPORT_PATH = LOG_DIR / "source_fetch_report.json"

logger = logging.getLogger("jlrw.fetch")


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

def setup_logging() -> None:
    """File handler (INFO, full detail) + console handler (WARNING and above)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    file_fmt = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    file_fmt.converter = time.gmtime  # log timestamps in UTC
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(file_fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(ch)


# --------------------------------------------------------------------------- #
# Fetch + parse (with stdlib fallbacks)
# --------------------------------------------------------------------------- #

def _http_get_once(
    url: str,
    timeout: int,
    prefer_urllib: bool = False,
    accept_html: bool = False,
    user_agent: str = USER_AGENT,
) -> bytes:
    """Fetch raw bytes. Prefer `requests`; fall back to urllib. Sets UA + timeout."""
    accept = (
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        if accept_html else
        "application/rss+xml, application/rdf+xml, application/atom+xml, application/xml, text/xml, */*"
    )
    headers = {
        "User-Agent": user_agent,
        "Accept": accept,
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    }
    if requests is not None and not prefer_urllib:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (trusted scheme below)
        status = getattr(r, "status", 200)
        if status and status >= 400:
            raise urllib.error.HTTPError(url, status, f"HTTP {status}", r.headers, None)
        return r.read()


def _http_status_from_exception(exc: BaseException) -> int | None:
    if isinstance(exc, urllib.error.HTTPError):
        return int(exc.code)
    if requests is not None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code is not None:
            return int(status_code)
    return None


def _is_retryable_fetch_error(exc: BaseException) -> bool:
    status = _http_status_from_exception(exc)
    if status is not None:
        return status == 429 or 500 <= status <= 599
    if isinstance(exc, (TimeoutError, socket.timeout, urllib.error.URLError)):
        return True
    if requests is not None and isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    return False


def _is_network_error(exc: BaseException) -> bool:
    """A read-timeout / connection-level failure (not an HTTP status error) for
    which switching transport (requests -> urllib) is worth trying."""
    if _http_status_from_exception(exc) is not None:
        return False  # an HTTP status error is not a transport problem
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError):  # HTTPError handled above by status
        return True
    if requests is not None and isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    return False


def _fetch_error_label(exc: BaseException) -> str:
    status = _http_status_from_exception(exc)
    if status is not None:
        return f"HTTP {status}"
    return type(exc).__name__


def http_get(
    url: str,
    timeout: int,
    prefer_urllib: bool = False,
    accept_html: bool = False,
    user_agent: str = USER_AGENT,
    timeouts: tuple[int, ...] | None = None,
    backoff: tuple[int, ...] | None = None,
    urllib_fallback: bool = False,
) -> bytes:
    """Fetch raw bytes with limited retry for transient network failures.

    `timeouts` gives a per-attempt (escalating) read timeout — e.g. (30, 45, 60)
    for a slow source — overriding the single `timeout`. `backoff` overrides the
    inter-attempt delays. `urllib_fallback` switches transport from requests to
    urllib after a requests network failure (some hosts time out under requests
    but respond to urllib). No unbounded retry: at most max(HTTP_MAX_ATTEMPTS,
    len(timeouts)) attempts.
    """
    schedule = list(timeouts) if timeouts else [timeout]
    delays = list(backoff) if backoff else list(HTTP_RETRY_BACKOFF_SECONDS)
    attempts = max(HTTP_MAX_ATTEMPTS, len(schedule))
    force_urllib = prefer_urllib
    last_exc: BaseException | None = None
    for attempt in range(attempts):
        attempt_timeout = schedule[min(attempt, len(schedule) - 1)]
        try:
            return _http_get_once(url, attempt_timeout, force_urllib, accept_html, user_agent)
        except Exception as exc:
            last_exc = exc
            # After a requests transport failure, switch to urllib for later attempts.
            if urllib_fallback and not force_urllib and _is_network_error(exc):
                force_urllib = True
                logger.warning(
                    "requests fetch failed for %s (%s); switching to urllib for retry.",
                    url, _fetch_error_label(exc),
                )
            if attempt >= attempts - 1 or not _is_retryable_fetch_error(exc):
                raise
            delay = delays[min(attempt, len(delays) - 1)]
            logger.warning(
                "Transient fetch error for %s (%s, attempt %d/%d); retrying in %ss.",
                url,
                _fetch_error_label(exc),
                attempt + 1,
                attempts,
                delay,
            )
            time.sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("unreachable fetch retry state")


_META_REFRESH_RE = re.compile(
    r"<meta\b(?=[^>]*http-equiv=[\"']?refresh[\"']?)[^>]*content=[\"'][^\"']*url=(?P<url>[^\"';>]+)",
    re.IGNORECASE,
)


def follow_meta_refresh_if_requested(content: bytes, source: dict, timeout: int) -> tuple[bytes, str]:
    """Follow an official HTML meta-refresh landing page when explicitly allowed."""
    original_url = source["url"]
    if not source.get("follow_meta_refresh"):
        return content, original_url

    text = content.decode("utf-8", errors="replace")
    match = _META_REFRESH_RE.search(text)
    if not match:
        return content, original_url

    next_url = urllib.parse.urljoin(original_url, html.unescape(match.group("url")).strip())
    if not next_url.startswith("https://"):
        logger.warning("Skipping non-HTTPS meta refresh target from %s: %s", original_url, next_url)
        return content, original_url

    logger.info("Following meta refresh for %s -> %s", original_url, next_url)
    refreshed = http_get(
        next_url,
        timeout,
        prefer_urllib=bool(source.get("prefer_urllib")),
        accept_html=True,
        user_agent=str(source.get("user_agent") or USER_AGENT),
    )
    return refreshed, next_url


def parse_feed(content: bytes) -> list[dict]:
    """Return a list of {title, link, summary, published_iso} dicts."""
    if feedparser is not None:
        return _parse_with_feedparser(content)
    return _parse_with_stdlib(content)


def parse_source_entries(content: bytes, source: dict) -> list[dict]:
    """Parse RSS/RDF/Atom sources, or a small allow-list of official HTML pages."""
    if str(source.get("source_type", "")).endswith("_html"):
        return parse_html_source(content, source)
    return parse_feed(content)


def parse_html_source(content: bytes, source: dict) -> list[dict]:
    parser_name = source.get("html_parser")
    text = content.decode("utf-8", errors="replace")
    if parser_name == "ppc_information":
        return _parse_ppc_information_html(text, source["url"])
    if parser_name == "jftc_pressrelease":
        return _parse_jftc_pressrelease_html(text, source["url"])
    if parser_name == "moe_press":
        return _parse_moe_press_html(text, source["url"])
    if parser_name == "mlit_press":
        return _parse_mlit_press_html(text, source["url"])
    if parser_name == "meti_press_index":
        return _parse_meti_press_index_html(text, source["url"])
    raise ValueError(f"Unsupported html_parser: {parser_name}")


_PPC_ITEM_RE = re.compile(
    r"<li>\s*"
    r"<time[^>]+datetime=[\"'](?P<date>\d{4}-\d{2}-\d{2})[\"'][^>]*>.*?</time>\s*"
    r"<div[^>]+class=[\"'][^\"']*news-label-wrap[^\"']*[\"'][^>]*>(?P<label_html>.*?)</div>\s*"
    r"<div[^>]+class=[\"'][^\"']*news-text[^\"']*[\"'][^>]*>\s*"
    r"<a\s+href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<title_html>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)


def _parse_ppc_information_html(text: str, base_url: str) -> list[dict]:
    items: list[dict] = []
    for match in _PPC_ITEM_RE.finditer(text):
        title = clean_text(match.group("title_html"))
        href = html.unescape(match.group("href")).strip()
        label = clean_text(match.group("label_html"))
        if not title or not href:
            continue
        items.append(
            {
                "title": title,
                "link": urllib.parse.urljoin(base_url, href),
                "summary": label,
                "published_iso": match.group("date"),
            }
        )
    return items


_JFTC_LIST_RE = re.compile(r"<ul[^>]+class=[\"'][^\"']*norcor[^\"']*[\"'][^>]*>(?P<body>.*?)</ul>", re.IGNORECASE | re.DOTALL)
_JFTC_LINK_RE = re.compile(r"<li>\s*<a\s+href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<title_html>.*?)</a>\s*</li>", re.IGNORECASE | re.DOTALL)
_JFTC_DATE_TITLE_RE = re.compile(r"^\((?P<date>[^)]+)\)\s*(?P<title>.+)$")


def _parse_jftc_pressrelease_html(text: str, base_url: str) -> list[dict]:
    items: list[dict] = []
    for list_match in _JFTC_LIST_RE.finditer(text):
        for match in _JFTC_LINK_RE.finditer(list_match.group("body")):
            raw_title = clean_text(match.group("title_html"))
            href = html.unescape(match.group("href")).strip()
            if not raw_title or not href:
                continue
            published_iso = ""
            title = raw_title
            date_match = _JFTC_DATE_TITLE_RE.match(raw_title)
            if date_match:
                published_iso = _normalize_japanese_date(date_match.group("date"))
                title = date_match.group("title").strip()
            items.append(
                {
                    "title": title,
                    "link": urllib.parse.urljoin(base_url, href),
                    "summary": "報道発表",
                    "published_iso": published_iso,
                }
            )
    return items


# MOE press-release list (env.go.jp/press/): <details> blocks grouped by a
# "YYYY年MM月DD日発表" heading, each containing c-news-link__item entries.
_MOE_BLOCK_RE = re.compile(
    r"<span[^>]+class=[\"'][^\"']*p-press-release-list__heading[^\"']*[\"'][^>]*>\s*"
    r"(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日発表\s*</span>"
    r"(?P<body>.*?)</details>",
    re.IGNORECASE | re.DOTALL,
)
_MOE_ITEM_RE = re.compile(
    r"<li[^>]+class=[\"'][^\"']*c-news-link__item[^\"']*[\"'][^>]*>(?P<item>.*?)</li>",
    re.IGNORECASE | re.DOTALL,
)
_MOE_LINK_RE = re.compile(
    r"<a\s+(?=[^>]*class=[\"'][^\"']*c-news-link__link[^\"']*[\"'])[^>]*href=[\"'](?P<href>[^\"']+)[\"'][^>]*>"
    r"(?P<title_html>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_MOE_TAG_RE = re.compile(
    r"<span[^>]+class=[\"'][^\"']*c-tag[^\"']*[\"'][^>]*>(?P<tag>.*?)</span>",
    re.IGNORECASE | re.DOTALL,
)


def _parse_moe_press_html(text: str, base_url: str) -> list[dict]:
    items: list[dict] = []
    for block in _MOE_BLOCK_RE.finditer(text):
        try:
            published_iso = datetime(
                int(block.group("year")), int(block.group("month")), int(block.group("day"))
            ).strftime("%Y-%m-%d")
        except ValueError:
            published_iso = ""  # malformed heading date — never guess
        for item_match in _MOE_ITEM_RE.finditer(block.group("body")):
            item_html = item_match.group("item")
            link = _MOE_LINK_RE.search(item_html)
            if not link:
                continue
            title = clean_text(link.group("title_html"))
            href = html.unescape(link.group("href")).strip()
            if not title or not href:
                continue
            tag = _MOE_TAG_RE.search(item_html)
            items.append(
                {
                    "title": title,
                    "link": urllib.parse.urljoin(base_url, href),
                    "summary": clean_text(tag.group("tag")) if tag else "報道発表",
                    "published_iso": published_iso,
                }
            )
    return items


_MLIT_BLOCK_RE = re.compile(
    r"<dt>\s*(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日\s*</dt>"
    r"(?P<body>.*?)(?=<dt>|</dl>)",
    re.IGNORECASE | re.DOTALL,
)
_MLIT_LINK_RE = re.compile(
    r"<a\s+[^>]*href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<title_html>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)


def _parse_mlit_press_html(text: str, base_url: str) -> list[dict]:
    """Parse MLIT's date-grouped current-month press-release list."""
    items: list[dict] = []
    seen_links: set[str] = set()
    for block in _MLIT_BLOCK_RE.finditer(text):
        try:
            published_iso = datetime(
                int(block.group("year")), int(block.group("month")), int(block.group("day"))
            ).strftime("%Y-%m-%d")
        except ValueError:
            published_iso = ""  # malformed heading date - never guess

        for link in _MLIT_LINK_RE.finditer(block.group("body")):
            title = clean_text(link.group("title_html"))
            href = html.unescape(link.group("href")).strip()
            full_url = urllib.parse.urljoin(base_url, href)
            if not title or not href or full_url in seen_links:
                continue
            if "/report/press/" not in full_url:
                continue
            if re.search(r"/report/press/(houdou\d{6}|[a-z_]+_news)\.html$", full_url):
                continue
            seen_links.add(full_url)
            items.append(
                {
                    "title": title,
                    "link": full_url,
                    "summary": "報道発表",
                    "published_iso": published_iso,
                }
            )
    return items


# METI "最新ニュースリリース" index (meti.go.jp/press/index.html): press-release
# links grouped under Japanese date headings (and/or per-item <time datetime>).
# Each press link is dated by the NEAREST PRECEDING date marker, so this tolerates
# both date-grouped and per-item layouts.
_METI_TIME_RE = re.compile(r'<time[^>]+datetime=["\'](?P<date>\d{4}-\d{2}-\d{2})["\']', re.IGNORECASE)
_METI_JP_DATE_RE = re.compile(r"(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日")
_METI_LINK_RE = re.compile(
    r'<a\s+[^>]*href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<title_html>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
# A METI press-release detail page lives under /press/<year>/... and ends in .html
# (this excludes nav, category, and back-number links such as /press/index.html).
_METI_PRESS_PATH_RE = re.compile(r"/press/\d{4}/")


def _parse_meti_press_index_html(text: str, base_url: str) -> list[dict]:
    date_markers: list[tuple[int, str]] = []
    for match in _METI_TIME_RE.finditer(text):
        date_markers.append((match.start(), match.group("date")))
    for match in _METI_JP_DATE_RE.finditer(text):
        try:
            iso = datetime(
                int(match.group("year")), int(match.group("month")), int(match.group("day"))
            ).strftime("%Y-%m-%d")
        except ValueError:
            continue  # malformed date — never guess
        date_markers.append((match.start(), iso))
    date_markers.sort()

    def date_before(position: int) -> str:
        iso = ""
        for marker_pos, marker_iso in date_markers:
            if marker_pos <= position:
                iso = marker_iso
            else:
                break
        return iso

    items: list[dict] = []
    seen_links: set[str] = set()
    for link in _METI_LINK_RE.finditer(text):
        title = clean_text(link.group("title_html"))
        href = html.unescape(link.group("href")).strip()
        if not title or not href:
            continue
        full_url = urllib.parse.urljoin(base_url, href)
        if not _METI_PRESS_PATH_RE.search(full_url) or not full_url.endswith(".html"):
            continue
        if full_url in seen_links:
            continue
        seen_links.add(full_url)
        items.append(
            {
                "title": title,
                "link": full_url,
                "summary": "ニュースリリース",
                "published_iso": date_before(link.start()),
            }
        )
    return items


def _parse_with_feedparser(content: bytes) -> list[dict]:
    parsed = feedparser.parse(content)
    items: list[dict] = []
    for e in parsed.entries:
        published_iso = ""
        for attr in ("published_parsed", "updated_parsed"):
            tp = getattr(e, attr, None)
            if tp:
                # feedparser normalizes *_parsed to UTC struct_time.
                published_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", tp)
                break
        items.append(
            {
                "title": getattr(e, "title", "") or "",
                "link": getattr(e, "link", "") or "",
                "summary": getattr(e, "summary", "") if hasattr(e, "summary") else "",
                "published_iso": published_iso,
            }
        )
    return items


def _local(tag: str) -> str:
    """Strip XML namespace, lowercase. '{ns}Item' -> 'item'."""
    return tag.split("}")[-1].lower()


def _parse_with_stdlib(content: bytes) -> list[dict]:
    """Minimal RSS 2.0 / RSS 1.0 (RDF) / Atom parser using xml.etree."""
    import xml.etree.ElementTree as ET

    # Parse raw bytes first so the XML-declared encoding is honored where expat
    # supports it. expat rejects multi-byte encodings such as Shift_JIS (the MIC
    # feed) with ValueError, so fall back to decoding with the declared encoding
    # and stripping the declaration (expat also refuses str input that still
    # carries an encoding declaration).
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, ValueError):
        declared = re.search(rb"encoding=[\"']([A-Za-z0-9_.-]+)[\"']", content[:200])
        encoding = declared.group(1).decode("ascii", "replace") if declared else "utf-8"
        try:
            text = content.decode(encoding, errors="replace")
        except LookupError:
            text = content.decode("utf-8", errors="replace")
        text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text.lstrip("﻿"), count=1)
        root = ET.fromstring(text)
    items: list[dict] = []

    # Atom: <feed><entry>...
    if _local(root.tag) == "feed":
        for entry in (el for el in root if _local(el.tag) == "entry"):
            title = link = summary = pub = ""
            for child in entry:
                ln = _local(child.tag)
                if ln == "title":
                    title = (child.text or "").strip()
                elif ln == "link":
                    rel = child.get("rel", "alternate")
                    if rel == "alternate" or not link:
                        link = child.get("href", "") or link
                elif ln in ("summary", "content") and not summary:
                    summary = child.text or ""
                elif ln in ("published", "updated") and not pub:
                    pub = (child.text or "").strip()
            items.append(
                {"title": title, "link": link, "summary": summary, "published_iso": _normalize_date(pub)}
            )
        return items

    # RSS 2.0 (<rss><channel><item>) and RSS 1.0 RDF (<rdf:RDF><item>):
    # collect every <item> regardless of namespace.
    for it in (el for el in root.iter() if _local(el.tag) == "item"):
        title = link = summary = pub = ""
        for child in it:
            ln = _local(child.tag)
            if ln == "title":
                title = (child.text or "").strip()
            elif ln == "link":
                if child.text and child.text.strip():
                    link = child.text.strip()
                elif child.get("href"):
                    link = child.get("href", "")
            elif ln in ("description", "summary", "encoded") and not summary:
                summary = child.text or ""
            elif ln in ("date", "pubdate", "published", "updated") and not pub:
                pub = (child.text or "").strip()
        items.append(
            {"title": title, "link": link, "summary": summary, "published_iso": _normalize_date(pub)}
        )
    return items


def _normalize_date(value: str) -> str:
    """Best-effort parse to ISO. Returns '' if unparseable (never guesses)."""
    if not value:
        return ""
    value = value.strip()

    # RFC 822 (RSS 2.0 pubDate), e.g. "Tue, 09 Jun 2026 17:00:00 +0900"
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(value)
        if dt is not None:
            return _to_utc_iso(dt)
    except (TypeError, ValueError):
        pass

    # ISO 8601 (Atom / dc:date), e.g. "2026-06-09T17:00:00+09:00" or "...Z"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _to_utc_iso(dt)
    except ValueError:
        pass

    # Date only, e.g. "2026-06-10"
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return ""


_JP_ERA_DATE_RE = re.compile(r"^(?P<era>令和|平成|昭和)(?P<year>\d+|元)年(?P<month>\d{1,2})月(?P<day>\d{1,2})日")
_JP_ERA_BASE_YEAR = {"令和": 2018, "平成": 1988, "昭和": 1925}


def _normalize_japanese_date(value: str) -> str:
    """Parse explicit Japanese era dates such as '令和8年6月10日'."""
    value = clean_text(value)
    match = _JP_ERA_DATE_RE.match(value)
    if not match:
        return ""
    year_text = match.group("year")
    era_year = 1 if year_text == "元" else int(year_text)
    year = _JP_ERA_BASE_YEAR[match.group("era")] + era_year
    month = int(match.group("month"))
    day = int(match.group("day"))
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Normalization + identity
# --------------------------------------------------------------------------- #

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(value: str) -> str:
    """Decode HTML entities, strip tags, collapse whitespace. Untrusted in -> safe-to-store text out."""
    if not value:
        return ""
    value = html.unescape(value)
    value = _TAG_RE.sub(" ", value)
    value = html.unescape(value)
    return _WS_RE.sub(" ", value).strip()


def make_id(source_url: str, title_ja: str, source_name: str, published_at: str) -> str:
    """Stable id. Prefer source_url; fall back to title+source+date when no URL."""
    if source_url:
        basis = "url:" + source_url
    else:
        basis = "meta:" + "|".join([title_ja, source_name, published_at])
    return "raw-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def content_hash(title_ja: str, raw_summary: str, published_at: str) -> str:
    """Hash of the content payload — lets a later stage detect changed items."""
    basis = "\n".join([title_ja, raw_summary, published_at])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def build_item(entry: dict, source: dict, fetched_at: str) -> dict | None:
    title_ja = clean_text(entry.get("title", ""))
    raw_summary = clean_text(entry.get("summary", ""))
    source_url = (entry.get("link") or "").strip()  # stored verbatim (whitespace only trimmed)
    published_at = entry.get("published_iso") or ""  # may be "" — we never guess a date

    if not title_ja and not source_url:
        return None  # nothing usable to identify or display

    return {
        "id": make_id(source_url, title_ja, source["name"], published_at),
        "title_ja": title_ja,
        "source_name": source["name"],
        "source_url": source_url,
        "published_at": published_at,
        "fetched_at": fetched_at,
        "source_language": source.get("source_language", "ja"),
        "raw_summary": raw_summary,
        "raw_content_hash": content_hash(title_ja, raw_summary, published_at),
        "source_type": source.get("source_type", "rss"),
    }


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #

def load_existing(path: Path) -> list[dict]:
    """Load existing raw items. If the file is corrupt, back it up rather than clobber it."""
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        logger.warning("Existing %s is not a JSON array; treating as empty.", path.name)
        return []
    except (json.JSONDecodeError, OSError) as exc:
        backup = path.with_name(path.name + ".corrupt-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        try:
            path.replace(backup)
            logger.error("Could not parse %s (%s); backed it up to %s and starting fresh.", path.name, exc, backup.name)
        except OSError:
            logger.error("Could not parse or back up %s (%s); starting fresh in memory.", path.name, exc)
        return []


def save_json(path: Path, data: list[dict]) -> None:
    """Atomic, human-readable write (ensure_ascii=False, indent=2)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def save_json_document(path: Path, data: dict) -> None:
    """Atomic, human-readable JSON document write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def sanitize_error_message(value: object, max_chars: int = 300) -> str:
    """Keep source-health errors concise and safe for logs/reports."""
    text = _WS_RE.sub(" ", str(value or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def valid_published_date(value: str) -> str | None:
    """Return YYYY-MM-DD for valid date/date-time strings; otherwise None."""
    if not value or not isinstance(value, str):
        return None
    date_text = value[:10]
    try:
        datetime.strptime(date_text, "%Y-%m-%d")
    except ValueError:
        return None
    return date_text


def latest_published_date(items: list[dict]) -> str | None:
    dates = [date for date in (valid_published_date(item.get("published_at", "")) for item in items) if date]
    return max(dates) if dates else None


def current_jst_date() -> str:
    """Return today's date for first detection, using the Japan service calendar."""
    return datetime.now(JST).date().isoformat()


def source_report_row(
    source: dict,
    status: str,
    duration_ms: int,
    fetched_count: int = 0,
    new_count: int = 0,
    latest_published_at: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> dict:
    return {
        "source_key": source.get("key", ""),
        "source_name": source.get("name", ""),
        "source_url": source.get("url", ""),
        "status": status,
        "fetched_count": fetched_count,
        "new_count": new_count,
        "latest_published_at": latest_published_at,
        "duration_ms": duration_ms,
        "error_type": error_type,
        "error_message": error_message,
    }


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def run(timeout: int, dry_run: bool, first_seen_date: str | None = None) -> int:
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fetched_at = started_at
    detected_date = first_seen_date or current_jst_date()
    logger.info("=== fetch_updates run start (fetched_at=%s, dry_run=%s) ===", fetched_at, dry_run)

    existing = load_existing(RAW_ITEMS_PATH)
    seen_ids = {it["id"] for it in existing if it.get("id")}
    seen_urls = {it["source_url"] for it in existing if it.get("source_url")}

    checked_sources = 0
    fetched_items = 0
    failed_sources: list[dict] = []
    new_items: list[dict] = []
    source_reports: list[dict] = []

    for source in SOURCES:
        checked_sources += 1
        name, url = source["name"], source["url"]
        source_started = time.monotonic()
        try:
            content = http_get(
                url,
                int(source.get("timeout", timeout)),
                prefer_urllib=bool(source.get("prefer_urllib")),
                accept_html=str(source.get("source_type", "")).endswith("_html"),
                user_agent=str(source.get("user_agent") or USER_AGENT),
                timeouts=source.get("timeouts"),
                backoff=source.get("backoff"),
                urllib_fallback=bool(source.get("urllib_fallback")),
            )
            content, effective_url = follow_meta_refresh_if_requested(content, source, timeout)
            parse_source = dict(source)
            parse_source["url"] = effective_url
            entries = parse_source_entries(content, parse_source)[:MAX_ITEMS_PER_SOURCE]
            fetched_items += len(entries)
            logger.info("OK   %s — %d entries from %s", name, len(entries), effective_url)
            if not entries:
                logger.warning("No entries parsed from %s (%s).", name, effective_url)

            source_items_by_id: dict[str, dict] = {}
            source_new_count = 0
            for entry in entries:
                item = build_item(entry, source, fetched_at)
                if item is None:
                    continue
                source_items_by_id.setdefault(item["id"], item)
                if item["id"] in seen_ids:
                    continue
                if item["source_url"] and item["source_url"] in seen_urls:
                    continue
                seen_ids.add(item["id"])
                if item["source_url"]:
                    seen_urls.add(item["source_url"])
                item["first_seen_at"] = detected_date
                new_items.append(item)
                source_new_count += 1
            duration_ms = int(round((time.monotonic() - source_started) * 1000))
            source_reports.append(
                source_report_row(
                    source,
                    "success",
                    duration_ms,
                    fetched_count=len(source_items_by_id),
                    new_count=source_new_count,
                    latest_published_at=latest_published_date(list(source_items_by_id.values())),
                )
            )
        except Exception as exc:  # noqa: BLE001 — one bad source must not stop the run
            duration_ms = int(round((time.monotonic() - source_started) * 1000))
            message = sanitize_error_message(exc)
            reason = f"{type(exc).__name__}: {message}"
            failed_sources.append({"name": name, "url": url, "reason": reason})
            logger.error("FAIL %s (%s): %s", name, url, reason)
            source_reports.append(
                source_report_row(
                    source,
                    "error",
                    duration_ms,
                    error_type=type(exc).__name__,
                    error_message=message,
                )
            )

    combined = existing + new_items
    if dry_run:
        logger.info("DRY-RUN: not writing %s (%d new, %d total).", RAW_ITEMS_PATH.name, len(new_items), len(combined))
    else:
        save_json(RAW_ITEMS_PATH, combined)
        logger.info("Wrote %s (%d new, %d total).", RAW_ITEMS_PATH.name, len(new_items), len(combined))

    finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = {
        "schema_version": 1,
        "started_at": started_at,
        "finished_at": finished_at,
        "configured_source_count": len(SOURCES),
        "sources": source_reports,
    }
    save_json_document(SOURCE_FETCH_REPORT_PATH, report)
    logger.info("Wrote %s (%d source rows).", SOURCE_FETCH_REPORT_PATH.name, len(source_reports))

    logger.info(
        "RUN SUMMARY checked_sources=%d fetched_items=%d new_items=%d total_items=%d failed_sources=%d",
        checked_sources, fetched_items, len(new_items), len(combined), len(failed_sources),
    )
    for fs in failed_sources:
        logger.info("failed_source: %s (%s) reason=%s", fs["name"], fs["url"], fs["reason"])

    _print_console_summary(checked_sources, fetched_items, len(new_items), len(combined), failed_sources, dry_run)

    # Exit non-zero only if we had sources but none succeeded (total failure).
    if SOURCES and len(failed_sources) == len(SOURCES):
        return 1
    return 0


def _print_console_summary(checked, fetched, new, total, failed, dry_run) -> None:
    print("\n==== fetch_updates summary ====")
    print(f"checked_sources : {checked}")
    print(f"fetched_items   : {fetched}")
    print(f"new_items       : {new}")
    print(f"total_items     : {total}")
    print(f"failed_sources  : {len(failed)}")
    for fs in failed:
        print(f"    - {fs['name']} ({fs['url']}): {fs['reason']}")
    if dry_run:
        print("(dry-run: data/raw_items.json was not modified)")
    print(f"log: {LOG_PATH}")
    print(f"source report: {SOURCE_FETCH_REPORT_PATH}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch raw Japanese legal/regulatory feed items.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="Per-request timeout (seconds).")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and report, but do not write raw_items.json.")
    args = parser.parse_args(argv)

    setup_logging()
    try:
        return run(timeout=args.timeout, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 — last-resort guard so we always log
        logger.exception("Unexpected fatal error: %s", exc)
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
