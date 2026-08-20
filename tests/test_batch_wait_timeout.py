"""A batch that outlives our local wait is still running, not failed.

Run 32339886147 lost a healthy batch: `msgbatch_01QYh9bqUJy6bGRi41bJop3z` was
created, the 3600s local wait expired, and the resulting TimeoutError was
classified as `network_error`. Because `request_summary_batch()` raised, the
caller's `batch_id, outcomes = ...` never executed, so the summary printed
`batch_id: none` — the id of a batch that was still processing and still billing.
Its requests were also counted as `failed_items`.

Nothing was lost provider-side (results stay retrievable for 29 days and the
batch is deliberately not cancelled), but the run reported an outage and the
operator had no id to follow.
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

import anthropic_batch as ab  # noqa: E402
import summarize_updates as su  # noqa: E402
import translate_updates as tu  # noqa: E402

REAL_BATCH_ID = "msgbatch_01QYh9bqUJy6bGRi41bJop3z"


class StuckBatches:
    """A batch that never leaves in_progress, and records any cancel attempt."""

    def __init__(self):
        self.cancelled = []
        self.created = 0

    def list(self, limit=20):
        return []

    def create(self, requests):
        self.created += 1
        return types.SimpleNamespace(id=REAL_BATCH_ID, processing_status="in_progress")

    def retrieve(self, batch_id):
        return types.SimpleNamespace(id=batch_id, processing_status="in_progress")

    def results(self, batch_id):
        return []

    def cancel(self, batch_id):
        self.cancelled.append(batch_id)


def stuck_client(batches):
    return types.SimpleNamespace(messages=types.SimpleNamespace(batches=batches))


class TestWaitTimeoutClassification(unittest.TestCase):
    def test_poll_timeout_raises_an_error_that_carries_the_batch_id(self):
        batches = StuckBatches()
        with self.assertRaises(ab.BatchStillRunningError) as raised:
            ab.run_message_batch(
                stuck_client(batches),
                [{"custom_id": "se-raw-1-abc-f", "params": {}}],
                poll_seconds=0,
                timeout_seconds=0.000001,
            )
        self.assertEqual(raised.exception.batch_id, REAL_BATCH_ID)
        self.assertIn(REAL_BATCH_ID, str(raised.exception))

    def test_timeout_does_not_cancel_the_batch(self):
        batches = StuckBatches()
        with self.assertRaises(ab.BatchStillRunningError):
            ab.run_message_batch(
                stuck_client(batches), [{"custom_id": "se-raw-1-abc-f", "params": {}}],
                poll_seconds=0, timeout_seconds=0.000001,
            )
        self.assertEqual(batches.cancelled, [], "a still-running batch must not be cancelled")

    def test_it_is_still_a_timeouterror_for_existing_handlers(self):
        self.assertTrue(issubclass(ab.BatchStillRunningError, TimeoutError))

    def test_classified_as_batch_still_running_not_network_error(self):
        exc = ab.BatchStillRunningError(REAL_BATCH_ID, 3600.0)
        self.assertEqual(su.classify_provider_error(exc), "batch_still_running")
        self.assertEqual(tu.classify_provider_error(exc), "batch_still_running")

    def test_real_network_failures_are_still_network_error(self):
        for exc in (ConnectionError("connection reset"),
                    TimeoutError("read timed out"),
                    OSError("socket closed")):
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(su.classify_provider_error(exc), "network_error")
                self.assertEqual(tu.classify_provider_error(exc), "network_error")

    def test_sdk_timeout_and_connection_errors_are_still_network_error(self):
        for name in ("APITimeoutError", "APIConnectionError"):
            with self.subTest(name=name):
                exc = type(name, (Exception,), {})()
                self.assertEqual(su.classify_provider_error(exc), "network_error")
                self.assertEqual(tu.classify_provider_error(exc), "network_error")


class SummarizeTimeoutBase(unittest.TestCase):
    """Drives summarize_updates.main() into a batch that never finishes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self._orig = (su.INPUT_PATH, su.OUTPUT_PATH, su.BEFORE_AI_PATH, su.CACHE_PATH,
                      su.RAW_PATH, su.LOG_PATH, su.make_client)
        self.published = base / "legal_updates.json"
        self.cache_path = base / "summary_cache.json"
        su.INPUT_PATH = su.OUTPUT_PATH = self.published
        su.BEFORE_AI_PATH = base / "before_ai.json"
        su.CACHE_PATH = self.cache_path
        su.RAW_PATH = base / "raw_items.json"
        su.LOG_PATH = base / "summarize.log"

        self.item = {
            "id": "raw-1",
            "title_en": "MOE Update: Regulatory announcement",
            "title_ja": "環境省告示第1号",
            "area": "Other", "stage": "In Force", "impact_level": "Low",
            "summary_en": "Rule-based preview.",
            "business_impact_en": "Not yet assessed.",
            "recommended_action_en": "Review the official Japanese source.",
            "source_name": "環境省 報道発表",
            "source_url": "https://www.env.go.jp/1",
            "published_at": "2026-06-16", "last_checked": "2026-06-16",
            "relevance_score": 10.0,
        }
        self.published.write_text(json.dumps([self.item], ensure_ascii=False), encoding="utf-8")
        su.RAW_PATH.write_text(json.dumps(
            [{"id": "raw-1", "raw_content_hash": "hash-abc", "raw_summary": "抜粋"}],
            ensure_ascii=False), encoding="utf-8")
        self.cache_path.write_text("{}", encoding="utf-8")

        self.batches = StuckBatches()
        su.make_client = lambda: stuck_client(self.batches)

    def tearDown(self):
        for handler in list(su.logger.handlers):
            handler.close()
        su.logger.handlers.clear()
        (su.INPUT_PATH, su.OUTPUT_PATH, su.BEFORE_AI_PATH, su.CACHE_PATH,
         su.RAW_PATH, su.LOG_PATH, su.make_client) = self._orig
        self._tmp.cleanup()

    def run_main(self, mode):
        argv = ["--all-items", mode, "--batch", "--api-limit", "5",
                "--batch-timeout-seconds", "0.000001"]
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                rc = su.main(argv)
        summary = {}
        for line in buf.getvalue().splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                summary[key.strip()] = value.strip()
        return rc, summary


class TestSummarizeWaitTimeout(SummarizeTimeoutBase):
    def test_english_run_keeps_the_batch_id(self):
        rc, summary = self.run_main("--english-only")
        self.assertEqual(rc, 0)
        self.assertEqual(summary["batch_id"], REAL_BATCH_ID,
                         "the id of a batch that is still running must be reported")

    def test_english_run_reports_batch_still_running(self):
        _rc, summary = self.run_main("--english-only")
        self.assertEqual(summary["provider_error_type"], "batch_still_running")

    def test_english_run_does_not_count_the_items_as_failed(self):
        _rc, summary = self.run_main("--english-only")
        self.assertEqual(summary["failed_items"], "0",
                         "requests still being processed are not failures")
        self.assertEqual(summary["batch_deferred"], "1",
                         "they are reported as deferred instead")

    def test_english_run_does_not_cancel_the_batch(self):
        self.run_main("--english-only")
        self.assertEqual(self.batches.cancelled, [])

    def test_japanese_run_keeps_the_batch_id_and_defers(self):
        rc, summary = self.run_main("--japanese-only")
        self.assertEqual(rc, 0)
        self.assertEqual(summary["japanese_batch_id"], REAL_BATCH_ID)
        self.assertEqual(summary["provider_error_type"], "batch_still_running")
        self.assertEqual(summary["japanese_failed_items"], "0")
        self.assertEqual(summary["batch_deferred"], "1")
        self.assertEqual(self.batches.cancelled, [])

    def test_a_real_network_failure_is_still_reported_as_one(self):
        def explode(*args, **kwargs):
            raise ConnectionError("connection reset by peer")

        with mock.patch.object(su, "request_summary_batch", explode):
            _rc, summary = self.run_main("--english-only")
        self.assertEqual(summary["provider_error_type"], "network_error")
        self.assertEqual(summary["failed_items"], "1",
                         "a genuine transport failure still counts as a failure")
        self.assertEqual(summary["batch_deferred"], "0")


class TestDeferredBatchIsCollectedNextRun(SummarizeTimeoutBase):
    """The whole point of not failing: the next run picks the work up for free."""

    def test_ended_batch_is_recovered_on_the_following_run(self):
        # Run 1: the batch is created but never finishes within the local wait.
        _rc, first = self.run_main("--english-only")
        self.assertEqual(first["batch_id"], REAL_BATCH_ID)
        self.assertEqual(first["summarized_items"], "0")

        # Run 2: the same batch has since ended, and discovery finds it by its
        # self-describing custom_id. No new call is made.
        payload = {
            "title_en": "Recovered English title",
            "summary_en": "A Japanese authority published an item.",
            "business_impact_en": "Operations may be affected.",
            "recommended_action_en": "Review the official Japanese source.",
            "confidence": "medium",
            "ai_notes": "Based on limited metadata.",
        }
        key = su.cache_key(self.item, {"raw-1": {"raw_content_hash": "hash-abc"}})
        custom_id = su.format_custom_id(su.BATCH_KIND_EN, "raw-1", key)
        block = types.SimpleNamespace(type="text", text=json.dumps(payload))
        message = types.SimpleNamespace(content=[block], model="fake-batch", usage=None)
        row = types.SimpleNamespace(
            custom_id=custom_id,
            result=types.SimpleNamespace(type="succeeded", message=message),
        )

        class Finished(StuckBatches):
            def list(self, limit=20):
                return [types.SimpleNamespace(
                    id=REAL_BATCH_ID, processing_status="ended", created_at=None)]

            def results(self, batch_id):
                return [row]

        finished = Finished()
        su.make_client = lambda: stuck_client(finished)
        _rc, second = self.run_main("--english-only")

        self.assertEqual(second["recovered_items"], "1",
                         "the deferred batch must be collected, not re-submitted")
        self.assertEqual(finished.created, 0, "no new batch may be created for recovered work")
        self.assertEqual(finished.cancelled, [])
        cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(cached[key]["title_en"], "Recovered English title")


if __name__ == "__main__":
    unittest.main()
