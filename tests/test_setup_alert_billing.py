"""Offline tests for the one-time Stripe setup script (Stripe is faked)."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qsl, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import alert_common as ac  # noqa: E402
import setup_alert_billing as sab  # noqa: E402

LEGACY_PRO = "https://buy.stripe.com/fZu6oH2Fjg1D4mB3Eiawo00"


class FakeStripeHttp:
    """In-memory Stripe with just enough behavior for the setup script."""

    def __init__(self):
        self.products, self.prices, self.links, self.portals = [], [], [], []
        self.posts = []
        self.counter = 0
        self.links.append({"id": "plink_legacy", "url": LEGACY_PRO, "active": True, "metadata": {}})

    def _id(self, prefix):
        self.counter += 1
        return f"{prefix}_{self.counter}"

    def __call__(self, method, url, *, headers, body=None):
        parsed = urlsplit(url)
        path = parsed.path
        if method == "GET":
            query = parse_qsl(parsed.query)
            if path == "/v1/products":
                return 200, {"data": self.products, "has_more": False}
            if path == "/v1/prices":
                keys = {value for key, value in query if key == "lookup_keys[]"}
                return 200, {"data": [p for p in self.prices if p["lookup_key"] in keys], "has_more": False}
            if path == "/v1/payment_links":
                return 200, {"data": [link for link in self.links if link["active"]], "has_more": False}
            if path == "/v1/billing_portal/configurations":
                return 200, {"data": self.portals, "has_more": False}
        params = dict(parse_qsl(body.decode("utf-8"))) if body else {}
        self.posts.append((path, params))
        if path == "/v1/products":
            obj = {"id": self._id("prod"), "metadata": {"jlrw_role": params["metadata[jlrw_role]"]}}
            self.products.append(obj)
            return 200, obj
        if path == "/v1/prices":
            obj = {"id": self._id("price"), "lookup_key": params["lookup_key"]}
            self.prices.append(obj)
            return 200, obj
        if path == "/v1/payment_links":
            obj = {
                "id": self._id("plink"),
                "url": f"https://buy.stripe.com/new{self.counter}",
                "active": True,
                "metadata": {"jlrw_role": params["metadata[jlrw_role]"], "jlrw_price": params["metadata[jlrw_price]"]},
            }
            self.links.append(obj)
            return 200, obj
        if path.startswith("/v1/payment_links/"):
            link = next(l for l in self.links if l["id"] == path.rsplit("/", 1)[1])
            link["active"] = params.get("active") != "false"
            return 200, link
        if path == "/v1/billing_portal/configurations":
            obj = {
                "id": self._id("bpc"),
                "metadata": {"jlrw_role": "alert_portal"},
                "login_page": {"url": "https://billing.stripe.com/p/login/fake"},
            }
            self.portals.append(obj)
            return 200, obj
        return 404, {"error": {"message": "unknown"}}


class TestParams(unittest.TestCase):
    def test_flatten_params_matches_stripe_form_encoding(self):
        pairs = sab.flatten_params({"a": {"b": [{"c": 1}, {"c": True}]}, "d": None, "e": "x"})
        self.assertEqual(pairs, [("a[b][0][c]", "1"), ("a[b][1][c]", "true"), ("e", "x")])

    def test_payment_link_collects_required_area_from_every_channel(self):
        params = sab.payment_link_params("alert_monthly", "price_1")
        field = params["custom_fields"][0]
        self.assertEqual(field["key"], "area")
        self.assertRegex(field["key"], r"^[A-Za-z0-9]{1,200}$")
        self.assertLessEqual(len(field["label"]["custom"]), 50)
        self.assertFalse(field["optional"])
        self.assertEqual(
            [option["value"] for option in field["dropdown"]["options"]],
            [channel.value for channel in ac.CHANNELS],
        )
        self.assertEqual(
            params["after_completion"]["redirect"]["url"],
            ac.DASHBOARD_URL + "alerts/thank-you.html?plan=monthly",
        )
        self.assertLessEqual(len(params["custom_text"]["submit"]["message"]), 1200)
        self.assertIn("not legal advice", params["custom_text"]["submit"]["message"])

    def test_portal_lets_customers_self_serve(self):
        params = sab.portal_params("prod_1", ["price_m", "price_y"])
        features = params["features"]
        self.assertTrue(features["subscription_cancel"]["enabled"])
        self.assertEqual(features["subscription_cancel"]["mode"], "at_period_end")
        self.assertEqual(features["customer_update"]["allowed_updates"], ["email"])
        self.assertEqual(features["subscription_update"]["products"][0]["prices"], ["price_m", "price_y"])
        self.assertTrue(params["login_page"]["enabled"])

    def test_lookup_key_changes_with_price(self):
        self.assertNotEqual(sab.lookup_key("alert_monthly", "month", 1900), sab.lookup_key("alert_monthly", "month", 2900))


class TestRun(unittest.TestCase):
    def test_plan_mode_only_reads(self):
        http = FakeStripeHttp()
        result = sab.run(sab.Stripe("rk_test_x", http), apply=False, deactivate_legacy=True, log=lambda _m: None)
        self.assertEqual(http.posts, [])
        self.assertIn("create product", result["actions"])
        self.assertIn("deactivate legacy pilot link", result["actions"])
        self.assertTrue(any(l["url"] == LEGACY_PRO and l["active"] for l in http.links))

    def test_apply_creates_everything_once_and_is_idempotent(self):
        http = FakeStripeHttp()
        stripe = sab.Stripe("rk_test_x", http)
        first = sab.run(stripe, apply=True, deactivate_legacy=True)
        self.assertEqual(len(http.products), 1)
        self.assertEqual(len(http.prices), 2)
        self.assertEqual(first["manage_url"], "https://billing.stripe.com/p/login/fake")
        self.assertTrue(all(url.startswith("https://buy.stripe.com/") for url in first["payment_links"].values()))
        self.assertFalse(next(l for l in http.links if l["url"] == LEGACY_PRO)["active"])

        posts_before = len(http.posts)
        second = sab.run(stripe, apply=True, deactivate_legacy=True)
        self.assertEqual(second["actions"], [])
        self.assertEqual(len(http.posts), posts_before)
        self.assertEqual(second["payment_links"], first["payment_links"])

    def test_legacy_links_stay_active_without_the_flag(self):
        http = FakeStripeHttp()
        sab.run(sab.Stripe("rk_test_x", http), apply=True, deactivate_legacy=False)
        self.assertTrue(next(l for l in http.links if l["url"] == LEGACY_PRO)["active"])


class TestMain(unittest.TestCase):
    def test_main_requires_a_stripe_key(self):
        err = io.StringIO()
        with mock.patch.dict("os.environ", {"STRIPE_SETUP_API_KEY": "not-a-key"}), contextlib.redirect_stderr(err):
            self.assertEqual(sab.main([]), 2)

    def test_main_never_prints_the_key(self):
        http = FakeStripeHttp()
        out = io.StringIO()
        with mock.patch.dict("os.environ", {"STRIPE_SETUP_API_KEY": "rk_test_secretvalue"}), mock.patch.object(
            sab, "default_http", http
        ), contextlib.redirect_stdout(out):
            self.assertEqual(sab.main([]), 0)
        self.assertNotIn("secretvalue", out.getvalue())
        self.assertIn("mode: plan only", out.getvalue())


if __name__ == "__main__":
    unittest.main()
