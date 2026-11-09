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
    def test_page_checks_require_three_languages_and_exact_titles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            locales = root / "locales"
            docs = root / "docs"
            locales.mkdir()
            dossier = "example-reform"
            titles = {"en": "English law & rule", "ja": "法律の解説", "zh": "法律解读"}
            en = {
                "dossier_id": dossier, "status": "published",
                "title_en": titles["en"], "verified_at": "2026-11-09",
                "quick_take": {"what_changed": "A substantive change to the English legal explanation."},
                "related_item_ids": ["raw-0123456789abcdef"],
            }
            (locales / f"{dossier}.json").write_text(json.dumps({
                "translations": {
                    "ja": {"title": titles["ja"], "quick_take": {"what_changed": "日本語の重要な改正"}},
                    "zh-Hans": {"title": titles["zh"], "quick_take": {"what_changed": "中文的重要修订"}}
                }
            }), encoding="utf-8")
            for lang in ("en", "ja", "zh"):
                path = docs / "raw-0123456789abcdef" / ("" if lang == "en" else lang) / "index.html"
                path.parent.mkdir(parents=True, exist_ok=True)
                marker = '<h2 id="explainer-title">' + html.escape(titles[lang], quote=True) + "</h2>"
                take = {
                    "en": "A substantive change to the English legal explanation.",
                    "ja": "日本語の重要な改正",
                    "zh": "中文的重要修订",
                }[lang]
                path.write_text(f"<html>{marker} {html.escape(take)} 2026-11-09</html>", encoding="utf-8")
            with mock.patch.object(live, "LOCALE_DIR", locales), mock.patch.object(live, "DOCS_DIR", docs):
                targets, errors = live.page_checks([en])
                self.assertEqual(len(targets), 3)
                self.assertEqual(errors, [])
                (docs / "raw-0123456789abcdef" / "zh" / "index.html").write_text("STALE", encoding="utf-8")
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
