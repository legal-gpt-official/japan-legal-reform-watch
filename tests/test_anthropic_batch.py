"""Offline contract tests for the shared Anthropic Message Batch helper."""

import sys
import types
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import anthropic_batch as ab  # noqa: E402


class FakeBatches:
    def __init__(self, rows):
        self.rows = rows
        self.created = None
        self.cancelled = []

    def create(self, *, requests):
        self.created = requests
        return types.SimpleNamespace(id="msgbatch_test", processing_status="ended")

    def results(self, batch_id):
        assert batch_id == "msgbatch_test"
        return iter(self.rows)

    def cancel(self, batch_id):
        self.cancelled.append(batch_id)

    def retrieve(self, batch_id):
        return types.SimpleNamespace(id=batch_id, processing_status="in_progress")



class TestAnthropicBatch(unittest.TestCase):
    def test_returns_successes_and_classified_individual_errors(self):
        message = types.SimpleNamespace(content=[], model="model")
        succeeded = types.SimpleNamespace(type="succeeded", message=message)
        error = types.SimpleNamespace(type="billing_error", message="insufficient credit")
        error_response = types.SimpleNamespace(error=error)
        errored = types.SimpleNamespace(type="errored", error=error_response)
        rows = [
            types.SimpleNamespace(custom_id="ok", result=succeeded),
            types.SimpleNamespace(custom_id="bad", result=errored),
        ]
        batches = FakeBatches(rows)
        client = types.SimpleNamespace(messages=types.SimpleNamespace(batches=batches))
        requests = [
            {"custom_id": "ok", "params": {"model": "m"}},
            {"custom_id": "bad", "params": {"model": "m"}},
        ]

        run = ab.run_message_batch(client, requests)

        self.assertEqual(run.batch_id, "msgbatch_test")
        self.assertIs(run.results["ok"], message)
        self.assertIsInstance(run.results["bad"], ab.BatchItemError)
        self.assertEqual(run.results["bad"].status_code, 402)
        self.assertEqual(batches.created, requests)

    def test_missing_provider_row_becomes_per_item_error(self):
        batches = FakeBatches([])
        client = types.SimpleNamespace(messages=types.SimpleNamespace(batches=batches))

        run = ab.run_message_batch(
            client,
            [{"custom_id": "missing", "params": {"model": "m"}}],
        )

        self.assertIsInstance(run.results["missing"], ab.BatchItemError)
        self.assertEqual(run.results["missing"].error_type, "missing_result")

    def _timeout(self, **kwargs):
        batches = FakeBatches([])
        batch = types.SimpleNamespace(id="msgbatch_test", processing_status="in_progress")
        client = types.SimpleNamespace(messages=types.SimpleNamespace(batches=batches))
        with self.assertRaisesRegex(TimeoutError, "msgbatch_test"):
            ab._wait_for_message_batch(
                client,
                batch,
                [{"custom_id": "pending", "params": {"model": "m"}}],
                poll_seconds=0,
                timeout_seconds=0.000001,
                **kwargs,
            )
        return batches

    def test_local_timeout_does_not_cancel_the_batch(self):
        """A batch runs up to 24h provider-side; a local wait expiring is not a
        reason to throw away requests that are already completed and billed."""
        batches = self._timeout()
        self.assertEqual(batches.cancelled, [])

    def test_cancellation_on_timeout_is_available_when_explicitly_requested(self):
        batches = self._timeout(cancel_on_timeout=True)
        self.assertEqual(batches.cancelled, ["msgbatch_test"])

    def test_custom_id_round_trips_item_identity(self):
        custom_id = ab.format_custom_id("t", "raw-4770d81e0718c77e", "a" * 64, "1")
        self.assertLessEqual(len(custom_id), ab.CUSTOM_ID_MAX_LEN)
        parsed = ab.parse_custom_id(custom_id, "t")
        self.assertEqual(parsed["item_id"], "raw-4770d81e0718c77e")
        self.assertEqual(parsed["source_hash_prefix"], "a" * ab.SOURCE_HASH_PREFIX_LEN)
        self.assertGreaterEqual(ab.SOURCE_HASH_PREFIX_LEN, 16, "64 bits of collision margin")
        self.assertEqual(parsed["mask"], "1")
        # Another tool's batch in the same workspace must not be misread as ours.
        self.assertIsNone(ab.parse_custom_id(custom_id, "se"))
        self.assertIsNone(ab.parse_custom_id("something-else", "t"))

    def test_shorter_legacy_prefixes_still_validate(self):
        """Reading is length-agnostic, so ids written before the widening still work."""
        legacy = "t-raw-4770d81e0718c77e-" + "a" * 12 + "-1"
        parsed = ab.parse_custom_id(legacy, "t")
        self.assertEqual(parsed["source_hash_prefix"], "a" * 12)
        self.assertTrue(("a" * 64).startswith(parsed["source_hash_prefix"]))

    def test_batch_age_is_read_from_the_provider_timestamp(self):
        import types as _t
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 8, 20, tzinfo=timezone.utc)
        fresh = _t.SimpleNamespace(created_at=now - timedelta(hours=6))
        old_batch = _t.SimpleNamespace(created_at="2026-08-10T00:00:00Z")
        self.assertAlmostEqual(ab.batch_age_days(fresh, now), 0.25, places=3)
        self.assertAlmostEqual(ab.batch_age_days(old_batch, now), 10.0, places=3)
        self.assertIsNone(ab.batch_age_days(_t.SimpleNamespace(), now))
        self.assertIsNone(ab.batch_age_days(_t.SimpleNamespace(created_at="not-a-date"), now))

    def test_custom_id_refuses_to_exceed_the_provider_limit(self):
        with self.assertRaises(ValueError):
            ab.format_custom_id("t", "x" * 80, "a" * 64, "f")
        with self.assertRaises(ValueError):
            ab.format_custom_id("t", "", "a" * 64, "f")

    def test_discovery_failure_is_raised_not_swallowed(self):
        """Fail closed: no batch list means no recovery and no interlock, so a
        caller must be able to refuse to submit rather than silently continue."""
        client = types.SimpleNamespace(messages=types.SimpleNamespace(batches=FakeBatches([])))
        with self.assertRaises(ab.BatchDiscoveryUnavailable):
            ab.list_recent_batches(client)  # FakeBatches has no .list()
        with self.assertRaises(ab.BatchDiscoveryUnavailable):
            ab.pending_batches(client)

    def test_pending_batches_reports_unfinished_work_only(self):
        batches = FakeBatches([])
        batches.list = lambda limit=20: [
            types.SimpleNamespace(id="msgbatch_running", processing_status="in_progress"),
            types.SimpleNamespace(id="msgbatch_canceling", processing_status="canceling"),
            types.SimpleNamespace(id="msgbatch_done", processing_status="ended"),
        ]
        client = types.SimpleNamespace(messages=types.SimpleNamespace(batches=batches))
        self.assertEqual(
            ab.pending_batches(client), ["msgbatch_running", "msgbatch_canceling"]
        )

    def test_empty_workspace_is_distinct_from_unreadable(self):
        batches = FakeBatches([])
        batches.list = lambda limit=20: []
        client = types.SimpleNamespace(messages=types.SimpleNamespace(batches=batches))
        self.assertEqual(ab.pending_batches(client), [])

if __name__ == "__main__":
    unittest.main()
