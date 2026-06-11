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
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

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
        "url": "https://public-comment.e-gov.go.jp/rss/pcm_list.xml",
        "source_type": "public_comment_rss",
        "source_language": "ja",
    },
    {
        "name": "Financial Services Agency (金融庁) 新着情報",
        "url": "https://www.fsa.go.jp/fsaNewsListAll_rss2.xml",
        "source_type": "regulator_rss",
        "source_language": "ja",
    },
    {
        "name": "経済産業省 (METI) ニュースリリース",
        "url": "https://www.meti.go.jp/ml_index_release_atom.xml",
        "source_type": "ministry_rss",
        "source_language": "ja",
    },
    {
        "name": "Ministry of Health, Labour and Welfare (厚生労働省) 新着情報",
        "url": "https://www.mhlw.go.jp/stf/news.rdf",
        "source_type": "ministry_rss",
        "source_language": "ja",
    },
    {
        "name": "Digital Agency (デジタル庁) 新着・更新",
        "url": "https://www.digital.go.jp/rss/news.xml",
        "source_type": "agency_rss",
        "source_language": "ja",
    },
    {
        "name": "消費者庁 (CAA) 新着情報",
        "url": "https://www.caa.go.jp/news.rss",
        "source_type": "agency_rss",
        "source_language": "ja",
    },
]

USER_AGENT = (
    "JapanLegalReformWatch/0.1 (+https://github.com/legalos/japan-legal-reform-watch; "
    "non-commercial ingestion bot; respects robots/ToS)"
)
DEFAULT_TIMEOUT_SECONDS = 20
MAX_ITEMS_PER_SOURCE = 100  # safety cap so one large feed cannot dominate a run

# Paths are resolved relative to the repository root (this file lives in scripts/).
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
RAW_ITEMS_PATH = DATA_DIR / "raw_items.json"
LOG_DIR = REPO_ROOT / "logs"
LOG_PATH = LOG_DIR / "fetch.log"

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

def http_get(url: str, timeout: int) -> bytes:
    """Fetch raw bytes. Prefer `requests`; fall back to urllib. Sets UA + timeout."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/rdf+xml, application/atom+xml, application/xml, text/xml, */*",
    }
    if requests is not None:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (trusted scheme below)
        status = getattr(r, "status", 200)
        if status and status >= 400:
            raise urllib.error.HTTPError(url, status, f"HTTP {status}", r.headers, None)
        return r.read()


def parse_feed(content: bytes) -> list[dict]:
    """Return a list of {title, link, summary, published_iso} dicts."""
    if feedparser is not None:
        return _parse_with_feedparser(content)
    return _parse_with_stdlib(content)


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

    text = content.decode("utf-8", errors="replace").lstrip("﻿")
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


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def run(timeout: int, dry_run: bool) -> int:
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    logger.info("=== fetch_updates run start (fetched_at=%s, dry_run=%s) ===", fetched_at, dry_run)

    existing = load_existing(RAW_ITEMS_PATH)
    seen_ids = {it["id"] for it in existing if it.get("id")}
    seen_urls = {it["source_url"] for it in existing if it.get("source_url")}

    checked_sources = 0
    fetched_items = 0
    failed_sources: list[dict] = []
    new_items: list[dict] = []

    for source in SOURCES:
        checked_sources += 1
        name, url = source["name"], source["url"]
        try:
            content = http_get(url, timeout)
            entries = parse_feed(content)[:MAX_ITEMS_PER_SOURCE]
            fetched_items += len(entries)
            logger.info("OK   %s — %d entries from %s", name, len(entries), url)
            if not entries:
                logger.warning("No entries parsed from %s (%s).", name, url)

            for entry in entries:
                item = build_item(entry, source, fetched_at)
                if item is None:
                    continue
                if item["id"] in seen_ids:
                    continue
                if item["source_url"] and item["source_url"] in seen_urls:
                    continue
                seen_ids.add(item["id"])
                if item["source_url"]:
                    seen_urls.add(item["source_url"])
                new_items.append(item)
        except Exception as exc:  # noqa: BLE001 — one bad source must not stop the run
            reason = f"{type(exc).__name__}: {exc}"
            failed_sources.append({"name": name, "url": url, "reason": reason})
            logger.error("FAIL %s (%s): %s", name, url, reason)

    combined = existing + new_items
    if dry_run:
        logger.info("DRY-RUN: not writing %s (%d new, %d total).", RAW_ITEMS_PATH.name, len(new_items), len(combined))
    else:
        save_json(RAW_ITEMS_PATH, combined)
        logger.info("Wrote %s (%d new, %d total).", RAW_ITEMS_PATH.name, len(new_items), len(combined))

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
