"""Behavior checks for the alert-pilot pure JavaScript helpers.

The production app intentionally has no JS build or runtime dependencies. These
tests expose only its pure URL/request-reference helpers inside Node's isolated
VM context; the DOM-ready callback is never invoked and no network call occurs.
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
class TestAlertPilotJavaScriptBehavior(unittest.TestCase):
    def test_request_reference_and_checkout_url_helpers(self):
        harness = textwrap.dedent(
            r"""
            import assert from "node:assert/strict";
            import crypto from "node:crypto";
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
                validAlertPilotRequestId,
                createAlertPilotRequestId,
                alertPilotCheckoutUrl,
              };
              ` +
              source.slice(closingIndex);

            const config = {
              stripePaymentLinks: {
                pro: "https://buy.stripe.com/pro-link",
                team: "https://buy.stripe.com/team-link",
              },
            };
            const context = {
              console,
              Date,
              document: { addEventListener() {} },
              URL,
              URLSearchParams,
              Uint8Array,
              window: {
                crypto: crypto.webcrypto,
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

            assert.equal(
              helpers.trustedIntegrationUrl(
                "https://buy.stripe.com:8443/abc",
                "buy.stripe.com",
                "/"
              ),
              ""
            );
            assert.equal(
              helpers.trustedIntegrationUrl(
                "https://user@buy.stripe.com/abc",
                "buy.stripe.com",
                "/"
              ),
              ""
            );
            assert.equal(
              helpers.trustedIntegrationUrl(
                "https://buy.stripe.com/abc#@evil.example",
                "buy.stripe.com",
                "/"
              ),
              "https://buy.stripe.com/abc"
            );

            const requestId = "jlrw_test_0123456789abcdef";
            const checkout = new URL(helpers.alertPilotCheckoutUrl("pro", requestId));
            assert.equal(checkout.origin, "https://buy.stripe.com");
            assert.equal(checkout.pathname, "/pro-link");
            assert.deepEqual(Array.from(checkout.searchParams.keys()), ["client_reference_id"]);
            assert.equal(checkout.searchParams.get("client_reference_id"), requestId);
            assert.equal(checkout.searchParams.has("email"), false);

            config.stripePaymentLinks.pro =
              "https://buy.stripe.com/pro-link?utm_source=pilot&client_reference_id=old#discard";
            const replaced = new URL(helpers.alertPilotCheckoutUrl("pro", requestId));
            assert.equal(replaced.searchParams.get("utm_source"), "pilot");
            assert.equal(replaced.searchParams.getAll("client_reference_id").length, 1);
            assert.equal(replaced.searchParams.get("client_reference_id"), requestId);
            assert.equal(replaced.hash, "");

            for (const invalid of [
              "jlrw_a_b&x=1",
              "jlrw_a_b#x",
              "JLRW_a_b",
              "jlrw_a-b",
              "jlrw_a_b\n",
              "jlrw_" + "a".repeat(195),
            ]) {
              assert.equal(helpers.validAlertPilotRequestId(invalid), "");
              assert.equal(helpers.alertPilotCheckoutUrl("pro", invalid), "");
            }

            const generated = new Set();
            for (let index = 0; index < 2000; index += 1) {
              const value = helpers.createAlertPilotRequestId();
              assert.match(value, /^[A-Za-z0-9_-]{1,200}$/);
              assert.equal(helpers.validAlertPilotRequestId(value), value);
              assert.equal(generated.has(value), false, "generated request ID collision");
              generated.add(value);
            }

            context.window.crypto = undefined;
            assert.equal(helpers.createAlertPilotRequestId(), "");
            """
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            harness_path = Path(temp_dir) / "alert-pilot-behavior.mjs"
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
