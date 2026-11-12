#!/usr/bin/env python3
"""Build small per-queue official-record packets for research agents.

The canonical raw_items.json can be too large for connected file readers.
This script runs inside GitHub Actions, where the whole file is available,
and emits one bounded JSON file per queued dossier seed. It does NOT verify
the external sources, interpret law, draft an explainer or mark one reviewed.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping

from explainers import OfficialIndex, QUEUE_PATH, RAW_PATH, REPO_ROOT, _law_ids, dump, write_if_changed

OUTPUT_DIR = REPO_ROOT / "data" / "explainer_research"
RAW_ID_PATTERN = re.compile(r"^raw-[0-9a-f]{16}$")
SUMMARY_LIMIT = 1800
RECORD_KEYS = (
    "id", "source_name", "title_ja", "published_at", "first_seen_at",
    "stage_hint", "source_url", "raw_summary",
)


def packets(index: OfficialIndex, queue: Mapping[str, Any]) -> dict[str, dict]:
    """Return deterministic source-only packets keyed by the seed raw ID."""
    out: dict[str, dict] = {}
    for entry in queue.get("entries", []):
        if not isinstance(entry, dict):
            raise ValueError("Queue entry is not an object")
        ids = entry.get("item_ids") or []
        seed = entry.get("suggested_seed_item_id") or (ids[0] if ids else None)
        if not isinstance(seed, str) or not RAW_ID_PATTERN.fullmatch(seed):
            raise ValueError(f"Invalid or missing queue seed in {entry.get('queue_id')!r}")
        closure = index.closure([seed])
        if not closure:
            raise ValueError(f"Queue seed {seed} is missing from raw official records")
        expected_count = entry.get("record_count")
        if expected_count != len(closure):
            raise ValueError(
                f"Queue/official-record closure mismatch for {seed}: "
                f"queue={expected_count}, actual={len(closure)}"
            )
        fingerprint = index.fingerprint([seed])
        if entry.get("current_fingerprint") and entry["current_fingerprint"] != fingerprint:
            raise ValueError(f"Queue/source fingerprint mismatch for {seed}")
        records = []
        apis: set[str] = set()
        for item_id in closure:
            raw = index.raw_by_id[item_id]
            record = {key: raw.get(key) for key in RECORD_KEYS}
            summary = record.get("raw_summary")
            truncated = isinstance(summary, str) and len(summary) > SUMMARY_LIMIT
            if truncated:
                record["raw_summary"] = summary[:SUMMARY_LIMIT]
            record["raw_summary_truncated"] = truncated
            records.append(record)
            for law_id in _law_ids(raw.get("source_url")):
                apis.add(f"https://laws.e-gov.go.jp/api/2/law_revisions/{law_id}")
        packet = {
            "schema_version": 1,
            "notice": (
                "Repository-derived context ONLY, not independently verified. "
                "Reopen and read original government/Diet/e-Gov sources, including "
                "later results and enforcement instruments. A truncated raw_summary "
                "is never evidence that its omitted portion is unimportant."
            ),
            "queue_id": entry.get("queue_id"),
            "kind": entry.get("kind"),
            "priority": entry.get("priority"),
            "first_queued_on": entry.get("first_queued_on"),
            "seed_item_id": seed,
            "related_item_ids": closure,
            "record_count": len(closure),
            "source_fingerprint": fingerprint,
            "e_gov_law_api": sorted(apis),
            "records": records,
        }
        if seed in out:
            raise ValueError(f"Duplicate queue seed {seed}; do not overwrite a different dossier")
        out[seed] = packet
    return out


def write_packets(items: Mapping[str, dict], output: Path) -> tuple[int, int]:
    output.mkdir(parents=True, exist_ok=True)
    changed = 0
    for seed, packet in sorted(items.items()):
        changed += write_if_changed(output / f"{seed}.json", dump(packet))
    removed = 0
    for stale in output.glob("raw-*.json"):
        if stale.stem not in items:
            stale.unlink()
            removed += 1
    return changed, removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw", type=Path, default=RAW_PATH)
    parser.add_argument("--queue", type=Path, default=QUEUE_PATH)
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    queue = json.loads(args.queue.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not isinstance(queue, dict):
        raise ValueError("Unexpected raw-items or queue structure")
    index = OfficialIndex.build(raw)
    items: dict[str, dict] = {}
    invalid = 0
    # An inconsistent source for one candidate must not suppress all others.
    for entry in queue.get("entries", []):
        try:
            packet = packets(index, {"entries": [entry]})
            if any(seed in items for seed in packet):
                raise ValueError("Two queued dossiers share the same seed")
            items.update(packet)
        except (ValueError, KeyError, TypeError) as exc:
            invalid += 1
            print(f"::warning::Explainer research packet skipped: {exc}")
    changed, removed = write_packets(items, args.out)
    print(f"research_packets={len(items)} invalid={invalid} changed={changed} stale_removed={removed}")
    # Leave source-health gating and publication unchanged. Invalid records
    # must never produce plausible-looking research evidence.
    return 0 if items or not queue.get("entries") else 1


if __name__ == "__main__":
    raise SystemExit(main())
