/* =============================================================
   Japan Legal Reform Watch — email-alert integration settings

   Public configuration only. Never place API keys, webhook secrets, or other
   credentials in this file: GitHub Pages serves it to every visitor.

   Values come from `python scripts/setup_alert_billing.py --apply`:
   checkoutLinks are Stripe Payment Links (buy.stripe.com) and
   manageSubscriptionUrl is the Stripe customer-portal login page
   (billing.stripe.com/p/login/...). Until they are set, the dashboard shows
   that subscriptions are not open yet and offers only the free feeds.
   ============================================================= */

(function () {
  "use strict";

  window.JLRW_ALERTS_CONFIG = Object.freeze({
    checkoutLinks: Object.freeze({
      monthly: "https://buy.stripe.com/cNiaEYa927jv7H36WW3cc00",
      yearly: "https://buy.stripe.com/14A4gA1CwbzLbXjbdc3cc01",
    }),
    manageSubscriptionUrl: "https://billing.stripe.com/p/login/cNiaEYa927jv7H36WW3cc00",
  });
})();
