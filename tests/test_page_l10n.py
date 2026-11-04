"""The detail pages name stages, areas and sources exactly as the dashboard does."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import alert_common as ac  # noqa: E402
import build_public_pages as bpp  # noqa: E402
import page_l10n as pl  # noqa: E402

I18N_JS = (ROOT / "docs" / "i18n.js").read_text(encoding="utf-8")


def js_label_map(name: str) -> dict[str, dict[str, str]]:
    """``var NAME = { ja: {...}, "zh-Hans": {...} };`` from docs/i18n.js."""
    block = re.search(r"var " + name + r" = \{(.*?)\n  \};", I18N_JS, re.S).group(1)
    result: dict[str, dict[str, str]] = {}
    for locale, body in re.findall(r'(?:^|\n)\s*"?(ja|zh-Hans)"?: \{(.*?)\}', block, re.S):
        result[locale] = dict(re.findall(r'"([^"]+)": "([^"]*)"', body))
    return result


class TestLabelsMatchDashboard(unittest.TestCase):
    def test_stage_impact_and_source_labels_equal_i18n_js(self):
        self.assertEqual(pl.STAGE_LABELS, js_label_map("STAGE_LABELS"))
        self.assertEqual(pl.IMPACT_LABELS, js_label_map("IMPACT_LABELS"))
        self.assertEqual(pl.SOURCE_LABELS, js_label_map("SOURCE_LABELS"))

    def test_area_labels_equal_i18n_js(self):
        js = js_label_map("AREA_LABELS")
        for locale in pl.LOCALES:
            for area, label in js[locale].items():
                self.assertEqual(pl.area_label(area, locale), label, area)
        self.assertEqual(ac.AREA_LABELS["ja"]["Finance / AML"], pl.area_label("Finance / AML", "ja"))


class TestFixedPhrases(unittest.TestCase):
    def test_every_phrase_has_all_three_languages(self):
        for key, entry in pl.STRINGS.items():
            self.assertEqual(set(entry), {"en", "ja", "zh-Hans"}, key)
            for locale in pl.LOCALES:
                self.assertTrue(entry[locale], (key, locale))

    def test_english_matches_the_existing_notices(self):
        self.assertEqual(pl.text("trust_body"), bpp.MONITORING_NOTICE)
        self.assertEqual(pl.text("timeline_notice"), bpp.TIMELINE_NOTICE)

    def test_deadline_keeps_its_date_and_time(self):
        self.assertEqual(pl.deadline_display("2026-12-04 (end of day, JST)", "ja"), "2026-12-04（日本時間・当日中）")
        self.assertEqual(pl.deadline_display("2026-12-04 00:00 JST", "zh-Hans"), "2026-12-04 00:00 JST")


if __name__ == "__main__":
    unittest.main()
