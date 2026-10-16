"""Targeted, one-shot regeneration of specific published items.

data/requeue.json names items whose AI output was found defective by an audit:

    {
      "created_at": "2026-10-16T09:00:00Z",
      "reason": "short note",
      "english": ["raw-...", ...],     # regenerate the English summary
      "english_title": ["raw-...", ...], # regenerate only the English title
      "zh-Hans": ["raw-...", ...],     # re-translate the Chinese block
      "zh-Hans-sync": ["raw-...", ...] # no API: make the field cache hold this
                                       # item's current (already fixed) Chinese
    }

An item is due only while its cached output is OLDER than `created_at`: the
first successful regeneration writes a newer timestamp, so the request is
satisfied once and never repeats -- no state to clear, and an item the run had
no budget for simply stays due until a later run picks it up. Nothing else is
regenerated, and an item that is not due keeps its existing output.
"""

from __future__ import annotations

import json
from pathlib import Path

REQUEUE_PATH = Path(__file__).resolve().parent.parent / "data" / "requeue.json"
# Written only by scripts/quality_gate.py in enforce mode: items a HIGH audit
# finding asked to be redone. Same format; read together with REQUEUE_PATH.
AUTO_REQUEUE_PATH = Path(__file__).resolve().parent.parent / "data" / "requeue_auto.json"
SECTIONS = ("english", "english_title", "zh-Hans", "zh-Hans-sync")


def _read(path: Path) -> dict:
    """{section: {id: created_at}} for one request file; empty when absent or malformed."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    since = data.get("created_at") if isinstance(data, dict) else None
    if not isinstance(since, str) or not since:
        return {}
    out = {}
    for section in SECTIONS:
        ids = data.get(section) or []
        if isinstance(ids, list):
            out[section] = {i: since for i in ids if isinstance(i, str)}
    return out


def load(path: Path | None = None) -> dict:
    """{section: {item_id: requested_at}} from one file, or from the manual and
    automatic request files together (the later request wins per item)."""
    paths = [path] if path is not None else [REQUEUE_PATH, AUTO_REQUEUE_PATH]
    merged: dict = {section: {} for section in SECTIONS}
    for p in paths:
        for section, ids in _read(p).items():
            for item_id, since in ids.items():
                if since > merged[section].get(item_id, ""):
                    merged[section][item_id] = since
    return merged


def due(requeue: dict, section: str, item_id: str, generated_at: str | None) -> bool:
    """True when this item was listed and its output predates the request."""
    since = (requeue.get(section) or {}).get(item_id)
    return since is not None and (generated_at or "") < since
