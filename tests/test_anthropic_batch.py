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

    def create(self, *, requests):
        self.created = requests
        return types.SimpleNamespace(id="msgbatch_test", processing_status="ended")

    def results(self, batch_id):
        assert batch_id == "msgbatch_test"
        return iter(self.rows)


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


if __name__ == "__main__":
    unittest.main()
