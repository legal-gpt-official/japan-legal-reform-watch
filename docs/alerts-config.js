/* =============================================================
   Japan Legal Reform Watch — email-alert integration settings

   Public configuration only. Never place API keys, webhook secrets, or other
   credentials in this file: GitHub Pages serves it to every visitor.

   Values come from `python scripts/setup_alert_billing.py --apply`:
   checkoutLinks are the English Stripe Payment Links (buy.stripe.com),
   localizedCheckoutLinks the same plans with the area list written in
   Japanese or Simplified Chinese (Stripe never translates that list), and
   manageSubscriptionUrl is the Stripe customer-portal login page
   (billing.stripe.com/p/login/...). The dashboard opens the link for its
   current language and uses the English link while a language's link is
   empty. Until checkoutLinks are set, it shows that subscriptions are not
   open yet and offers only the free feeds.
   ============================================================= */

(function () {
  "use strict";

  window.JLRW_ALERTS_CONFIG = Object.freeze({
    checkoutLinks: Object.freeze({
      monthly: "https://buy.stripe.com/cNiaEYa927jv7H36WW3cc00",
      yearly: "https://buy.stripe.com/14A4gA1CwbzLbXjbdc3cc01",
    }),
    localizedCheckoutLinks: Object.freeze({
      ja: Object.freeze({ monthly: "", yearly: "" }),
      "zh-Hans": Object.freeze({ monthly: "", yearly: "" }),
    }),
    manageSubscriptionUrl: "https://billing.stripe.com/p/login/cNiaEYa927jv7H36WW3cc00",
  });
})();
