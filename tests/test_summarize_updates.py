"""Offline tests for scripts/summarize_updates.py.

No API calls: these tests cover local AI-result application and validation only.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_public_data as bpd  # noqa: E402
import summarize_updates as su  # noqa: E402


class TestSummarizeTitleCap(unittest.TestCase):
    def _item(self):
        return {
            "id": "raw-test",
            "title_en": "Rule-based title",
            "title_ja": "意見募集",
            "area": "Other",
            "stage": "Public Comment Open",
            "impact_level": "Low",
            "summary_en": "Template summary.",
            "business_impact_en": "Template impact.",
            "recommended_action_en": "Review the official source.",
            "source_name": "Test Source",
            "source_url": "https://example.go.jp/a",
            "published_at": "2026-06-16",
            "last_checked": "2026-06-16",
        }

    def _result(self, title):
        return {
            "title_en": title,
            "summary_en": "AI summary.",
            "business_impact_en": "AI impact.",
            "recommended_action_en": "Consider reviewing the official source.",
            "confidence": "medium",
            "ai_notes": "Limited metadata.",
        }

    def test_summary_model_default_and_overrides(self):
        self.assertEqual(su.DEFAULT_MODEL, "claude-opus-4-8")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(su.resolve_model(None), "claude-opus-4-8")
        with mock.patch.dict("os.environ", {"ANTHROPIC_SUMMARY_MODEL": "summary-env"}, clear=True):
            self.assertEqual(su.resolve_model(None), "summary-env")
        with mock.patch.dict(
            "os.environ",
            {"ANTHROPIC_SUMMARY_MODEL": "summary-env", "ANTHROPIC_MODEL": "legacy-env"},
            clear=True,
        ):
            self.assertEqual(su.resolve_model("cli-model"), "cli-model")
            self.assertEqual(su.resolve_model(None), "summary-env")
        with mock.patch.dict("os.environ", {"ANTHROPIC_MODEL": "legacy-env"}, clear=True):
            self.assertEqual(su.resolve_model(None), "legacy-env")

    def test_apply_result_shortens_ai_title_to_public_cap(self):
        item = self._item()
        su.apply_result(item, self._result("Long AI title " * 20), "2026-06-16T00:00:00Z", "model")

        self.assertLessEqual(len(item["title_en"]), bpd.TITLE_MAX_CHARS)
        self.assertEqual(item["summary_source"], "claude")
        self.assertFalse(bpd.contains_japanese(item["title_en"]))

    def test_apply_result_preserves_first_seen_at_and_does_not_backfill_legacy_items(self):
        detected = self._item()
        detected["first_seen_at"] = "2026-06-17"
        legacy = self._item()

        su.apply_result(detected, self._result("AI title"), "2026-06-18T00:00:00Z", "model")
        su.apply_result(legacy, self._result("AI title"), "2026-06-18T00:00:00Z", "model")

        self.assertEqual(detected["first_seen_at"], "2026-06-17")
        self.assertNotIn("first_seen_at", legacy)

    def test_apply_result_preserves_comment_deadline(self):
        item = self._item()
        item["comment_deadline"] = "2026-07-18T23:59:00+09:00"

        su.apply_result(item, self._result("AI title"), "2026-06-18T00:00:00Z", "model")

        self.assertEqual(item["comment_deadline"], "2026-07-18T23:59:00+09:00")

    def test_validate_output_rejects_overlong_or_japanese_title_en(self):
        overlong = self._item()
        overlong["title_en"] = "A" * (bpd.TITLE_MAX_CHARS + 1)
        japanese = self._item()
        japanese["id"] = "raw-japanese"
        japanese["title_en"] = "Japanese title 個人情報"

        problems = su.validate_output(
            [overlong, japanese],
            {
                "raw-test": {"id": "raw-test", "source_url": "https://example.go.jp/a"},
                "raw-japanese": {"id": "raw-japanese", "source_url": "https://example.go.jp/a"},
            },
        )

        self.assertTrue(any("raw-test: title_en exceeds" in problem for problem in problems))
        self.assertTrue(any("raw-japanese: title_en contains Japanese" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()
