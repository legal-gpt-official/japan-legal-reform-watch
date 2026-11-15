"""Three-language legal-meaning review list (lexical cues; reported, never blocking)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import explainer_meaning as em  # noqa: E402

EN = {
    "title_en": "Draft ordinance on reporting",
    "quick_take": {"what_changed": "Operators would be required to endeavour to report incidents.",
                   "who_is_affected": "Operators.", "what_to_do_now": "Review procedures."},
    "background": {"paragraphs": ["The draft has not yet been promulgated."]},
    "history": {"paragraphs": []}, "business_impact": {"paragraphs": [], "checklist": []},
    "next_steps": {"confirmed": [], "expected": [], "uncertainties": []}, "key_dates": [],
}


class MeaningTests(unittest.TestCase):
    def test_faithful_translation_has_no_findings(self):
        ja = {"title": "報告に関する省令案", "quick_take": {"what_changed": "事業者は事故の報告に努めることとなる案である。",
                                                     "who_is_affected": "事業者。", "what_to_do_now": "手続を見直す。"},
              "background": ["当該案はまだ公布されていない。"]}
        found = [f for f in em.findings(EN, ja, "ja") if f["concept"] in ("obligation", "not_yet", "promulgated")]
        self.assertEqual(found, [])

    def test_obligation_added_and_negation_dropped_are_listed_with_paths(self):
        zh = {"title": "关于报告的省令草案", "quick_take": {"what_changed": "运营者负有报告事故的法定义务。",
                                                    "who_is_affected": "运营者。", "what_to_do_now": "审查程序。"},
              "background": ["该草案已公布。"]}
        found = {(f["path"], f["concept"], f["direction"]) for f in em.findings(EN, zh, "zh-Hans")}
        self.assertIn(("quick_take.what_changed", "obligation", "added_in_translation"), found)
        self.assertIn(("background[0]", "not_yet", "missing_in_translation"), found)

    def test_review_list_never_blocks_until_a_calibration_justifies_it(self):
        self.assertEqual(em.BLOCKING, frozenset())
        self.assertEqual(em.blocking([{"path": "x", "concept": "obligation", "direction": "added_in_translation"}]), [])

    def test_corpus_report_covers_published_dossiers(self):
        report = em.corpus_report()
        self.assertGreater(report["segments"], 1000)
        self.assertEqual(set(report["rates"]), set(em.CONCEPTS))


if __name__ == "__main__":
    unittest.main()
