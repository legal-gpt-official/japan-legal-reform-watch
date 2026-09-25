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
      monthly: "",
      yearly: "",
    }),
    manageSubscriptionUrl: "",
  });
})();
