/* =============================================================
   Japan Legal Reform Watch — alert-pilot integration settings

   Public configuration only. Never place API keys, webhook secrets, or other
   credentials in this file: GitHub Pages serves it to every visitor.
   ============================================================= */

(function () {
  "use strict";

  window.JLRW_ALERTS_CONFIG = Object.freeze({
    inquiryEndpoint:
      "https://legal-gpt.com/wp-json/contact-form-7/v1/contact-forms/99/feedback",
    inquiryFormId: "99",
    inquiryUnitTag: "wpcf7-f99-p100-o1",
    inquiryContainerPost: "100",
    fallbackContactUrl: "https://legal-gpt.com/contact/?inquiry=jlrw-alert-pilot",
    privacyPolicyUrl: "https://legal-gpt.com/privacy-policy/",

    // Public recurring-price checkout URLs. These are Payment Links, not secret
    // API credentials. Checkout remains gated behind an accepted pilot inquiry.
    stripePaymentLinks: Object.freeze({
      pro: "https://buy.stripe.com/fZu6oH2Fjg1D4mB3Eiawo00",
      team: "https://buy.stripe.com/fZu9AT5RvdTvbP38YCawo01",
    }),
  });
})();
