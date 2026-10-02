"""Behavior checks for the email-alert link helpers in docs/app.js.

The production app intentionally has no JS build or runtime dependencies. These
tests expose only its pure URL helpers inside Node's isolated VM context; the
DOM-ready callback is never invoked and no network call occurs.
"""

import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS = REPO_ROOT / "docs" / "app.js"
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is required for JavaScript behavior checks")
class TestAlertLinkJavaScriptBehavior(unittest.TestCase):
    def test_checkout_and_portal_urls_are_confined_to_stripe(self):
        harness = textwrap.dedent(
            r"""
            import assert from "node:assert/strict";
            import fs from "node:fs";
            import vm from "node:vm";

            const appPath = process.argv[2];
            let source = fs.readFileSync(appPath, "utf8");
            const closing = "\n})();";
            const closingIndex = source.lastIndexOf(closing);
            assert.notEqual(closingIndex, -1, "app.js IIFE closing marker must exist");
            source =
              source.slice(0, closingIndex) +
              `
              window.__JLRW_ALERT_TEST__ = {
                trustedIntegrationUrl,
                alertCheckoutUrl,
                alertManageUrl,
                filters,
              };
              ` +
              source.slice(closingIndex);

            const config = {
              checkoutLinks: {
                monthly: "https://buy.stripe.com/monthly-link",
                yearly: "https://buy.stripe.com/yearly-link",
              },
              localizedCheckoutLinks: {
                ja: {
                  monthly: "https://buy.stripe.com/ja-monthly",
                  yearly: "https://buy.stripe.com/ja-yearly?utm_source=dashboard",
                },
                "zh-Hans": {
                  monthly: "https://buy.stripe.com/zh-monthly",
                  yearly: "https://buy.stripe.com/zh-yearly",
                },
              },
              manageSubscriptionUrl: "https://billing.stripe.com/p/login/abc123",
            };
            const context = {
              console,
              Date,
              document: { addEventListener() {} },
              URL,
              URLSearchParams,
              window: {
                JLRW_ALERTS_CONFIG: config,
                JLRW_I18N: {
                  DEFAULT_LANG: "en",
                  SUPPORTED: ["en", "ja", "zh-Hans"],
                  csvHeadersLocalized() { return []; },
                },
              },
            };
            vm.runInNewContext(source, context, { filename: appPath });
            const helpers = context.window.__JLRW_ALERT_TEST__;

            // English opens the English link with the Stripe page pinned to English,
            // the language its area list is written in.
            assert.equal(helpers.filters.lang, "en");
            assert.equal(helpers.alertCheckoutUrl("monthly"), "https://buy.stripe.com/monthly-link?locale=en");
            assert.equal(helpers.alertCheckoutUrl("yearly"), "https://buy.stripe.com/yearly-link?locale=en");
            // Anything that is not "yearly" falls back to the monthly link.
            assert.equal(helpers.alertCheckoutUrl("team"), "https://buy.stripe.com/monthly-link?locale=en");
            assert.equal(helpers.alertManageUrl(), "https://billing.stripe.com/p/login/abc123");

            // Japanese and Chinese open their own links; existing query parameters stay.
            helpers.filters.lang = "ja";
            assert.equal(helpers.alertCheckoutUrl("monthly"), "https://buy.stripe.com/ja-monthly?locale=ja");
            assert.equal(
              helpers.alertCheckoutUrl("yearly"),
              "https://buy.stripe.com/ja-yearly?utm_source=dashboard&locale=ja"
            );
            helpers.filters.lang = "zh-Hans";
            assert.equal(helpers.alertCheckoutUrl("monthly"), "https://buy.stripe.com/zh-monthly?locale=zh");
            assert.equal(helpers.alertCheckoutUrl("yearly"), "https://buy.stripe.com/zh-yearly?locale=zh");

            // Without a link for the language (not yet created, or rejected as
            // untrusted), the English link is used exactly as configured: no
            // locale is forced onto an English area list.
            for (const missing of ["", undefined, "https://evil.example/zh", "http://buy.stripe.com/zh"]) {
              config.localizedCheckoutLinks["zh-Hans"].monthly = missing;
              assert.equal(helpers.alertCheckoutUrl("monthly"), "https://buy.stripe.com/monthly-link", String(missing));
            }
            config.localizedCheckoutLinks = undefined;
            helpers.filters.lang = "ja";
            assert.equal(helpers.alertCheckoutUrl("yearly"), "https://buy.stripe.com/yearly-link");
            helpers.filters.lang = "en";

            for (const bad of [
              "http://buy.stripe.com/monthly-link",
              "https://buy.stripe.com.evil.example/x",
              "https://buy.stripe.com:8443/x",
              "https://user@buy.stripe.com/x",
              "javascript:alert(1)",
              "https://billing.stripe.com/p/login/x",
              "",
            ]) {
              config.checkoutLinks.monthly = bad;
              assert.equal(helpers.alertCheckoutUrl("monthly"), "", bad);
            }

            for (const bad of [
              "https://billing.stripe.com/session/abc",
              "https://buy.stripe.com/p/login/abc",
              "https://billing.stripe.com.evil.example/p/login/abc",
              "http://billing.stripe.com/p/login/abc",
            ]) {
              config.manageSubscriptionUrl = bad;
              assert.equal(helpers.alertManageUrl(), "", bad);
            }

            assert.equal(
              helpers.trustedIntegrationUrl("https://buy.stripe.com/abc#@evil.example", "buy.stripe.com", "/"),
              "https://buy.stripe.com/abc"
            );

            config.checkoutLinks = undefined;
            assert.equal(helpers.alertCheckoutUrl("monthly"), "");
            """
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            harness_path = Path(temp_dir) / "alert-links-behavior.mjs"
            harness_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(harness_path), str(APP_JS)],
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
