#!/usr/bin/env python3
"""Bounded, opt-in extraction of official Japanese source text for AI summaries.

This is NOT a legal verification engine. It preserves traceable excerpts from
official HTTPS pages and selected PDF attachments; missing/undecodable sources
are not filled in by inference. Feed URLs are untrusted even when read from
the repository: network requests are restricted to public-sector domains,
redirects are checked at each hop, and response sizes and pages are capped.

The evidence cache is separate from immutable raw ingestion records. A content
hash change triggers regeneration only for successfully enriched items.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = ROOT / "data" / "raw_items.json"
PUBLIC_PATH = ROOT / "docs" / "data" / "legal_updates.json"
CACHE_PATH = ROOT / "data" / "official_source_evidence.json"

MAX_HTML_BYTES = 1_500_000
MAX_PDF_BYTES = 4_000_000
MAX_TOTAL_CHARS = 4500
MAX_HTML_CHARS = 2300
MAX_PDF_CHARS = 2400
MAX_PDF_PAGES = 8
MAX_PDF_LINKS = 2
MIN_DOCUMENT_CHARS = 180
DEFAULT_LIMIT = 12
NEGATIVE_RETRY_HOURS = 72  # blocked/empty pages must not starve the queue

# No HTTP or arbitrary internet hosts, even if introduced by a compromised feed.
OFFICIAL_SUFFIXES = (
    "go.jp", "lg.jp", "courts.go.jp", "shugiin.go.jp", "sangiin.go.jp",
    "jpx.co.jp", "jsda.or.jp",
)
PDF_LABEL = re.compile(
    r"概要|新旧対照|案文|命令書|別紙|本文|改正内容|政令案|省令案|資料|要綱|報道発表|PDF",
    re.IGNORECASE,
)
SQUEEZE = re.compile(r"\s+")


def safe_official_url(value: str) -> bool:
    """Permit only HTTPS URLs on explicitly public-sector domain families."""
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        u = urlsplit(value)
        host = (u.hostname or "").lower().rstrip(".")
        return bool(
            u.scheme == "https"
            and u.port in (None, 443)
            and not u.username and not u.password
            and host
            and all(ch not in host for ch in ("%", "\\", " "))
            and any(host == suffix or host.endswith("." + suffix) for suffix in OFFICIAL_SUFFIXES)
        )
    except ValueError:
        return False


def fetch_bounded(url: str, limit: int) -> tuple[bytes, str, str]:
    """Read a response, validating each redirect and never buffering unbounded data."""
    if not safe_official_url(url):
        raise ValueError("non-official source URL")
    current = url
    for _ in range(4):
        if not safe_official_url(current):
            raise ValueError("redirect to non-official URL")
        with requests.get(
            current,
            timeout=(5, 12),
            allow_redirects=False,
            stream=True,
            headers={"User-Agent": "JapanLegalReformWatch/1.0 (official-source excerpts)"},
        ) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location", "")
                if not location:
                    raise ValueError("redirect without location")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            if int(response.headers.get("Content-Length", "0") or "0") > limit:
                raise ValueError("source document over size limit")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=32_768):
                body.extend(chunk)
                if len(body) > limit:
                    raise ValueError("source document over size limit")
            return bytes(body), response.headers.get("Content-Type", "").lower(), current
    raise ValueError("too many redirects")


def normalize_text(value: str) -> str:
    return SQUEEZE.sub(" ", value or "").strip()


def html_documents(data: bytes, url: str) -> tuple[str, list[str]]:
    """Prefer article/main text to navigation; choose at most two labeled PDFs."""
    soup = BeautifulSoup(data, "html.parser")
    for node in soup.select("script, style, nav, footer, header, aside, form, noscript, iframe"):
        node.decompose()
    root = (
        soup.select_one("main, [role='main'], article, #main, #contents, #content, #mainContents, .main-content")
        or soup.body
    )
    if root is None:
        return "", []

    lines: list[str] = []
    last = ""
    for tag in root.select("h1, h2, h3, h4, p, li, dt, dd, th, td"):
        # Skip text already represented by a child list/table element.
        if tag.name in ("li", "p") and tag.find(("li", "p")):
            continue
        value = normalize_text(tag.get_text(" ", strip=True))
        if 10 <= len(value) <= 1800 and value != last:
            lines.append(value)
            last = value
    text = normalize_text(" ".join(lines))
    pdfs: list[tuple[int, str]] = []
    for link in root.select("a[href]"):
        href = str(link.get("href") or "")
        full = urljoin(url, href)
        if not safe_official_url(full):
            continue
        if not re.search(r"\.pdf(?:$|[?#])", urlsplit(full).path + "?" + urlsplit(full).query, re.I):
            continue
        label = normalize_text(link.get_text(" ", strip=True))
        if not PDF_LABEL.search(label):
            continue
        score = 4 if re.search(r"新旧対照|概要|改正内容|案文|命令書", label) else 1
        pdfs.append((score, full))
    selected: list[str] = []
    for _, link in sorted(pdfs, reverse=True):
        if link not in selected:
            selected.append(link)
        if len(selected) >= MAX_PDF_LINKS:
            break
    return text[:MAX_HTML_CHARS], selected


def pdf_text(data: bytes) -> str:
    if not data.startswith(b"%PDF"):
        return ""
    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        return ""
    pieces = []
    for index in range(min(len(reader.pages), MAX_PDF_PAGES)):
        pieces.append(reader.pages[index].extract_text() or "")
    return normalize_text(" ".join(pieces))[:MAX_PDF_CHARS]


def related_to_title(title: str, documents: list[dict]) -> bool:
    """Reject apparent indexes/menus whose excerpt cannot be tied to this item.

    This is only a conservative relevance filter, NOT legal fact verification.
    Check normalized contiguous n-grams instead of fuzzy semantic guesses.
    """
    title = re.sub(r"[^\w]+", "", unicodedata.normalize("NFKC", title or "")).lower()
    combined = re.sub(r"[^\w]+", "", unicodedata.normalize(
        "NFKC", " ".join(d.get("excerpt", "") for d in documents))).lower()
    if not title or not combined:
        return False
    if len(title) < 9:
        return title in combined
    width = 7 if len(title) >= 18 else 5
    ngrams = {title[i:i + width] for i in range(len(title) - width + 1)}
    return sum(gram in combined for gram in ngrams) >= min(3, len(ngrams))


def gather(url: str) -> list[dict]:
    """Return only bounded, substantive extracted excerpts with their exact URLs."""
    data, mime, final_url = fetch_bounded(url, MAX_HTML_BYTES if not urlsplit(url).path.lower().endswith(".pdf") else MAX_PDF_BYTES)
    documents: list[dict] = []
    pdf_urls: list[str] = []
    if data.startswith(b"%PDF"):
        excerpt = pdf_text(data)
        if len(excerpt) >= MIN_DOCUMENT_CHARS:
            documents.append({"url": final_url, "format": "pdf", "excerpt": excerpt})
    elif "html" in mime or data.lstrip().startswith((b"<!DOCTYPE", b"<html", b"<?xml")):
        excerpt, pdf_urls = html_documents(data, final_url)
        if len(excerpt) >= MIN_DOCUMENT_CHARS:
            documents.append({"url": final_url, "format": "html", "excerpt": excerpt})
    for pdf_url in pdf_urls:
        if sum(len(d["excerpt"]) for d in documents) >= MAX_TOTAL_CHARS:
            break
        try:
            pdf, _, pdf_final_url = fetch_bounded(pdf_url, MAX_PDF_BYTES)
            excerpt = pdf_text(pdf)
            if len(excerpt) >= MIN_DOCUMENT_CHARS:
                documents.append({"url": pdf_final_url, "format": "pdf", "excerpt": excerpt})
        except (requests.RequestException, ValueError, OSError, Exception) as exc:
            # Malformed or scanned attachments are optional; never infer content.
            print(f"attachment skipped ({type(exc).__name__})")
    remaining = MAX_TOTAL_CHARS
    result = []
    for entry in documents:
        excerpt = entry["excerpt"][:remaining]
        if len(excerpt) >= MIN_DOCUMENT_CHARS:
            result.append({**entry, "excerpt": excerpt})
            remaining -= len(excerpt)
    return result


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def eligible_cached(raw: dict, record: dict | None) -> bool:
    return bool(
        isinstance(record, dict)
        and record.get("source_url") == raw.get("source_url")
        and record.get("raw_hash") == raw.get("raw_content_hash")
        and isinstance(record.get("documents"), list)
        and record["documents"]
    )


def recently_failed(raw: dict, record: dict | None) -> bool:
    """Do not spend every day's quota retrying the same blocked official pages."""
    if not isinstance(record, dict) or not record.get("status") in ("no_evidence", "error"):
        return False
    if record.get("source_url") != raw.get("source_url") or record.get("raw_hash") != raw.get("raw_content_hash"):
        return False
    try:
        previous = datetime.fromisoformat(record["checked_at"])
        return previous.tzinfo is not None and datetime.now(timezone.utc) - previous < timedelta(hours=NEGATIVE_RETRY_HOURS)
    except (KeyError, ValueError, TypeError):
        return False


def collect(limit: int, item_ids: set[str] | None = None, dry_run: bool = False) -> dict:
    """Enrich bounded public items. Existing successful evidence is reused."""
    raw = {r["id"]: r for r in read_json(RAW_PATH, []) if isinstance(r, dict) and r.get("id")}
    items = read_json(PUBLIC_PATH, [])
    cache = read_json(CACHE_PATH, {})
    if not isinstance(cache, dict):
        raise ValueError("evidence cache is not a JSON object")
    # Recent first. Item ids explicitly supplied for controlled backfills win.
    selected = [i for i in items if isinstance(i, dict) and i.get("id") in raw]
    selected.sort(key=lambda i: (i.get("published_at") or i.get("first_seen_at") or "", i.get("relevance_score") or 0), reverse=True)
    if item_ids:
        selected = [i for i in selected if i.get("id") in item_ids]
    counts = {"attempted": 0, "enriched": 0, "cached": 0, "negatively_cached": 0,
              "no_evidence": 0, "errors": 0}
    changed = False
    for item in selected:
        record = raw[item["id"]]
        url = record.get("source_url", "")
        # An unchanged full-law page or a Parliament-wide bill index is not
        # evidence of what changed in this particular event.
        if record.get("source_type") == "law_api" or urlsplit(url).path.endswith("/menu.htm"):
            continue
        if not safe_official_url(url):
            continue
        if eligible_cached(record, cache.get(item["id"])):
            counts["cached"] += 1
            continue
        if recently_failed(record, cache.get(item["id"])):
            counts["negatively_cached"] += 1
            continue
        if counts["attempted"] >= limit:
            break
        counts["attempted"] += 1
        try:
            docs = gather(url)
            if not docs or not related_to_title(item.get("title_ja", ""), docs):
                counts["no_evidence"] += 1
                if not dry_run:
                    cache[item["id"]] = {
                        "source_url": url, "raw_hash": record.get("raw_content_hash", ""),
                        "documents": [], "status": "no_evidence",
                        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    }
                    changed = True
                continue
            counts["enriched"] += 1
            if not dry_run:
                cache[item["id"]] = {
                    "source_url": url,
                    "raw_hash": record.get("raw_content_hash", ""),
                    "documents": docs,
                    "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                changed = True
        except (requests.RequestException, ValueError, OSError) as exc:
            counts["errors"] += 1
            print(f"source {item['id']} skipped ({type(exc).__name__})")
            if not dry_run:
                cache[item["id"]] = {
                    "source_url": url, "raw_hash": record.get("raw_content_hash", ""),
                    "documents": [], "status": "error",
                    "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                changed = True
    if not dry_run and changed:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = CACHE_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(CACHE_PATH)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--id", action="append", default=[], help="Restrict to an item id; repeatable")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not 0 <= args.limit <= 100:
        parser.error("--limit must be between 0 and 100")
    print(json.dumps(collect(args.limit, set(args.id) or None, args.dry_run), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
