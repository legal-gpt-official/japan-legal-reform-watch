"""Cached LIVE reports must be no more trusted than freshly downloaded reports."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from live_report_evidence import validated_names, validated_v3


class EvidenceTests(unittest.TestCase):
    def test_whitelist_prevents_cached_run_metadata_override(self):
        value = {"dossiers": ["valid-dossier"], "dossier_versions": {"valid-dossier": "a"*64},
                 "checked_started_at": "2026-11-27T03:00:00+00:00", "source_sha": "b"*40,
                 "publish_run_id": "123", "pages_run_id": "456"}
        self.assertEqual(validated_v3({**value, "conclusion": "success", "id": 999}), value)
        for patch in ({"dossier_versions": {}}, {"source_sha": "bad"}, {"checked_started_at": "2026-11-27"},
                      {"publish_run_id": None}, {"dossiers": ["valid-dossier", "valid-dossier"]}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validated_v3({**value, **patch})

    def test_empty_report_carries_no_credit(self):
        self.assertEqual(validated_names([]), [])
        for value in (None, ["../bad"], ["good-dossier", "good-dossier"], [3]):
            with self.assertRaises(ValueError):
                validated_names(value)
