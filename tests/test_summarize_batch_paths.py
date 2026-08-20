"""End-to-end control-flow coverage for `--batch` in scripts/summarize_updates.py.

Phase 3A's first production run died with UnboundLocalError on a batch-metrics
counter. Nothing caught it because no offline test had ever executed the batch
recovery/interlock block in `main()` — the existing batch tests patch
`request_summary_batch` and never reach it.

These tests walk every branch of that block, for both `--english-only` and
`--japanese-only`, and assert the run completes and reports its metrics. They are
deliberately about reachability, not about summarisation quality: any
UnboundLocalError, any counter that never gets printed, fails here.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import anthropic_batch  # noqa: E402
import summarize_updates as su  # noqa: E402


def item(idx: str = "raw-1") -> dict:
    return {
        "id": idx,
        "title_en": "MOE Update: Regulatory announcement",
        "title_ja": "環境省告示第1号",
        "area": "Other",
        "stage": "In Force",
        "impact_level": "Low",
        "summary_en": "Rule-based preview.",
        "business_impact_en": "Not yet assessed.",
        "recommended_action_en": "Review the official Japanese source.",
        "source_name": "環境省 報道発表",
        "source_url": "https://www.env.go.jp/1",
        "published_at": "2026-06-16",
        "last_checked": "2026-06-16",
        "relevance_score": 10.0,
    }


def english_payload() -> dict:
    return {
        "title_en": "Recovered English title",
        "summary_en": "A Japanese authority published an item.",
        "business_impact_en": "Operations may be affected.",
        "recommended_action_en": "Review the official Japanese source.",
        "confidence": "medium",
        "ai_notes": "Based on limited metadata.",
    }


def japanese_payload() -> dict:
    return {
        "summary_ja": "日本語の公表情報に関する要約です。",
        "business_impact_ja": "事業に影響が生じる可能性があります。",
        "recommended_action_ja": "日本語の公式情報源の確認が考えられます。",
    }


def succeeded_row(custom_id: str, payload: dict):
    block = types.SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))
    message = types.SimpleNamespace(content=[block], model="fake-batch", usage=None)
    result = types.SimpleNamespace(type="succeeded", message=message)
    return types.SimpleNamespace(custom_id=custom_id, result=result)


def client(*, listing=None, rows=(), listable=True):
    """A stand-in Anthropic client for the batch discovery/interlock paths."""

    class Batches:
        def results(self, batch_id):
            return list(rows)

        def create(self, requests):
            return types.SimpleNamespace(id="msgbatch_new", processing_status="ended")

        def retrieve(self, batch_id):
            return types.SimpleNamespace(id=batch_id, processing_status="ended")

        def cancel(self, batch_id):
            return None

    if listable:
        def _list(self, limit=20):
            return list(listing or [])
        Batches.list = _list

    # The SDK shape is client.messages.batches.<method>, not client.messages.<method>.
    return types.SimpleNamespace(messages=types.SimpleNamespace(batches=Batches()))


def ended(batch_id="msgbatch_old"):
    return types.SimpleNamespace(id=batch_id, processing_status="ended", created_at=None)


def in_progress(batch_id="msgbatch_running"):
    return types.SimpleNamespace(id=batch_id, processing_status="in_progress", created_at=None)


class BatchPathBase(unittest.TestCase):
    """Runs summarize_updates.main() against temp files, never the repo's data."""

    def setUp(self):
        self._orig = (su.INPUT_PATH, su.OUTPUT_PATH, su.BEFORE_AI_PATH, su.CACHE_PATH,
                      su.RAW_PATH, su.LOG_PATH, su.make_client,
                      su.request_summary_batch, su.request_japanese_summary_batch)
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.published = base / "legal_updates.json"
        self.cache_path = base / "summary_cache.json"
        self.raw_path = base / "raw_items.json"
        su.INPUT_PATH = su.OUTPUT_PATH = self.published
        su.BEFORE_AI_PATH = base / "before_ai.json"
        su.CACHE_PATH = self.cache_path
        su.RAW_PATH = self.raw_path
        su.LOG_PATH = base / "summarize.log"

        self.item = item()
        self.raw = {"id": "raw-1", "raw_content_hash": "hash-abc",
                    "raw_summary": "原文の抜粋", "source_type": "ministry"}
        self.published.write_text(json.dumps([self.item], ensure_ascii=False), encoding="utf-8")
        self.raw_path.write_text(json.dumps([self.raw], ensure_ascii=False), encoding="utf-8")
        self.cache_path.write_text("{}", encoding="utf-8")

        # No batch is ever really submitted: these record the attempt instead.
        self.submitted = {"english": 0, "japanese": 0}

        def fake_en(client_, model, candidates, *, timeout_seconds, custom_ids=None):
            self.submitted["english"] += 1
            return "msgbatch_en", [(english_payload(), "fake-batch", su.message_usage(None))
                                   for _ in candidates]

        def fake_ja(client_, model, candidates, *, timeout_seconds, custom_ids=None):
            self.submitted["japanese"] += 1
            return "msgbatch_ja", [(japanese_payload(), "fake-batch", su.message_usage(None))
                                   for _ in candidates]

        su.request_summary_batch = fake_en
        su.request_japanese_summary_batch = fake_ja

    def tearDown(self):
        for handler in list(su.logger.handlers):
            handler.close()
        su.logger.handlers.clear()
        (su.INPUT_PATH, su.OUTPUT_PATH, su.BEFORE_AI_PATH, su.CACHE_PATH,
         su.RAW_PATH, su.LOG_PATH, su.make_client,
         su.request_summary_batch, su.request_japanese_summary_batch) = self._orig
        self._tmp.cleanup()

    def cache_key(self) -> str:
        return su.cache_key(self.item, {"raw-1": self.raw})

    def run_main(self, mode, *, extra=(), with_key=True):
        argv = ["--all-items", mode, "--batch", "--api-limit", "5", *extra]
        env = {"ANTHROPIC_API_KEY": "test-key"} if with_key else {}
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=not with_key):
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                rc = su.main(argv)
        return rc, buf.getvalue()

    def assertReports(self, out, **expected):
        """Every Phase 3A metric must be printed; a missing one means an unbound path."""
        for metric in ("recovered_items", "batch_blocked", "batch_succeeded", "batch_errored",
                       "batch_expired", "batch_canceled", "batch_missing",
                       "preflight_cost_usd", "preflight_trimmed", "duration_seconds"):
            self.assertIn(metric, out, f"{metric} was never reported")
        summary = {}
        for line in out.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                summary[key.strip()] = value.strip()
        for key, value in expected.items():
            self.assertEqual(summary.get(key), value, f"{key}: {summary.get(key)!r} != {value!r}\n{out}")
        return summary


MODES = {"english": "--english-only", "japanese": "--japanese-only"}
RECOVERY_KIND = {"--english-only": (su.BATCH_KIND_EN, english_payload),
                 "--japanese-only": (su.BATCH_KIND_JA, japanese_payload)}


class TestBatchControlFlowPaths(BatchPathBase):
    """Six control-flow paths x two language modes.

    Each combination is its own test method so unittest builds and tears down a
    fresh fixture for it. Re-running setUp by hand inside a subTest leaked the
    patched module globals into other test modules and tripped over the still-open
    log file handle on Windows.
    """

    # -- 1. nothing to recover ---------------------------------------------
    def scenario_no_recovery_target(self, mode):
        su.make_client = lambda: client(listing=[])
        rc, out = self.run_main(mode)
        self.assertEqual(rc, 0)
        self.assertReports(out, recovered_items="0", batch_blocked="none")

    # -- 2. a finished batch is waiting to be collected ---------------------
    def scenario_recovery_target_present(self, mode):
        kind, payload = RECOVERY_KIND[mode]
        custom_id = su.format_custom_id(kind, "raw-1", self.cache_key())
        su.make_client = lambda: client(listing=[ended()],
                                        rows=[succeeded_row(custom_id, payload())])
        rc, out = self.run_main(mode)
        self.assertEqual(rc, 0)
        summary = self.assertReports(out, recovered_items="1", batch_blocked="none")
        self.assertEqual(summary.get("api_calls"), "0",
                         "a recovered item must not be paid for again")

    # -- 3. the batch list cannot be read -----------------------------------
    def scenario_discovery_unavailable(self, mode):
        su.make_client = lambda: client(listable=False)
        rc, out = self.run_main(mode)
        self.assertEqual(rc, 0)
        self.assertReports(out, batch_blocked="discovery_unavailable", recovered_items="0")
        self.assertEqual(sum(self.submitted.values()), 0,
                         "must not submit when the batch list is unreadable")

    # -- 4. something is still running --------------------------------------
    def scenario_running_batch_blocks_submission(self, mode):
        su.make_client = lambda: client(listing=[in_progress()])
        rc, out = self.run_main(mode)
        self.assertEqual(rc, 0)
        self.assertReports(out, batch_blocked="batch_still_running")
        self.assertEqual(sum(self.submitted.values()), 0,
                         "must not submit while a batch is running")

    # -- 5. no API key ------------------------------------------------------
    def scenario_no_api_key(self, mode):
        def explode():
            raise AssertionError("must not construct a client without a key")

        su.make_client = explode
        rc, out = self.run_main(mode, with_key=False)
        self.assertEqual(rc, 0)
        self.assertReports(out, recovered_items="0", batch_blocked="none")

    # -- 6. dry run ---------------------------------------------------------
    def scenario_dry_run_recovers_without_writing(self, mode):
        kind, payload = RECOVERY_KIND[mode]
        custom_id = su.format_custom_id(kind, "raw-1", self.cache_key())
        su.make_client = lambda: client(listing=[ended()],
                                        rows=[succeeded_row(custom_id, payload())])
        rc, out = self.run_main(mode, extra=("--dry-run",))
        self.assertEqual(rc, 0)
        self.assertReports(out, recovered_items="1")
        self.assertEqual(self.cache_path.read_text(encoding="utf-8"), "{}",
                         "--dry-run must not persist the cache")


def _attach_mode_tests(cls):
    """Turn each scenario_* method into one real test per language mode."""
    for name in [n for n in vars(cls) if n.startswith("scenario_")]:
        scenario = getattr(cls, name)
        for label, mode in MODES.items():
            def make(scenario=scenario, mode=mode):
                def test(self):
                    scenario(self, mode)
                return test
            test_name = f"test_{name[len('scenario_'):]}_{label}"
            method = make()
            method.__name__ = test_name
            method.__doc__ = f"{name[len('scenario_'):]} via {mode}"
            setattr(cls, test_name, method)
    return cls


_attach_mode_tests(TestBatchControlFlowPaths)


class TestBlockedInterlockIsNotResetLater(BatchPathBase):
    """The bug hid a second one: initialisation ran after the block and reset it.

    If `provider_fatal` / `provider_error_type` are re-initialised after the
    interlock sets them, a blocked run goes on to submit anyway — the exact
    duplicate-spend the interlock exists to prevent.
    """

    def test_blocked_run_reports_the_reason_it_was_blocked(self):
        su.make_client = lambda: client(listing=[in_progress()])
        rc, out = self.run_main("--english-only")
        self.assertEqual(rc, 0)
        summary = self.assertReports(out, batch_blocked="batch_still_running")
        self.assertEqual(summary.get("provider_error_type"), "batch_still_running",
                         "the interlock reason must survive to the summary")
        self.assertEqual(summary.get("provider_status"), "unavailable",
                         "a blocked run must not look healthy")

    def test_blocked_run_schedules_no_work(self):
        su.make_client = lambda: client(listing=[in_progress()])
        rc, out = self.run_main("--english-only")
        self.assertEqual(rc, 0)
        self.assertEqual(self.submitted["english"], 0)
        summary = self.assertReports(out)
        self.assertEqual(summary.get("api_calls"), "0")

    def test_discovery_failure_reason_also_survives(self):
        su.make_client = lambda: client(listable=False)
        rc, out = self.run_main("--japanese-only")
        self.assertEqual(rc, 0)
        summary = self.assertReports(out, batch_blocked="discovery_unavailable")
        self.assertEqual(summary.get("provider_error_type"), "batch_discovery_unavailable")


if __name__ == "__main__":
    unittest.main()
