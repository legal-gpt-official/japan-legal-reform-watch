"""Checks for the email digest's terms, disclosure, and privacy links.

The paid section on the dashboard and the checkout follow-up page link to three
pages on legal-gpt.com. The terms page is Japanese (authoritative) followed by
an English reference translation, so the terms link depends on the language.
These tests read the repository only; no page is fetched.
"""

import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
INDEX_HTML = (DOCS / "index.html").read_text(encoding="utf-8")
THANK_YOU_HTML = (DOCS / "alerts" / "thank-you.html").read_text(encoding="utf-8")
THANK_YOU_JS = (DOCS / "alerts" / "thank-you.js").read_text(encoding="utf-8")
APP_JS = (DOCS / "app.js").read_text(encoding="utf-8")
I18N_PATH = DOCS / "i18n.js"
I18N_JS = I18N_PATH.read_text(encoding="utf-8")
NODE = shutil.which("node")

TERMS_URL = "https://legal-gpt.com/japan-legal-reform-watch-terms/"
# The id of the English section on the published terms page.
TERMS_EN_URL = TERMS_URL + "#jlrw-terms-en"
COMMERCE_URL = "https://legal-gpt.com/commerce-disclosur/"
PRIVACY_URL = "https://legal-gpt.com/privacy-policy/"
LINK_KEYS = ("alert_legal_terms", "alert_legal_commerce", "alert_legal_privacy")


def paid_legal_block():
    start = INDEX_HTML.index('<div id="alert-plan-legal"')
    return INDEX_HTML[start:INDEX_HTML.index("</div>", start)]


def thank_you_legal_nav():
    start = THANK_YOU_HTML.index('<nav aria-label="Legal information"')
    return THANK_YOU_HTML[start:THANK_YOU_HTML.index("</nav>", start)]


def anchor_tags(html):
    return re.findall(r"<a\b[^>]*>", html)


def dictionary_value(language_marker, next_marker, key):
    block = I18N_JS[I18N_JS.index(language_marker):I18N_JS.index(next_marker)]
    match = re.search(rf'^\s{{6}}{key}: "(.*)",$', block, re.M)
    if match is None:
        raise AssertionError(f"{key} missing between {language_marker!r} and {next_marker!r}")
    return match.group(1)


def english(key):
    return dictionary_value("    en: {", "    ja: {", key)


def japanese(key):
    return dictionary_value("    ja: {", '    "zh-Hans": {', key)


def chinese(key):
    return dictionary_value('    "zh-Hans": {', "  // -------- Controlled-vocabulary", key)


def function_body(source, signature, next_signature):
    start = source.index(signature)
    return source[start:source.index(next_signature, start)]


class TestAlertLegalLinksMarkup(unittest.TestCase):
    def test_paid_section_links_terms_disclosure_and_privacy(self):
        block = paid_legal_block()
        self.assertIn('class="alert-plan-legal" hidden', block)
        self.assertIn('data-i18n="alert_plan_consent"', block)
        self.assertIn('data-i18n-aria-label="alert_legal_nav"', block)
        tags = anchor_tags(block)
        self.assertEqual(len(tags), 3)
        for tag, (url, key) in zip(
            tags,
            ((TERMS_EN_URL, "alert_legal_terms"), (COMMERCE_URL, "alert_legal_commerce"), (PRIVACY_URL, "alert_legal_privacy")),
        ):
            with self.subTest(key=key):
                self.assertIn(f'href="{url}"', tag)
                self.assertIn(f'data-i18n="{key}"', tag)
                self.assertIn('target="_blank"', tag)
                self.assertIn('rel="noopener noreferrer"', tag)
        self.assertIn("data-localized-terms", tags[0])
        self.assertNotIn("data-localized-terms", tags[1] + tags[2])

    def test_consent_line_sits_beside_the_subscribe_buttons(self):
        actions = INDEX_HTML.index('id="alert-plan-actions"')
        legal = INDEX_HTML.index('id="alert-plan-legal"')
        unavailable = INDEX_HTML.index('id="alert-plan-unavailable"')
        self.assertLess(actions, legal)
        self.assertLess(legal, unavailable)

    def test_checkout_follow_up_page_repeats_the_links(self):
        tags = anchor_tags(thank_you_legal_nav())
        for url, key in ((TERMS_EN_URL, "alert_legal_terms"), (COMMERCE_URL, "alert_legal_commerce"), (PRIVACY_URL, "alert_legal_privacy")):
            with self.subTest(key=key):
                matching = [tag for tag in tags if f'href="{url}"' in tag]
                self.assertEqual(len(matching), 1)
                self.assertIn(f'data-i18n="{key}"', matching[0])
                self.assertIn('rel="noopener noreferrer"', matching[0])
        terms = [tag for tag in tags if TERMS_URL in tag]
        self.assertIn("data-localized-terms", terms[0])

    def test_english_markup_matches_the_dictionary(self):
        for html in (INDEX_HTML, THANK_YOU_HTML):
            for key in ("alert_plan_consent",) + LINK_KEYS:
                match = re.search(rf'data-i18n="{key}"\s*>\s*([^<]*?)\s*<', html)
                if match is None:
                    continue
                with self.subTest(key=key):
                    self.assertEqual(" ".join(match.group(1).split()), english(key))
        self.assertIn('aria-label="' + english("alert_legal_nav") + '"', INDEX_HTML)

    def test_consent_wording_in_every_language(self):
        self.assertIn("agree to the Terms of Service", english("alert_plan_consent"))
        self.assertIn("利用規約に同意", japanese("alert_plan_consent"))
        self.assertIn("同意服务条款", chinese("alert_plan_consent"))
        self.assertEqual(japanese("alert_legal_terms"), "利用規約")
        self.assertEqual(japanese("alert_legal_commerce"), "特定商取引法に基づく表記")
        # Pages that exist only in Japanese say so outside Japanese.
        for key in ("alert_legal_commerce", "alert_legal_privacy"):
            with self.subTest(key=key):
                self.assertTrue(english(key).endswith("(Japanese)"))
                self.assertTrue(chinese(key).endswith("（日文）"))


class TestAlertLegalLinksScripts(unittest.TestCase):
    def test_legal_block_follows_checkout_availability(self):
        self.assertIn('alertPlanLegal = $("#alert-plan-legal");', APP_JS)
        sync = function_body(APP_JS, "function syncAlertCheckoutLinks()", "function initAlerts()")
        self.assertIn("if (alertPlanLegal) alertPlanLegal.hidden = !available;", sync)

    def test_terms_links_follow_the_language(self):
        dashboard = function_body(APP_JS, "function applyLanguageDom()", "\n  }\n")
        self.assertIn('querySelectorAll("[data-localized-terms]")', dashboard)
        self.assertIn("I18N.subscriptionTermsUrl()", dashboard)
        follow_up = function_body(THANK_YOU_JS, "function applyLanguage(lang, syncUrl)", "\n  }\n")
        self.assertIn("updateTermsLinks();", follow_up)
        self.assertIn("I18N.subscriptionTermsUrl()", THANK_YOU_JS)
        self.assertIn("subscriptionTermsUrl: subscriptionTermsUrl,", I18N_JS)

    @unittest.skipUnless(NODE, "Node.js is required for JavaScript behavior checks")
    def test_terms_url_per_language(self):
        harness = textwrap.dedent(
            r"""
            import assert from "node:assert/strict";
            import fs from "node:fs";
            import vm from "node:vm";

            const [i18nPath, termsUrl] = process.argv.slice(2);
            const context = { window: {} };
            vm.runInNewContext(fs.readFileSync(i18nPath, "utf8"), context, { filename: i18nPath });
            const I18N = context.window.JLRW_I18N;

            assert.equal(I18N.getLang(), "en");
            assert.equal(I18N.subscriptionTermsUrl(), termsUrl + "#jlrw-terms-en");
            I18N.setLang("ja");
            assert.equal(I18N.subscriptionTermsUrl(), termsUrl);
            I18N.setLang("zh-Hans");
            assert.equal(I18N.subscriptionTermsUrl(), termsUrl + "#jlrw-terms-en");
            I18N.setLang("fr");
            assert.equal(I18N.getLang(), "en");
            assert.equal(I18N.subscriptionTermsUrl(), termsUrl + "#jlrw-terms-en");
            """
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            harness_path = Path(temp_dir) / "terms-url-behavior.mjs"
            harness_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(harness_path), str(I18N_PATH), TERMS_URL],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"Node behavior harness failed:\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
