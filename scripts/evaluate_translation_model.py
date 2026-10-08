#!/usr/bin/env python3
"""
evaluate_translation_model.py — Japan Legal Reform Watch by LegalOS

Compare two Claude models on the SAME frozen set of Stage 4 zh-Hans translation
inputs, using the SAME prompt, validation, normalization, and title quality gate
that scripts/translate_updates.py uses in production, so a translation-model
change can be judged on evidence instead of price.

This script never changes the published data, the translation cache, or the
production model. It writes only under data/eval/.

Workflow
--------
1. Freeze a sample (no API, no cost):

       python scripts/evaluate_translation_model.py build-set --profile hard --size 30
       python scripts/evaluate_translation_model.py build-set --profile stratified --size 24

2. Run each model over the frozen set (this DOES call the API):

       python scripts/evaluate_translation_model.py run --model claude-sonnet-5 --profile hard --confirm-spend
       python scripts/evaluate_translation_model.py run --model claude-haiku-5-5 --profile hard --confirm-spend

   `run` refuses to spend without --confirm-spend, prints the projected worst
   case first, and stops at --max-cost-usd (default 1.00). A rejected title gets
   the same single recovery call production makes (title_ja blanked), so the
   final rejection rate is the one the dashboard would actually see.

3. Compare, and emit a side-by-side file for human review:

       python scripts/evaluate_translation_model.py compare \\
           --baseline claude-sonnet-5 --candidate claude-haiku-5-5 --profile hard

What is measured automatically
------------------------------
Schema validity; title quality-gate rejections before and after the recovery
call, with reasons; kana anywhere in the four fields; characters that are not
Simplified Chinese (traditional forms, Japanese shinjitai); the statute
instrument named in the title (政令 / 省令 / 府令 / 告示) against what the source
supports; numbers dropped from or added to the English; obligation wording
(必须 / 应当 ...) the English does not support; the preferred stage prefix; and
measured tokens and cost.

What is NOT measured automatically
----------------------------------
Faithfulness of meaning, fluency, whether a Japan-specific concept survived, and
whether caution in the English survived. Those need a person reading the
side-by-side file; the comparison says so rather than implying a verdict.

Only items whose English body is an AI summary are sampled: those are the items
Stage 4 actually sends to the model in full. Rule-based bodies are shared
boilerplate served from the field cache, so translating them again would measure
something production never pays for.

Python 3.11+. Requires the `anthropic` SDK only for `run`.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import evaluate_summary_model as summary_eval
from anthropic_batch import lowest_thinking_config
import translate_updates as translator

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PUBLISHED_PATH = REPO_ROOT / "docs" / "data" / "legal_updates.json"
CACHE_PATH = REPO_ROOT / "data" / "translation_cache.json"
EVAL_DIR = REPO_ROOT / "data" / "eval"
LOCALE = "zh-Hans"

DEFAULT_SET_SIZE = 24
DEFAULT_MAX_COST_USD = 1.00

ENGLISH_FIELDS = ("title_en", "summary_en", "business_impact_en", "recommended_action_en")

load_json = summary_eval.load_json
save_json = summary_eval.save_json


def set_path(profile: str) -> Path:
    suffix = "" if profile == "stratified" else f"_{profile}"
    return EVAL_DIR / f"translation_eval_set{suffix}.json"


def results_path(profile: str, model: str) -> Path:
    # Matches the existing data/eval/results_*.json ignore rule.
    suffix = "" if profile == "stratified" else f"_{profile}"
    return EVAL_DIR / f"results_translation{suffix}_{model}.json"


def side_by_side_path(profile: str, baseline: str, candidate: str) -> Path:
    suffix = "" if profile == "stratified" else f"_{profile}"
    return EVAL_DIR / f"side_by_side_translation{suffix}_{baseline}_vs_{candidate}.md"


# --------------------------------------------------------------------------- #
# 1. Freeze the evaluation set
# --------------------------------------------------------------------------- #

_KATAKANA_RE = re.compile(r"[ァ-ヺー]")
_ASCII_NUMBER_RE = re.compile(r"\d+")
JAPAN_SPECIFIC_TERMS = ("育成就労", "特定外来生物", "勧告", "課徴金", "技能実習", "特定技能", "排除措置命令")
INSTRUMENT_TERMS_JA = ("政令", "省令", "府令", "告示", "施行令", "施行規則")


def _english(item: dict) -> str:
    return " ".join(str(item.get(field) or "") for field in ENGLISH_FIELDS)


# Each signal is a case the production title gate or prompt rules exist for, so
# a cheaper model is most likely to fail exactly there. All are computable from
# the frozen fields, so selection is deterministic.
HARD_CASE_SIGNALS = (
    ("previously_rejected", lambda item, rejected: item.get("id") in rejected),
    ("name_separator", lambda item, rejected: "・" in (item.get("title_ja") or "")),
    ("katakana_in_title_ja", lambda item, rejected: len(_KATAKANA_RE.findall(item.get("title_ja") or "")) >= 4),
    ("japan_specific_term", lambda item, rejected: any(t in (item.get("title_ja") or "") for t in JAPAN_SPECIFIC_TERMS)),
    ("instrument_term", lambda item, rejected: any(t in (item.get("title_ja") or "") for t in INSTRUMENT_TERMS_JA)),
    ("long_title", lambda item, rejected: len(item.get("title_en") or "") > 100 or len(item.get("title_ja") or "") > 60),
    ("stage_prefix_rule", lambda item, rejected: (item.get("stage") or "").startswith(("Public Comment", "Draft Guideline", "Bill Submitted"))),
    ("number_dense", lambda item, rejected: len(_ASCII_NUMBER_RE.findall(_english(item))) >= 6),
    ("ellipsis_in_english_title", lambda item, rejected: "..." in (item.get("title_en") or "") or "…" in (item.get("title_en") or "")),
)


def hard_case_signals(item: dict, rejected: dict) -> list[str]:
    return [name for name, test in HARD_CASE_SIGNALS if test(item, rejected)]


def frozen_entry(item: dict, signals: list[str] | None = None) -> dict:
    """The exact inputs build_user_content reads, so later runs see identical prompts."""
    entry = {
        "id": item.get("id"),
        "stage": item.get("stage", ""),
        "area": item.get("area", ""),
        "source_name": item.get("source_name", ""),
        "title_ja": item.get("title_ja", ""),
        **{field: item.get(field, "") for field in ENGLISH_FIELDS},
    }
    if signals is not None:
        entry["hard_signals"] = signals
    return entry


def eligible_items(items: list) -> list[dict]:
    return [
        item for item in sorted(items, key=lambda i: i.get("id") or "")
        if isinstance(item, dict)
        and item.get("summary_source") == "claude"
        and (item.get("title_ja") or "").strip()
        and all((item.get(field) or "").strip() for field in ENGLISH_FIELDS)
    ]


def build_set(size: int, profile: str = "stratified", items=None, cache=None) -> dict:
    items = load_json(PUBLISHED_PATH, []) if items is None else items
    pool = eligible_items(items if isinstance(items, list) else [])
    if not pool:
        raise SystemExit(f"ERROR: no AI-summarized published items at {PUBLISHED_PATH}")
    cache = load_json(CACHE_PATH, {}) if cache is None else cache
    rejected = ((cache or {}).get("rejected") or {}).get(LOCALE) or {}

    if profile == "hard":
        scored = []
        for item in pool:
            signals = hard_case_signals(item, rejected)
            if signals:
                # A title production has actually rejected outranks any count of
                # proxy signals: it is the failure this evaluation exists to catch.
                first = 0 if "previously_rejected" in signals else 1
                scored.append((first, -len(signals), item.get("id") or "", item, signals))
        ranked = sorted(scored, key=lambda row: row[:3])[:size]
        selected = [frozen_entry(row[3], row[4]) for row in ranked]
    else:
        # Every stage first (the stage decides the title prefix), then round-robin
        # over the largest (stage, area) buckets, as the summary harness does.
        buckets: dict[tuple[str, str], list[dict]] = {}
        for item in pool:
            buckets.setdefault((item.get("stage", ""), item.get("area", "")), []).append(item)
        chosen: list[dict] = []
        taken: set[str] = set()

        def take(item: dict) -> None:
            if item.get("id") not in taken:
                taken.add(item.get("id"))
                chosen.append(item)

        by_stage: dict[str, list[dict]] = {}
        for (stage, _area), bucket in buckets.items():
            by_stage.setdefault(stage, []).extend(bucket)
        for stage in sorted(by_stage):
            if len(chosen) >= size:
                break
            take(sorted(by_stage[stage], key=lambda i: i.get("id") or "")[0])
        ordered = [buckets[k] for k in sorted(buckets, key=lambda k: (-len(buckets[k]), k))]
        depth = 0
        while len(chosen) < size and any(len(b) > depth for b in ordered):
            for bucket in ordered:
                if len(chosen) >= size:
                    break
                if len(bucket) > depth:
                    take(bucket[depth])
            depth += 1
        selected = [frozen_entry(item) for item in chosen[:size]]

    return {
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "profile": profile,
        "locale": LOCALE,
        "prompt_version": translator.PROMPT_VERSION,
        "items": selected,
    }


# --------------------------------------------------------------------------- #
# 2. Automatic metrics
# --------------------------------------------------------------------------- #

# Characters that are valid Traditional Chinese or Japanese shinjitai but never
# Simplified Chinese. Curated rather than exhaustive: a false positive here would
# block a correct model. 勧 is deliberately absent -- the prompt asks for 勧告.
NON_SIMPLIFIED_CHARS = frozenset(
    "與為國關規條號實發際準標時從業務員會這們說對個經濟進動產稅證據機構學醫藥環電網資訊開歷預處車門問題書長東應當結"
    "労済関円検証沢図総広辺経営変団払帰単様歳薬楽駅鉄対発県隣険査価継続剤処乗"
)

# Which Chinese instrument word in a title the source supports.
_INSTRUMENT_SUPPORT = {
    "政令": (("政令", "施行令"), ("cabinet order", "enforcement order")),
    "省令": (("省令", "施行規則", "規則"), ("ministerial ordinance", "ministerial order", "enforcement regulation", "enforcement rule")),
    "府令": (("府令",), ("cabinet office order", "cabinet office ordinance")),
    "告示": (("告示",), ("notice", "notification")),
}
# What each Japanese instrument should still be called somewhere in the Chinese.
_INSTRUMENT_EXPECTED = {
    "政令": ("政令", "施行令"),
    "府令": ("府令",),
    "省令": ("省令", "施行规则", "规则"),
    "告示": ("告示",),
    "施行令": ("施行令", "政令"),
    "施行規則": ("施行规则", "省令", "规则"),
}

_OBLIGATION_ZH = re.compile(r"必须|应当|务必|有义务|须遵守")
_OBLIGATION_EN = re.compile(r"\b(must|shall|required|requires|obliged|obligation|obligations|mandatory)\b", re.I)
_ERA_EN_RE = re.compile(r"\b(Meiji|Taisho|Showa|Heisei|Reiwa)\s*(\d+)", re.I)
_ERA_BASE_EN = {"meiji": 1867, "taisho": 1911, "showa": 1925, "heisei": 1988, "reiwa": 2018}

STAGE_PREFIXES = {
    "Public Comment Open": "公开征求意见：",
    "Public Comment Closed": "（已结束）公开征求意见：",
    "Public Comment Results Published": "公开征求意见结果：",
    "Draft Guideline": "指南草案：",
    "Bill Submitted": "法案提交：",
}


def _digits(text: str) -> set[int]:
    return {int(n) for n in _ASCII_NUMBER_RE.findall(unicodedata.normalize("NFKC", text or ""))}


_MONTHS_EN = {name: index for index, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"), 1)}
_MONTH_EN_RE = re.compile(r"\b(" + "|".join(_MONTHS_EN) + r")\b", re.I)


def source_numbers(entry: dict) -> set[int]:
    """Every number the model was given, in any spelling a faithful rendering uses.

    English digits; month names (a Chinese date writes June as 6); era years in
    both directions; and kanji numerals, which the AI English often quotes
    verbatim (令和八年国土交通省令第七十一号) as well as title_ja.
    """
    english = _english(entry)
    values = output_numbers(english + " " + entry.get("title_ja", ""), include_single_kanji=True)
    for era, number in _ERA_EN_RE.findall(english):
        values.add(_ERA_BASE_EN[era.lower()] + int(number))
    values |= {_MONTHS_EN[m.lower()] for m in _MONTH_EN_RE.findall(english)
               if m.lower() != "may" or re.search(r"\bMay\s+\d", english)}
    return values


def era_supported(text: str, source: set[int]) -> set[int]:
    """Era-year numbers in the Chinese whose Gregorian year the source gives.

    昭和33年 in the Chinese against 1958 in the English is the same year, so the
    33 is not a new number.
    """
    supported = set()
    for era, number in summary_eval._ERA_YEAR_RE.findall(unicodedata.normalize("NFKC", text or "")):
        year = 1 if number == "元" else (int(number) if number.isdigit() else summary_eval.kanji_numeral_value(number))
        if year is not None and summary_eval._ERA_BASE_YEAR[era] + year in source:
            supported.add(year)
    return supported


def output_numbers(text: str, *, include_single_kanji: bool) -> set[int]:
    """Numbers in the Chinese: digits, kanji numerals, and era years."""
    text = unicodedata.normalize("NFKC", text or "")
    values = _digits(text)
    for match in summary_eval._KANJI_NUMERAL_RE.findall(text):
        # A lone 一 is usually 一部 / 一项, not a figure; count it only when
        # checking whether a source number survived.
        if len(match) > 1 or include_single_kanji:
            value = summary_eval.kanji_numeral_value(match)
            if value is not None:
                values.add(value)
    for era, number in summary_eval._ERA_YEAR_RE.findall(text):
        year = 1 if number == "元" else (int(number) if number.isdigit() else summary_eval.kanji_numeral_value(number))
        if year is not None:
            values.add(summary_eval._ERA_BASE_YEAR[era] + year)
    return values


def instrument_findings(fields: dict, entry: dict) -> tuple[list[str], list[str]]:
    """(missing, unsupported): instruments dropped from, or invented in, the Chinese."""
    title_ja = entry.get("title_ja") or ""
    english = _english(entry).lower()
    chinese = (fields.get("title") or "") + " " + (fields.get("summary") or "")
    # Only the operative instrument counts: a Japanese title ends with what the
    # item actually is (…を改正する省令案), while instruments quoted earlier in a
    # long name (…を定める政令に規定する…) may legitimately be shortened away.
    operative = max(
        (term for term in _INSTRUMENT_EXPECTED if term in title_ja),
        key=lambda term: (title_ja.rfind(term) + len(term), len(term)),
        default=None,
    )
    missing = [
        operative for _ in (0,)
        if operative and not any(r in chinese for r in _INSTRUMENT_EXPECTED[operative])
    ]
    unsupported = []
    title = fields.get("title") or ""
    for term, (ja_support, en_support) in _INSTRUMENT_SUPPORT.items():
        if term in title and not any(s in title_ja for s in ja_support) and not any(s in english for s in en_support):
            unsupported.append(term)
    return missing, unsupported


def score_translation(fields: dict, entry: dict, first_title_errors: list[str],
                      final_title_errors: list[str]) -> dict:
    joined = " ".join(str(fields.get(f) or "") for f in translator.TRANSLATION_FIELDS)
    source = source_numbers(entry)
    dropped = sorted(n for n in _digits(_english(entry))
                     if n not in output_numbers(joined, include_single_kanji=True))
    supported = source | era_supported(joined, source)
    added = sorted(n for n in output_numbers(joined, include_single_kanji=False) if n not in supported)
    missing_instruments, unsupported_instruments = instrument_findings(fields, entry)
    prefix = STAGE_PREFIXES.get(entry.get("stage") or "")
    return {
        "schema_compliant": True,
        "title_rejected_first_pass": bool(first_title_errors),
        "title_rejected_final": bool(final_title_errors),
        "title_reasons": final_title_errors or first_title_errors,
        "kana_anywhere": bool(translator._KANA_RE.search(joined)),
        "non_simplified_chars": sorted({ch for ch in joined if ch in NON_SIMPLIFIED_CHARS}),
        "instrument_missing": missing_instruments,
        "instrument_unsupported": unsupported_instruments,
        "numbers_dropped": dropped,
        "numbers_added": added,
        "obligation_added": bool(_OBLIGATION_ZH.search(joined)) and not _OBLIGATION_EN.search(_english(entry)),
        "stage_prefix_expected": prefix is not None,
        "stage_prefix_ok": None if prefix is None else (fields.get("title") or "").startswith(prefix),
        "lengths": {f: len(str(fields.get(f) or "")) for f in translator.TRANSLATION_FIELDS},
    }


# --------------------------------------------------------------------------- #
# 3. Run one model over the frozen set
# --------------------------------------------------------------------------- #

def projected_cost_usd(model: str, count: int) -> float | None:
    """Conservative worst case: two calls per item (the title retry), full output."""
    price = translator.model_pricing(model)
    if price is None:
        return None
    approx_input = 3500  # system prompt + glossary + one item, uncached
    return 2 * count * (approx_input * price["input"] + translator.MAX_TOKENS * price["output"]) / 1e6


def translate_once(client, model: str, item: dict) -> tuple[dict, str, dict]:
    result, model_used, usage = translator.unpack_api_outcome(
        translator.request_translation(client, model, item, LOCALE)
    )
    fields = translator.normalized_fields(
        translator.merge_response_fields({}, result, translator.TRANSLATION_FIELDS)
    )
    return fields, model_used, usage


def run_model(args) -> int:
    frozen = load_json(set_path(args.profile), None)
    if not isinstance(frozen, dict) or not frozen.get("items"):
        raise SystemExit("ERROR: no evaluation set. Run `build-set` first.")
    entries = frozen["items"][: max(0, args.limit)]
    if translator.model_pricing(args.model) is None:
        raise SystemExit(f"ERROR: no configured pricing for {args.model}; refusing to run uncapped.")

    print(f"model            : {args.model}")
    print(f"profile          : {args.profile}")
    print(f"items            : {len(entries)}")
    print(f"prompt_version   : {translator.PROMPT_VERSION} (set frozen at {frozen.get('prompt_version')})")
    print(f"projected max USD: {projected_cost_usd(args.model, len(entries)):.4f}  (worst case)")
    print(f"max_cost_usd     : {args.max_cost_usd}")
    if not args.confirm_spend:
        print("\nNothing was called. Re-run with --confirm-spend to make real API calls.")
        return 0

    client = translator.make_client()
    usage_totals = translator.message_usage(None)
    spent = 0.0
    records = []

    def account(usage: dict, model_used: str) -> float | None:
        nonlocal spent
        translator.add_usage(usage_totals, usage)
        estimate = translator.estimate_usage_cost_usd(usage, model_used)
        if estimate is not None:
            spent += estimate
        return estimate

    for index, entry in enumerate(entries, 1):
        if spent >= args.max_cost_usd:
            print(f"cost cap reached after {index - 1} items; stopping.")
            break
        item = dict(entry)
        record = {"id": entry["id"], "stage": entry.get("stage"), "area": entry.get("area"), "calls": 0}
        cost = 0.0
        try:
            fields, model_used, usage = translate_once(client, args.model, item)
            record["calls"] += 1
            cost += account(usage, model_used) or 0.0
            first_errors = translator.title_quality_errors(fields["title"])
            final_errors = first_errors
            if first_errors:
                # Production's single recovery call: the same request with the
                # Japanese reference title blanked, judged by the same gate.
                retry_item = dict(item, title_ja="")
                retry_fields, retry_model, retry_usage = translate_once(client, args.model, retry_item)
                record["calls"] += 1
                cost += account(retry_usage, retry_model) or 0.0
                final_errors = translator.title_quality_errors(retry_fields["title"])
                record["first_pass_title"] = fields["title"]
                fields = retry_fields
            record["output"] = fields
            record["model"] = model_used
            record["scores"] = score_translation(fields, entry, first_errors, final_errors)
            record["estimated_cost_usd"] = cost
            flag = " TITLE-REJECTED" if final_errors else (" retried" if first_errors else "")
            print(f"[{index}/{len(entries)}] {entry['id']} ok{flag}  spent=${spent:.4f}")
        except Exception as exc:  # never log the provider body or any text
            error_type = (
                "item_validation_error" if isinstance(exc, (ValueError, json.JSONDecodeError))
                else translator.classify_provider_error(exc)
            )
            if exc.__class__.__name__ == "ModelRefusal":
                error_type = "refusal"
            record["error_type"] = error_type
            record["scores"] = {"schema_compliant": False}
            record["estimated_cost_usd"] = cost
            print(f"[{index}/{len(entries)}] {entry['id']} FAILED type={error_type}")
            if error_type in translator.FATAL_PROVIDER_ERRORS:
                records.append(record)
                print("provider unavailable; stopping.")
                break
        records.append(record)

    out = {
        "model": args.model,
        "profile": args.profile,
        "locale": LOCALE,
        "prompt_version": translator.PROMPT_VERSION,
        "thinking": lowest_thinking_config(args.model),
        "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "usage_totals": usage_totals,
        "estimated_cost_usd": spent,
        "records": records,
    }
    path = results_path(args.profile, args.model)
    save_json(path, out)
    print(f"\nwrote {path}")
    return 0


# --------------------------------------------------------------------------- #
# 4. Compare two runs
# --------------------------------------------------------------------------- #

def aggregate(run: dict) -> dict:
    records = [r for r in run.get("records", []) if r.get("scores")]
    n = len(records) or 1
    scored = [r["scores"] for r in records]
    costs = [r["estimated_cost_usd"] for r in records if r.get("estimated_cost_usd") is not None]
    with_prefix = [s for s in scored if s.get("stage_prefix_expected")]

    def rate(pred):
        return 100.0 * sum(1 for s in scored if pred(s)) / n

    reasons = Counter(reason for s in scored if s.get("title_rejected_first_pass") for reason in s.get("title_reasons", []))
    return {
        "items": len(records),
        "schema_compliance_pct": rate(lambda s: s.get("schema_compliant")),
        "refusal_pct": 100.0 * sum(1 for r in records if r.get("error_type") == "refusal") / n,
        "title_rejected_first_pct": rate(lambda s: s.get("title_rejected_first_pass")),
        "title_rejected_final_pct": rate(lambda s: s.get("title_rejected_final")),
        "kana_pct": rate(lambda s: s.get("kana_anywhere")),
        "non_simplified_pct": rate(lambda s: bool(s.get("non_simplified_chars"))),
        "instrument_missing_pct": rate(lambda s: bool(s.get("instrument_missing"))),
        "instrument_unsupported_pct": rate(lambda s: bool(s.get("instrument_unsupported"))),
        "numbers_dropped_pct": rate(lambda s: bool(s.get("numbers_dropped"))),
        "numbers_added_pct": rate(lambda s: bool(s.get("numbers_added"))),
        "obligation_added_pct": rate(lambda s: s.get("obligation_added")),
        "stage_prefix_ok_pct": (100.0 * sum(1 for s in with_prefix if s.get("stage_prefix_ok")) / len(with_prefix)) if with_prefix else 0.0,
        "mean_calls": statistics.mean([r.get("calls", 0) for r in records]) if records else 0,
        "mean_cost_usd": statistics.mean(costs) if costs else 0.0,
        "total_cost_usd": run.get("estimated_cost_usd", 0.0),
        "title_reasons": dict(reasons.most_common()),
    }


# A candidate rate higher than the baseline on any of these blocks a switch.
# Kana in the title already blocks through the production title gate; kana in a
# body field is reported but not blocking, because Japanese sub-item markers
# (イ / ロ / ハ) are sometimes kept verbatim in a faithful rendering.
BLOCKING_METRICS = (
    ("title_rejected_final_pct", "more titles rejected (cards fall back to English)"),
    ("non_simplified_pct", "more non-Simplified characters"),
    ("instrument_unsupported_pct", "more titles naming an instrument the source does not support"),
    ("instrument_missing_pct", "more statute instruments dropped"),
    ("numbers_added_pct", "more numbers not in the English (possible hallucination)"),
    ("numbers_dropped_pct", "more numbers dropped from the English"),
    ("obligation_added_pct", "more obligation wording the English does not support"),
    ("refusal_pct", "more safety refusals"),
)


def blockers(a: dict, b: dict) -> list[str]:
    found = [label for key, label in BLOCKING_METRICS if b[key] > a[key]]
    if b["schema_compliance_pct"] < a["schema_compliance_pct"]:
        found.insert(0, "schema compliance regressed")
    return found


def compare(args) -> int:
    base_path = results_path(args.profile, args.baseline)
    cand_path = results_path(args.profile, args.candidate)
    base, cand = load_json(base_path, None), load_json(cand_path, None)
    for path, run in ((base_path, base), (cand_path, cand)):
        if not isinstance(run, dict):
            raise SystemExit(f"ERROR: missing run file {path}. Run `run` for that model first.")

    a, b = aggregate(base), aggregate(cand)
    rows = [
        ("items evaluated", "items", "{:.0f}"),
        ("schema compliance %", "schema_compliance_pct", "{:.1f}"),
        ("refusal %", "refusal_pct", "{:.1f}"),
        ("title rejected, first pass %", "title_rejected_first_pct", "{:.1f}"),
        ("title rejected, final %", "title_rejected_final_pct", "{:.1f}"),
        ("kana anywhere %", "kana_pct", "{:.1f}"),
        ("non-Simplified chars %", "non_simplified_pct", "{:.1f}"),
        ("instrument dropped %", "instrument_missing_pct", "{:.1f}"),
        ("instrument unsupported %", "instrument_unsupported_pct", "{:.1f}"),
        ("numbers dropped %", "numbers_dropped_pct", "{:.1f}"),
        ("numbers added %", "numbers_added_pct", "{:.1f}"),
        ("obligation added %", "obligation_added_pct", "{:.1f}"),
        ("stage prefix followed %", "stage_prefix_ok_pct", "{:.1f}"),
        ("mean API calls / item", "mean_calls", "{:.2f}"),
        ("mean USD / item", "mean_cost_usd", "{:.5f}"),
        ("total USD", "total_cost_usd", "{:.4f}"),
    ]
    print(f"\n{'metric':30s} {args.baseline:>20s} {args.candidate:>20s}")
    print("-" * 72)
    for label, key, fmt in rows:
        print(f"{label:30s} {fmt.format(a[key]):>20s} {fmt.format(b[key]):>20s}")
    for name, agg in ((args.baseline, a), (args.candidate, b)):
        if agg["title_reasons"]:
            print(f"title gate reasons ({name}): " + ", ".join(f"{k} x{v}" for k, v in agg["title_reasons"].items()))
    if a["mean_cost_usd"]:
        print(f"\ncost per item: {100 * b['mean_cost_usd'] / a['mean_cost_usd']:.1f}% of baseline")

    found = blockers(a, b)
    print("\nautomatic verdict:")
    if found:
        for blocker in found:
            print(f"  BLOCKER: {blocker}")
        print("  -> do NOT switch the production translation model on these results.")
    else:
        print("  no automatic regression detected.")
    print("  Automatic metrics cannot judge faithfulness of meaning, fluency, whether")
    print("  a Japan-specific concept survived, or whether the English caution did.")
    print("  Read the side-by-side file below before deciding.")

    lines = [f"# zh-Hans translation: {args.baseline} vs {args.candidate} ({args.profile})", ""]
    frozen = load_json(set_path(args.profile), {})
    entry_by_id = {e["id"]: e for e in frozen.get("items", [])}
    base_by_id = {r["id"]: r for r in base.get("records", [])}
    for record in cand.get("records", []):
        other = base_by_id.get(record["id"])
        if not other:
            continue
        entry = entry_by_id.get(record["id"], {})
        lines += [f"## {record['id']} — {record.get('stage', '')} / {record.get('area', '')}", ""]
        lines.append(f"- `title_ja`: {entry.get('title_ja', '')}")
        for field in ENGLISH_FIELDS:
            lines.append(f"- `{field}`: {entry.get(field, '')}")
        lines.append("")
        for label, run_record in ((args.baseline, other), (args.candidate, record)):
            lines.append(f"**{label}**")
            lines.append("")
            if run_record.get("first_pass_title"):
                lines.append(f"- first-pass title (rejected): {run_record['first_pass_title']}")
            for key, value in (run_record.get("output") or {}).items():
                lines.append(f"- `{key}`: {value}")
            if run_record.get("error_type"):
                lines.append(f"- error: {run_record['error_type']}")
            flags = _flags(run_record.get("scores") or {})
            if flags:
                lines.append(f"- flags: {flags}")
            lines.append("")
    path = side_by_side_path(args.profile, args.baseline, args.candidate)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nside-by-side for human review: {path}")
    return 0


def _flags(scores: dict) -> str:
    flags = []
    if scores.get("title_rejected_final"):
        flags.append("title rejected: " + "; ".join(scores.get("title_reasons", [])))
    elif scores.get("title_rejected_first_pass"):
        flags.append("title recovered on retry")
    for key in ("non_simplified_chars", "instrument_missing", "instrument_unsupported",
                "numbers_dropped", "numbers_added"):
        if scores.get(key):
            flags.append(f"{key}={scores[key]}")
    if scores.get("kana_anywhere"):
        flags.append("kana")
    if scores.get("obligation_added"):
        flags.append("obligation added")
    if scores.get("stage_prefix_ok") is False:
        flags.append("stage prefix not followed")
    return ", ".join(flags)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[3])
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build-set", help="Freeze an evaluation set (no API calls).")
    p_build.add_argument("--size", type=int, default=DEFAULT_SET_SIZE)
    p_build.add_argument("--profile", choices=("stratified", "hard"), default="stratified")

    p_run = sub.add_parser("run", help="Run one model over the frozen set (calls the API).")
    p_run.add_argument("--model", required=True)
    p_run.add_argument("--profile", choices=("stratified", "hard"), default="stratified")
    p_run.add_argument("--limit", type=int, default=30)
    p_run.add_argument("--max-cost-usd", type=float, default=DEFAULT_MAX_COST_USD)
    p_run.add_argument("--confirm-spend", action="store_true",
                       help="Required. Without it the command only prints the projected cost.")

    p_cmp = sub.add_parser("compare", help="Compare two completed runs (no API calls).")
    p_cmp.add_argument("--baseline", required=True)
    p_cmp.add_argument("--candidate", required=True)
    p_cmp.add_argument("--profile", choices=("stratified", "hard"), default="stratified")

    args = parser.parse_args(argv)
    if args.command == "build-set":
        frozen = build_set(args.size, args.profile)
        path = set_path(args.profile)
        save_json(path, frozen)
        print(f"froze {len(frozen['items'])} items ({args.profile}) -> {path}")
        print("stages covered: " + ", ".join(sorted({i['stage'] or '(none)' for i in frozen['items']})))
        if args.profile == "hard":
            tally = Counter(sig for i in frozen["items"] for sig in i.get("hard_signals", []))
            print("difficulty signals: " + ", ".join(f"{k}={v}" for k, v in tally.most_common()))
        return 0
    if args.command == "run":
        if args.max_cost_usd <= 0:
            parser.error("--max-cost-usd must be positive")
        return run_model(args)
    return compare(args)


if __name__ == "__main__":
    raise SystemExit(main())
