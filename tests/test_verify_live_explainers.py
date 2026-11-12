"""Offline regression tests for the networked read-only explainer verification."""
import html
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import verify_live_explainers as live


class LiveExplainerTests(unittest.TestCase):
    def test_page_checks_require_full_three_language_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            locales = root / "locales"
            docs = root / "docs"
            locales.mkdir()
            dossier = "example-reform"
            item_id = "raw-0123456789abcdef"
            en = {
                "dossier_id": dossier, "status": "published", "title_en": "English law & rule",
                "verified_at": "2026-11-09", "related_item_ids": [item_id],
                "quick_take": {
                    "what_changed": "Change in legal scope.", "who_is_affected": "Affected employers.",
                    "what_to_do_now": "Check compliance."
                },
                "background": {"paragraphs": ["Evidence-based policy background & limits."]},
                "history": {"paragraphs": ["Council examined alternatives."]},
                "next_steps": {
                    "confirmed": [{"text": "Gazette published."}], "expected": ["Review final rule."],
                    "uncertainties": ["Commencement unresolved."]
                },
                "business_impact": {
                    "paragraphs": ["Contracts may change."], "checklist": ["Check applicable scope."]
                },
                "key_dates": [{"label": "Consultation closed."}]
            }
            tr = {
                "ja": {
                    "title": "法律の解説", "quick_take": {
                        "what_changed": "重要な改正", "who_is_affected": "対象事業者",
                        "what_to_do_now": "適用範囲を確認"
                    },
                    "background": ["政策背景と旧制度の限界"], "history": ["審議会の検討"],
                    "next_steps": {
                        "confirmed": ["官報で公表"], "expected": ["最終省令を確認"],
                        "uncertainties": ["施行期日未確定"]
                    },
                    "business_impact": ["委託契約への影響"], "checklist": ["対象を確認"],
                    "key_dates": ["意見募集終了"]
                },
                "zh-Hans": {
                    "title": "法律解读", "quick_take": {
                        "what_changed": "重要修订", "who_is_affected": "适用企业",
                        "what_to_do_now": "确认适用范围"
                    },
                    "background": ["政策背景和旧法限制"], "history": ["专家审议"],
                    "next_steps": {
                        "confirmed": ["官报公布"], "expected": ["核对最终规则"],
                        "uncertainties": ["施行日未定"]
                    },
                    "business_impact": ["合同或受影响"], "checklist": ["确认范围"],
                    "key_dates": ["征求意见结束"]
                },
            }
            (locales / f"{dossier}.json").write_text(
                json.dumps({"translations": tr}, ensure_ascii=False), encoding="utf-8"
            )
            for lang in ("en", "ja", "zh"):
                path = docs / item_id / ("" if lang == "en" else lang) / "index.html"
                path.parent.mkdir(parents=True, exist_ok=True)
                phrases = live.content_fragments(en, None if lang == "en" else tr["ja" if lang == "ja" else "zh-Hans"])
                path.write_text("<html>" + " ".join(phrases) + "</html>", encoding="utf-8")
            with mock.patch.object(live, "LOCALE_DIR", locales), mock.patch.object(live, "DOCS_DIR", docs):
                targets, errors = live.page_checks([en])
                self.assertEqual((len(targets), errors), (3, []))
                self.assertGreater(len(targets[0][1]), 10)
                # A stale history paragraph must fail despite unchanged title and quick take.
                ja_path = docs / item_id / "ja" / "index.html"
                ja_path.write_text(
                    ja_path.read_text(encoding="utf-8").replace("審議会の検討", "旧審議会の記載"),
                    encoding="utf-8",
                )
                targets, errors = live.page_checks([en])
                self.assertEqual(len(targets), 2)
                self.assertEqual(len(errors), 1)
                self.assertIn("stale", errors[0])


    def test_verify_never_accepts_http_200_with_wrong_article(self):
        with mock.patch.object(live, "fetch_and_match", side_effect=lambda item: (item[0], "HTTP 200 but expected title missing")):
            errors = live.verify([("https://example.test/a", "expected-title")], attempts=1, delay=0)
        self.assertEqual(set(errors), {"https://example.test/a"})

    def test_recent_dossier_selection_uses_jst_verification_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "yes.json").write_text(json.dumps({
                "dossier_id": "yes", "status": "published", "verified_at": "2026-11-09"
            }), encoding="utf-8")
            (path / "draft.json").write_text(json.dumps({
                "dossier_id": "draft", "status": "draft", "verified_at": "2026-11-09"
            }), encoding="utf-8")
            with mock.patch.object(live, "EN_DIR", path):
                self.assertEqual([x["dossier_id"] for x in live.recent_dossiers(3, {"yes"})], ["yes"])
                with self.assertRaises(ValueError):
                    live.recent_dossiers(3, {"draft"})


if __name__ == "__main__":
    unittest.main()
