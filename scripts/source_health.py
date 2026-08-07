#!/usr/bin/env python3
"""
Source Health Monitor for Japan Legal Reform Watch.

Evaluates the per-source raw-fetch report written by fetch_updates.py. Health is
based on raw parsed item counts, not on whether items survived later publishing
filters.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fetch_updates

SCHEMA_VERSION = 1
STATE_SCHEMA_VERSION = 1
GATE_STREAK_THRESHOLD = 3
ERROR_MESSAGE_MAX_CHARS = 300
PERSIST_STATE_ENV = "SOURCE_HEALTH_PERSIST_STATE"

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_REPORT_PATH = REPO_ROOT / "logs" / "source_fetch_report.json"
DEFAULT_STATE_PATH = REPO_ROOT / "data" / "source_health_state.json"

DISPLAY_NAMES = {
    "egov": "e-Gov Public Comment",
    "shugiin-bills": "House of Representatives — Diet Bills",
    "egov-laws": "e-Gov Law Search — Updated Laws",
    "jpx-comments": "JPX — Public Comments",
    "jpx-rules": "JPX — TSE Rule Revisions",
    "pmda": "PMDA — Safety Updates",
    "jsda-comments": "JSDA — Public Comments",
    "jsda-results": "JSDA — Public Comment Results",
    "courts-supreme": "Courts in Japan — Recent Supreme Court Decisions",
    "sesc": "SESC — Enforcement Updates",
    "fsa": "Financial Services Agency",
    "mhlw": "MHLW",
    "digital-agency": "Digital Agency",
    "meti": "METI",
    "caa": "Consumer Affairs Agency",
    "ppc": "Personal Information Protection Commission",
    "jftc": "Japan Fair Trade Commission",
    "moj": "Ministry of Justice",
    "moe": "Ministry of the Environment",
    "mof": "Ministry of Finance",
    "nta": "National Tax Agency",
    "mic": "Ministry of Internal Affairs and Communications",
    "mlit": "MLIT",
    "maff": "MAFF",
}

REPORT_SOURCE_FIELDS = {
    "source_key",
    "source_name",
    "source_url",
    "status",
    "fetched_count",
    "new_count",
    "latest_published_at",
    "duration_ms",
    "error_type",
    "error_message",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def configured_sources() -> list[dict[str, Any]]:
    return [
        {
            "key": str(source.get("key", "")),
            "name": str(source.get("name", "")),
            "url": str(source.get("url", "")),
            "display_name": DISPLAY_NAMES.get(str(source.get("key", "")), str(source.get("name", ""))),
            # Most sources are required: a 3-run streak fails the gate. A source
            # marked gate_required=False (e.g. METI, which ReadTimeouts from CI) is
            # warning-only — it is still fetched and reported, but its streaks do
            # not fail the gate.
            "gate_required": bool(source.get("gate_required", True)),
        }
        for source in fetch_updates.SOURCES
    ]


def configured_keys() -> list[str]:
    return [source["key"] for source in configured_sources()]


def sanitize_error_message(value: Any, max_chars: int = ERROR_MESSAGE_MAX_CHARS) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def workflow_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def should_persist_state(args: argparse.Namespace) -> bool:
    if getattr(args, "persist_state", None) is not None:
        return bool(args.persist_state)
    return env_flag(PERSIST_STATE_ENV, True)


def load_json(path: Path) -> tuple[Any | None, list[str]]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), []
    except FileNotFoundError:
        return None, [f"Missing JSON file: {path}"]
    except json.JSONDecodeError as exc:
        return None, [f"Invalid JSON in {path}: {exc}"]
    except OSError as exc:
        return None, [f"Could not read {path}: {exc}"]


def save_json_if_changed(path: Path, data: dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    try:
        old_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        old_text = None
    if old_text == new_text:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(path)
    return True


def default_source_state() -> dict[str, Any]:
    return {
        "consecutive_zero_runs": 0,
        "consecutive_error_runs": 0,
        "last_status": "healthy",
        "last_problem_at": None,
        "last_recovered_at": None,
    }


def initial_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "sources": {key: default_source_state() for key in configured_keys()},
    }


def normalize_state(raw_state: Any | None) -> dict[str, Any]:
    state = initial_state()
    if not isinstance(raw_state, dict):
        return state
    raw_sources = raw_state.get("sources")
    if not isinstance(raw_sources, dict):
        return state
    for key in configured_keys():
        prior = raw_sources.get(key)
        if isinstance(prior, dict):
            merged = default_source_state()
            merged.update({k: prior.get(k, v) for k, v in merged.items()})
            merged["consecutive_zero_runs"] = int(merged.get("consecutive_zero_runs") or 0)
            merged["consecutive_error_runs"] = int(merged.get("consecutive_error_runs") or 0)
            state["sources"][key] = merged
    return state


def validate_configured_sources(sources: list[dict[str, str]] | None = None) -> list[str]:
    sources = sources or configured_sources()
    problems: list[str] = []
    keys = [source.get("key", "") for source in sources]
    if len(keys) != len(set(keys)):
        problems.append("Configured source keys are not unique.")
    for source in sources:
        for field in ("key", "name", "url"):
            if not str(source.get(field, "")).strip():
                problems.append(f"Configured source missing {field}: {source}")
        if source.get("key") not in DISPLAY_NAMES:
            problems.append(f"Configured source has no display name: {source.get('key')}")
    return problems


def validate_report(report: Any) -> list[str]:
    problems = validate_configured_sources()
    if not isinstance(report, dict):
        return problems + ["Report must be a JSON object."]
    if report.get("schema_version") != SCHEMA_VERSION:
        problems.append("Report schema_version must be 1.")
    if not isinstance(report.get("started_at"), str) or not report.get("started_at"):
        problems.append("Report started_at must be a non-empty string.")
    if not isinstance(report.get("finished_at"), str) or not report.get("finished_at"):
        problems.append("Report finished_at must be a non-empty string.")
    if report.get("configured_source_count") != len(configured_keys()):
        problems.append("Report configured_source_count does not match configured sources.")

    sources = report.get("sources")
    if not isinstance(sources, list):
        return problems + ["Report sources must be a list."]

    report_keys: list[str] = []
    for index, row in enumerate(sources):
        if not isinstance(row, dict):
            problems.append(f"Report source row {index} must be an object.")
            continue
        missing = sorted(REPORT_SOURCE_FIELDS - set(row))
        if missing:
            problems.append(f"Report source row {index} missing fields: {', '.join(missing)}")
        key = row.get("source_key")
        if isinstance(key, str):
            report_keys.append(key)
        if row.get("status") not in ("success", "error"):
            problems.append(f"Report source row {index} has invalid status.")
        for field in ("fetched_count", "new_count", "duration_ms"):
            if not isinstance(row.get(field), int) or row.get(field) < 0:
                problems.append(f"Report source row {index} has invalid {field}.")

    expected = set(configured_keys())
    actual = set(report_keys)
    duplicates = sorted({key for key in report_keys if report_keys.count(key) > 1})
    if duplicates:
        problems.append(f"Report contains duplicate sources: {', '.join(duplicates)}")
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        problems.append(f"Report missing configured sources: {', '.join(missing)}")
    if unknown:
        problems.append(f"Report contains unknown sources: {', '.join(unknown)}")
    return problems


def source_current_status(row: dict[str, Any]) -> str:
    if row.get("status") == "error":
        return "error"
    if row.get("status") == "success" and int(row.get("fetched_count") or 0) > 0:
        return "healthy"
    return "warning_zero"


def status_label(status: str) -> str:
    if status == "healthy":
        return "Healthy"
    if status == "warning_zero":
        return "Warning: zero results"
    return "Error"


def update_one_source_state(previous: dict[str, Any], current_status: str, checked_at: str) -> dict[str, Any]:
    next_state = dict(default_source_state())
    next_state.update(previous)
    if current_status == "healthy":
        recovered = (
            next_state.get("last_status") != "healthy"
            or int(next_state.get("consecutive_zero_runs") or 0) > 0
            or int(next_state.get("consecutive_error_runs") or 0) > 0
        )
        next_state["consecutive_zero_runs"] = 0
        next_state["consecutive_error_runs"] = 0
        next_state["last_status"] = "healthy"
        if recovered:
            next_state["last_recovered_at"] = checked_at
        return next_state

    if current_status == "warning_zero":
        next_state["consecutive_zero_runs"] = int(next_state.get("consecutive_zero_runs") or 0) + 1
        next_state["consecutive_error_runs"] = 0
        next_state["last_status"] = "warning_zero"
        next_state["last_problem_at"] = checked_at
        return next_state

    next_state["consecutive_zero_runs"] = 0
    next_state["consecutive_error_runs"] = int(next_state.get("consecutive_error_runs") or 0) + 1
    next_state["last_status"] = "error"
    next_state["last_problem_at"] = checked_at
    return next_state


def evaluate_report(
    report: Any,
    previous_state: Any | None,
    checked_at: str | None = None,
    persist_state: bool = True,
) -> dict[str, Any]:
    checked_at = checked_at or (report.get("finished_at") if isinstance(report, dict) else None) or utc_now_iso()
    problems = validate_report(report)
    state = normalize_state(previous_state)
    if problems:
        return {
            "valid": False,
            "problems": problems,
            "state": state,
            "rows": [],
            "healthy_count": 0,
            "warning_count": 0,
            "error_count": 0,
        }

    rows_by_key = {row["source_key"]: row for row in report["sources"]}
    next_state = normalize_state(state)
    rows: list[dict[str, Any]] = []
    healthy_count = warning_count = error_count = 0

    for source in configured_sources():
        key = source["key"]
        row = rows_by_key[key]
        current_status = source_current_status(row)
        if persist_state:
            source_state = update_one_source_state(next_state["sources"][key], current_status, checked_at)
            next_state["sources"][key] = source_state
        else:
            source_state = next_state["sources"][key]
        if current_status == "healthy":
            healthy_count += 1
        elif current_status == "warning_zero":
            warning_count += 1
        else:
            error_count += 1
        rows.append(
            {
                "source_key": key,
                "display_name": source["display_name"],
                "gate_required": source["gate_required"],
                "status": current_status,
                "status_label": status_label(current_status),
                "fetched_count": row["fetched_count"],
                "new_count": row["new_count"],
                "latest_published_at": row.get("latest_published_at"),
                "zero_streak": source_state["consecutive_zero_runs"],
                "error_streak": source_state["consecutive_error_runs"],
                "error_type": row.get("error_type"),
                "error_message": sanitize_error_message(row.get("error_message")),
            }
        )

    return {
        "valid": True,
        "problems": [],
        "state": next_state,
        "rows": rows,
        "healthy_count": healthy_count,
        "warning_count": warning_count,
        "error_count": error_count,
    }


def generate_step_summary(evaluation: dict[str, Any]) -> str:
    lines = ["## Source Health Summary"]
    if not evaluation.get("valid"):
        lines.append("")
        lines.append("Source health report could not be evaluated.")
        for problem in evaluation.get("problems", []):
            lines.append(f"- {problem}")
        lines.append("")
        return "\n".join(lines)

    lines.extend(
        [
            "| Source | Status | Fetched | New | Latest published | Zero streak | Error streak |",
            "|---|---:|---:|---:|---|---:|---:|",
        ]
    )
    for row in evaluation["rows"]:
        latest = row["latest_published_at"] or "--"
        lines.append(
            f"| {row['display_name']} | {row['status_label']} | {row['fetched_count']} | "
            f"{row['new_count']} | {latest} | {row['zero_streak']} | {row['error_streak']} |"
        )
    lines.extend(
        [
            f"Checked sources: {len(evaluation['rows'])} / {len(configured_keys())}",
            f"Healthy: {evaluation['healthy_count']}",
            f"Warnings: {evaluation['warning_count']}",
            f"Errors: {evaluation['error_count']}",
        ]
    )
    warning_only_failed = [
        row["display_name"]
        for row in evaluation["rows"]
        if row["status"] != "healthy" and not row.get("gate_required", True)
    ]
    if warning_only_failed:
        lines.append("")
        lines.append(
            "Warning-only source failed (does not fail the gate): "
            + ", ".join(warning_only_failed)
        )
    lines.append("")
    return "\n".join(lines)


def append_step_summary(markdown: str, summary_path: Path | None = None) -> None:
    path_text = str(summary_path or os.environ.get("GITHUB_STEP_SUMMARY") or "")
    if not path_text:
        return
    path = Path(path_text)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(markdown)
        if not markdown.endswith("\n"):
            f.write("\n")


def annotation_lines(evaluation: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if not evaluation.get("valid"):
        for problem in evaluation.get("problems", []):
            lines.append(f"::warning title=Source health warning::{workflow_escape(problem)}")
        return lines
    for row in evaluation["rows"]:
        if row["status"] == "warning_zero":
            message = f"{row['display_name']} returned 0 parsed items in this run."
            lines.append(f"::warning title=Source health warning::{workflow_escape(message)}")
        elif row["status"] == "error":
            detail = sanitize_error_message(
                ": ".join(part for part in (row.get("error_type"), row.get("error_message")) if part)
            )
            message = f"{row['display_name']} fetch failed"
            if not row.get("gate_required", True):
                message += " (warning-only source; does not fail the source-health gate)"
            if detail:
                message += f": {detail}"
            lines.append(f"::warning title=Source health warning::{workflow_escape(message)}")
    return lines


def validate_state_for_gate(state: Any) -> list[str]:
    if not isinstance(state, dict):
        return ["Health state must be a JSON object."]
    if state.get("schema_version") != STATE_SCHEMA_VERSION:
        return ["Health state schema_version must be 1."]
    sources = state.get("sources")
    if not isinstance(sources, dict):
        return ["Health state sources must be an object."]
    expected = set(configured_keys())
    actual = set(sources)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    problems = []
    if missing:
        problems.append(f"Health state missing configured sources: {', '.join(missing)}")
    if unknown:
        problems.append(f"Health state contains unknown sources: {', '.join(unknown)}")
    return problems


def gate_failures(report: Any, state: Any, enforce_streaks: bool = True) -> list[str]:
    failures = validate_report(report)
    failures.extend(validate_state_for_gate(state))
    if failures:
        return failures

    rows_by_key = {row["source_key"]: row for row in report["sources"]}
    current_statuses = [source_current_status(rows_by_key[key]) for key in configured_keys()]
    if current_statuses and all(status != "healthy" for status in current_statuses):
        failures.append("All configured sources returned errors or zero parsed items in this run.")

    if enforce_streaks:
        for source in configured_sources():
            if not source.get("gate_required", True):
                continue  # warning-only source: its streaks never fail the gate
            key = source["key"]
            source_state = state["sources"][key]
            if int(source_state.get("consecutive_zero_runs") or 0) >= GATE_STREAK_THRESHOLD:
                failures.append(f"{source['display_name']} returned zero parsed items for 3 consecutive runs.")
            if int(source_state.get("consecutive_error_runs") or 0) >= GATE_STREAK_THRESHOLD:
                failures.append(f"{source['display_name']} failed for 3 consecutive runs.")
    return failures


def gate_warnings(report: Any, state: Any, enforce_streaks: bool = True) -> list[str]:
    """Non-blocking warnings: warning-only sources (gate_required=False) that have
    hit the streak threshold. Surfaced so an operator notices, without failing the
    gate. Returns [] if the report/state are invalid (gate_failures reports those).
    """
    warnings: list[str] = []
    if not enforce_streaks:
        return warnings
    if validate_report(report) or validate_state_for_gate(state):
        return warnings
    for source in configured_sources():
        if source.get("gate_required", True):
            continue
        source_state = state["sources"][source["key"]]
        if int(source_state.get("consecutive_error_runs") or 0) >= GATE_STREAK_THRESHOLD:
            warnings.append(
                f"{source['display_name']} failed for 3 consecutive runs "
                "(warning-only source; gate not failed)."
            )
        if int(source_state.get("consecutive_zero_runs") or 0) >= GATE_STREAK_THRESHOLD:
            warnings.append(
                f"{source['display_name']} returned zero parsed items for 3 consecutive runs "
                "(warning-only source; gate not failed)."
            )
    return warnings


def load_report_and_state(report_path: Path, state_path: Path) -> tuple[Any | None, Any | None, list[str]]:
    report, report_errors = load_json(report_path)
    state, state_errors = load_json(state_path)
    return report, state, report_errors + state_errors


def cmd_evaluate(args: argparse.Namespace) -> int:
    persist_state = should_persist_state(args)
    report, report_errors = load_json(args.report)
    state, state_errors = load_json(args.state)
    if state_errors:
        state = None
    if report_errors:
        evaluation = {
            "valid": False,
            "problems": report_errors,
            "state": normalize_state(state),
            "rows": [],
            "healthy_count": 0,
            "warning_count": 0,
            "error_count": 0,
        }
    else:
        evaluation = evaluate_report(report, state, persist_state=persist_state)
        if evaluation["valid"] and persist_state:
            changed = save_json_if_changed(args.state, evaluation["state"])
            print(f"Source health state {'updated' if changed else 'unchanged'}: {args.state}")
        elif evaluation["valid"]:
            print(f"Source health state not persisted for this run: {args.state}")

    summary = generate_step_summary(evaluation)
    append_step_summary(summary, args.summary)
    for line in annotation_lines(evaluation):
        print(line)
    if not evaluation.get("valid"):
        print("Source health report has validation problems; final gate will fail after commit.")
    else:
        print(
            "Source health evaluated: "
            f"{evaluation['healthy_count']} healthy, {evaluation['warning_count']} warnings, "
            f"{evaluation['error_count']} errors. "
            f"persist_state={'true' if persist_state else 'false'}."
        )
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    enforce_streaks = should_persist_state(args)
    report, state, load_errors = load_report_and_state(args.report, args.state)
    failures = list(load_errors)
    warnings: list[str] = []
    if not load_errors:
        failures.extend(gate_failures(report, state, enforce_streaks=enforce_streaks))
        warnings = gate_warnings(report, state, enforce_streaks=enforce_streaks)
    # Warning-only source streaks are surfaced but never fail the gate.
    for warning in warnings:
        print(f"::warning title=Source health gate::{workflow_escape(warning)}")
        print(f"Source health gate warning: {warning}", file=sys.stderr)
    if failures:
        for failure in failures:
            print(f"::error title=Source health gate::{workflow_escape(failure)}")
            print(f"Source health gate failure: {failure}", file=sys.stderr)
        return 1
    print(f"Source health gate passed. enforce_streaks={'true' if enforce_streaks else 'false'}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate and enforce source fetch health.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    evaluate = subparsers.add_parser("evaluate", help="Update health state and write a GitHub Step Summary.")
    evaluate.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    evaluate.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    evaluate.add_argument("--summary", type=Path, default=None)
    evaluate.add_argument("--persist-state", dest="persist_state", action="store_true", default=None)
    evaluate.add_argument("--no-persist-state", dest="persist_state", action="store_false")
    evaluate.set_defaults(func=cmd_evaluate)

    gate = subparsers.add_parser("gate", help="Fail only on source-health gate conditions.")
    gate.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    gate.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    gate.add_argument("--persist-state", dest="persist_state", action="store_true", default=None)
    gate.add_argument("--no-persist-state", dest="persist_state", action="store_false")
    gate.set_defaults(func=cmd_gate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
