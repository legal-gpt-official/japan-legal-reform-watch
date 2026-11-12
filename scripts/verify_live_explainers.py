"""Read-only verification of live multilingual reform-explainer pages.

A GitHub Pages deployment success is necessary but does not prove that a
custom-domain HTTP response serves the newest EN/JA/zh text. Run this on a
networked GitHub-hosted runner rather than on a ChatGPT sandbox without DNS.
This check never publishes, edits records, or bypasses a legal/translation gate.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
EN_DIR = ROOT / "data" / "explainers"
LOCALE_DIR = ROOT / "data" / "explainer_locales"
DOCS_DIR = ROOT / "docs" / "u"
HOST = "https://updates.legal-gpt.com"
HEADER = {"User-Agent": "JLRW-live-explainer-check/1.0 (+https://updates.legal-gpt.com/)"}


def recent_dossiers(days: int, requested: set[str] | None = None) -> list[dict]:
    cutoff = (datetime.now(ZoneInfo("Asia/Tokyo")).date() - timedelta(days=days)).isoformat()
    out = []
    for path in sorted(EN_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") != "published":
            continue
        if requested is not None:
            if data.get("dossier_id") not in requested:
                continue
        elif str(data.get("verified_at") or "") < cutoff:
            continue
        out.append(data)
    if requested is not None and len(out) != len(requested):
        missing = sorted(requested - {d["dossier_id"] for d in out})
        raise ValueError("Requested dossiers not found or not published: " + ", ".join(missing))
    return out


def content_fragments(en: dict, tr: dict | None) -> tuple[str, ...]:
    """Return each canonical substantive statement to match in generated and live HTML."""
    is_en = tr is None
    data = en if is_en else tr
    title = data.get("title_en" if is_en else "title")
    if not isinstance(title, str) or not title:
        raise ValueError("Missing localized title")
    verified = en.get("verified_at")
    if not isinstance(verified, str) or not verified:
        raise ValueError("Missing verified_at")
    parts = ['<h2 id="explainer-title">' + html.escape(title, quote=True) + "</h2>", verified]
    quick = data.get("quick_take") or {}
    for key in ("what_changed", "who_is_affected", "what_to_do_now"):
        statement = quick.get(key)
        if not isinstance(statement, str) or not statement:
            raise ValueError("Missing quick_take." + key)
        parts.append(html.escape(statement, quote=True))

    def push(values, label, key=None):
        if not isinstance(values, list):
            raise ValueError("Missing list: " + label)
        for obj in values:
            value = obj.get(key) if key and isinstance(obj, dict) else obj
            if not isinstance(value, str) or not value:
                raise ValueError("Invalid value in " + label)
            parts.append(html.escape(value, quote=True))

    for section in ("background", "history"):
        values = data.get(section)
        if is_en:
            values = values.get("paragraphs") if isinstance(values, dict) else None
        push(values, section)
    steps = data.get("next_steps") or {}
    push(steps.get("confirmed"), "confirmed", "text" if is_en else None)
    for section in ("expected", "uncertainties"):
        push(steps.get(section), section)
    impact = data.get("business_impact")
    if is_en:
        if not isinstance(impact, dict):
            raise ValueError("Missing English business_impact")
        push(impact.get("paragraphs"), "business_impact.paragraphs")
        push(impact.get("checklist"), "business_impact.checklist")
    else:
        push(impact, "business_impact")
        push(data.get("checklist"), "checklist")
    push(data.get("key_dates"), "key_dates", "label" if is_en else None)
    return tuple(parts)


def page_checks(dossiers: list[dict]) -> tuple[list[tuple[str, tuple[str, ...]]], list[str]]:
    """Return verified targets from GitHub-generated HTML and integrity errors."""
    targets = []
    failures = []
    for en in dossiers:
        dossier = en["dossier_id"]
        bundle = LOCALE_DIR / f"{dossier}.json"
        if not bundle.is_file():
            failures.append(f"{dossier}: missing locale bundle")
            continue
        translations = json.loads(bundle.read_text(encoding="utf-8")).get("translations", {})
        try:
            by_lang = {
                "en": content_fragments(en, None),
                "ja": content_fragments(en, translations.get("ja")),
                "zh": content_fragments(en, translations.get("zh-Hans")),
            }
        except (AttributeError, ValueError) as exc:
            failures.append(f"{dossier}: invalid source content: {exc}")
            continue
        item_ids = en.get("related_item_ids") or []
        if not item_ids:
            failures.append(f"{dossier}: no related_item_ids")
            continue
        for raw_id in item_ids:
            if not re.fullmatch(r"raw-[a-f0-9]{16}", raw_id):
                failures.append(f"{dossier}: invalid raw ID {raw_id!r}")
                continue
            for lang, suffix in (("en", ""), ("ja", "/ja"), ("zh", "/zh")):
                expected = by_lang[lang]
                rel = f"u/{raw_id}{suffix}/"
                local = DOCS_DIR / raw_id / ("" if lang == "en" else lang) / "index.html"
                if not local.is_file():
                    failures.append(f"{dossier}: missing title or generated page {local}")
                    continue
                source = local.read_text(encoding="utf-8")
                if not all(part in source for part in expected):
                    failures.append(f"{dossier}: generated HTML has stale/missing full explainer prose {rel}")
                    continue
                targets.append((HOST + "/" + rel, expected))
    return targets, failures


def fetch_and_match(target: tuple[str, tuple[str, ...] | str]) -> tuple[str, str | None]:
    url, marker = target
    req = urllib.request.Request(url, headers=HEADER)
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            status = response.status
            body = response.read(1_500_000).decode("utf-8", errors="replace")
            if status != 200:
                return url, f"HTTP {status}"
            if not all(part in body for part in (marker.split("\n") if isinstance(marker, str) else marker)):
                return url, "HTTP 200 but full canonical explainer prose is missing or stale"
            return url, None
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        return url, f"{type(exc).__name__}: {exc}"


def verify(targets: list[tuple[str, tuple[str, ...] | str]], attempts: int, delay: int) -> dict[str, str]:
    pending = {url: marker for url, marker in targets}
    errors: dict[str, str] = {}
    for attempt in range(1, attempts + 1):
        if not pending:
            break
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(fetch_and_match, list(pending.items())))
        for url, error in results:
            if error is None:
                pending.pop(url, None)
                errors.pop(url, None)
                print(f"LIVE_OK {url}", flush=True)
            else:
                errors[url] = error
        if pending and attempt < attempts:
            print(f"Attempt {attempt}/{attempts}: {len(pending)} pages pending; retrying", flush=True)
            time.sleep(delay)
    return errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=2, help="JST verified_at age (default: 2 days)")
    p.add_argument("--dossiers", default="", help="optional comma-separated dossier IDs")
    p.add_argument("--attempts", type=int, default=6)
    p.add_argument("--delay", type=int, default=15)
    args = p.parse_args(argv)
    if args.days < 0 or not 1 <= args.attempts <= 15 or not 0 <= args.delay <= 120:
        p.error("invalid days, attempts or delay")
    requested = {s.strip() for s in args.dossiers.split(",") if s.strip()} or None
    dossiers = recent_dossiers(args.days, requested=requested)
    targets, failures = page_checks(dossiers)
    print(f"JST recent published dossiers: {len(dossiers)}; generated language URLs: {len(targets)}")
    errors = verify(targets, args.attempts, args.delay)
    for url, message in sorted(errors.items()):
        print(f"LIVE_FAILED {url}: {message}", file=sys.stderr)
    for message in failures:
        print(f"INTEGRITY_FAILED {message}", file=sys.stderr)
    summary = {
        "dossiers_checked": len(dossiers), "live_urls_checked": len(targets),
        "live_urls_verified": len(targets) - len(errors),
        "failed_http": len(errors), "failed_local": len(failures),
    }
    print("LIVE_CHECK_SUMMARY " + json.dumps(summary))
    if "GITHUB_STEP_SUMMARY" in __import__("os").environ:
        with open(__import__("os").environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
            out.write("### Live explainer verification\n")
            out.write(f"Verified {summary['live_urls_verified']}/{len(targets)} EN/JA/ZH URLs; ")
            out.write(f"{len(dossiers)} dossiers, {len(failures)} local integrity issues.\n")
            for url, message in sorted(errors.items()):
                out.write(f"- FAILED: {url} — {message}\n")
    return 0 if dossiers and targets and not errors and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
