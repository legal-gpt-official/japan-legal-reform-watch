#!/usr/bin/env python3
"""
quality_audit.py — Japan Legal Reform Watch by LegalOS

Deterministic, offline quality audit of the published corpus, run by the daily
workflow after Stage 4 and before the commit. No API calls, no writes to the
published data or caches.

It re-checks, on EVERY published item, the defect classes the 2026-10-16
multilingual audit found and fixed, so a recurrence is caught the day it is
introduced instead of in the next manual audit:

HIGH (expected 0; each is reported by item id with a ::warning annotation and a
ready-to-paste data/requeue.json section):
- en_japanese            Japanese characters in a displayed English field
- en_title_truncated     English title cut with an ellipsis
- en_main_law_number     the item's own (amending) law number dropped
- en_law_number_year     "No. N of YYYY" whose year contradicts the source
- en_case_code           a court case code romanized differently from the rule
- en_field_names         internal input field names shown to readers
- en_wrong_bureau        a known wrong or former bureau name
- zh_missing             an AI-summarized item with no Chinese (English fallback)
- zh_kana_or_ja_forms    kana or Japanese-only character forms in Chinese
- zh_era                 a Chinese era year the source does not state
- zh_truncated           a Chinese field cut off mid-sentence
- zh_title_gate          a published Chinese title the Stage 4 gate rejects
- zh_stage_prefix        a Chinese title whose open/closed prefix contradicts the stage
- zh_cache_mismatch      published Chinese differs from its current cache entry
                         (the 2026-10-16 field-cache rewind class)
- pc_stage_results       an e-Gov call for comments labelled as results

MEDIUM (counted, ids listed): en_target_law_number, en_key_date,
ja_main_law_number, confidence_over_evidence.

Checks known to be noisy on this corpus (numbers added/dropped in Chinese,
instrument names, generic party wording) stay in the model-evaluation harness,
where a person reads the side-by-side output.

    python scripts/quality_audit.py [--json out.json] [--fail-on-high]
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import unicodedata
from pathlib import Path

import evaluate_summary_model as ev
import evaluate_translation_model as et
import summarize_updates as su
import translate_updates as tu

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLISHED_PATH = REPO_ROOT / "docs" / "data" / "legal_updates.json"
RAW_PATH = REPO_ROOT / "data" / "raw_items.json"
CACHE_PATH = REPO_ROOT / "data" / "translation_cache.json"
LOCALE = "zh-Hans"

HIGH = ("en_japanese", "en_title_truncated", "en_main_law_number", "en_law_number_year",
        "en_case_code", "en_field_names", "en_wrong_bureau", "zh_missing",
        "zh_kana_or_ja_forms", "zh_era", "zh_truncated", "zh_title_gate", "zh_stage_prefix",
        "zh_cache_mismatch", "pc_stage_results")
MEDIUM = ("en_target_law_number", "en_key_date", "ja_main_law_number", "confidence_over_evidence")
# Which requeue section fixes each HIGH check (see scripts/requeue.py).
REMEDY = {
    "en_japanese": "english", "en_main_law_number": "english", "en_law_number_year": "english",
    "en_case_code": "english", "en_field_names": "english", "en_wrong_bureau": "english",
    "en_title_truncated": "english_title",
    "zh_missing": "zh-Hans", "zh_kana_or_ja_forms": "zh-Hans", "zh_era": "zh-Hans",
    "zh_truncated": "zh-Hans", "zh_title_gate": "zh-Hans", "zh_stage_prefix": "zh-Hans",
    "zh_cache_mismatch": "zh-Hans", "pc_stage_results": "stage rule (scripts/build_public_data.py)",
}

JP_RE = re.compile(r"[぀-ヿ一-鿿]")
FIELD_NAME_RE = re.compile(
    r"\b(?:source_evidence|legal_identifiers|court_case_numbers|official_office_names|raw_summary|"
    r"published_at|title_only|structured_metadata|descriptive_text)\b")
# Wrong or former bureau names seen in the audit. The MHLW pre-2023 name is only
# wrong for notices published after the September 2023 reorganization.
WRONG_BUREAU = (
    # "Health and Welfare Bureau for the Elderly" (老健局) and "...for Persons with
    # Disabilities" are MHLW's own English names, so only the bare form is wrong.
    (re.compile(r"Health and Welfare Bureau(?! for )"), None),
    (re.compile(r"City Planning Bureau"), None),
    (re.compile(r"Consumer Safety Bureau|Consumer and Consumer Affairs Bureau"), None),
    (re.compile(r"Pharmaceutical Safety and Environmental Health Bureau"), "2023-09-01"),
)
KANA_RE = re.compile(r"[぀-ゟ゠-ヺー-ヿ]")
ERA_RE = re.compile(r"(明治|大正|昭和|平成|令和)(元|\d+|[〇一二三四五六七八九十百]+)年")
ERA_EN = {"meiji": "明治", "taisho": "大正", "showa": "昭和", "heisei": "平成", "reiwa": "令和"}
END_PUNCT = tuple("。．.！？!?）)」”\"》】")
PC_RESULT_MARKERS = ("結果について", "結果の公示", "意見募集の結果", "意見募集結果", "の結果")


def era_pairs(text: str) -> set[tuple[str, int]]:
    out = set()
    for era, year in ERA_RE.findall(unicodedata.normalize("NFKC", text)):
        value = 1 if year == "元" else su.kanji_numeral_value(year)
        if value:
            out.add((era, value))
    return out


def ja_number_kept(identifier: dict, text: str) -> bool:
    """The Japanese summary keeps the number in arabic or kanji form."""
    t = unicodedata.normalize("NFKC", text)
    n = identifier["number"]
    if f"第{n}号" in t or identifier["as_written"] in text:
        return True
    for kanji in re.findall(r"第([〇一二三四五六七八九十百千]+)号", text):
        if su.kanji_numeral_value(kanji) == n:
            return True
    return False


def audit(items: list[dict], raw_by_id: dict, cache: dict) -> dict:
    entries = ((cache or {}).get("entries") or {}).get(LOCALE, {})
    found: dict[str, list[str]] = collections.defaultdict(list)
    for item in items:
        iid = item.get("id") or ""
        raw = raw_by_id.get(iid, {})
        ai = item.get("summary_source") == "claude"
        zh = (item.get("translations") or {}).get(LOCALE)
        hit = lambda check: found[check].append(iid)  # noqa: E731

        if ai:
            displayed = " ".join(item.get(k) or "" for k in su.AI_TEXT_FIELDS)
            if JP_RE.search(displayed):
                hit("en_japanese")
            if (item.get("title_en") or "").endswith(("...", "…")):
                hit("en_title_truncated")
            if FIELD_NAME_RE.search(displayed):
                hit("en_field_names")
            for pattern, after in WRONG_BUREAU:
                if pattern.search(displayed) and (after is None or (item.get("published_at") or "") >= after):
                    hit("en_wrong_bureau")
                    break
            entry = {"id": iid, "source": su.source_payload(item, raw)}
            s = ev.score_english({k: item.get(k) for k in su.AI_FIELDS}, entry)
            if s["primary_identifier_expected"] and not s["primary_identifier_kept"]:
                hit("en_main_law_number")
            elif s["identifiers_kept"] < s["identifiers_expected"]:
                hit("en_target_law_number")
            if s["key_dates_kept"] < s["key_dates_expected"]:
                hit("en_key_date")
            if s["model_overconfident"]:
                hit("confidence_over_evidence")
            by_number = collections.defaultdict(set)
            for ident in su.legal_identifiers(item, raw):
                by_number[ident["number"]].add(ident["gregorian_year"])
            for num, year in re.findall(r"No\.\s*(\d+)\s+of\s+(\d{4})", displayed):
                if int(num) in by_number and int(year) not in by_number[int(num)]:
                    hit("en_law_number_year")
                    break
            if "Court" in (item.get("source_name") or ""):
                expected = {c["code_romanized"].lower() for c in su.court_case_numbers(item, raw)}
                written = {c.lower() for c in re.findall(
                    r"\(([A-Z][a-z]{0,3}(?:-[A-Z][a-z]{0,3})?)\)\s*(?:No\.\s*)?\d+", displayed)}
                if expected and written - expected:
                    hit("en_case_code")

        if item.get("summary_ja_source") == "claude":
            ja = " ".join(item.get(k) or "" for k in su.JA_AI_FIELDS)
            ids = su.legal_identifiers(item, raw)
            primary = next((i for i in ids if i["role"] == "amending_instrument"), ids[0] if ids else None)
            if primary and not ja_number_kept(primary, ja):
                hit("ja_main_law_number")

        if ai and not zh:
            hit("zh_missing")
        if zh:
            text = " ".join(str(v) for v in zh.values())
            if KANA_RE.search(tu._QUOTED_SPAN_RE.sub("", text)) or any(ch in et.NON_SIMPLIFIED_CHARS for ch in text):
                hit("zh_kana_or_ja_forms")
            allowed = era_pairs((item.get("title_ja") or "") + (raw.get("raw_summary") or ""))
            en_text = " ".join(item.get(k) or "" for k in su.AI_TEXT_FIELDS)
            allowed |= {(ERA_EN[e.lower()], int(y)) for e, y in
                        re.findall(r"\b(Meiji|Taisho|Showa|Heisei|Reiwa)\s+(\d+)", en_text)}
            if era_pairs(text) - allowed:
                hit("zh_era")
            for f_en, f_zh in (("summary_en", "summary"), ("business_impact_en", "business_impact"),
                               ("recommended_action_en", "recommended_action")):
                if len(item.get(f_en) or "") > 60 and not (zh.get(f_zh) or "").strip().endswith(END_PUNCT):
                    hit("zh_truncated")
                    break
            if tu.title_quality_errors(zh.get("title")):
                hit("zh_title_gate")
            title = zh.get("title") or ""
            stage = item.get("stage") or ""
            # Only a contradiction counts; 公开征求意见（已结束）： is a notational
            # variant with the right meaning (new titles are normalized on save).
            if (stage == "Public Comment Closed" and title.startswith(tu._OPEN_PREFIX)) or                     (stage == "Public Comment Open" and "已结束" in title):
                hit("zh_stage_prefix")
            cached = entries.get(iid)
            if not isinstance(cached, dict) or any(cached.get(f) != zh.get(f) for f in tu.TRANSLATION_FIELDS):
                hit("zh_cache_mismatch")

        if (item.get("source_name") or "").startswith("e-Gov Public Comment") \
                and item.get("stage") == "Public Comment Results Published":
            title = item.get("title_ja") or ""
            if not any(m in title for m in PC_RESULT_MARKERS):
                hit("pc_stage_results")

    return {"items": len(items), "found": {k: sorted(set(v)) for k, v in found.items()}}


def report(result: dict) -> tuple[str, int]:
    found = result["found"]
    high_total = sum(len(found.get(k, [])) for k in HIGH)
    lines = [f"Quality audit: {result['items']} published items; HIGH findings: {high_total}", ""]
    lines.append("| check | severity | items | remedy |")
    lines.append("|---|---|---|---|")
    for check in HIGH + MEDIUM:
        ids = found.get(check, [])
        sev = "HIGH" if check in HIGH else "medium"
        lines.append(f"| {check} | {sev} | {len(ids)} | {REMEDY.get(check, 'review')} |")
    sections = collections.defaultdict(set)
    for check in HIGH:
        for iid in found.get(check, []):
            sections[REMEDY[check]].add(iid)
    requeue_sections = {k: sorted(v) for k, v in sections.items() if not k.startswith("stage rule")}
    if requeue_sections:
        lines += ["", "Ready-to-paste data/requeue.json sections (set a new created_at):", "```json",
                  json.dumps(requeue_sections, ensure_ascii=False, indent=1), "```"]
    return "\n".join(lines), high_total


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Offline quality audit of the published corpus")
    parser.add_argument("--json", help="also write the findings to this path")
    parser.add_argument("--fail-on-high", action="store_true", help="exit 1 when any HIGH finding exists")
    args = parser.parse_args(argv)
    items = json.loads(PUBLISHED_PATH.read_text(encoding="utf-8"))
    raw_by_id = {r.get("id"): r for r in json.loads(RAW_PATH.read_text(encoding="utf-8")) if isinstance(r, dict)}
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    result = audit(items, raw_by_id, cache)
    text, high = report(result)
    print(text)
    for check in HIGH:
        ids = result["found"].get(check, [])
        if ids:
            print(f"::warning::Quality audit {check}: {len(ids)} item(s), e.g. {', '.join(ids[:5])}")
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if (args.fail_on_high and high) else 0


if __name__ == "__main__":
    raise SystemExit(main())
