#!/usr/bin/env python3
"""One-time Stripe setup for the self-serve email digest.

Creates (or finds, when re-run) everything the digest needs in Stripe, so nobody
has to click through the Stripe dashboard:

1. the Product ``Japan Legal Reform Watch — Daily Email Digest``;
2. a monthly and a yearly USD Price;
3. one Payment Link per Price and dashboard language (English, Japanese,
   Simplified Chinese), each with a required area dropdown built from
   alert_common.CHANNELS (the Stripe dashboard only allows 10 dropdown options;
   the API allows 200, which is why this is a script) and a redirect to the
   dashboard's checkout follow-up page. Stripe does not translate custom-field
   labels, so each language needs its own link; the option values are the same
   in every language, so the digest treats all links alike. Links created before
   languages existed carry no language tag and are kept as the English links;
4. a customer-portal configuration with a no-code login page, where customers
   cancel, switch monthly/yearly, change their delivery email, update cards,
   and download invoices themselves;
5. optionally (``--deactivate-legacy``) deactivation of the retired Pro/Team
   pilot Payment Links.

Every object is tagged with ``metadata[jlrw_role]`` and found again by that tag,
so the script is safe to re-run. Without ``--apply`` it only reads Stripe and
prints what it would do.

The key is read from STRIPE_SETUP_API_KEY or ``--key-file`` and is never
printed. Use a restricted key with write access to Products, Prices, Payment
Links and Customer portal, and delete it after the setup has run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlencode

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from alert_common import CHANNELS, CHECKOUT_LANGUAGES, DASHBOARD_URL, channel_label  # noqa: E402

STRIPE_API = "https://api.stripe.com"
USER_AGENT = "jlrw-alert-setup/1"

PRODUCT_NAME = "Japan Legal Reform Watch — Daily Email Digest"
PRODUCT_DESCRIPTION = (
    "Daily English email digest of Japanese legal and regulatory updates newly detected by the "
    "Japan Legal Reform Watch dashboard for one monitoring area, with public-comment deadline "
    "reminders when structured official data is available. Monitoring aid only; not legal advice."
)
PRICES = (
    # (role, interval, unit_amount in cents)
    ("alert_monthly", "month", 1900),
    ("alert_yearly", "year", 19000),
)
PLAN_PARAM = {"alert_monthly": "monthly", "alert_yearly": "yearly"}
THANK_YOU_URL = DASHBOARD_URL + "alerts/thank-you.html"
PRIVACY_POLICY_URL = "https://legal-gpt.com/privacy-policy/"
AREA_FIELD_KEY = "area"
# Stripe limits: field label <= 50 characters, submit message <= 1200.
AREA_FIELD_LABELS = {
    "en": "Monitoring area",
    "ja": "配信を受ける分野",
    "zh-Hans": "接收邮件的领域",
}
# The digest email is written in English whatever the checkout language, so the
# Japanese and Chinese checkouts say so next to the subscribe button.
SUBMIT_MESSAGES = {
    "en": (
        "Alert emails are monitoring aids, not legal advice. Original Japanese official sources remain "
        "authoritative. You can cancel at any time from the subscription portal linked in every email."
    ),
    "ja": (
        "アラートメールは英語で配信されます（日本語の原題を併記）。モニタリングの補助であり、法的助言ではありません。"
        "日本語の公式情報源が優先します。各メールに記載の契約管理ページから、いつでも解約できます。"
    ),
    "zh-Hans": (
        "提醒邮件以英文发送（附日文原标题），仅供监测参考，不构成法律建议；应以日文官方原始来源为准。"
        "您可随时通过每封邮件中的订阅管理链接取消订阅。"
    ),
}
LEGACY_PAYMENT_LINK_URLS = frozenset(
    {
        "https://buy.stripe.com/fZu6oH2Fjg1D4mB3Eiawo00",  # retired Pro pilot
        "https://buy.stripe.com/fZu9AT5RvdTvbP38YCawo01",  # retired Team pilot
    }
)
CANCELLATION_REASONS = (
    "too_expensive",
    "missing_features",
    "switched_service",
    "unused",
    "low_quality",
    "other",
)

HttpFunc = Callable[..., tuple[int, Any]]


class StripeError(Exception):
    pass


def lookup_key(role: str, interval: str, amount: int) -> str:
    # The amount is part of the key: changing the price creates a new Price
    # instead of silently reusing the old one.
    return f"jlrw_{role}_{interval}_usd_{amount}"


def flatten_params(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    """Encode nested dicts/lists the way Stripe's form API expects."""
    pairs: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key, sub in value.items():
            pairs.extend(flatten_params(sub, f"{prefix}[{key}]" if prefix else str(key)))
    elif isinstance(value, (list, tuple)):
        for index, sub in enumerate(value):
            pairs.extend(flatten_params(sub, f"{prefix}[{index}]"))
    elif isinstance(value, bool):
        pairs.append((prefix, "true" if value else "false"))
    elif value is not None:
        pairs.append((prefix, str(value)))
    return pairs


def area_dropdown_options(language: str = "en") -> list[dict[str, str]]:
    return [{"label": channel_label(channel, language), "value": channel.value} for channel in CHANNELS]


def thank_you_url(role: str, language: str) -> str:
    query = {"plan": PLAN_PARAM[role]}
    if language != "en":
        query["lang"] = language
    return f"{THANK_YOU_URL}?{urlencode(query)}"


def payment_link_params(role: str, price_id: str, language: str = "en") -> dict[str, Any]:
    return {
        "line_items": [{"price": price_id, "quantity": 1}],
        "custom_fields": [
            {
                "key": AREA_FIELD_KEY,
                "label": {"type": "custom", "custom": AREA_FIELD_LABELS[language]},
                "type": "dropdown",
                "optional": False,
                "dropdown": {"options": area_dropdown_options(language)},
            }
        ],
        "after_completion": {
            "type": "redirect",
            "redirect": {"url": thank_you_url(role, language)},
        },
        "submit_type": "subscribe",
        "billing_address_collection": "auto",
        "allow_promotion_codes": True,
        "tax_id_collection": {"enabled": True},
        "custom_text": {"submit": {"message": SUBMIT_MESSAGES[language]}},
        "subscription_data": {"metadata": {"jlrw_role": "alert_digest"}},
        "metadata": {"jlrw_role": role, "jlrw_price": price_id, "jlrw_language": language},
    }


def portal_params(product_id: str, price_ids: Sequence[str]) -> dict[str, Any]:
    return {
        "business_profile": {
            "headline": "Japan Legal Reform Watch — email digest subscription",
            "privacy_policy_url": PRIVACY_POLICY_URL,
        },
        "default_return_url": DASHBOARD_URL,
        "features": {
            "customer_update": {"enabled": True, "allowed_updates": ["email"]},
            "invoice_history": {"enabled": True},
            "payment_method_update": {"enabled": True},
            "subscription_cancel": {
                "enabled": True,
                "mode": "at_period_end",
                "cancellation_reason": {"enabled": True, "options": list(CANCELLATION_REASONS)},
            },
            "subscription_update": {
                "enabled": True,
                "default_allowed_updates": ["price"],
                "proration_behavior": "create_prorations",
                "products": [{"product": product_id, "prices": list(price_ids)}],
            },
        },
        "login_page": {"enabled": True},
        "metadata": {"jlrw_role": "alert_portal"},
    }


def default_http(method: str, url: str, *, headers: Mapping[str, str], body: bytes | None = None) -> tuple[int, Any]:
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    try:
        return status, json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, None


class Stripe:
    def __init__(self, api_key: str, http: HttpFunc = default_http):
        self._key = api_key
        self._http = http

    def _call(self, method: str, path: str, params: Sequence[tuple[str, str]] = ()) -> Mapping[str, Any]:
        headers = {"Authorization": f"Bearer {self._key}", "User-Agent": USER_AGENT}
        url = f"{STRIPE_API}{path}"
        body = None
        if method == "GET":
            if params:
                url += "?" + urlencode(list(params))
        else:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urlencode(list(params)).encode("utf-8")
        status, payload = self._http(method, url, headers=headers, body=body)
        if status != 200 or not isinstance(payload, dict):
            message = ""
            if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                # Stripe's error message names the parameter; it never echoes the key.
                message = str(payload["error"].get("message", ""))[:300]
            raise StripeError(f"{method} {path} failed (HTTP {status}) {message}".strip())
        return payload

    def get(self, path: str, params: Sequence[tuple[str, str]] = ()) -> Mapping[str, Any]:
        return self._call("GET", path, params)

    def post(self, path: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._call("POST", path, flatten_params(params))

    def list(self, path: str, params: Sequence[tuple[str, str]] = ()) -> Iterable[Mapping[str, Any]]:
        starting_after = ""
        while True:
            page = list(params) + [("limit", "100")]
            if starting_after:
                page.append(("starting_after", starting_after))
            payload = self.get(path, page)
            data = payload.get("data") or []
            yield from (obj for obj in data if isinstance(obj, dict))
            if not payload.get("has_more") or not data:
                return
            starting_after = data[-1].get("id", "")
            if not starting_after:
                return


def _metadata(obj: Mapping[str, Any], key: str) -> str:
    metadata = obj.get("metadata")
    value = metadata.get(key, "") if isinstance(metadata, dict) else ""
    return value if isinstance(value, str) else ""


def _role(obj: Mapping[str, Any]) -> str:
    return _metadata(obj, "jlrw_role")


def _language(link: Mapping[str, Any]) -> str:
    # Links created before checkout languages existed have no tag; they are the
    # live English links and must be found again, not replaced.
    return _metadata(link, "jlrw_language") or "en"


def run(stripe: Stripe, *, apply: bool, deactivate_legacy: bool, log: Callable[[str], None] = print) -> dict[str, Any]:
    result: dict[str, Any] = {}
    actions: list[str] = []

    # 1. Product
    product = next(
        (obj for obj in stripe.list("/v1/products", [("active", "true")]) if _role(obj) == "alert_digest"),
        None,
    )
    if product is None:
        actions.append("create product")
        if apply:
            product = stripe.post(
                "/v1/products",
                {"name": PRODUCT_NAME, "description": PRODUCT_DESCRIPTION, "metadata": {"jlrw_role": "alert_digest"}},
            )
    product_id = product.get("id") if product else "<new product>"
    result["product_id"] = product_id

    # 2. Prices
    price_ids: dict[str, str] = {}
    keys = {role: lookup_key(role, interval, amount) for role, interval, amount in PRICES}
    found = {
        obj.get("lookup_key"): obj
        for obj in stripe.list("/v1/prices", [("active", "true")] + [("lookup_keys[]", key) for key in keys.values()])
    }
    for role, interval, amount in PRICES:
        existing = found.get(keys[role])
        if existing:
            price_ids[role] = existing["id"]
            continue
        actions.append(f"create price {keys[role]}")
        if apply:
            created = stripe.post(
                "/v1/prices",
                {
                    "product": product_id,
                    "currency": "usd",
                    "unit_amount": amount,
                    "recurring": {"interval": interval},
                    "lookup_key": keys[role],
                    "transfer_lookup_key": True,
                    "nickname": f"JLRW digest ({interval}ly, USD {amount // 100})",
                },
            )
            price_ids[role] = created["id"]
        else:
            price_ids[role] = f"<new {role} price>"

    # 3. Payment links, one per price and checkout language (and the legacy
    # pilot links in the same pass)
    active_links = list(stripe.list("/v1/payment_links", [("active", "true")]))
    links: dict[str, dict[str, str]] = {language: {} for language in CHECKOUT_LANGUAGES}
    for role, _, _ in PRICES:
        plan = PLAN_PARAM[role]
        for language in CHECKOUT_LANGUAGES:
            # Only links of this role AND language are candidates, so adding a
            # language never touches the links of the others.
            slot = [link for link in active_links if _role(link) == role and _language(link) == language]
            match = next((link for link in slot if _metadata(link, "jlrw_price") == price_ids[role]), None)
            if match:
                links[language][plan] = match.get("url", "")
                continue
            actions.append(f"create {language} payment link {role}")
            if apply:
                created = stripe.post("/v1/payment_links", payment_link_params(role, price_ids[role], language))
                links[language][plan] = created.get("url", "")
            else:
                links[language][plan] = f"<new {language} {role} link>"
            # A link for an older price in the same slot stops selling that price.
            for stale in slot:
                actions.append(f"deactivate superseded {language} {role} link")
                if apply:
                    stripe.post(f"/v1/payment_links/{stale['id']}", {"active": False})
    result["payment_links"] = links

    legacy = [link for link in active_links if link.get("url") in LEGACY_PAYMENT_LINK_URLS]
    result["legacy_links_active"] = len(legacy)
    if deactivate_legacy:
        for link in legacy:
            actions.append("deactivate legacy pilot link")
            if apply:
                stripe.post(f"/v1/payment_links/{link['id']}", {"active": False})

    # 4. Customer portal
    portal = next(
        (
            obj
            for obj in stripe.list("/v1/billing_portal/configurations", [("active", "true")])
            if _role(obj) == "alert_portal"
        ),
        None,
    )
    if portal is None:
        actions.append("create customer portal configuration")
        if apply:
            portal = stripe.post(
                "/v1/billing_portal/configurations",
                portal_params(product_id, [price_ids[role] for role, _, _ in PRICES]),
            )
    login = portal.get("login_page") if portal else None
    result["manage_url"] = login.get("url", "") if isinstance(login, dict) else "<new portal login page>"

    result["actions"] = actions
    return result


def _read_key(args: argparse.Namespace) -> str:
    if args.key_file:
        return Path(args.key_file).expanduser().read_text(encoding="utf-8").strip()
    return os.environ.get("STRIPE_SETUP_API_KEY", "").strip()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="Create/update objects. Without it, only read and report.")
    parser.add_argument("--deactivate-legacy", action="store_true", help="Deactivate the retired Pro/Team pilot links.")
    parser.add_argument("--key-file", help="Read the Stripe key from this file instead of STRIPE_SETUP_API_KEY.")
    args = parser.parse_args(argv)

    key = _read_key(args)
    if not key.startswith(("sk_live_", "rk_live_", "sk_test_", "rk_test_")):
        print("error: provide a Stripe secret or restricted key via STRIPE_SETUP_API_KEY or --key-file.", file=sys.stderr)
        return 2
    try:
        result = run(Stripe(key, default_http), apply=args.apply, deactivate_legacy=args.deactivate_legacy)
    except StripeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    mode = "applied" if args.apply else "plan only (re-run with --apply to make these changes)"
    print(f"mode: {mode}")
    print(f"livemode_key: {key.startswith(('sk_live_', 'rk_live_'))}")
    for action in result["actions"] or ["nothing to change"]:
        print(f"action: {action}")
    print(f"product_id: {result['product_id']}")
    for language, plans in result["payment_links"].items():
        for plan, url in plans.items():
            print(f"payment_link_{plan}_{language}: {url}")
    print(f"manage_url: {result['manage_url']}")
    links = result["payment_links"]
    print("docs/alerts-config.js values:")
    print(f"  checkoutLinks: {json.dumps(links['en'])}")
    localized = {language: plans for language, plans in links.items() if language != "en"}
    print(f"  localizedCheckoutLinks: {json.dumps(localized)}")
    print(f"legacy_links_still_active: {result['legacy_links_active'] if not (args.apply and args.deactivate_legacy) else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
