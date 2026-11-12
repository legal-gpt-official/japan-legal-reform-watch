"""Regression tests for bounded official-record packets."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_explainer_research_context as research


class FakeIndex:
    def __init__(self):
        self.raw_by_id = {
            "raw-0000000000000001": {
                "id": "raw-0000000000000001",
                "source_name": "e-Gov",
                "title_ja": "改正規則案",
                "source_url": "https://www.example.go.jp/notice",
                "raw_summary": "甲" * 5000,
            },
            "raw-0000000000000002": {
                "id": "raw-0000000000000002",
                "source_name": "official",
                "title_ja": "関連する公布記録",
                "source_url": "https://www.example.go.jp/order",
                "raw_summary": "確定施行日について確認を要する",
            },
        }

    def closure(self, ids):
        return sorted(self.raw_by_id) if ids[0] in self.raw_by_id else []

    def fingerprint(self, ids):
        return "sha256:verified-by-official-index"


class ResearchPacketTests(unittest.TestCase):
    def setUp(self):
        self.queue = {
            "entries": [{
                "queue_id": "draft:raw-0000000000000001",
                "kind": "new",
                "priority": 20,
                "record_count": 2,
                "first_queued_on": "2026-11-12",
                "item_ids": ["raw-0000000000000001"],
            }]
        }

    def test_full_closure_and_explicit_truncation(self):
        result = research.packets(FakeIndex(), self.queue)
        packet = result["raw-0000000000000001"]
        self.assertEqual(len(packet["related_item_ids"]), 2)
        self.assertEqual(len(packet["records"]), 2)
        self.assertTrue(packet["records"][0]["raw_summary_truncated"])
        self.assertEqual(len(packet["records"][0]["raw_summary"]), research.SUMMARY_LIMIT)
        self.assertFalse(packet["records"][1]["raw_summary_truncated"])
        self.assertIn("not independently verified", packet["notice"])

    def test_inconsistent_closure_fails_closed(self):
        self.queue["entries"][0]["record_count"] = 3
        with self.assertRaisesRegex(ValueError, "closure mismatch"):
            research.packets(FakeIndex(), self.queue)

    def test_inconsistent_fingerprint_fails_closed(self):
        self.queue["entries"][0]["current_fingerprint"] = "sha256:other"
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            research.packets(FakeIndex(), self.queue)

    def test_idempotent_write_and_remove_stale(self):
        result = research.packets(FakeIndex(), self.queue)
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp)
            self.assertEqual(research.write_packets(result, dest), (1, 0))
            self.assertEqual(research.write_packets(result, dest), (0, 0))
            self.assertEqual(json.loads(next(dest.glob("*.json")).read_text())["record_count"], 2)
            self.assertEqual(research.write_packets({}, dest), (0, 1))


if __name__ == "__main__":
    unittest.main()
