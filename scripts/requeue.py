"""Targeted, one-shot regeneration of specific published items.

data/requeue.json names items whose AI output was found defective by an audit:

    {
      "created_at": "2026-10-16T09:00:00Z",
      "reason": "short note",
      "english": ["raw-...", ...],     # regenerate the English summary
      "zh-Hans": ["raw-...", ...]      # re-translate the Chinese block
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
SECTIONS = ("english", "zh-Hans")


def load(path: Path | None = None) -> dict:
    """{"since": str, "english": set, "zh-Hans": set}; empty when absent or malformed."""
    path = path or REQUEUE_PATH
    empty = {"since": "", **{section: set() for section in SECTIONS}}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    since = data.get("created_at") if isinstance(data, dict) else None
    if not isinstance(since, str) or not since:
        return empty
    out = {"since": since}
    for section in SECTIONS:
        ids = data.get(section) or []
        out[section] = {i for i in ids if isinstance(i, str)} if isinstance(ids, list) else set()
    return out


def due(requeue: dict, section: str, item_id: str, generated_at: str | None) -> bool:
    """True when this item was listed and its output predates the request."""
    if item_id not in requeue.get(section, ()):
        return False
    return (generated_at or "") < requeue.get("since", "")
