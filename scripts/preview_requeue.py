#!/usr/bin/env python3
"""
preview_requeue.py — Japan Legal Reform Watch by LegalOS

Read-only preview of a data/requeue.json request, run BEFORE production data is
regenerated. It calls the same production functions the daily workflow would
(summarize_updates.request_summary / request_title, translate_updates field
memoization + request_translation) for a deterministic sample of the listed
items, and writes old-vs-new English and Chinese side by side under data/eval/.
It never writes the published file, a cache, or the request itself.

What it lets a reviewer check, per item:
- full regeneration: amending vs amended law numbers (legal_identifiers roles),
  official office names, every score the model evaluation uses;
- title-only regeneration: the body is not requested at all, the new title is
  complete and agrees with the unchanged summary;
- Chinese: which fields the field cache would still serve (a title-only change
  must request the title alone), and that the new Chinese title passes the gate.

    python scripts/preview_requeue.py --full 20 --titles 30 --confirm-spend
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import evaluate_summary_model as ev
import requeue
import summarize_updates as su
import translate_updates as tu

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_JSON = REPO_ROOT / "data" / "eval" / "requeue_preview.json"
OUT_MD = REPO_ROOT / "data" / "eval" / "requeue_preview.md"
LOCALE = "zh-Hans"


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def pick(ids: list[str], n: int, prefer: set[str]) -> list[str]:
    """Deterministic sample: every preferred id first, then the rest in id order."""
    first = [i for i in sorted(ids) if i in prefer]
    rest = [i for i in sorted(ids) if i not in prefer]
    return (first + rest)[: max(0, n)] if n < len(ids) else sorted(ids)


def zh_preview(new_item: dict, field_entries: dict, client, model: str) -> dict:
    """What production Stage 4 would request and publish for this English."""
    known, missing, _keys = tu.resolve_cached_fields(new_item, field_entries, LOCALE)
    out = {"requested_fields": list(missing)}
    if not missing:
        fields = tu.normalized_fields(known)
    else:
        result, _model, usage = tu.request_translation(client, model, new_item, LOCALE, tuple(missing))
        out["usage"] = usage
        fields = tu.normalized_fields(tu.merge_response_fields(known, result, tuple(missing)))
    out["fields"] = fields
    out["title_gate"] = tu.title_quality_errors(fields.get("title"))
    return out


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Read-only preview of data/requeue.json")
    parser.add_argument("--full", type=int, default=20, help="full English regenerations to preview")
    parser.add_argument("--titles", type=int, default=30, help="title-only regenerations to preview")
    parser.add_argument("--confirm-spend", action="store_true")
    args = parser.parse_args(argv)

    request = load(requeue.REQUEUE_PATH, {})
    published = {i["id"]: i for i in load(su.INPUT_PATH, [])}
    raw_by_id = {r.get("id"): r for r in load(su.RAW_PATH, []) if isinstance(r, dict)}
    tcache = load(tu.CACHE_PATH, {})
    field_entries = (tcache.get("fields") or {}).get(LOCALE, {})

    full_ids = [i for i in request.get("english", []) if i in published]
    title_ids = [i for i in request.get("english_title", []) if i in published]
    # Prefer the items whose defect this request exists for: named offices and
    # e-Gov law entries that carry both an amending and an amended number.
    office_items = {i for i in full_ids if su.official_office_names(published[i], raw_by_id.get(i, {}))
                    or "生活衛生局" in (raw_by_id.get(i, {}).get("raw_summary") or "")
                    or "医薬" in (raw_by_id.get(i, {}).get("raw_summary") or "")}
    two_numbers = {i for i in full_ids if len({x["role"] for x in su.legal_identifiers(
        published[i], raw_by_id.get(i, {}))}) >= 2}
    full_sample = pick(full_ids, args.full, office_items | set(sorted(two_numbers)[:10]))
    title_sample = pick(title_ids, args.titles, set())

    sum_model = su.resolve_model(None)
    tr_model = tu.resolve_model(None)
    print(f"summary model {sum_model} | translation model {tr_model}")
    print(f"requeue: english {len(full_ids)}, english_title {len(title_ids)}, zh-Hans {len(request.get('zh-Hans', []))}")
    print(f"preview sample: full {len(full_sample)}, titles {len(title_sample)}")
    if not args.confirm_spend:
        print("Nothing was called. Re-run with --confirm-spend.")
        return 0

    sclient = su.provider_client(sum_model)
    tclient = tu.provider_client(tr_model)
    usage_en = su.message_usage(None)
    usage_zh = su.message_usage(None)
    records = []
    for kind, ids in (("full", full_sample), ("title", title_sample)):
        for item_id in ids:
            old = published[item_id]
            raw = raw_by_id.get(item_id, {})
            rec = {"id": item_id, "kind": kind, "source_name": old.get("source_name"),
                   "published_at": old.get("published_at"), "stage": old.get("stage"),
                   "title_ja": old.get("title_ja"), "raw_summary": raw.get("raw_summary", ""),
                   "legal_identifiers": su.legal_identifiers(old, raw),
                   "official_office_names": su.official_office_names(old, raw),
                   "old": {k: old.get(k) for k in su.AI_TEXT_FIELDS},
                   "old_zh": (old.get("translations") or {}).get(LOCALE)}
            try:
                if kind == "full":
                    result, _m, usage = su.unpack_api_outcome(su.request_summary(sclient, sum_model, old, raw))
                    if not su.valid_result(result):
                        raise ValueError("invalid summary")
                    result = su.calibrate_confidence(result, old, raw)
                    new_item = {**old, **{k: result[k] for k in su.AI_FIELDS}}
                    new_item["title_en"] = su.public_data.shorten_title(result["title_en"].strip())
                    entry = {"id": item_id, "source": su.source_payload(old, raw)}
                    rec["scores"] = ev.score_english(result, entry)
                else:
                    result, _m, usage = su.unpack_api_outcome(su.request_title(sclient, sum_model, old, raw))
                    rec["title_valid"] = su.valid_title_result(result)
                    new_item = {**old, "title_en": (result.get("title_en") or "").strip()}
                su.add_usage(usage_en, usage)
                rec["new"] = {k: new_item.get(k) for k in su.AI_TEXT_FIELDS}
                rec["body_unchanged"] = all(new_item.get(k) == old.get(k) for k in
                                            ("summary_en", "business_impact_en", "recommended_action_en"))
                zh = zh_preview(new_item, field_entries, tclient, tr_model)
                if zh.get("usage"):
                    su.add_usage(usage_zh, zh.pop("usage"))
                rec["new_zh"] = zh
            except Exception as exc:  # never log provider bodies
                rec["error_type"] = su.classify_provider_error(exc)
            records.append(rec)
            print(f"{kind:5s} {item_id} {'ERR ' + rec['error_type'] if 'error_type' in rec else 'ok'}")

    cost_en = su.estimate_usage_cost_usd(usage_en, sum_model) or 0.0
    cost_zh = su.estimate_usage_cost_usd(usage_zh, tr_model) or 0.0
    summary = {
        "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "models": {"summary": sum_model, "translation": tr_model},
        "service_tier": os.environ.get("OPENAI_SERVICE_TIER", ""),
        "usage_en": usage_en, "usage_zh": usage_zh,
        "cost_en_usd": cost_en, "cost_zh_usd": cost_zh,
        "records": records,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# requeue preview", ""]
    for r in records:
        lines += [f"## {r['id']} ({r['kind']}) — {r['source_name']} — {r['published_at']} — {r['stage']}",
                  f"- title_ja: {r['title_ja']}", f"- raw: {r['raw_summary'][:300]}",
                  f"- identifiers: {[(i['instrument'], i['number'], i['gregorian_year'], i['role']) for i in r['legal_identifiers']]}",
                  f"- offices: {r['official_office_names']}"]
        if "error_type" in r:
            lines += [f"- ERROR {r['error_type']}", ""]
            continue
        for k in su.AI_TEXT_FIELDS:
            if r["old"].get(k) != r["new"].get(k):
                lines += [f"- OLD {k}: {r['old'].get(k)}", f"- NEW {k}: {r['new'].get(k)}"]
        zh = r["new_zh"]
        lines += [f"- zh requested fields: {zh['requested_fields']} | title gate: {zh['title_gate'] or 'pass'}",
                  f"- OLD zh title: {(r['old_zh'] or {}).get('title')}",
                  f"- NEW zh title: {zh['fields'].get('title')}"]
        if r["kind"] == "full":
            lines.append(f"- NEW zh summary: {zh['fields'].get('summary')}")
        lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"cost: English ${cost_en:.4f}  Chinese ${cost_zh:.4f}  (standard rate; Flex halves a confirmed call)")
    print(f"wrote {OUT_JSON.name} and {OUT_MD.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
