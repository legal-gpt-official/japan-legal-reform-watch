#!/usr/bin/env python3
"""Read-only, reproducible 20-item quality comparison for routine legal summaries.

This is an evidence-coverage / text-change report, NOT verification of legal
accuracy. Source facts, dates, and legal conclusions still require semantic
verification against the linked Japanese official material.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ITEMS_PATH = ROOT / "docs" / "data" / "legal_updates.json"
RAW_PATH = ROOT / "data" / "raw_items.json"
EVIDENCE_PATH = ROOT / "data" / "official_source_evidence.json"
FIELDS = (
    "summary_en", "business_impact_en", "recommended_action_en",
    "summary_ja", "business_impact_ja", "recommended_action_ja",
)
DETAIL_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:％|%|円|日|月|年|条|号)|第[\d一二三四五六七八九十百千]+条")
GENERIC_RE = re.compile(
    r"具体的な(?:内容|改正|措置).*?(?:不明|記載されていません|確認できません)"
    r"|提供された(?:情報|資料).*?(?:不明|記載されていません|確認できません)"
    r"|公式(?:資料|情報|発表).*?(?:確認(?:する|して)|参照)"
)


def load(path: Path, fallback):
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def valid_documents(item: dict, raw: dict, evidence: dict) -> list[dict]:
    """Only evidence bound to the source URL AND source content hash is usable."""
    record = evidence.get(item.get("id")) if isinstance(evidence, dict) else None
    if (not isinstance(record, dict)
            or record.get("source_url") != raw.get("source_url")
            or record.get("raw_hash") != raw.get("raw_content_hash")):
        return []
    documents = record.get("documents") or []
    return [d for d in documents if isinstance(d, dict)
            and isinstance(d.get("excerpt"), str) and d.get("excerpt").strip()
            and isinstance(d.get("url"), str) and d["url"].startswith("https://")]


def choose(items: list[dict], raw_by_id: dict, evidence: dict, limit: int) -> list[dict]:
    """Prioritize source-backed examples; fill remaining slots with recent items."""
    eligible = [i for i in items if isinstance(i, dict) and i.get("id") in raw_by_id
                and str(i.get("id", "")).startswith("raw-")]
    def key(item):
        has_source = bool(valid_documents(item, raw_by_id[item["id"]], evidence))
        return (
            has_source,
            str(item.get("published_at") or item.get("first_seen_at") or ""),
            item.get("impact_level") == "High",
            str(item.get("id", "")),
        )
    return sorted(eligible, key=key, reverse=True)[:limit]


def view(item: dict) -> dict:
    return {name: str(item.get(name) or "") for name in FIELDS}


def signals(field_values: dict) -> dict:
    ja = " ".join(field_values.get(name, "") for name in FIELDS[3:])
    return {
        "ja_chars": len(ja.strip()),
        "ja_detail_tokens": len(DETAIL_RE.findall(ja)),
        "generic_phrases": len(GENERIC_RE.findall(ja)),
    }


def build_snapshot(items: list[dict], raw: dict, evidence: dict, limit: int) -> dict:
    chosen = choose(items, raw, evidence, limit)
    entries = []
    for item in chosen:
        docs = valid_documents(item, raw[item["id"]], evidence)
        entries.append({
            "id": item["id"],
            "title_ja": item.get("title_ja", ""),
            "source_url": item.get("source_url", ""),
            "published_at": item.get("published_at", ""),
            "source_name": item.get("source_name", ""),
            "official_excerpt_urls": [d["url"] for d in docs],
            "official_excerpt_chars": sum(len(d["excerpt"]) for d in docs),
            # Excerpt quotations in the artifact make source-by-source checking possible.
            "official_excerpt_sample": " ".join(d["excerpt"][:350] for d in docs)[:700],
            "before": view(item),
        })
    return {"captured_at": datetime.now(timezone.utc).isoformat(),
            "requested": limit, "selected": len(entries), "entries": entries}


def compare(snapshot: dict, after_items: list[dict]) -> dict:
    by_id = {i.get("id"): i for i in after_items if isinstance(i, dict)}
    records = []
    for row in snapshot["entries"]:
        post = by_id.get(row["id"])
        before = row["before"]
        after = view(post) if post else before
        changed_fields = [f for f in FIELDS if before[f] != after[f]]
        records.append({
            **row, "after": after,
            "changed_fields": changed_fields,
            "before_signals": signals(before), "after_signals": signals(after),
            "not_in_output": post is None,
        })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sampled": len(records),
        "with_official_excerpts": sum(bool(r["official_excerpt_urls"]) for r in records),
        "changed": sum(bool(r["changed_fields"]) for r in records),
        "unchanged": sum(not r["changed_fields"] for r in records),
        "records": records,
        "limitation": "Text length, changed fields and numeric tokens do NOT establish "
                      "legal accuracy or that substantive amendments were captured.",
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Japan Legal Reform Watch — 20-item summary review",
        "",
        f"Selected: {report['sampled']}; official-source excerpts available: "
        f"{report['with_official_excerpts']}; changed: {report['changed']}; "
        f"unchanged: {report['unchanged']}.",
        "",
        "**Caution:** Counts and length are diagnostic only. Compare each change with the "
        "Japanese primary source before treating any legal statement as verified.",
        "",
        "| ID | Source excerpt | EN/JA fields changed | JA chars before → after |",
        "|---|---:|---|---:|",
    ]
    for r in report["records"]:
        diff = ", ".join(r["changed_fields"]) or "none"
        lines.append(f"| {r['id']} | {r['official_excerpt_chars']} chars | {diff} | "
                     f"{r['before_signals']['ja_chars']} → {r['after_signals']['ja_chars']} |")
    for r in report["records"]:
        lines.extend([
            "", f"## {r['id']} — {r['title_ja']}",
            f"Official record: {r['source_url']}",
            f"Extracted source URLs: {', '.join(r['official_excerpt_urls']) or '(not available)'}",
            f"Source excerpt sample: {r['official_excerpt_sample'] or '(not available)'}",
        ])
        for key in ("summary_ja", "business_impact_ja", "recommended_action_ja",
                    "summary_en", "business_impact_en", "recommended_action_en"):
            lines.append(f"- **{key} before:** {r['before'][key]}")
            lines.append(f"- **{key} after:** {r['after'][key]}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", metavar="JSON", help="Capture before state after evidence extraction")
    group.add_argument("--report", metavar="JSON", help="Compare a snapshot to current published summaries")
    parser.add_argument("--markdown", metavar="MD", help="Markdown destination for --report")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be 1..100")
    items = load(ITEMS_PATH, [])
    if not isinstance(items, list):
        raise ValueError("published items must be a JSON list")
    if args.snapshot:
        raw = load(RAW_PATH, [])
        if not isinstance(raw, list):
            raise ValueError("raw items must be a JSON list")
        by_id = {r.get("id"): r for r in raw if isinstance(r, dict) and r.get("id")}
        snapshot = build_snapshot(items, by_id, load(EVIDENCE_PATH, {}), args.limit)
        destination = Path(args.snapshot)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        print(f"source-quality sample selected={snapshot['selected']} "
              f"enriched={sum(bool(i['official_excerpt_urls']) for i in snapshot['entries'])}")
    else:
        prior = load(Path(args.report), {})
        if not prior.get("entries"):
            raise ValueError("missing or empty before-state snapshot")
        output = compare(prior, items)
        destination = Path(args.report).with_name("summary_value_comparison.json")
        destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        if args.markdown:
            markdown_path = Path(args.markdown)
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render_markdown(output), encoding="utf-8")
        print(f"source-quality sample={output['sampled']} "
              f"source_backed={output['with_official_excerpts']} "
              f"changed={output['changed']} unchanged={output['unchanged']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
