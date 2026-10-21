"""Regression tests for localized reform explainers and strict translation freshness."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from html import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_public_pages as bpp  # noqa: E402
import explainer_facts as ef  # noqa: E402
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


def fresh(bundle, data, langs=el.LOCALES):
    """A copy of ``bundle`` stamped as current for ``data``.

    Tests of rendering and validation must not depend on whether the committed
    translations happen to be fresh today: English may legitimately be updated
    ahead of them, and a test that then fails stops the daily data pipeline.
    """
    out = copy.deepcopy(bundle)
    for lang in langs:
        out["translations"][lang].update({
            "english_verified_at": data.get("verified_at"),
            "english_source_fingerprint": data.get("source_fingerprint"),
            "english_file_sha": el.canonical_file_sha(data["dossier_id"]),
        })
    return out


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
        stale = fresh(self.translations[item["dossier_id"]], item)
        for lang in el.LOCALES:
            stale["translations"][lang]["english_source_fingerprint"] = "sha256:stale"
        self.assertIsNone(el.localized_entry(entry_for(item), stale, "ja"))
        self.assertIn("stale official-record fingerprint",
                      el.translation_problems(item, stale, "zh-Hans"))
        stale = fresh(self.translations[item["dossier_id"]], item)
        stale["translations"]["ja"]["english_verified_at"] = "2020-01-01"
        self.assertIsNone(el.localized_entry(entry_for(item), stale, "ja"))

    def test_partial_or_unsafe_translation_is_withheld(self):
        item = self.canonical[INITIAL_DOSSIERS[0]]
        bad = fresh(self.translations[item["dossier_id"]], item)
        self.assertIsNotNone(el.localized_entry(entry_for(item), bad, "ja"))
        bad["translations"]["ja"]["history"].pop()
        self.assertIsNone(el.localized_entry(entry_for(item), bad, "ja"))
        bad = fresh(self.translations[item["dossier_id"]], item)
        bad["translations"]["zh-Hans"]["quick_take"]["what_changed"] = "<script>alert(1)</script>"
        self.assertIsNone(el.localized_entry(entry_for(item), bad, "zh-Hans"))

    def test_indexable_localized_page_has_hreflang_and_navigation(self):
        original = self.canonical[INITIAL_DOSSIERS[0]]
        bundle = fresh(self.translations[original["dossier_id"]], original)
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
        stale = fresh(self.translations[data["dossier_id"]], data)
        for lang in el.LOCALES:
            stale["translations"][lang]["english_file_sha"] = "0" * 40
        with mock.patch.object(el.ex, "load_explainers", return_value={data["dossier_id"]: data}), \
             mock.patch.object(el, "load_locales", return_value={data["dossier_id"]: stale}):
            errors, warnings = el.validate_translations()
            self.assertEqual([], errors)
            self.assertEqual(2, len(warnings))

    def test_malformed_current_locale_blocks_ci(self):
        from unittest import mock
        data = self.canonical[INITIAL_DOSSIERS[0]]
        invalid = fresh(self.translations[data["dossier_id"]], data)
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


    # ---------------------------------------------------------- 2026-10-21 audit

    def test_crlf_checkout_computes_the_git_blob_sha(self):
        # A Windows checkout (core.autocrlf=true) holds the English JSON with
        # CRLF; the stamp is Git's LF blob. Hashing raw bytes made every
        # translation look stale and a local page build deleted them all.
        import hashlib
        import tempfile
        from unittest import mock
        body = b'{\n  "dossier_id": "x-1"\n}\n'
        blob = hashlib.sha1(b"blob %d\0" % len(body) + body).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "x-1.json").write_bytes(body.replace(b"\n", b"\r\n"))
            with mock.patch.object(el.ex, "EXPLAINERS_DIR", Path(tmp)):
                self.assertEqual(blob, el.canonical_file_sha("x-1"))

    def test_each_locale_carries_its_own_freshness_stamp(self):
        # Refreshing the Japanese text and its stamp must not re-certify a
        # Chinese text that was written for older English.
        data = self.canonical[INITIAL_DOSSIERS[0]]
        bundle = fresh(self.translations[data["dossier_id"]], data, langs=("ja",))
        bundle["translations"]["zh-Hans"]["english_file_sha"] = "0" * 40
        self.assertIsNotNone(el.localized_entry(entry_for(data), bundle, "ja"))
        self.assertIsNone(el.localized_entry(entry_for(data), bundle, "zh-Hans"))
        # A bundle-level stamp is no longer accepted for either locale.
        legacy = fresh(self.translations[data["dossier_id"]], data)
        for lang in el.LOCALES:
            for key in el.STAMP_KEYS:
                legacy[key] = legacy["translations"][lang].pop(key, None)
        self.assertIsNone(el.localized_entry(entry_for(data), legacy, "ja"))

    def test_bundle_level_stamp_is_reported(self):
        from unittest import mock
        data = self.canonical[INITIAL_DOSSIERS[0]]
        bundle = fresh(self.translations[data["dossier_id"]], data)
        bundle["english_file_sha"] = "0" * 40
        with mock.patch.object(el.ex, "load_explainers", return_value={data["dossier_id"]: data}), \
             mock.patch.object(el, "load_locales", return_value={data["dossier_id"]: bundle}):
            errors, _ = el.validate_translations()
        self.assertTrue(any("one per locale" in e for e in errors))

    def test_english_only_update_keeps_english_and_withholds_locales(self):
        # The English is edited after translation (its blob changes). The English
        # page renders in full, both translations are withheld, and validation
        # warns instead of failing CI.
        import tempfile
        from unittest import mock
        data = copy.deepcopy(self.canonical[INITIAL_DOSSIERS[0]])
        bundle = fresh(self.translations[data["dossier_id"]], data)
        data["background"]["paragraphs"][0] += " A later English clarification."
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / (data["dossier_id"] + ".json")).write_text(ex.dump(data), encoding="utf-8")
            with mock.patch.object(el.ex, "EXPLAINERS_DIR", Path(tmp)), \
                 mock.patch.object(el.ex, "load_explainers", return_value={data["dossier_id"]: data}), \
                 mock.patch.object(el, "load_locales", return_value={data["dossier_id"]: bundle}):
                for lang in el.LOCALES:
                    self.assertIsNone(el.localized_entry(entry_for(data), bundle, lang))
                errors, warnings = el.validate_translations()
        self.assertEqual([], errors)
        self.assertEqual(2, len(warnings))
        html = "\n".join(bpp.render_explainer(entry_for(data)))
        self.assertIn("A later English clarification.", html)

    def test_translation_stating_a_changed_number_or_date_is_withheld(self):
        data = self.canonical["building-energy-2026-lifecycle-carbon"]
        for lang, old, new in (("ja", "15,000戸", "1,500戸"), ("zh-Hans", "2027年4月1日", "2027年4月2日")):
            with self.subTest(lang=lang):
                bundle = fresh(self.translations[data["dossier_id"]], data)
                self.assertEqual([], el.translation_problems(data, bundle, lang))
                text = json.dumps(bundle["translations"][lang], ensure_ascii=False)
                self.assertIn(old, text)
                bundle["translations"][lang] = json.loads(text.replace(old, new))
                problems = el.translation_problems(data, bundle, lang)
                self.assertTrue(any("date or number the English does not" in p for p in problems), problems)
                self.assertIsNone(el.localized_entry(entry_for(data), bundle, lang))

    def test_markdown_links_in_translations_are_rejected(self):
        data = self.canonical[INITIAL_DOSSIERS[0]]
        bundle = fresh(self.translations[data["dossier_id"]], data)
        bundle["translations"]["ja"]["checklist"][0] = "[個人情報保護委員会](https://evil.example/)を確認する。"
        self.assertIsNone(el.localized_entry(entry_for(data), bundle, "ja"))

    def test_unreadable_translation_file_is_reported_without_hiding_others(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            good = self.translations[INITIAL_DOSSIERS[0]]
            (Path(tmp) / (INITIAL_DOSSIERS[0] + ".json")).write_text(json.dumps(good), encoding="utf-8")
            (Path(tmp) / "broken.json").write_text("{not json", encoding="utf-8")
            (Path(tmp) / "renamed.json").write_text(json.dumps(good), encoding="utf-8")
            problems = []
            loaded = el.load_locales(Path(tmp), problems=problems)
        self.assertEqual([INITIAL_DOSSIERS[0]], list(loaded))
        self.assertEqual(2, len(problems))

    def test_translated_page_shows_its_own_instrument_status(self):
        # A shared explainer must never imply that every amended instrument shares
        # the reform's stage -- on the Japanese and Chinese pages too.
        data = self.canonical[INITIAL_DOSSIERS[0]]
        bundle = fresh(self.translations[data["dossier_id"]], data)
        status = {"instrument": "外国為替令の一部を改正する政令", "number": "令和八年政令第二百九十六号",
                  "target": "外国為替令", "promulgated": bpp.date(2026, 9, 16), "effective_dates": [],
                  "pending": True}
        for lang, marker in (("ja", "政令で定められる予定"), ("zh-Hans", "将由政令规定")):
            with self.subTest(lang=lang):
                loc = el.localized_entry(entry_for(data), bundle, lang)
                html = "\n".join(bpp.render_explainer(loc, status, lang))
                self.assertIn(marker, html)
                self.assertIn("外国為替令", html)
                self.assertNotIn("timeline of related official records below", html)


class TestExplainerFacts(unittest.TestCase):
    def test_equivalent_notations_are_not_flagged(self):
        en = ["Promulgated on 17 July 2026 as Act No. 56 of 2026; about 500,000 tonnes a year; "
              "about 40% of emissions; comments closed between 7 and 10 October 2026; "
              "around January and February 2027; within one year and six months; 2,000 units or more."]
        for local in (
            "令和8年法律第56号として令和八年7月17日に公布。年間約50万トン。排出量の約4割。"
            "意見募集は2026年10月7日から10日まで。2027年1〜2月頃。1年6か月以内。2,000戸以上。",
            "2026年7月17日以2026年第56号法律公布；每年约50万吨；约40%；2026年10月7日至10日；"
            "2027年1月至2月；一年六个月内；2,000户或以上。",
        ):
            with self.subTest(local=local[:12]):
                self.assertEqual([], ef.unsupported_facts(en, [local]))

    def test_changed_dates_and_numbers_are_flagged(self):
        en = ["Promulgated on 17 July 2026 as Act No. 56 of 2026; 15,000 units; 30 days."]
        self.assertEqual(["number 57"], ef.unsupported_facts(en, ["令和8年法律第57号として2026年7月17日に公布。15,000戸。30日。"]))
        self.assertEqual(["date 2026-07-18"], ef.unsupported_facts(en, ["2026年7月18日公布。令和8年法律第56号。"]))
        self.assertEqual(["number 1500"], ef.unsupported_facts(en, ["1,500户；30日；2026年第56号法律。"]))
        self.assertEqual(["number 60"], ef.unsupported_facts(en, ["等待期为60日。"]))
        self.assertEqual(["number 3"], ef.unsupported_facts(en, ["时效为三年。"]))

    def test_committed_translations_state_only_english_facts(self):
        canonical = ex.load_explainers()
        for dossier_id, bundle in el.load_locales().items():
            for lang in el.LOCALES:
                with self.subTest(dossier=dossier_id, lang=lang):
                    self.assertEqual([], ef.unsupported_facts(
                        el.english_texts(canonical[dossier_id]), el.local_texts(bundle["translations"][lang])))


if __name__ == "__main__":
    unittest.main()
