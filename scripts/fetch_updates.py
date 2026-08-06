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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from public_comment_deadlines import extract_egov_comment_deadline, normalize_comment_deadline

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
        # Stable current-session list. Each bill/status pair gets an event
        # identity so a later transition (for example, deliberating -> enacted)
        # is appended without changing the original official source URL.
        "name": "House of Representatives (衆議院) 議案情報",
        "key": "shugiin-bills",
        "url": "https://www.shugiin.go.jp/internet/itdb_gian.nsf/html/gian/menu.htm",
        "source_type": "legislature_html",
        "source_language": "ja",
        "html_parser": "shugiin_current_bills",
        "encoding": "shift_jis",
        "dedupe_by_url": False,
        "max_items": 250,
    },
    {
        # The endpoint requires a yyyyMMdd suffix. The source-specific fetcher
        # requests a seven-day lookback (excluding today) so a missed daily run
        # does not create a permanent gap. HTTP 404 means no updates for a date.
        "name": "e-Gov Law Search (法令更新一覧)",
        "key": "egov-laws",
        "url": "https://laws.e-gov.go.jp/api/1/updatelawlists/",
        "source_type": "law_api",
        "source_language": "ja",
        "entry_fetcher": "egov_updated_laws",
        "lookback_days": 7,
        "dedupe_by_url": False,
        "max_items": 300,
    },
    {
        "name": "Japan Exchange Group (JPX) Public Comments",
        "key": "jpx-comments",
        "url": "https://www.jpx.co.jp/rules-participants/public-comment/index.html",
        "source_type": "public_comment_html",
        "source_language": "ja",
        "html_parser": "jpx_public_comments",
    },
    {
        "name": "Tokyo Stock Exchange (JPX) Rule Revisions",
        "key": "jpx-rules",
        "url": "https://www.jpx.co.jp/rules-participants/rules/revise/index.html",
        "source_type": "regulator_html",
        "source_language": "ja",
        "html_parser": "jpx_rule_revisions",
    },
    {
        "name": "Pharmaceuticals and Medical Devices Agency (PMDA) Safety Updates",
        "key": "pmda",
        "url": "https://www.pmda.go.jp/safety/0001.html",
        "source_type": "pmda_safety_html",
        "source_language": "ja",
        "html_parser": "pmda_safety_updates",
        # PMDA reuses landing URLs for recurring monthly updates. The event key
        # keeps date/title changes while the official link remains untouched.
        "dedupe_by_url": False,
        "history_days": 550,
        "max_items": 200,
    },
    {
        "name": "Japan Securities Dealers Association (JSDA) Public Comments",
        "key": "jsda-comments",
        "url": "https://www.jsda.or.jp/about/public/bosyu/index.html",
        "source_type": "public_comment_html",
        "source_language": "ja",
        "html_parser": "jsda_public_comments",
    },
    {
        "name": "Japan Securities Dealers Association (JSDA) Public Comment Results",
        "key": "jsda-results",
        "url": "https://www.jsda.or.jp/about/public/kekka/index.html",
        "source_type": "public_comment_results_html",
        "source_language": "ja",
        "html_parser": "jsda_public_comment_results",
    },
    {
        # The official recent filter is limited by the Courts site to Supreme
        # Court judgments and decisions from the past three months. This avoids
        # treating the much broader all-courts search as comprehensive coverage.
        "name": "Courts in Japan (裁判所) Recent Supreme Court Decisions",
        "key": "courts-supreme",
        "url": (
            "https://www.courts.go.jp/hanrei/search2/index.html?"
            "courtCaseType=1&filter%5Brecent%5D=1"
        ),
        "source_type": "court_html",
        "source_language": "ja",
        "html_parser": "courts_recent_supreme",
        "max_items": 100,
    },
    {
        # Resolve the current-year page from this stable archive index on every
        # run instead of hard-coding /c_2026/. The adapter retains only focused
        # enforcement, market-monitoring policy, and public-comment updates.
        "name": "Securities and Exchange Surveillance Commission (SESC) Enforcement Updates",
        "key": "sesc",
        "url": "https://www.fsa.go.jp/sesc/news/news.html",
        "source_type": "enforcement_html",
        "source_language": "ja",
        "entry_fetcher": "sesc_current_year",
        "html_parser": "sesc_current_year",
        "max_items": 100,
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
        # www.meti.go.jp is slow/unreliable from CI. We still try every run, but a
        # METI-only failure must NOT keep the daily workflow red, so it is a
        # warning-only (non-gating) source. Timeouts are kept modest because the
        # failure no longer blocks the pipeline (worst case ~20+3+35+6+50 ≈ 114s).
        "timeouts": (20, 35, 50),
        "backoff": (3, 6),
        "urllib_fallback": True,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "JapanLegalReformWatch/0.1 (+https://github.com/legalos/japan-legal-reform-watch)"
        ),
        "gate_required": False,
        "health_severity": "warning",
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


def _compact_date(value: str) -> str:
    value = str(value or "").strip()
    try:
        return datetime.strptime(value, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _parse_egov_updated_laws_xml(content: bytes, update_date: str, today: str) -> list[dict]:
    """Map one e-Gov update-list response into the raw entry shape."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(content)
    result_code = (root.findtext("./Result/Code") or "").strip()
    rows = root.findall("./ApplData/LawNameListInfo")
    if result_code != "0" and not rows:
        # The endpoint normally expresses a no-update date as HTTP 404. Treat a
        # body with no rows the same way; no arbitrary dates or records are made.
        return []

    entries: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        value = lambda tag: (row.findtext(tag) or "").strip()  # noqa: E731
        law_name = value("LawName")
        amend_name = value("AmendName")
        title = amend_name or law_name
        law_url = value("LawUrl")
        if not title or not law_url:
            continue

        promulgation_date = _compact_date(value("AmendPromulgationDate")) or _compact_date(
            value("PromulgationDate")
        )
        enforcement_date = _compact_date(value("EnforcementDate"))
        if enforcement_date and enforcement_date > today:
            stage_hint = "Scheduled to Take Effect"
        elif enforcement_date and enforcement_date <= today:
            stage_hint = "In Force"
        else:
            stage_hint = "Promulgated"

        identity = (law_url, value("AmendNo"), enforcement_date)
        if identity in seen:
            continue
        seen.add(identity)
        summary_parts = [
            f"更新一覧日: {update_date}",
            f"対象法令: {law_name}",
            f"法令番号: {value('LawNo')}",
        ]
        if amend_name:
            summary_parts.append(f"改正法令: {amend_name}")
        if value("AmendNo"):
            summary_parts.append(f"改正法令番号: {value('AmendNo')}")
        if promulgation_date:
            summary_parts.append(f"公布日: {promulgation_date}")
        if enforcement_date:
            summary_parts.append(f"施行日: {enforcement_date}")
        entries.append(
            {
                "title": title,
                "link": law_url,
                "summary": "; ".join(summary_parts),
                "published_iso": promulgation_date,
                "identity_key": f"law:{law_url}:{stage_hint}",
                "stage_hint": stage_hint,
            }
        )
    return entries


def _egov_update_dates(lookback_days: int, today: str | None = None) -> list[str]:
    """Return newest-first yyyyMMdd dates, excluding the incomplete current day."""
    base = datetime.strptime(today or current_jst_date(), "%Y-%m-%d").date()
    return [(base - timedelta(days=offset)).strftime("%Y%m%d") for offset in range(1, lookback_days + 1)]


def fetch_source_entries(source: dict, timeout: int) -> tuple[list[dict], str]:
    """Fetch and parse a configured source, including allow-listed adapters."""
    fetcher = source.get("entry_fetcher")
    if fetcher == "egov_updated_laws":
        entries: list[dict] = []
        lookback_days = max(1, min(int(source.get("lookback_days", 7)), 31))
        today = current_jst_date()
        for compact_date in _egov_update_dates(lookback_days, today):
            request_url = urllib.parse.urljoin(source["url"], compact_date)
            try:
                content = http_get(
                    request_url,
                    int(source.get("timeout", timeout)),
                    user_agent=str(source.get("user_agent") or USER_AGENT),
                )
            except Exception as exc:
                if _http_status_from_exception(exc) == 404:
                    logger.info("No e-Gov law updates for %s (HTTP 404).", compact_date)
                    continue
                raise
            entries.extend(_parse_egov_updated_laws_xml(content, compact_date, today))
        return entries, source["url"]
    if fetcher == "sesc_current_year":
        index_content = http_get(
            source["url"],
            int(source.get("timeout", timeout)),
            accept_html=True,
            user_agent=str(source.get("user_agent") or USER_AGENT),
        )
        index_text = index_content.decode(str(source.get("encoding") or "utf-8"), errors="replace")
        year_url = _find_sesc_year_url(index_text, source["url"], current_jst_date()[:4])
        if not year_url:
            raise ValueError("SESC current-year archive link was not found")
        content = http_get(
            year_url,
            int(source.get("timeout", timeout)),
            accept_html=True,
            user_agent=str(source.get("user_agent") or USER_AGENT),
        )
        parse_source = dict(source)
        parse_source["url"] = year_url
        return parse_source_entries(content, parse_source), year_url
    if fetcher:
        raise ValueError(f"Unsupported entry_fetcher: {fetcher}")

    content = http_get(
        source["url"],
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
    return parse_source_entries(content, parse_source), effective_url


def parse_html_source(content: bytes, source: dict) -> list[dict]:
    parser_name = source.get("html_parser")
    text = content.decode(str(source.get("encoding") or "utf-8"), errors="replace")
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
    if parser_name == "shugiin_current_bills":
        return _parse_shugiin_current_bills_html(text, source["url"])
    if parser_name == "jpx_public_comments":
        return _parse_jpx_public_comments_html(text, source["url"])
    if parser_name == "jpx_rule_revisions":
        return _parse_jpx_rule_revisions_html(text, source["url"])
    if parser_name == "pmda_safety_updates":
        return _parse_pmda_safety_updates_html(
            text,
            source["url"],
            history_days=int(source.get("history_days", 550)),
            today=current_jst_date(),
        )
    if parser_name == "jsda_public_comments":
        return _parse_jsda_public_comments_html(text, source["url"])
    if parser_name == "jsda_public_comment_results":
        return _parse_jsda_public_comment_results_html(text, source["url"])
    if parser_name == "courts_recent_supreme":
        return _parse_courts_recent_supreme_html(text, source["url"])
    if parser_name == "sesc_current_year":
        return _parse_sesc_current_year_html(text, source["url"])
    raise ValueError(f"Unsupported html_parser: {parser_name}")


_HTML_TABLE_RE = re.compile(r"<table\b[^>]*>(?P<body>.*?)</table>", re.IGNORECASE | re.DOTALL)
_HTML_CAPTION_RE = re.compile(r"<caption\b[^>]*>(?P<body>.*?)</caption>", re.IGNORECASE | re.DOTALL)
_HTML_ROW_RE = re.compile(r"<tr\b[^>]*>(?P<body>.*?)</tr>", re.IGNORECASE | re.DOTALL)
_HTML_CELL_RE = re.compile(r"<td\b[^>]*>(?P<body>.*?)</td>", re.IGNORECASE | re.DOTALL)
_HTML_LINK_RE = re.compile(
    r"<a\b[^>]*href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<body>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_DIET_CATEGORIES = ("衆法", "参法", "閣法")
_DIET_TERMINAL_NOT_ENACTED = ("未了", "否決", "撤回", "審査未了", "廃案")


def _diet_stage_hint(status: str) -> str:
    if "成立" in status:
        return "Enacted"
    if any(marker in status for marker in _DIET_TERMINAL_NOT_ENACTED):
        return "Government Announcement"
    return "Bill Submitted"


def _parse_shugiin_current_bills_html(text: str, base_url: str) -> list[dict]:
    """Parse the House of Representatives current-session bill tables.

    The list contains member bills from both houses and Cabinet bills. Dates are
    intentionally left empty because the list page does not state a submission
    date per row. The official progress page is used as the source URL.
    """
    items: list[dict] = []
    for table in _HTML_TABLE_RE.finditer(text):
        caption_match = _HTML_CAPTION_RE.search(table.group("body"))
        caption = clean_text(caption_match.group("body")) if caption_match else ""
        category = next((value for value in _DIET_CATEGORIES if value in caption), "")
        if not category:
            continue

        for row in _HTML_ROW_RE.finditer(table.group("body")):
            cells = _HTML_CELL_RE.findall(row.group("body"))
            if len(cells) < 4:
                continue
            session = clean_text(cells[0])
            number = clean_text(cells[1])
            title = clean_text(cells[2])
            status = clean_text(cells[3])
            if not session.isdigit() or not title:
                continue

            links = list(_HTML_LINK_RE.finditer(row.group("body")))
            progress_link = next(
                (
                    urllib.parse.urljoin(base_url, html.unescape(link.group("href")).strip())
                    for link in links
                    if clean_text(link.group("body")) == "経過"
                ),
                "",
            )
            source_url = progress_link or next(
                (
                    urllib.parse.urljoin(base_url, html.unescape(link.group("href")).strip())
                    for link in links
                    if link.group("href").strip()
                ),
                base_url,
            )
            normalized_status = status or "提出"
            items.append(
                {
                    "title": title,
                    "link": source_url,
                    "summary": (
                        f"{category}; 審議状況: {normalized_status}; "
                        f"提出回次: {session}; 番号: {number or '不明'}"
                    ),
                    "published_iso": "",  # the list page has no per-item submission date
                    "identity_key": f"diet:{category}:{session}:{number}:{normalized_status}",
                    "stage_hint": _diet_stage_hint(normalized_status),
                }
            )
    return items


def _normalize_slash_date(value: str) -> str:
    try:
        return datetime.strptime(clean_text(value), "%Y/%m/%d").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _normalize_gregorian_japanese_date(value: str) -> str:
    match = re.fullmatch(r"\s*(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日\s*", clean_text(value))
    if not match:
        return ""
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).strftime("%Y-%m-%d")
    except ValueError:
        return ""


_GREGORIAN_JAPANESE_DATE_RE = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")


def _extract_gregorian_japanese_dates(value: str) -> list[str]:
    dates: list[str] = []
    for match in _GREGORIAN_JAPANESE_DATE_RE.finditer(clean_text(value)):
        try:
            dates.append(
                datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).strftime("%Y-%m-%d")
            )
        except ValueError:
            continue
    return dates


def _parse_jpx_public_comments_html(text: str, base_url: str) -> list[dict]:
    """Parse JPX's structured current-year public-comment table."""
    items: list[dict] = []
    for table in _HTML_TABLE_RE.finditer(text):
        body = table.group("body")
        if "募集開始日" not in body or "募集終了日" not in body or "案件名" not in body:
            continue
        for row in _HTML_ROW_RE.finditer(body):
            cells = _HTML_CELL_RE.findall(row.group("body"))
            if len(cells) < 4:
                continue
            start_date = _normalize_slash_date(cells[0])
            end_date = _normalize_slash_date(cells[1])
            entity = clean_text(cells[2])
            link = _HTML_LINK_RE.search(cells[3])
            title = clean_text(link.group("body")) if link else clean_text(cells[3])
            href = html.unescape(link.group("href")).strip() if link else ""
            if not title or not href:
                continue
            entry = {
                "title": title,
                "link": urllib.parse.urljoin(base_url, href),
                "summary": f"JPX public comment; 法人: {entity}; 募集期間: {start_date}～{end_date}",
                "published_iso": start_date,
            }
            if end_date:
                entry["comment_deadline"] = end_date
            items.append(entry)
    return items


def _parse_jpx_rule_revisions_html(text: str, base_url: str) -> list[dict]:
    """Parse TSE's current-year rule-revision comparison table."""
    items: list[dict] = []
    for table in _HTML_TABLE_RE.finditer(text):
        body = table.group("body")
        if "公表日" not in body or "新旧" not in body or "内容" not in body:
            continue
        for row in _HTML_ROW_RE.finditer(body):
            cells = _HTML_CELL_RE.findall(row.group("body"))
            if len(cells) < 2:
                continue
            published_iso = _normalize_slash_date(cells[0])
            title = clean_text(cells[1])
            links = list(_HTML_LINK_RE.finditer(row.group("body")))
            href = html.unescape(links[0].group("href")).strip() if links else ""
            if not published_iso or not title or not href:
                continue
            items.append(
                {
                    "title": title,
                    "link": urllib.parse.urljoin(base_url, href),
                    "summary": "東京証券取引所 規則改正新旧対照表",
                    "published_iso": published_iso,
                }
            )
    return items


_PMDA_NEWS_ITEM_RE = re.compile(
    r"<li\b[^>]*>\s*<a\b[^>]*href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<body>.*?)</a>\s*</li>",
    re.IGNORECASE | re.DOTALL,
)


def _pmda_field(body: str, class_name: str) -> str:
    match = re.search(
        rf"<p\b[^>]*class=[\"'][^\"']*\b{re.escape(class_name)}\b[^\"']*[\"'][^>]*>(?P<value>.*?)</p>",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    return clean_text(match.group("value")) if match else ""


def _parse_pmda_safety_updates_html(
    text: str,
    base_url: str,
    *,
    history_days: int = 550,
    today: str | None = None,
) -> list[dict]:
    """Parse PMDA safety-news rows, excluding non-safety review/event rows."""
    items: list[dict] = []
    seen_events: set[tuple[str, str, str]] = set()
    today_date = datetime.strptime(today or current_jst_date(), "%Y-%m-%d").date()
    cutoff = today_date - timedelta(days=max(1, min(history_days, 3660)))
    for match in _PMDA_NEWS_ITEM_RE.finditer(text):
        body = match.group("body")
        category = _pmda_field(body, "category")
        if category != "安全":
            continue
        published_iso = _normalize_gregorian_japanese_date(_pmda_field(body, "date"))
        title = _pmda_field(body, "title")
        href = html.unescape(match.group("href")).strip()
        link = urllib.parse.urljoin(base_url, href)
        identity = (published_iso, title, link)
        if not published_iso or not title or not href or identity in seen_events:
            continue
        if datetime.strptime(published_iso, "%Y-%m-%d").date() < cutoff:
            continue
        seen_events.add(identity)
        items.append(
            {
                "title": title,
                "link": link,
                "summary": "PMDA 安全対策業務 新着情報",
                "published_iso": published_iso,
                "identity_key": f"pmda:{published_iso}:{link}:{title}",
                "stage_hint": "Government Announcement",
            }
        )
    return items


def _parse_jsda_public_comments_html(text: str, base_url: str) -> list[dict]:
    """Parse JSDA's structured public-comment solicitation table."""
    items: list[dict] = []
    for table in _HTML_TABLE_RE.finditer(text):
        body = table.group("body")
        if "案　件　名" not in body and "案件名" not in body:
            continue
        if "募集期間" not in body:
            continue
        for row in _HTML_ROW_RE.finditer(body):
            cells = _HTML_CELL_RE.findall(row.group("body"))
            if len(cells) < 2:
                continue
            dates = _extract_gregorian_japanese_dates(cells[1])
            links = list(_HTML_LINK_RE.finditer(cells[0]))
            link = next((candidate for candidate in links if candidate.group("href").strip()), None)
            title = clean_text(link.group("body")) if link else ""
            href = html.unescape(link.group("href")).strip() if link else ""
            if len(dates) < 2 or not title or not href:
                continue
            items.append(
                {
                    "title": title,
                    "link": urllib.parse.urljoin(base_url, href),
                    "summary": f"JSDA public comment; 募集期間: {dates[0]}～{dates[1]}",
                    "published_iso": dates[0],
                    "comment_deadline": dates[1],
                }
            )
    return items


def _parse_jsda_public_comment_results_html(text: str, base_url: str) -> list[dict]:
    """Parse JSDA's structured public-comment result table."""
    items: list[dict] = []
    for table in _HTML_TABLE_RE.finditer(text):
        body = table.group("body")
        if "公表日" not in body or "案件名" not in body or "募集期間" not in body:
            continue
        for row in _HTML_ROW_RE.finditer(body):
            cells = _HTML_CELL_RE.findall(row.group("body"))
            if len(cells) < 3:
                continue
            published_dates = _extract_gregorian_japanese_dates(cells[0])
            period_dates = _extract_gregorian_japanese_dates(cells[2])
            links = list(_HTML_LINK_RE.finditer(cells[1]))
            link = next((candidate for candidate in links if candidate.group("href").strip()), None)
            title = clean_text(cells[1]).split("【資料】", 1)[0].strip()
            href = html.unescape(link.group("href")).strip() if link else ""
            if not published_dates or not title or not href:
                continue
            period = f"; 募集期間: {period_dates[0]}～{period_dates[1]}" if len(period_dates) >= 2 else ""
            items.append(
                {
                    "title": title,
                    "link": urllib.parse.urljoin(base_url, href),
                    "summary": f"JSDA public comment results{period}",
                    "published_iso": published_dates[0],
                    "stage_hint": "Public Comment Results Published",
                }
            )
    return items


_HTML_PARAGRAPH_RE = re.compile(r"<p\b[^>]*>(?P<body>.*?)</p>", re.IGNORECASE | re.DOTALL)


def _parse_courts_recent_supreme_html(text: str, base_url: str) -> list[dict]:
    """Parse the Courts site's official recent Supreme Court result table."""
    items: list[dict] = []
    seen_links: set[str] = set()
    for row in _HTML_ROW_RE.finditer(text):
        row_body = row.group("body")
        if "最高裁判例" not in row_body:
            continue
        detail_link = next(
            (
                link
                for link in _HTML_LINK_RE.finditer(row_body)
                if "detail2/" in html.unescape(link.group("href"))
            ),
            None,
        )
        cells = _HTML_CELL_RE.findall(row_body)
        if detail_link is None or not cells:
            continue
        paragraphs = [clean_text(match.group("body")) for match in _HTML_PARAGRAPH_RE.finditer(cells[0])]
        paragraphs = [value for value in paragraphs if value]
        if not paragraphs:
            continue
        title = paragraphs[0]
        metadata = paragraphs[1] if len(paragraphs) > 1 else ""
        published_iso = _normalize_japanese_date(metadata)
        href = html.unescape(detail_link.group("href")).strip()
        source_url = urllib.parse.urljoin(base_url, href)
        if not title or not published_iso or not href or source_url in seen_links:
            continue
        seen_links.add(source_url)
        items.append(
            {
                "title": title,
                "link": source_url,
                "summary": metadata,
                "published_iso": published_iso,
                "stage_hint": "Court Decision",
            }
        )
    return items


_SESC_YEAR_LINK_RE = re.compile(
    r"<a\b[^>]*href=[\"'](?P<href>[^\"']*/c_(?P<year>\d{4})/c_(?P=year)\.html)[\"'][^>]*>",
    re.IGNORECASE,
)
_HTML_LIST_ITEM_RE = re.compile(r"<li\b[^>]*>(?P<body>.*?)</li>", re.IGNORECASE | re.DOTALL)
_SESC_RELEVANT_MARKERS = (
    "勧告", "告発", "禁止及び停止命令", "課徴金", "行政処分",
    "金融商品取引法違反", "パブリックコメント", "証券モニタリングに関する基本指針",
    "証券モニタリング基本方針",
)
_SESC_EXCLUDE_MARKERS = ("事例集", "活動状況", "パンフレット")
_SESC_ENFORCEMENT_MARKERS = (
    "勧告", "告発", "禁止及び停止命令", "課徴金", "行政処分",
    "金融商品取引法違反",
)


def _find_sesc_year_url(text: str, base_url: str, year: str) -> str:
    """Resolve an exact current-year archive link from SESC's stable index."""
    for match in _SESC_YEAR_LINK_RE.finditer(text):
        if match.group("year") == year:
            return urllib.parse.urljoin(base_url, html.unescape(match.group("href")).strip())
    return ""


def _parse_sesc_current_year_html(text: str, base_url: str) -> list[dict]:
    """Parse focused SESC enforcement and market-monitoring developments."""
    items: list[dict] = []
    seen_links: set[str] = set()
    for item_match in _HTML_LIST_ITEM_RE.finditer(text):
        body = item_match.group("body")
        link = _HTML_LINK_RE.search(body)
        if link is None:
            continue
        title = clean_text(link.group("body"))
        if (
            not title
            or any(marker in title for marker in _SESC_EXCLUDE_MARKERS)
            or not any(marker in title for marker in _SESC_RELEVANT_MARKERS)
        ):
            continue
        published_iso = _normalize_japanese_date(clean_text(body))
        href = html.unescape(link.group("href")).strip()
        source_url = urllib.parse.urljoin(base_url, href)
        if not published_iso or not href or source_url in seen_links:
            continue
        seen_links.add(source_url)
        entry = {
            "title": title,
            "link": source_url,
            "summary": "SESC enforcement or securities market-monitoring policy update",
            "published_iso": published_iso,
        }
        if any(marker in title for marker in _SESC_ENFORCEMENT_MARKERS):
            entry["stage_hint"] = "Enforcement Action"
        items.append(entry)
    return items


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


def make_id(
    source_url: str,
    title_ja: str,
    source_name: str,
    published_at: str,
    identity_key: str = "",
) -> str:
    """Stable id. Allow an opt-in event identity, otherwise prefer source_url."""
    if identity_key:
        basis = "event:" + "|".join([source_name, identity_key])
    elif source_url:
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

    item = {
        "id": make_id(
            source_url,
            title_ja,
            source["name"],
            published_at,
            clean_text(entry.get("identity_key", "")),
        ),
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
    stage_hint = entry.get("stage_hint")
    if isinstance(stage_hint, str) and stage_hint in {
        "Bill Submitted", "Enacted", "Promulgated", "Scheduled to Take Effect", "In Force",
        "Government Announcement", "Public Comment Results Published", "Court Decision", "Enforcement Action",
    }:
        item["stage_hint"] = stage_hint
    comment_deadline = normalize_comment_deadline(entry.get("comment_deadline"))
    if not comment_deadline:
        comment_deadline = extract_egov_comment_deadline(
            entry.get("summary", ""),
            source.get("source_type", "rss"),
        )
    if comment_deadline:
        item["comment_deadline"] = comment_deadline
    return item


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
            entries, effective_url = fetch_source_entries(source, timeout)
            max_items = max(1, min(int(source.get("max_items", MAX_ITEMS_PER_SOURCE)), 500))
            entries = entries[:max_items]
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
                dedupe_by_url = bool(source.get("dedupe_by_url", True))
                if dedupe_by_url and item["source_url"] and item["source_url"] in seen_urls:
                    continue
                seen_ids.add(item["id"])
                if dedupe_by_url and item["source_url"]:
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
