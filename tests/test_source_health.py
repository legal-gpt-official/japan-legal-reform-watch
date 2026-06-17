"""Offline tests for Source Health Monitor.

No network access: report fixtures and mocked fetch calls exercise source keys,
health-state transitions, gate behavior, GitHub summary output, and workflow
ordering.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_updates as fu  # noqa: E402
import source_health as sh  # noqa: E402

EXPECTED_SOURCE_KEYS = {
    "egov",
    "fsa",
    "mhlw",
    "digital-agency",
    "meti",
    "caa",
    "ppc",
    "jftc",
    "moj",
    "moe",
    "mof",
    "mic",
    "mlit",
    "maff",
}


def report_row(source, **overrides):
    row = {
        "source_key": source["key"],
        "source_name": source["name"],
        "source_url": source["url"],
        "status": "success",
        "fetched_count": 5,
        "new_count": 1,
        "latest_published_at": "2026-06-16",
        "duration_ms": 10,
        "error_type": None,
        "error_message": None,
    }
    row.update(overrides)
    return row


def make_report(**overrides_by_key):
    rows = []
    for source in sh.configured_sources():
        overrides = overrides_by_key.get(source["key"], {})
        rows.append(report_row(source, **overrides))
    return {
        "schema_version": 1,
        "started_at": "2026-06-16T00:00:00Z",
        "finished_at": "2026-06-16T00:00:12Z",
        "configured_source_count": 14,
        "sources": rows,
    }


def row_by_key(evaluation, key):
    return next(row for row in evaluation["rows"] if row["source_key"] == key)


class TestSourceHealthConfig(unittest.TestCase):
    def test_all_14_source_keys_are_unique(self):
        keys = [source.get("key") for source in fu.SOURCES]
        self.assertEqual(set(keys), EXPECTED_SOURCE_KEYS)
        self.assertEqual(len(keys), 14)
        self.assertEqual(len(keys), len(set(keys)))

    def test_all_sources_have_required_fields(self):
        for source in fu.SOURCES:
            with self.subTest(source=source.get("key")):
                for key in ("key", "name", "url", "source_type", "source_language"):
                    self.assertIn(key, source)
                    self.assertTrue(str(source[key]).strip(), f"empty {key}")


class TestSourceHealthEvaluation(unittest.TestCase):
    def test_success_with_fetched_items_is_healthy(self):
        evaluation = sh.evaluate_report(make_report(), sh.initial_state(), "2026-06-16T00:00:12Z")
        mlit = row_by_key(evaluation, "mlit")
        self.assertEqual(mlit["status"], "healthy")
        self.assertEqual(mlit["zero_streak"], 0)
        self.assertEqual(mlit["error_streak"], 0)

    def test_success_with_zero_items_is_warning(self):
        report = make_report(mlit={"fetched_count": 0, "new_count": 0, "latest_published_at": None})
        evaluation = sh.evaluate_report(report, sh.initial_state(), "2026-06-16T00:00:12Z")
        mlit = row_by_key(evaluation, "mlit")
        self.assertEqual(mlit["status"], "warning_zero")
        self.assertEqual(mlit["zero_streak"], 1)
        self.assertEqual(mlit["error_streak"], 0)

    def test_error_increments_error_streak(self):
        report = make_report(maff={"status": "error", "fetched_count": 0, "new_count": 0,
                                   "latest_published_at": None, "error_type": "TimeoutError",
                                   "error_message": "timed out"})
        evaluation = sh.evaluate_report(report, sh.initial_state(), "2026-06-16T00:00:12Z")
        maff = row_by_key(evaluation, "maff")
        self.assertEqual(maff["status"], "error")
        self.assertEqual(maff["zero_streak"], 0)
        self.assertEqual(maff["error_streak"], 1)

    def test_healthy_recovery_resets_streaks(self):
        state = sh.initial_state()
        state["sources"]["moe"].update(
            {
                "consecutive_zero_runs": 2,
                "consecutive_error_runs": 0,
                "last_status": "warning_zero",
                "last_problem_at": "2026-06-15T00:00:00Z",
            }
        )
        evaluation = sh.evaluate_report(make_report(), state, "2026-06-16T00:00:12Z")
        moe_state = evaluation["state"]["sources"]["moe"]
        self.assertEqual(moe_state["consecutive_zero_runs"], 0)
        self.assertEqual(moe_state["consecutive_error_runs"], 0)
        self.assertEqual(moe_state["last_status"], "healthy")
        self.assertEqual(moe_state["last_recovered_at"], "2026-06-16T00:00:12Z")

    def test_continued_healthy_run_does_not_change_state(self):
        state = sh.initial_state()
        evaluation = sh.evaluate_report(make_report(), state, "2026-06-16T00:00:12Z")
        self.assertEqual(evaluation["state"], state)

    def test_error_message_is_single_line_and_truncated(self):
        message = "first line\n" + ("x" * 400)
        cleaned = sh.sanitize_error_message(message)
        self.assertNotIn("\n", cleaned)
        self.assertLessEqual(len(cleaned), 300)

    def test_step_summary_contains_14_source_rows(self):
        report = make_report(mlit={"fetched_count": 0, "new_count": 0, "latest_published_at": None})
        evaluation = sh.evaluate_report(report, sh.initial_state(), "2026-06-16T00:00:12Z")
        summary = sh.generate_step_summary(evaluation)
        source_lines = [
            line for line in summary.splitlines()
            if line.startswith("| ") and not line.startswith("| Source") and not line.startswith("|---")
        ]
        self.assertEqual(len(source_lines), 14)
        self.assertIn("| MLIT | Warning: zero results | 0 | 0 | -- | 1 | 0 |", summary)


class TestSourceHealthGate(unittest.TestCase):
    def test_three_consecutive_zero_runs_fail_gate(self):
        state = sh.initial_state()
        state["sources"]["mlit"]["consecutive_zero_runs"] = 3
        failures = sh.gate_failures(make_report(mlit={"fetched_count": 0, "new_count": 0}), state)
        self.assertTrue(any("MLIT returned zero parsed items" in failure for failure in failures))

    def test_three_consecutive_errors_fail_gate(self):
        state = sh.initial_state()
        state["sources"]["maff"]["consecutive_error_runs"] = 3
        failures = sh.gate_failures(make_report(maff={"status": "error", "fetched_count": 0, "new_count": 0}), state)
        self.assertTrue(any("MAFF failed for 3 consecutive runs" in failure for failure in failures))

    def test_one_or_two_warnings_do_not_fail_gate(self):
        for streak in (1, 2):
            with self.subTest(streak=streak):
                state = sh.initial_state()
                state["sources"]["mlit"]["consecutive_zero_runs"] = streak
                failures = sh.gate_failures(make_report(mlit={"fetched_count": 0, "new_count": 0}), state)
                self.assertFalse(failures)

    def test_all_sources_failed_or_zero_is_immediate_gate_failure(self):
        report = make_report(**{
            key: {"fetched_count": 0, "new_count": 0, "latest_published_at": None}
            for key in EXPECTED_SOURCE_KEYS
        })
        failures = sh.gate_failures(report, sh.initial_state())
        self.assertTrue(any("All configured sources" in failure for failure in failures))

    def test_missing_configured_source_fails_gate(self):
        report = make_report()
        report["sources"] = [row for row in report["sources"] if row["source_key"] != "mlit"]
        failures = sh.gate_failures(report, sh.initial_state())
        self.assertTrue(any("missing configured sources: mlit" in failure for failure in failures))

    def test_unknown_source_fails_gate(self):
        report = make_report()
        report["sources"].append(report_row({"key": "nta", "name": "NTA", "url": "https://example.go.jp"}))
        failures = sh.gate_failures(report, sh.initial_state())
        self.assertTrue(any("unknown sources: nta" in failure for failure in failures))


class TestFetchReportingAndWorkflow(unittest.TestCase):
    def test_fetch_run_preserves_raw_merge_and_writes_source_report(self):
        source = {
            "key": "egov",
            "name": "Test Source",
            "url": "https://example.go.jp/feed.xml",
            "source_type": "public_comment_rss",
            "source_language": "ja",
        }
        existing_entry = {
            "title": "Existing title",
            "link": "https://example.go.jp/a",
            "summary": "",
            "published_iso": "2026-06-01",
        }
        new_entry = {
            "title": "New title",
            "link": "https://example.go.jp/b",
            "summary": "",
            "published_iso": "2026-06-16",
        }
        existing_item = fu.build_item(existing_entry, source, "2026-06-01T00:00:00Z")
        saved_raw = {}
        saved_report = {}

        def capture_raw(_path, data):
            saved_raw["data"] = data

        def capture_report(_path, data):
            saved_report["data"] = data

        with mock.patch.object(fu, "SOURCES", [source]), \
                mock.patch.object(fu, "load_existing", return_value=[existing_item]), \
                mock.patch.object(fu, "save_json", side_effect=capture_raw), \
                mock.patch.object(fu, "save_json_document", side_effect=capture_report), \
                mock.patch.object(fu, "http_get", return_value=b"fixture"), \
                mock.patch.object(fu, "parse_source_entries", return_value=[existing_entry, new_entry, new_entry]):
            exit_code = fu.run(timeout=1, dry_run=False)

        self.assertEqual(exit_code, 0)
        merged = saved_raw["data"]
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["id"], existing_item["id"])
        self.assertEqual(merged[1]["id"], fu.make_id("https://example.go.jp/b", "New title", "Test Source", "2026-06-16"))

        report = saved_report["data"]
        self.assertEqual(report["configured_source_count"], 1)
        self.assertEqual(report["sources"][0]["source_key"], "egov")
        self.assertEqual(report["sources"][0]["fetched_count"], 2)
        self.assertEqual(report["sources"][0]["new_count"], 1)
        self.assertEqual(report["sources"][0]["latest_published_at"], "2026-06-16")

    def test_workflow_evaluate_after_fetch_and_gate_after_commit(self):
        workflow = (REPO_ROOT / ".github" / "workflows" / "daily-update.yml").read_text(encoding="utf-8")
        self.assertIn("uses: actions/checkout@v5", workflow)
        self.assertIn("uses: actions/setup-python@v6", workflow)
        self.assertNotIn("actions/checkout@v4", workflow)
        self.assertNotIn("actions/setup-python@v5", workflow)
        self.assertNotIn("FORCE_JAVASCRIPT_ACTIONS_TO_NODE24", workflow)
        self.assertNotIn("ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION", workflow)
        self.assertIn("permissions:\n  contents: write", workflow)

        fetch_pos = workflow.index("name: Fetch raw updates")
        evaluate_pos = workflow.index("name: Evaluate source health")
        build_pos = workflow.index("name: Build public data")
        commit_pos = workflow.index("name: Commit and push updated data")
        gate_pos = workflow.index("name: Enforce source health gate")
        self.assertLess(fetch_pos, evaluate_pos)
        self.assertLess(evaluate_pos, build_pos)
        self.assertLess(commit_pos, gate_pos)
        self.assertIn("data/source_health_state.json", workflow)


if __name__ == "__main__":
    unittest.main()
