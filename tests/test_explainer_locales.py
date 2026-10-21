"""Regression tests for localized reform explainers and strict translation freshness."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from html import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_public_pages as bpp  # noqa: E402
import explainer_locales as el  # noqa: E402
import explainers as ex  # noqa: E402

INITIAL_DOSSIERS = (
    "appi-2026-amendment",
    "building-energy-2026-lifecycle-carbon",
    "fefta-2026-inward-investment-screening",
    "industrial-competitiveness-2026-amendment",
    "solar-pv-waste-recycling-2026",
    "waste-management-scrap-yards-2026",
    "workers-compensation-2026-amendment",
)


def entry_for(data):
    dossier = data["dossier_id"]
    return ex.Published(dossier, data, ex.Report(dossier), frozenset(ex.CATEGORIES))


class TestLocalizedExplainres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.canonical = ex.load_explainers()
        cls.translations = el.load_locales()

    def test_initial_seven_locales_are_rendered_only_when_fresh(self):
        # All seven have been backfilled. Do NOT require permanent freshness in
        # every PR: a later canonical English update must be publishable even
        # before the corresponding Japanese/Chinese rewrite catches up.
        for dossier_id in INITIAL_DOSSIERS:
            with self.subTest(dossier=dossier_id):
                data = self.canonical[dossier_id]
                bundle = self.translations[dossier_id]
                for lang in el.LOCALES:
                    self.assertIn(lang, bundle["translations"])
                    problems = el.translation_problems(data, bundle, lang)
                    stale = any(p in el.STALE_MARKERS for p in problems)
                    localized = el.localized_entry(entry_for(data), bundle, lang)
                    if stale:
                        self.assertIsNone(localized, "stale translation must not be published")
                        continue
                    self.assertEqual([], problems)
                    self.assertIsNotNone(localized)
                    html = "\n".join(bpp.render_explainer(localized, locale=lang))
                    self.assertIn(bundle["translations"][lang]["title"], html)
                    self.assertIn(bundle["translations"][lang]["quick_take"]["what_changed"], html)
                    self.assertIn(escape(bundle["translations"][lang]["business_impact"][0]), html)
                    self.assertIn("explainer-source-1", html)

    def test_stale_fingerprint_blocks_localized_publication(self):
        item = self.canonical[INITIAL_DOSSIERS[0]]
        stale = copy.deepcopy(self.translations[item["dossier_id"]])
        stale["english_source_fingerprint"] = "sha256:stale"
        self.assertIsNone(el.localized_entry(entry_for(item), stale, "ja"))
        self.assertIn("stale official-record fingerprint",
                      el.translation_problems(item, stale, "zh-Hans"))
        stale = copy.deepcopy(self.translations[item["dossier_id"]])
        stale["english_verified_at"] = "2020-01-01"
        self.assertIsNone(el.localized_entry(entry_for(item), stale, "ja"))

    def test_partial_or_unsafe_translation_is_withheld(self):
        item = self.canonical[INITIAL_DOSSIERS[0]]
        bad = copy.deepcopy(self.translations[item["dossier_id"]])
        bad["translations"]["ja"]["history"].pop()
        self.assertIsNone(el.localized_entry(entry_for(item), bad, "ja"))
        bad = copy.deepcopy(self.translations[item["dossier_id"]])
        bad["translations"]["zh-Hans"]["quick_take"]["what_changed"] = "<script>alert(1)</script>"
        self.assertIsNone(el.localized_entry(entry_for(item), bad, "zh-Hans"))

    def test_indexable_localized_page_has_hreflang_and_navigation(self):
        original = self.canonical[INITIAL_DOSSIERS[0]]
        bundle = self.translations[original["dossier_id"]]
        loc = el.localized_entry(entry_for(original), bundle, "ja")
        self.assertIsNotNone(loc)
        item = {
            "id": "raw-111222333",
            "title_en": "English original record",
            "title_ja": "原公表資料",
            "source_name": "Ministry of Finance (MOF) 新着情報",
            "source_url": "https://www.mof.go.jp/example.html",
            "summary_source": "claude",
            "summary_en": "An original record in English.",
            "published_at": "2026-09-01",
            "area": "Data / Privacy / AI",
            "stage": "Promulgated",
            "impact_level": "Medium",
        }
        html = bpp.render_item_page(
            item,
            explainer=loc,
            locale="ja",
            available_locales=("ja", "zh-Hans"),
        )
        self.assertIn('<html lang="ja">', html)
        self.assertIn('hreflang="ja"', html)
        self.assertIn('hreflang="zh-Hans"', html)
        self.assertIn('href="../../../style.css', html)
        self.assertIn('aria-current="page"', html)
        self.assertIn(bundle["translations"]["ja"]["title"], html)
        self.assertIn("改正の背景と目的", html)
        self.assertIn("企業への影響と実務対応", html)
        self.assertNotIn('id="item-summary-title"', html)


    def test_missing_and_stale_locales_do_not_block_valid_english(self):
        from unittest import mock
        data = self.canonical[INITIAL_DOSSIERS[0]]
        with mock.patch.object(el.ex, "load_explainers", return_value={data["dossier_id"]: data}), \
             mock.patch.object(el, "load_locales", return_value={}):
            errors, warnings = el.validate_translations()
            self.assertEqual([], errors)
            self.assertTrue(any("English-only" in w for w in warnings))
        stale = copy.deepcopy(self.translations[data["dossier_id"]])
        stale["english_file_sha"] = "0" * 40
        with mock.patch.object(el.ex, "load_explainers", return_value={data["dossier_id"]: data}), \
             mock.patch.object(el, "load_locales", return_value={data["dossier_id"]: stale}):
            errors, warnings = el.validate_translations()
            self.assertEqual([], errors)
            self.assertEqual(2, len(warnings))

    def test_malformed_current_locale_blocks_ci(self):
        from unittest import mock
        data = self.canonical[INITIAL_DOSSIERS[0]]
        invalid = copy.deepcopy(self.translations[data["dossier_id"]])
        invalid["translations"]["ja"]["history"] = []
        with mock.patch.object(el.ex, "load_explainers", return_value={data["dossier_id"]: data}), \
             mock.patch.object(el, "load_locales", return_value={data["dossier_id"]: invalid}):
            errors, warnings = el.validate_translations()
            self.assertEqual([], warnings)
            self.assertTrue(any("history" in e for e in errors))

    def test_missing_locale_fallback_is_explained_on_english_page(self):
        data = self.canonical[INITIAL_DOSSIERS[0]]
        item = {
            "id": "raw-111222333", "title_en": "Official original",
            "source_url": "https://www.mof.go.jp/example.html",
            "summary_source": "claude", "published_at": "2026-09-01",
        }
        html = bpp.render_item_page(item, explainer=entry_for(data), available_locales=("ja",))
        self.assertIn("explainer-locale-fallback", html)
        self.assertIn("中文版详细解读正在更新", html)
        self.assertIn("日本語の詳細解説は現在更新中", html)
        self.assertIn('hreflang="ja"', html)
        self.assertNotIn('hreflang="zh-Hans"', html)

    def test_default_english_remains_fully_rendered(self):
        data = self.canonical[INITIAL_DOSSIERS[0]]
        item = {
            "id": "raw-111222333", "title_en": "Official original", "title_ja": "原文",
            "source_url": "https://www.mof.go.jp/example.html",
            "summary_source": "claude", "summary_en": "A summary.", "published_at": "2026-09-01",
        }
        html = bpp.render_item_page(item, explainer=entry_for(data),
                                    available_locales=("ja", "zh-Hans"))
        self.assertIn('<html lang="en">', html)
        self.assertIn("Background &amp; Purpose", html)
        self.assertIn('id="item-summary-title"', html)
        self.assertIn('href="../../style.css', html)


if __name__ == "__main__":
    unittest.main()
