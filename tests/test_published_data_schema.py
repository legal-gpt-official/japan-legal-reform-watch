"""Schema regression tests for the published file docs/data/legal_updates.json.

Read-only: validates the checked-in artifact that the dashboard (docs/app.js)
actually loads, so a bad rebuild cannot silently ship a broken or weakened file.
Allowed vocabularies are derived from scripts/build_public_data.py where
possible, so rule additions stay in sync automatically.
"""

import json
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_public_data as bpd  # noqa: E402

PUBLISHED_PATH = REPO_ROOT / "docs" / "data" / "legal_updates.json"

REQUIRED_FIELDS = (
    "id", "title_en", "title_ja", "area", "stage", "impact_level",
    "summary_en", "business_impact_en", "recommended_action_en",
    "source_name", "source_url", "published_at", "last_checked",
    "relevance_score",
)
NON_EMPTY_FIELDS = (
    "id", "title_en", "title_ja", "area", "stage", "impact_level",
    "summary_en", "business_impact_en", "recommended_action_en",
    "source_name", "source_url", "last_checked",
)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Stages classify_stage() can emit (keep in sync with that function).
ALLOWED_STAGES = {
    "Public Comment Open", "Public Comment Closed", "Public Comment Results Published",
    "Draft Guideline", "Bill Submitted", "Enacted", "Promulgated",
    "Scheduled to Take Effect", "In Force", "Government Announcement",
}


def allowed_areas() -> set:
    """Collect every area label the classifier can produce, plus 'Other'."""
    areas = {"Other"}
    for table in (
        bpd.AREA_RULES, bpd.UTF8_AREA_RULES, bpd.ADDITIONAL_AREA_RULES,
        bpd.METI_AREA_RULES, bpd.CAA_AREA_RULES, bpd.PPC_AREA_RULES, bpd.JFTC_AREA_RULES,
    ):
        areas.update(area for area, _ in table)
    areas.update(area for area, _ in bpd.AREA_SOURCE_FALLBACK)
    return areas


class TestPublishedDataSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(PUBLISHED_PATH, encoding="utf-8") as f:
            cls.items = json.load(f)

    def test_is_nonempty_array_capped_at_50(self):
        self.assertIsInstance(self.items, list)
        self.assertGreater(len(self.items), 0)
        self.assertLessEqual(len(self.items), bpd.MAX_OUTPUT_ITEMS)

    def test_ids_are_unique(self):
        ids = [it["id"] for it in self.items]
        self.assertEqual(len(ids), len(set(ids)))

    def test_required_fields_present(self):
        for it in self.items:
            missing = [k for k in REQUIRED_FIELDS if k not in it]
            self.assertFalse(missing, f"{it.get('id')}: missing {missing}")

    def test_key_fields_non_empty(self):
        for it in self.items:
            for key in NON_EMPTY_FIELDS:
                with self.subTest(id=it["id"], field=key):
                    value = it[key]
                    self.assertIsInstance(value, str)
                    self.assertTrue(value.strip(), f"{it['id']}: empty {key}")

    def test_impact_level_vocabulary(self):
        for it in self.items:
            self.assertIn(it["impact_level"], ("Low", "Medium", "High"), it["id"])

    def test_stage_vocabulary(self):
        for it in self.items:
            self.assertIn(it["stage"], ALLOWED_STAGES, it["id"])

    def test_area_vocabulary(self):
        allowed = allowed_areas()
        for it in self.items:
            self.assertIn(it["area"], allowed, it["id"])

    def test_dates_are_iso_or_empty(self):
        for it in self.items:
            with self.subTest(id=it["id"]):
                if it["published_at"]:
                    self.assertRegex(it["published_at"], DATE_RE)
                self.assertRegex(it["last_checked"], DATE_RE)

    def test_source_url_is_http(self):
        for it in self.items:
            self.assertTrue(
                it["source_url"].startswith(("https://", "http://")),
                f"{it['id']}: unexpected source_url scheme",
            )

    def test_relevance_score_is_numeric(self):
        for it in self.items:
            self.assertIsInstance(it["relevance_score"], (int, float), it["id"])

    def test_summary_source_vocabulary(self):
        for it in self.items:
            if "summary_source" in it:
                self.assertIn(it["summary_source"], ("claude", "rule_based"), it["id"])

    def test_claude_items_carry_ai_metadata(self):
        claude_items = [it for it in self.items if it.get("summary_source") == "claude"]
        # The checked-in file contains Stage 3 summaries; preservation must keep >= 1.
        self.assertGreaterEqual(len(claude_items), 1)
        for it in claude_items:
            with self.subTest(id=it["id"]):
                self.assertTrue(str(it.get("summarized_at", "")).strip())
                self.assertTrue(str(it.get("summary_model", "")).strip())
                self.assertIn(it.get("confidence"), ("high", "medium", "low"))
                # AI summaries replace the rule-based template sentence.
                self.assertNotEqual(it["summary_en"], bpd.SUMMARY_EN)


if __name__ == "__main__":
    unittest.main()
