#!/usr/bin/env python3
"""Fetch official sources for a dossier case file, with caching and error classes.

    python scripts/explainer_sources.py fetch <dossier_id> [--url URL --kind KIND] [--refresh]
    python scripts/explainer_sources.py status <dossier_id>

Each fetch records, in ``data/explainer_state/<dossier_id>.json``: the final
official URL, fetch time (UTC), SHA-256 of the exact bytes received, HTTP outcome,
a short plain-text excerpt, and, for an e-Gov Law API ``law_revisions`` URL, the
revision list (so an old and a new version of the same law are never confused).

* Only HTTPS official hosts are fetched, every redirect is re-checked, bodies are
  size-bounded (``official_source_evidence.fetch_bounded``).
* Transient failures (timeout, connection, 429, 5xx) are retried in-process with
  backoff, and across runs up to ``MAX_FETCH_ATTEMPTS`` in total; permanent ones
  (403/404, WAF page, JavaScript-only page, scanned PDF) are recorded once and NOT
  retried by later slots unless ``--force``: a writer then reads it in a browser,
  uses another official source, or holds the dossier and moves to another one.
* A source fetched recently is reused (``--max-age-days``), so a resumed slot does
  not download it again. A refetch whose hash differs records ``previous_sha256``
  and flags ``changed_since_previous`` so facts cited from it are re-checked.

This stores evidence of what an official page said when fetched. It never
interprets law, and an unreadable source is never filled in by inference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import explainer_state as es  # noqa: E402
import explainers as ex  # noqa: E402

MAX_HTML_BYTES = 2_000_000
MAX_PDF_BYTES = 6_000_000
RETRY_BACKOFF_SECONDS = (2, 6)
DEFAULT_MAX_AGE_DAYS = 7
JS_HINTS = ("javascript", "enable javascript", "javascriptを有効", "javascript を有効")


class FetchError(Exception):
    def __init__(self, error_class: str, detail: str, http_status: int | None = None):
        super().__init__(detail)
        self.error_class = error_class
        self.http_status = http_status


def classify_exception(exc: BaseException) -> FetchError:
    """Map a fetch exception to one of ``explainer_state.ERROR_CLASSES``."""
    if isinstance(exc, FetchError):
        return exc
    try:
        import requests
    except ImportError:  # pragma: no cover - requests is a pipeline dependency
        requests = None  # type: ignore[assignment]
    status = None
    if requests is not None and isinstance(exc, requests.HTTPError):
        status = getattr(exc.response, "status_code", None)
    if status is not None:
        if status == 429:
            return FetchError("http_429", "HTTP 429", status)
        if status >= 500:
            return FetchError("http_5xx", f"HTTP {status}", status)
        if status == 403:
            # Public government pages answer 403 almost only to automated clients (WAF).
            return FetchError("blocked_or_waf", "HTTP 403", status)
        return FetchError("http_4xx", f"HTTP {status}", status)
    if requests is not None and isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return FetchError("transient_network", type(exc).__name__)
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return FetchError("transient_network", type(exc).__name__)
    message = str(exc)
    if "non-official" in message:
        return FetchError("non_official_url", message)
    if "size limit" in message:
        return FetchError("too_large", message)
    if "redirect" in message:
        return FetchError("http_4xx", message)
    return FetchError("parse_error", type(exc).__name__)


def _default_fetch(url: str) -> tuple[bytes, str, str]:
    import official_source_evidence as ose

    limit = MAX_PDF_BYTES if ose.is_pdf_attachment_url(url) else MAX_HTML_BYTES
    return ose.fetch_bounded(url, limit)


def egov_revisions(data: bytes) -> dict[str, Any] | None:
    """Summarise an e-Gov Law API v2 ``law_revisions`` response (defensive; fields may be absent)."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    revisions = doc.get("revisions") if isinstance(doc, Mapping) else None
    if not isinstance(revisions, list):
        return None
    out = []
    for rev in revisions[:30]:
        if not isinstance(rev, Mapping):
            continue
        out.append({
            key: rev.get(key) for key in (
                "law_revision_id", "amendment_law_title", "amendment_law_num",
                "amendment_promulgate_date", "amendment_enforcement_date",
                "amendment_enforcement_comment", "current_revision_status",
            ) if rev.get(key) not in (None, "")
        })
    info = doc.get("law_info") if isinstance(doc.get("law_info"), Mapping) else {}
    return {"law_id": info.get("law_id"), "law_num": info.get("law_num"), "revisions": out}


def extract(data: bytes, content_type: str, url: str) -> tuple[str, dict[str, Any] | None]:
    """Plain-text excerpt and optional version info. Raises FetchError when nothing is readable."""
    import official_source_evidence as ose

    host = (urlsplit(url).hostname or "").lower()
    if host.startswith("laws.e-gov.go.jp") and "/api/" in urlsplit(url).path:
        version = egov_revisions(data)
        if version is None:
            raise FetchError("parse_error", "e-Gov API response not understood")
        lines = [
            " / ".join(str(r.get(k, "")) for k in ("amendment_law_title", "amendment_promulgate_date",
                                                     "amendment_enforcement_date", "amendment_enforcement_comment")
                       if r.get(k))
            for r in version["revisions"][:8]
        ]
        return ose.normalize_text(" | ".join(lines))[: es.MAX_EXCERPT], version
    if data.startswith(b"%PDF"):
        text = ose.pdf_text(data)
        if len(text) < ose.MIN_DOCUMENT_CHARS:
            raise FetchError("empty_or_scanned", "no extractable PDF text")
        return text[: es.MAX_EXCERPT], None
    if "html" in content_type or data.lstrip()[:15].lower().startswith((b"<!doctype", b"<html", b"<?xml")):
        text, _ = ose.html_documents(data, url)
        if len(text) < ose.MIN_DOCUMENT_CHARS:
            lowered = data[:200_000].decode("utf-8", errors="ignore").lower()
            if any(h in lowered for h in JS_HINTS):
                raise FetchError("js_required", "page renders its content with JavaScript")
            raise FetchError("empty_or_scanned", "no substantive page text")
        return text[: es.MAX_EXCERPT], None
    raise FetchError("parse_error", f"unsupported content type {content_type or 'unknown'}")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(ts: Any) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def should_fetch(source: Mapping[str, Any], *, refresh: bool, force: bool, max_age_days: int,
                 now: datetime) -> tuple[bool, str]:
    status = source.get("status")
    if status == "fetched":
        fetched = _parse(source.get("fetched_at"))
        if refresh or fetched is None or now - fetched > timedelta(days=max_age_days):
            return True, "refresh"
        return False, "reused (fetched recently)"
    if status == "failed":
        if force:
            return True, "forced retry"
        if es.retry_due(source):
            return True, "transient error retry"
        if source.get("error_class") in es.TRANSIENT_ERRORS:
            return False, "retry budget exhausted"
        return False, f"permanent error ({source.get('error_class')}); read it another way or use another source"
    return True, "first fetch"


def fetch_source(
    source: dict[str, Any],
    *,
    fetcher: Callable[[str], tuple[bytes, str, str]] = _default_fetch,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Fetch one source in place (bounded in-process retries for transient errors)."""
    url = source["url"]
    if not es.official_url(url):
        error = FetchError("non_official_url", "not an official https host")
        attempts_now = 0
    else:
        error = None
        attempts_now = 0
        budget = max(1, es.MAX_FETCH_ATTEMPTS - int(source.get("attempts") or 0)) if source.get("status") == "failed" \
            else es.MAX_FETCH_ATTEMPTS
        for attempt in range(budget):
            attempts_now += 1
            try:
                data, content_type, final_url = fetcher(url)
                if not es.official_url(final_url):
                    raise FetchError("non_official_url", "redirected to a non-official host")
                excerpt, version = extract(data, content_type, final_url)
            except BaseException as exc:  # noqa: BLE001 - classified and recorded, never re-raised
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                error = classify_exception(exc)
                if error.error_class in es.TRANSIENT_ERRORS and attempt + 1 < budget:
                    sleep(RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
                    continue
                break
            digest = hashlib.sha256(data).hexdigest()
            previous = source.get("sha256") if source.get("status") == "fetched" else None
            source.update({
                "status": "fetched", "fetched_at": _now(), "sha256": digest, "final_url": final_url,
                "http_status": 200, "error_class": None, "excerpt": excerpt,
                "attempts": int(source.get("attempts") or 0) + attempts_now,
            })
            source.pop("last_attempt_at", None)
            source.pop("error_detail", None)
            if version is not None:
                version["as_of"] = datetime.now(timezone.utc).date().isoformat()
                source["version"] = version
            if previous and previous != digest:
                source["previous_sha256"] = previous
                source["changed_since_previous"] = True
            elif previous:
                source["changed_since_previous"] = False
            return source
    assert error is not None
    source.update({
        "status": "failed", "error_class": error.error_class, "error_detail": str(error)[:200],
        "http_status": error.http_status, "last_attempt_at": _now(),
        "attempts": int(source.get("attempts") or 0) + attempts_now,
    })
    return source


def facts_citing(state: Mapping[str, Any], source_id: str) -> list[str]:
    return [f.get("id") for f in state.get("facts") or []
            if isinstance(f, Mapping) and source_id in (f.get("source_ids") or [])]


def run_fetch(state: dict[str, Any], *, url: str | None = None, kind: str | None = None,
              refresh: bool = False, force: bool = False, max_age_days: int = DEFAULT_MAX_AGE_DAYS,
              fetcher: Callable[[str], tuple[bytes, str, str]] = _default_fetch,
              sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print) -> dict[str, int]:
    """Fetch every due source (or one URL, added if new). Returns counters."""
    sources: list[dict[str, Any]] = state.setdefault("sources", [])
    if url is not None:
        if not es.official_url(url):
            raise SystemExit("only https URLs on official Japanese public-sector hosts may be recorded")
        if not any(s.get("url") == url for s in sources):
            if len(sources) >= es.MAX_SOURCES:
                raise SystemExit(f"case file already lists {es.MAX_SOURCES} sources")
            used = {s.get("id") for s in sources}
            n = 1
            while f"s{n}" in used:
                n += 1
            sources.append({"id": f"s{n}", "url": url, "kind": kind or "other_official",
                            "status": "not_fetched", "attempts": 0})
    counters = {"fetched": 0, "reused": 0, "failed": 0, "skipped": 0, "changed": 0}
    now = datetime.now(timezone.utc)
    for source in sources:
        if url is not None and source.get("url") != url:
            continue
        due, why = should_fetch(source, refresh=refresh, force=force, max_age_days=max_age_days, now=now)
        if not due:
            counters["reused" if why.startswith("reused") else "skipped"] += 1
            log(f"{source['id']} SKIP {why}: {source['url']}")
            continue
        fetch_source(source, fetcher=fetcher, sleep=sleep)
        if source["status"] == "fetched":
            counters["fetched"] += 1
            if source.get("changed_since_previous"):
                counters["changed"] += 1
                log(f"{source['id']} CHANGED since last fetch; re-check facts {facts_citing(state, source['id'])}")
            log(f"{source['id']} OK sha256={source['sha256'][:12]} {source['url']}")
        else:
            counters["failed"] += 1
            hint = ("will retry in a later run" if es.retry_due(source)
                    else "do not retry automatically: open it in a browser, use another official source, "
                         "or hold the dossier and switch candidate")
            log(f"{source['id']} FAILED {source['error_class']} (attempts={source['attempts']}): {hint}")
    return counters


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=es.STATE_DIR)
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("dossier_id")
    f.add_argument("--url", default=None, help="fetch (and record if new) one official URL")
    f.add_argument("--kind", choices=es.SOURCE_KINDS, default=None)
    f.add_argument("--refresh", action="store_true", help="refetch even recently fetched sources")
    f.add_argument("--force", action="store_true", help="retry a permanently failed source")
    f.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    s = sub.add_parser("status")
    s.add_argument("dossier_id")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    path = es.state_path(args.dossier_id, args.dir)
    if not path.is_file():
        raise SystemExit(f"{path} does not exist; create it with: python scripts/explainer_state.py init ...")
    state = ex.load_json(path)
    if args.command == "status":
        for src in state.get("sources") or []:
            print(f"{src.get('id')} {src.get('status')} {src.get('error_class') or ''} "
                  f"attempts={src.get('attempts', 0)} {src.get('fetched_at') or src.get('last_attempt_at') or ''} {src.get('url')}")
        print(json.dumps(es.source_summary(state), ensure_ascii=False))
        return 0
    counters = run_fetch(state, url=args.url, kind=args.kind, refresh=args.refresh, force=args.force,
                         max_age_days=args.max_age_days)
    errors, _ = es.validate_state(args.dossier_id, state)
    if errors:
        raise SystemExit("refusing to write an invalid case file:\n  " + "\n  ".join(errors))
    ex.write_if_changed(path, es.dump(state))
    print("fetch " + json.dumps(counters))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
