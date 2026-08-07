#!/usr/bin/env python3
"""Build uncapped yearly browser datasets from the canonical public JSON.

`docs/data/legal_updates.json` remains the complete, relevance-ranked pipeline
artifact consumed by the summarization and translation stages. This script runs
after those stages and writes small, on-demand browser datasets:

* `docs/data/legal_updates_manifest.json`
* `docs/data/archive/<year>.json`
* `docs/data/archive/undated.json` (only when needed)

There is deliberately no per-year item limit. The manifest contains no run
timestamp, so an unchanged corpus produces no daily diff.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_INPUT_PATH = REPO_ROOT / "docs" / "data" / "legal_updates.json"
DEFAULT_MANIFEST_PATH = REPO_ROOT / "docs" / "data" / "legal_updates_manifest.json"
DEFAULT_ARCHIVE_DIR = REPO_ROOT / "docs" / "data" / "archive"

SCHEMA_VERSION = 1
UNDATED_PERIOD = "undated"
GENERATED_ARCHIVE_RE = re.compile(r"^(?:\d{4}|undated)\.json$")


def load_items(path: Path) -> list[dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"input not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"expected a JSON array in {path}")
    if any(not isinstance(item, dict) for item in data):
        raise ValueError(f"every published item must be an object: {path}")
    return data


def period_for_item(item: dict[str, Any]) -> str:
    value = item.get("published_at")
    if not isinstance(value, str) or len(value) < 10:
        return UNDATED_PERIOD
    date_text = value[:10]
    try:
        parsed = datetime.strptime(date_text, "%Y-%m-%d")
    except ValueError:
        return UNDATED_PERIOD
    return f"{parsed.year:04d}"


def build_archive_payload(
    items: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    shards: dict[str, list[dict[str, Any]]] = {}
    seen_ids: set[str] = set()
    for index, item in enumerate(items):
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            raise ValueError(f"published item {index} has no stable id")
        if item_id in seen_ids:
            raise ValueError(f"duplicate published item id: {item_id}")
        seen_ids.add(item_id)
        shards.setdefault(period_for_item(item), []).append(item)

    years = sorted((key for key in shards if key != UNDATED_PERIOD), reverse=True)
    ordered_periods = years + ([UNDATED_PERIOD] if UNDATED_PERIOD in shards else [])
    latest_period = years[0] if years else (UNDATED_PERIOD if shards else "")
    periods = [
        {
            "value": period,
            "label": "Undated" if period == UNDATED_PERIOD else period,
            "file": f"./data/archive/{period}.json",
            "count": len(shards[period]),
        }
        for period in ordered_periods
    ]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "total_items": len(items),
        "latest_period": latest_period,
        "periods": periods,
    }
    return manifest, {period: shards[period] for period in ordered_periods}


def save_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp_path.replace(path)


def write_archives(
    manifest: dict[str, Any],
    shards: dict[str, list[dict[str, Any]]],
    *,
    manifest_path: Path,
    archive_dir: Path,
) -> list[Path]:
    archive_dir.mkdir(parents=True, exist_ok=True)
    expected_names = {f"{period}.json" for period in shards}
    removed: list[Path] = []
    for existing in archive_dir.iterdir():
        if (
            existing.is_file()
            and GENERATED_ARCHIVE_RE.fullmatch(existing.name)
            and existing.name not in expected_names
        ):
            existing.unlink()
            removed.append(existing)
    for period, items in shards.items():
        save_json_atomic(archive_dir / f"{period}.json", items)
    save_json_atomic(manifest_path, manifest)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build uncapped yearly public archive JSON files.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        items = load_items(args.input)
        manifest, shards = build_archive_payload(items)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    removed: list[Path] = []
    if not args.dry_run:
        removed = write_archives(
            manifest,
            shards,
            manifest_path=args.manifest,
            archive_dir=args.archive_dir,
        )

    print("\n==== build_public_archives summary ====")
    print(f"input_items   : {len(items)}")
    print(f"periods       : {len(shards)}")
    print(f"latest_period : {manifest['latest_period'] or 'none'}")
    for period, period_items in shards.items():
        print(f"period_{period:<7}: {len(period_items)}")
    print(f"removed_files : {len(removed)}")
    print(f"manifest_path : {args.manifest}")
    if args.dry_run:
        print("(dry-run: no manifest or archive files written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
