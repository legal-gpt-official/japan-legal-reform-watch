#!/usr/bin/env python3
"""
evaluate_summary_model.py — Japan Legal Reform Watch by LegalOS

Compare two Claude models on the SAME fixed evaluation set, the SAME prompts, and
the SAME guardrails that scripts/summarize_updates.py uses in production, so a
model change can be judged on evidence instead of intuition.

This script never changes the published data, the caches, or the production
model. It writes only under data/eval/.

Why it exists
-------------
Stage 3 summarization is the product: a cautious English (and Japanese) rendering
of a Japanese government notice. Moving it to a cheaper model is only defensible
if the cheaper model holds quality. That needs a frozen input set, identical
prompts, and metrics that are actually checkable.

Workflow
--------
1. Freeze a stratified sample (no API, no cost):

       python scripts/evaluate_summary_model.py build-set --size 24

2. Run each model over that frozen set (this DOES call the API):

       python scripts/evaluate_summary_model.py run --model claude-opus-4-8 --limit 24
       python scripts/evaluate_summary_model.py run --model claude-sonnet-5  --limit 24

   `run` refuses to start without --confirm-spend, prints the projected cost
   first, and stops at --max-cost-usd (default 1.00).

3. Compare, and emit a side-by-side file for human review:

       python scripts/evaluate_summary_model.py compare \\
           --baseline claude-opus-4-8 --candidate claude-sonnet-5

What is measured automatically
------------------------------
schema compliance, validation-failure rate, guardrail violations (definitive /
legal-advice wording, Japanese characters in title_en), unsupported-number rate
(a checkable hallucination proxy: digits in the output that appear nowhere in the
source), field lengths, confidence distribution, measured input/output tokens,
and measured cost per item.

What is NOT measured automatically
----------------------------------
Factual accuracy, faithfulness to the Japanese original, Japanese writing
quality, and how practically useful the business-impact / recommended-action
sentences are. Those need a human reading the emitted side-by-side file. The
comparison report states this explicitly rather than implying a verdict.

Python 3.11+. Requires the `anthropic` SDK only for `run`.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import openai_provider
import summarize_updates as summarizer

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PUBLISHED_PATH = REPO_ROOT / "docs" / "data" / "legal_updates.json"
RAW_PATH = REPO_ROOT / "data" / "raw_items.json"
EVAL_DIR = REPO_ROOT / "data" / "eval"
EVAL_SET_PATH = EVAL_DIR / "summary_eval_set.json"

DEFAULT_SET_SIZE = 24
DEFAULT_MAX_COST_USD = 1.00

# Digits that are structural rather than factual claims about the source.
_NUMBER_RE = re.compile(r"\d+")
_TRIVIAL_NUMBERS = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "19", "20", "21"}


def load_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def atomic_replace(tmp, path, *, attempts: int = 6, delay: float = 0.05) -> None:
    """Move `tmp` over `path`, retrying briefly on a transient Windows lock.

    os.replace is atomic, but on Windows an on-access virus scanner can hold the
    freshly written temp file for a few milliseconds and the move fails with
    PermissionError (WinError 5). Retrying a handful of times turns that into a
    non-event; a genuine permission problem still surfaces after the last attempt.
    Linux (where CI runs) never takes this path.
    """
    for attempt in range(attempts):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    atomic_replace(tmp, path)


# --------------------------------------------------------------------------- #
# 1. Freeze the evaluation set
# --------------------------------------------------------------------------- #

# Signals that make an item genuinely hard to summarize cautiously. A random
# sample is mostly easy items, so a model swap can pass it while still failing on
# exactly the cases this dashboard exists to triage. Each check is computable
# from the frozen source payload, so `hard` selection is deterministic.
HARD_CASE_SIGNALS = (
    ("thin_source", lambda t, s, stage: len((s.get("raw_summary") or "").strip()) < 20),
    ("amendment_vs_enactment", lambda t, s, stage: any(k in t for k in ("一部を改正", "改正案", "改正する法律"))),
    ("has_deadline_or_effective_date", lambda t, s, stage: any(
        k in (t + " " + (s.get("raw_summary") or "")) for k in ("施行", "期限", "締切", "までに", "適用開始"))),
    ("long_statute_name", lambda t, s, stage: len(t) > 60),
    ("instrument_distinction", lambda t, s, stage: any(
        k in t for k in ("政令", "省令", "告示", "施行令", "施行規則", "府令"))),
    ("public_comment", lambda t, s, stage: "Public Comment" in (stage or "")),
    ("legal_status_easy_to_overstate", lambda t, s, stage: (stage or "") in (
        "Enacted", "Promulgated", "In Force", "Scheduled to Take Effect", "Bill Submitted")),
    ("foreign_or_treaty", lambda t, s, stage: any(k in t for k in ("外国", "国際", "条約", "輸入", "輸出"))),
)


def hard_case_score(item: dict, raw: dict) -> tuple[int, list[str]]:
    """Count the difficulty signals an item trips, and name them."""
    title = item.get("title_ja") or ""
    source = {"raw_summary": (raw.get("raw_summary") or item.get("raw_summary") or "")}
    stage = item.get("stage") or ""
    hit = [name for name, test in HARD_CASE_SIGNALS if test(title, source, stage)]
    return len(hit), hit


def build_set(size: int, profile: str = "stratified") -> dict:
    """Freeze a deterministic, stratified sample of real published items.

    Stratified by `stage` and `area` so the set is not dominated by whichever
    category happens to be most common, and sorted by id so two runs of this
    command on the same corpus produce the same set.
    """
    items = load_json(PUBLISHED_PATH, [])
    if not isinstance(items, list) or not items:
        raise SystemExit(f"ERROR: no published items at {PUBLISHED_PATH}")
    raw_list = load_json(RAW_PATH, [])
    raw_by_id = {r.get("id"): r for r in raw_list if isinstance(r, dict) and r.get("id")}

    buckets: dict[tuple[str, str], list[dict]] = {}
    for item in sorted(items, key=lambda i: i.get("id") or ""):
        if not (item.get("title_ja") or "").strip():
            continue
        buckets.setdefault((item.get("stage", ""), item.get("area", "")), []).append(item)

    if profile == "hard":
        # Deliberately adversarial: the cases where a cheaper model is most likely
        # to overstate legal status, drop a statute name, or invent a date. A
        # stratified sample is mostly easy items and can pass while these fail.
        scored = []
        for item in sorted(items, key=lambda i: i.get("id") or ""):
            if not (item.get("title_ja") or "").strip():
                continue
            score, signals = hard_case_score(item, raw_by_id.get(item.get("id"), {}))
            if score:
                scored.append((-score, item.get("id") or "", item, signals))
        ranked = sorted(scored, key=lambda row: (row[0], row[1]))[:size]
        selected = [row[2] for row in ranked]
        signals_by_id = {row[2].get("id"): row[3] for row in ranked}
        return {
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "profile": profile,
            "prompt_fingerprint": summarizer.SYSTEM_PROMPT[:64],
            "items": [
                {
                    "id": item.get("id"),
                    "source": summarizer.source_payload(item, raw_by_id.get(item.get("id"), {})),
                    "stage": item.get("stage", ""),
                    "area": item.get("area", ""),
                    "hard_signals": signals_by_id.get(item.get("id"), []),
                }
                for item in selected
            ],
        }

    # Two-level stratification. A plain round-robin over sorted (stage, area)
    # keys never gets past depth 0 when there are more buckets than slots, so the
    # sample ends up alphabetically truncated — every item from the first stages
    # and none from the rest. Cover each distinct STAGE first (the label that most
    # changes how an item must be summarized), then fill the remainder from the
    # largest buckets so the set reflects what the corpus actually contains.
    selected: list[dict] = []
    taken: set[str] = set()

    def take(item: dict) -> None:
        item_id = item.get("id") or ""
        if item_id not in taken:
            taken.add(item_id)
            selected.append(item)

    by_stage: dict[str, list[dict]] = {}
    for (stage, _area), bucket in buckets.items():
        by_stage.setdefault(stage, []).extend(bucket)
    for stage in sorted(by_stage):
        if len(selected) >= size:
            break
        take(sorted(by_stage[stage], key=lambda i: i.get("id") or "")[0])

    ordered = [buckets[key] for key in sorted(buckets, key=lambda k: (-len(buckets[k]), k))]
    depth = 0
    while len(selected) < size and any(len(b) > depth for b in ordered):
        for bucket in ordered:
            if len(selected) >= size:
                break
            if len(bucket) > depth:
                take(bucket[depth])
        depth += 1
    selected = selected[:size]

    frozen = {
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "profile": profile,
        "prompt_fingerprint": summarizer.SYSTEM_PROMPT[:64],
        "items": [
            {
                # The exact model input is frozen, so a later run is comparable
                # even if the published corpus has moved on.
                "id": item.get("id"),
                "source": summarizer.source_payload(item, raw_by_id.get(item.get("id"), {})),
                "stage": item.get("stage", ""),
                "area": item.get("area", ""),
            }
            for item in selected
        ],
    }
    return frozen


# --------------------------------------------------------------------------- #
# 2. Automatic metrics
# --------------------------------------------------------------------------- #

_KANJI_NUMERAL_RE = re.compile(r"[〇一二三四五六七八九十百千]+")
kanji_numeral_value = summarizer.kanji_numeral_value


_ERA_BASE_YEAR = summarizer.ERA_BASE_YEAR
_ERA_YEAR_RE = re.compile(r"(明治|大正|昭和|平成|令和)(元|\d+|[〇一二三四五六七八九十百]+)")


def source_text(entry: dict) -> str:
    """Everything the model was shown that can legitimately supply a number.

    Statute and order numbers arrive as kanji numerals (令和七年政令第三百四十一号),
    and a correct English or Japanese summary writes them as 341. The official
    URL is also in the model's input and carries dates (…/20260728/…). Without
    both, faithful renderings were scored as unsupported numbers.
    """
    source = entry.get("source") or {}
    text = unicodedata.normalize("NFKC", " ".join(
        str(source.get(key, "")) for key in
        ("title_ja", "raw_summary", "published_at", "source_name", "source_url")
    ))
    values = [kanji_numeral_value(m) for m in _KANJI_NUMERAL_RE.findall(text)]
    # Era years become Gregorian ones in a faithful summary (平成五年 -> 1993,
    # 令和7年度 -> FY2025), so the converted year is supported by the source too.
    for era, number in _ERA_YEAR_RE.findall(text):
        year = 1 if number == "元" else (int(number) if number.isdigit() else kanji_numeral_value(number))
        if year is not None:
            values.append(_ERA_BASE_YEAR[era] + year)
    return text + " " + " ".join(str(v) for v in values if v is not None)


def unsupported_numbers(output_text: str, source: str) -> list[str]:
    """Digits asserted in the output that appear nowhere in the source.

    A conservative hallucination proxy: a summary should not introduce article
    numbers, dates, or thresholds the source text never mentions.
    """
    found = []
    # NFKC so a full-width ６ in a Japanese summary matches the source's 6.
    for number in _NUMBER_RE.findall(unicodedata.normalize("NFKC", output_text or "")):
        if number in _TRIVIAL_NUMBERS or number in source:
            continue
        found.append(number)
    return sorted(set(found))


# Metadata fields whose dates carry legal meaning. e-Gov's 更新一覧日 (the day
# the list was updated) and 案の公示日 are bookkeeping, not legal dates.
KEY_DATE_FIELDS = ("公布日", "施行日", "受付締切日時", "募集終了日", "募集期間")
_ISO_DATE_RE = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
_MONTHS = {m: i for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"), 1)}
_MDY_RE = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),?\s+(\d{4})", re.IGNORECASE)
_DMY_RE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r"),?\s+(\d{4})", re.IGNORECASE)
_FIELD_SPLIT_RE = re.compile(r"[;\n]|\s(?=[^\s:：]{2,8}[:：])")


def key_dates(entry: dict) -> list[str]:
    """ISO dates stated in legally meaningful metadata fields (closing date for a period)."""
    raw_summary = unicodedata.normalize("NFKC", (entry.get("source") or {}).get("raw_summary") or "")
    dates: list[str] = []
    for segment in _FIELD_SPLIT_RE.split(raw_summary):
        field = re.split(r"[:：]", segment, maxsplit=1)[0].strip()
        if field not in KEY_DATE_FIELDS:
            continue
        matches = _ISO_DATE_RE.findall(segment)
        if field == "募集期間" and matches:
            matches = matches[-1:]
        for y, m, d in matches:
            iso = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
            if iso not in dates:
                dates.append(iso)
    return dates


def dates_in_text(text: str) -> set[str]:
    """Every calendar date written in the output, in ISO form."""
    found = {f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in _ISO_DATE_RE.findall(text)}
    found |= {f"{int(y):04d}-{_MONTHS[mo.lower()]:02d}-{int(d):02d}" for mo, d, y in _MDY_RE.findall(text)}
    found |= {f"{int(y):04d}-{_MONTHS[mo.lower()]:02d}-{int(d):02d}" for d, mo, y in _DMY_RE.findall(text)}
    return found


def identifier_in_text(number: int, text: str) -> bool:
    """True when the output cites the instrument number ('No. 341' or similar)."""
    return re.search(rf"(?:No\.?|Number|#)\s*{number}(?!\d)", text, re.IGNORECASE) is not None


def entry_item_raw(entry: dict) -> tuple[dict, dict]:
    """Rebuild the production (item, raw) pair from a frozen entry.

    The frozen `source` payload IS the production prompt input, so every model
    sees byte-identical prompts.
    """
    source = entry.get("source") or {}
    item = {"id": entry.get("id"), **source}
    labels = source.get("preliminary_rule_based_labels") or {}
    for key in ("area", "stage", "impact_level"):
        item.setdefault(key, labels.get(key, ""))
    raw = {"raw_summary": source.get("raw_summary", ""), "source_type": source.get("source_type", "")}
    return item, raw


def score_english(result: dict, entry: dict, model_confidence: str | None = None) -> dict:
    """Automatic, checkable quality signals for one English result.

    `model_confidence` is what the model said before calibrate_confidence()
    capped it; `confidence` is what production would publish.
    """
    schema_ok = summarizer.valid_result(result)
    text_fields = {k: str(result.get(k, "")) for k in summarizer.AI_TEXT_FIELDS}
    joined = " ".join(text_fields.values())
    probe = dict(result)
    probe["summary_source"] = "claude"
    item, raw = entry_item_raw(entry)
    evidence = summarizer.source_evidence(item, raw)
    identifiers = summarizer.legal_identifiers(item, raw)
    body = " ".join(text_fields[k] for k in ("summary_en", "business_impact_en", "recommended_action_en"))
    primary = next((i for i in identifiers if i["role"] == "amending_instrument"),
                   identifiers[0] if identifiers else None)
    expected_dates = key_dates(entry)
    output_dates = dates_in_text(joined)
    model_confidence = model_confidence or result.get("confidence")
    ceiling = summarizer.MAX_CONFIDENCE_BY_EVIDENCE[evidence]
    return {
        "schema_compliant": schema_ok,
        "title_has_japanese": summarizer.public_data.contains_japanese(text_fields["title_en"]),
        "title_over_cap": len(text_fields["title_en"]) > summarizer.public_data.TITLE_MAX_CHARS,
        "caution_violations": summarizer.caution_phrases_in_item(probe),
        "unsupported_numbers": unsupported_numbers(joined, source_text(entry)),
        "confidence": result.get("confidence"),
        "model_confidence": model_confidence,
        "source_evidence": evidence,
        "model_overconfident": bool(
            model_confidence in summarizer.CONFIDENCE_RANK
            and summarizer.CONFIDENCE_RANK[model_confidence] > summarizer.CONFIDENCE_RANK[ceiling]
        ),
        "identifiers_expected": len(identifiers),
        "identifiers_kept": sum(1 for i in identifiers if identifier_in_text(i["number"], joined)),
        "primary_identifier_expected": primary is not None,
        "primary_identifier_kept": primary is not None and identifier_in_text(primary["number"], body),
        "key_dates_expected": len(expected_dates),
        "key_dates_kept": sum(1 for d in expected_dates if d in output_dates),
        "lengths": {k: len(v) for k, v in text_fields.items()},
    }


def score_japanese(result: dict, entry: dict) -> dict:
    valid = summarizer.valid_japanese_result(result)
    joined = " ".join(str(result.get(k, "")) for k in summarizer.JA_AI_FIELDS)
    # A Japanese summary that contains no Japanese script is a failure mode worth
    # catching automatically; so is one that silently answers in English.
    has_japanese = summarizer.public_data.contains_japanese(joined)
    return {
        "schema_compliant": valid,
        "contains_japanese": has_japanese,
        "unsupported_numbers": unsupported_numbers(joined, source_text(entry)),
        "lengths": {k: len(str(result.get(k, ""))) for k in summarizer.JA_AI_FIELDS},
    }


# --------------------------------------------------------------------------- #
# 3. Run one model over the frozen set
# --------------------------------------------------------------------------- #

def projected_cost_usd(model: str, count: int, language: str) -> float | None:
    """Conservative worst case: measured-size input plus the full max_tokens out."""
    price = summarizer.model_pricing(model)
    if price is None:
        return None
    approx_input = 900 if language == "japanese" else 800
    # An OpenAI reasoning model may spend its reasoning headroom on top of the
    # visible ceiling, and it bills as output, so the worst case includes it.
    ceiling = (openai_provider.max_output_tokens(summarizer.MAX_TOKENS)
               if openai_provider.is_openai_model(model) else summarizer.MAX_TOKENS)
    return count * (approx_input * price["input"] + ceiling * price["output"]) / 1e6


def results_path(language: str, profile: str, model: str) -> Path:
    """One path rule for `run` and `compare`, so a hard-profile run can be compared."""
    suffix = "" if profile == "stratified" else f"_{profile}"
    return EVAL_DIR / f"results_{language}{suffix}_{model}.json"


def eval_set_path(profile: str) -> Path:
    return EVAL_SET_PATH if profile == "stratified" else EVAL_SET_PATH.with_name(f"summary_eval_set_{profile}.json")


def rescore_english(run: dict, profile: str) -> dict:
    """Recompute English scores from the stored outputs with the current metrics.

    A reused baseline (or a run made before a metric existed) is then scored on
    exactly the same rules as the fresh candidate. The confidence the model
    originally returned is kept when the run recorded it.
    """
    frozen = load_json(eval_set_path(profile), None)
    if run.get("language") != "english" or not isinstance(frozen, dict):
        return run
    entries = {e["id"]: e for e in frozen.get("items", [])}
    for record in run.get("records", []):
        entry, output = entries.get(record.get("id")), record.get("output")
        if entry is None or not isinstance(output, dict):
            continue
        previous = record.get("scores") or {}
        model_confidence = previous.get("model_confidence") or output.get("confidence")
        published = output
        if summarizer.valid_result(output):
            item, raw = entry_item_raw(entry)
            published = summarizer.calibrate_confidence(output, item, raw)
        record["scores"] = score_english(published, entry, model_confidence)
    return run


def run_model(args) -> int:
    frozen = load_json(eval_set_path(getattr(args, "profile", "stratified")), None)
    if not isinstance(frozen, dict) or not frozen.get("items"):
        raise SystemExit("ERROR: no evaluation set. Run `build-set` first.")
    entries = frozen["items"][: max(0, args.limit)]
    if summarizer.model_pricing(args.model) is None:
        raise SystemExit(f"ERROR: no configured pricing for {args.model}; refusing to run uncapped.")

    projected = projected_cost_usd(args.model, len(entries), args.language)
    print(f"model            : {args.model}")
    print(f"language         : {args.language}")
    print(f"items            : {len(entries)}")
    print(f"projected max USD: {projected:.4f}  (worst case: full max_tokens on every item)")
    print(f"max_cost_usd     : {args.max_cost_usd}")
    if not args.confirm_spend:
        print("\nNothing was called. Re-run with --confirm-spend to make real API calls.")
        return 0

    client = summarizer.provider_client(args.model)
    usage_totals = summarizer.message_usage(None)
    spent = 0.0
    records = []
    for index, entry in enumerate(entries, 1):
        if spent >= args.max_cost_usd:
            print(f"cost cap reached after {index - 1} items; stopping.")
            break
        item, raw = entry_item_raw(entry)
        record = {"id": entry["id"], "stage": entry.get("stage"), "area": entry.get("area")}
        try:
            if args.language == "japanese":
                result, model_used, usage = summarizer.unpack_api_outcome(
                    summarizer.request_japanese_summary(client, args.model, item, raw)
                )
                record["scores"] = score_japanese(result, entry)
            else:
                result, model_used, usage = summarizer.unpack_api_outcome(
                    summarizer.request_summary(client, args.model, item, raw)
                )
                # Score what production would publish: the same cap runs there.
                model_confidence = result.get("confidence")
                if summarizer.valid_result(result):
                    result = summarizer.calibrate_confidence(result, item, raw)
                record["scores"] = score_english(result, entry, model_confidence)
            record["output"] = result
            record["model"] = model_used
            record["usage"] = usage
            summarizer.add_usage(usage_totals, usage)
            estimate = summarizer.estimate_usage_cost_usd(usage, model_used)
            if estimate is not None:
                spent += estimate
            record["estimated_cost_usd"] = estimate
            print(f"[{index}/{len(entries)}] {entry['id']} ok  spent=${spent:.4f}")
        except Exception as exc:  # never log the provider body or source text
            record["error_type"] = summarizer.classify_provider_error(exc)
            record["scores"] = {"schema_compliant": False}
            print(f"[{index}/{len(entries)}] {entry['id']} FAILED type={record['error_type']}")
            if record["error_type"] in summarizer.FATAL_PROVIDER_ERRORS:
                records.append(record)
                print("provider unavailable; stopping.")
                break
        records.append(record)

    out = {
        "model": args.model,
        "language": args.language,
        # Recorded so a comparison shows whether thinking tokens were in play:
        # the same prompt costs very different amounts with and without them.
        "thinking": (
            {"openai_reasoning_effort": openai_provider.reasoning_effort()}
            if openai_provider.is_openai_model(args.model)
            else summarizer.lowest_thinking_config(args.model)
        ),
        "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "usage_totals": usage_totals,
        "estimated_cost_usd": spent,
        "records": records,
    }
    path = results_path(args.language, getattr(args, "profile", "stratified"), args.model)
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
    ok = [s for s in scored if s.get("schema_compliant")]
    usages = [r.get("usage") or {} for r in records if r.get("usage")]
    costs = [r["estimated_cost_usd"] for r in records if r.get("estimated_cost_usd") is not None]

    def rate(pred):
        return 100.0 * sum(1 for s in scored if pred(s)) / n

    agg = {
        "items": len(records),
        "schema_compliance_pct": 100.0 * len(ok) / n,
        "validation_failure_pct": 100.0 * (n - len(ok)) / n,
        "guardrail_violation_pct": rate(lambda s: bool(s.get("caution_violations"))),
        "unsupported_number_pct": rate(lambda s: bool(s.get("unsupported_numbers"))),
        "title_japanese_pct": rate(lambda s: s.get("title_has_japanese")),
        "title_over_cap_pct": rate(lambda s: s.get("title_over_cap")),
        "missing_japanese_pct": rate(lambda s: s.get("contains_japanese") is False),
        "refusal_pct": 100.0 * sum(1 for r in records if r.get("error_type") == "refusal") / n,
        "mean_input_tokens": statistics.mean([u.get("input_tokens", 0) for u in usages]) if usages else 0,
        "mean_output_tokens": statistics.mean([u.get("output_tokens", 0) for u in usages]) if usages else 0,
        # Part of mean_output_tokens, not in addition to it.
        "mean_reasoning_tokens": statistics.mean([u.get("reasoning_tokens", 0) for u in usages]) if usages else 0,
        "mean_cost_usd": statistics.mean(costs) if costs else 0.0,
        "total_cost_usd": run.get("estimated_cost_usd", 0.0),
        "confidence": {},
        "model_confidence": {},
    }
    for level in ("high", "medium", "low"):
        agg["confidence"][level] = sum(1 for s in scored if s.get("confidence") == level)
        agg["model_confidence"][level] = sum(1 for s in scored if s.get("model_confidence") == level)

    def pct(kept: str, expected: str) -> float | None:
        total = sum(int(s.get(expected) or 0) for s in scored)
        return None if not total else 100.0 * sum(int(s.get(kept) or 0) for s in scored) / total

    agg["identifiers_kept_pct"] = pct("identifiers_kept", "identifiers_expected")
    agg["primary_identifier_kept_pct"] = pct("primary_identifier_kept", "primary_identifier_expected")
    agg["key_dates_kept_pct"] = pct("key_dates_kept", "key_dates_expected")
    agg["model_overconfident_pct"] = rate(lambda s: s.get("model_overconfident"))
    lengths = [s.get("lengths") or {} for s in ok]
    for field in ("summary_en", "business_impact_en", "recommended_action_en"):
        values = [l[field] for l in lengths if field in l]
        agg[f"mean_len_{field}"] = statistics.mean(values) if values else None
    return agg


def compare(args) -> int:
    profile = getattr(args, "profile", "stratified")
    base_path = results_path(args.language, profile, args.baseline)
    cand_path = results_path(args.language, profile, args.candidate)
    base, cand = load_json(base_path, None), load_json(cand_path, None)
    for path, run in ((base_path, base), (cand_path, cand)):
        if not isinstance(run, dict):
            raise SystemExit(f"ERROR: missing run file {path}. Run `run` for that model first.")

    a, b = aggregate(rescore_english(base, profile)), aggregate(rescore_english(cand, profile))
    rows = [
        ("items evaluated", "items", "{:.0f}"),
        ("schema compliance %", "schema_compliance_pct", "{:.1f}"),
        ("validation failure %", "validation_failure_pct", "{:.1f}"),
        ("guardrail violation %", "guardrail_violation_pct", "{:.1f}"),
        ("unsupported-number %", "unsupported_number_pct", "{:.1f}"),
        ("title has Japanese %", "title_japanese_pct", "{:.1f}"),
        ("title over cap %", "title_over_cap_pct", "{:.1f}"),
        ("missing Japanese %", "missing_japanese_pct", "{:.1f}"),
        ("refusal %", "refusal_pct", "{:.1f}"),
        ("law numbers kept %", "identifiers_kept_pct", "{:.1f}"),
        ("main law number kept %", "primary_identifier_kept_pct", "{:.1f}"),
        ("key dates kept %", "key_dates_kept_pct", "{:.1f}"),
        ("overconfident (pre-cap) %", "model_overconfident_pct", "{:.1f}"),
        ("mean len summary", "mean_len_summary_en", "{:.0f}"),
        ("mean len business impact", "mean_len_business_impact_en", "{:.0f}"),
        ("mean len recommended act.", "mean_len_recommended_action_en", "{:.0f}"),
        ("mean input tokens", "mean_input_tokens", "{:.0f}"),
        ("mean output tokens", "mean_output_tokens", "{:.0f}"),
        ("  of which reasoning", "mean_reasoning_tokens", "{:.0f}"),
        ("mean USD / item", "mean_cost_usd", "{:.5f}"),
        ("total USD", "total_cost_usd", "{:.4f}"),
    ]
    print(f"\n{'metric':26s} {args.baseline:>22s} {args.candidate:>22s}")
    print("-" * 74)
    def cell(value, fmt):
        return "n/a" if value is None else fmt.format(value)

    for label, key, fmt in rows:
        if a.get(key) is None and b.get(key) is None:
            continue  # metric does not apply to this language
        print(f"{label:26s} {cell(a.get(key), fmt):>22s} {cell(b.get(key), fmt):>22s}")
    print(f"{'confidence high/med/low':26s} "
          f"{'/'.join(str(a['confidence'][k]) for k in ('high','medium','low')):>22s} "
          f"{'/'.join(str(b['confidence'][k]) for k in ('high','medium','low')):>22s}")
    if any(a["model_confidence"].values()) or any(b["model_confidence"].values()):
        print(f"{'  as returned (pre-cap)':26s} "
              f"{'/'.join(str(a['model_confidence'][k]) for k in ('high','medium','low')):>22s} "
              f"{'/'.join(str(b['model_confidence'][k]) for k in ('high','medium','low')):>22s}")

    if a["mean_cost_usd"]:
        print(f"\ncost per item: {100 * b['mean_cost_usd'] / a['mean_cost_usd']:.1f}% of baseline")
        if b["mean_cost_usd"] >= a["mean_cost_usd"]:
            # Not a quality blocker, but a switch made to save money has no case.
            print("  NOTE: the candidate is not cheaper per item than the baseline.")

    blockers = []
    if b["schema_compliance_pct"] < a["schema_compliance_pct"]:
        blockers.append("schema compliance regressed")
    if b["guardrail_violation_pct"] > a["guardrail_violation_pct"]:
        blockers.append("more definitive / legal-advice wording")
    if b["unsupported_number_pct"] > a["unsupported_number_pct"]:
        blockers.append("more unsupported numbers (possible hallucination)")
    if b["title_japanese_pct"] > a["title_japanese_pct"]:
        blockers.append("more Japanese characters in title_en")
    if b["missing_japanese_pct"] > a["missing_japanese_pct"]:
        blockers.append("more Japanese summaries missing Japanese script")
    if b["refusal_pct"] > a["refusal_pct"]:
        blockers.append("more safety refusals (items fall back to rule-based text)")
    for key, label in (("identifiers_kept_pct", "drops more law numbers"),
                       ("primary_identifier_kept_pct", "drops the main law number more often"),
                       ("key_dates_kept_pct", "drops more promulgation / effective / deadline dates")):
        if a.get(key) is not None and b.get(key) is not None and b[key] < a[key]:
            blockers.append(label)

    print("\nautomatic verdict:")
    if blockers:
        for blocker in blockers:
            print(f"  BLOCKER: {blocker}")
        print("  -> do NOT switch the production model.")
    else:
        print("  no automatic regression detected.")
    print("  Automatic metrics cannot judge factual accuracy, faithfulness to the")
    print("  Japanese original, Japanese writing quality, or how practically useful")
    print("  the business-impact and recommended-action sentences are.")
    print("  Read the side-by-side file below before deciding.")

    lines = [f"# {args.baseline} vs {args.candidate} ({args.language})", ""]
    base_by_id = {r["id"]: r for r in base.get("records", [])}
    for record in cand.get("records", []):
        other = base_by_id.get(record["id"])
        if not other:
            continue
        lines += [f"## {record['id']} — {record.get('stage','')} / {record.get('area','')}", ""]
        for label, run_record in ((args.baseline, other), (args.candidate, record)):
            lines.append(f"**{label}**")
            lines.append("")
            for key, value in (run_record.get("output") or {}).items():
                lines.append(f"- `{key}`: {value}")
            lines.append("")
    suffix = "" if profile == "stratified" else f"_{profile}"
    side_by_side = EVAL_DIR / f"side_by_side_{args.language}{suffix}_{args.baseline}_vs_{args.candidate}.md"
    side_by_side.parent.mkdir(parents=True, exist_ok=True)
    side_by_side.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nside-by-side for human review: {side_by_side}")
    return 0


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
    p_build.add_argument(
        "--profile", choices=("stratified", "hard"), default="stratified",
        help=(
            "stratified: broad coverage of every stage and area (first pass). "
            "hard: only items that trip difficulty signals — thin source text, "
            "amendment-vs-enactment wording, effective dates and deadlines, long "
            "statute names, ordinance/ministerial-order distinctions, public "
            "comments, and statuses that are easy to overstate."
        ),
    )

    p_run = sub.add_parser("run", help="Run one model over the frozen set (calls the API).")
    p_run.add_argument("--model", required=True)
    p_run.add_argument("--language", choices=("english", "japanese"), default="english")
    p_run.add_argument("--limit", type=int, default=DEFAULT_SET_SIZE)
    p_run.add_argument("--max-cost-usd", type=float, default=DEFAULT_MAX_COST_USD)
    p_run.add_argument("--confirm-spend", action="store_true",
                       help="Required. Without it the command only prints the projected cost.")
    p_run.add_argument("--profile", choices=("stratified", "hard"), default="stratified")

    p_cmp = sub.add_parser("compare", help="Compare two completed runs (no API calls).")
    p_cmp.add_argument("--baseline", required=True)
    p_cmp.add_argument("--candidate", required=True)
    p_cmp.add_argument("--language", choices=("english", "japanese"), default="english")
    p_cmp.add_argument("--profile", choices=("stratified", "hard"), default="stratified")

    args = parser.parse_args(argv)
    if args.command == "build-set":
        frozen = build_set(args.size, args.profile)
        path = EVAL_SET_PATH if args.profile == "stratified" else EVAL_SET_PATH.with_name(
            f"summary_eval_set_{args.profile}.json"
        )
        save_json(path, frozen)
        stages = sorted({i["stage"] for i in frozen["items"]})
        print(f"froze {len(frozen['items'])} items ({args.profile}) -> {path}")
        print(f"stages covered: {', '.join(s or '(none)' for s in stages)}")
        if args.profile == "hard":
            from collections import Counter
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
