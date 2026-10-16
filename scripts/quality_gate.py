#!/usr/bin/env python3
"""
quality_gate.py — Japan Legal Reform Watch by LegalOS

Publication policy for quality-audit findings. scripts/quality_audit.py finds
defects; this decides, per finding TYPE, what happens to the published item.
Nothing is ever unpublished as a whole: source attribution, the Japanese title,
the stage and the official link always stay. What can be withheld is the one AI
rendering that is both certainly wrong and legally misleading, and the reader
then sees the existing, truthful fallback (rule-based English preview, or the
English text in the Chinese view with its "translation unavailable" note).

Policy (certainty of the check x impact on a reader):

| finding              | certainty | impact | action                                   |
|----------------------|-----------|--------|------------------------------------------|
| en_law_number_year   | high      | high   | WITHHOLD English AI + Chinese; requeue   |
| zh_era               | high      | high   | WITHHOLD Chinese; requeue                |
| zh_truncated         | high      | high   | WITHHOLD Chinese; requeue                |
| zh_title_gate        | high      | medium | WITHHOLD Chinese; requeue                |
| zh_stage_prefix      | high      | medium | AUTO-CORRECT the prefix (deterministic)  |
| en_main_law_number   | high      | medium | keep (omission, not error); requeue      |
| en_case_code         | high      | medium | keep; requeue                            |
| en_wrong_bureau      | medium    | medium | keep (pattern list); requeue             |
| en_japanese          | high      | low    | keep; requeue                            |
| en_field_names       | high      | low    | keep; requeue                            |
| en_title_truncated   | high      | low    | keep; requeue title only                 |
| zh_kana_or_ja_forms  | medium    | low    | keep (sub-item markers can be faithful); requeue |
| zh_cache_mismatch    | medium    | medium | keep; requeue                            |
| zh_missing           | high      | low    | keep (already English fallback); requeue |
| pc_stage_results     | medium    | high   | keep; MANUAL (fix the Stage 2 rule)      |

Withholding is re-decided on every run from the current data, so it needs no
state: once a regenerated rendering passes, it is published again. Automatic
requeues are written to data/requeue_auto.json and capped per item
(MAX_AUTO_REQUEUES, counted in data/quality_state.json); an item that still
fails after that stays withheld/flagged for a person and costs nothing more.

Modes: `report` (default) changes nothing and prints what `enforce` would do;
`enforce` applies it. The daily workflow runs `report` until the owner
approves enforcement.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import quality_audit as qa
import requeue
import summarize_updates as su

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "data" / "quality_state.json"
LOCALE = "zh-Hans"
MAX_AUTO_REQUEUES = 2

WITHHOLD_EN = ("en_law_number_year",)
WITHHOLD_ZH = ("zh_era", "zh_truncated", "zh_title_gate")
AUTO_CORRECT = ("zh_stage_prefix",)
MANUAL = ("pc_stage_results",)
POLICY = {
    "en_law_number_year": ("high", "high", "withhold English AI + Chinese; requeue"),
    "zh_era": ("high", "high", "withhold Chinese; requeue"),
    "zh_truncated": ("high", "high", "withhold Chinese; requeue"),
    "zh_title_gate": ("high", "medium", "withhold Chinese; requeue"),
    "zh_stage_prefix": ("high", "medium", "auto-correct prefix"),
    "en_main_law_number": ("high", "medium", "keep; requeue"),
    "en_case_code": ("high", "medium", "keep; requeue"),
    "en_wrong_bureau": ("medium", "medium", "keep; requeue"),
    "en_japanese": ("high", "low", "keep; requeue"),
    "en_field_names": ("high", "low", "keep; requeue"),
    "en_title_truncated": ("high", "low", "keep; requeue title"),
    "zh_kana_or_ja_forms": ("medium", "low", "keep; requeue"),
    "zh_cache_mismatch": ("medium", "medium", "keep; requeue"),
    "zh_missing": ("high", "low", "keep; requeue"),
    "pc_stage_results": ("medium", "high", "keep; manual rule fix"),
}
assert set(POLICY) == set(qa.HIGH), "every HIGH check needs a publication policy"


def plan(found: dict, state: dict) -> dict:
    """Decide actions from audit findings and the per-item requeue attempt count."""
    attempts = (state or {}).get("attempts", {})
    withhold_en = sorted(set().union(*(found.get(c, []) for c in WITHHOLD_EN)))
    withhold_zh = sorted(set().union(*(found.get(c, []) for c in WITHHOLD_ZH)) - set(withhold_en))
    correct = sorted(set().union(*(found.get(c, []) for c in AUTO_CORRECT)))
    manual = sorted(set().union(*(found.get(c, []) for c in MANUAL)))
    requeue_sections: dict[str, set] = collections.defaultdict(set)
    parked = set()
    for check in qa.HIGH:
        section = qa.REMEDY.get(check, "")
        if check in MANUAL or check in AUTO_CORRECT or section not in requeue.SECTIONS:
            continue
        for item_id in found.get(check, []):
            if attempts.get(item_id, {}).get("count", 0) >= MAX_AUTO_REQUEUES:
                parked.add(item_id)
            else:
                requeue_sections[section].add(item_id)
    # An English regeneration re-translates the Chinese; a full one rewrites the title.
    english = requeue_sections.get("english", set())
    for section in ("zh-Hans", "english_title"):
        requeue_sections[section] -= english
    return {
        "withhold_en": withhold_en,
        "withhold_zh": withhold_zh,
        "auto_correct": correct,
        "manual": manual,
        "requeue": {k: sorted(v) for k, v in requeue_sections.items() if v},
        "parked": sorted(parked),
    }


def apply(items: list[dict], decisions: dict) -> dict:
    """Apply withholding / auto-correction to the published items in place."""
    by_id = {i.get("id"): i for i in items}
    counts = collections.Counter()
    for item_id in decisions["withhold_en"]:
        item = by_id.get(item_id)
        if item is None:
            continue
        su.restore_rule_based_english_preview(item)
        (item.get("translations") or {}).pop(LOCALE, None)
        if item.get("translations") == {}:
            item.pop("translations")
        counts["withheld_en"] += 1
    for item_id in decisions["withhold_zh"]:
        item = by_id.get(item_id)
        if item is None or LOCALE not in (item.get("translations") or {}):
            continue
        item["translations"].pop(LOCALE)
        if not item["translations"]:
            item.pop("translations")
        counts["withheld_zh"] += 1
    for item_id in decisions["auto_correct"]:
        item = by_id.get(item_id)
        block = (item or {}).get("translations", {}).get(LOCALE)
        if block:
            fixed = qa.tu.align_stage_prefix(block, item.get("stage") or "")
            if fixed is not block:
                item["translations"][LOCALE] = fixed
                counts["auto_corrected"] += 1
    return dict(counts)


def next_state(state: dict, decisions: dict, today: str) -> dict:
    """Count automatic requeues; forget items that no longer fail."""
    old = (state or {}).get("attempts", {})
    flagged = {i for ids in decisions["requeue"].values() for i in ids} | set(decisions["parked"])
    attempts = {}
    for item_id in sorted(flagged):
        entry = dict(old.get(item_id, {"count": 0}))
        if any(item_id in ids for ids in decisions["requeue"].values()):
            entry["count"] = entry.get("count", 0) + 1
            entry["last"] = today
        attempts[item_id] = entry
    return {"version": 1, "attempts": attempts}


def summary_text(found: dict, decisions: dict, mode: str, counts: dict | None) -> str:
    lines = [f"Publication policy ({mode} mode)", "",
             "| finding | certainty | impact | items | action |", "|---|---|---|---|---|"]
    for check in qa.HIGH:
        cert, impact, action = POLICY[check]
        lines.append(f"| {check} | {cert} | {impact} | {len(found.get(check, []))} | {action} |")
    verb = "applied" if mode == "enforce" else "would apply"
    lines += ["", f"{verb}: withhold English {len(decisions['withhold_en'])}, withhold Chinese "
              f"{len(decisions['withhold_zh'])}, auto-correct {len(decisions['auto_correct'])}, "
              f"requeue {sum(len(v) for v in decisions['requeue'].values())}, "
              f"parked after {MAX_AUTO_REQUEUES} attempts {len(decisions['parked'])}, "
              f"manual {len(decisions['manual'])}"]
    if counts:
        lines.append(f"changed: {counts}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Publication policy for quality-audit findings")
    parser.add_argument("--mode", choices=("report", "enforce"), default="report")
    args = parser.parse_args(argv)

    items = json.loads(qa.PUBLISHED_PATH.read_text(encoding="utf-8"))
    raw_by_id = {r.get("id"): r for r in json.loads(qa.RAW_PATH.read_text(encoding="utf-8")) if isinstance(r, dict)}
    cache = json.loads(qa.CACHE_PATH.read_text(encoding="utf-8")) if qa.CACHE_PATH.exists() else {}
    result = qa.audit(items, raw_by_id, cache)
    text, _high = qa.report(result)
    print(text)
    for check in qa.HIGH:
        ids = result["found"].get(check, [])
        if ids:
            print(f"::warning::Quality audit {check}: {len(ids)} item(s), e.g. {', '.join(ids[:5])}")
    state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
    decisions = plan(result["found"], state)
    counts = None
    if args.mode == "enforce":
        counts = apply(items, decisions)
        su.save_json(qa.PUBLISHED_PATH, items)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        new_state = next_state(state, decisions, now[:10])
        su.save_json(STATE_PATH, new_state)
        auto = {"created_at": now, "reason": "quality_gate automatic requeue", **decisions["requeue"]}
        su.save_json(requeue.AUTO_REQUEUE_PATH, auto)
    print("")
    print(summary_text(result["found"], decisions, args.mode, counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
