/* global window, document, localStorage, URL, URLSearchParams */

(function () {
  "use strict";

  var I18N = window.JLRW_I18N;
  if (!I18N) return;

  var languageSelect = document.getElementById("completion-language-select");
  var planWrap = document.getElementById("completion-plan");
  var planValue = document.getElementById("completion-plan-value");
  var manageLink = document.getElementById("completion-manage");
  var config = window.JLRW_ALERTS_CONFIG || {};

  function readStoredLanguage() {
    try {
      return localStorage.getItem(I18N.STORAGE_KEY);
    } catch (error) {
      return null;
    }
  }

  function persistLanguage(lang) {
    try {
      if (lang === I18N.DEFAULT_LANG) {
        localStorage.removeItem(I18N.STORAGE_KEY);
      } else {
        localStorage.setItem(I18N.STORAGE_KEY, lang);
      }
    } catch (error) {
      // URL language state remains available if storage is blocked.
    }
  }

  function selectedPlan() {
    var plan = new URLSearchParams(window.location.search).get("plan");
    if (plan === "monthly" || plan === "yearly") return plan;
    return "";
  }

  function updatePlan() {
    var plan = selectedPlan();
    if (!plan || !planWrap || !planValue) {
      if (planWrap) planWrap.hidden = true;
      return;
    }
    planValue.textContent = I18N.t(
      plan === "yearly" ? "checkout_thanks_plan_yearly" : "checkout_thanks_plan_monthly"
    );
    planWrap.hidden = false;
  }

  // Only Stripe's own customer-portal login page may become this link.
  function trustedManageUrl(value) {
    if (typeof value !== "string" || !value.trim()) return "";
    try {
      var parsed = new URL(value.trim());
      if (
        parsed.protocol !== "https:" ||
        parsed.host !== "billing.stripe.com" ||
        parsed.username ||
        parsed.password ||
        parsed.pathname.indexOf("/p/login/") !== 0
      ) {
        return "";
      }
      parsed.hash = "";
      return parsed.href;
    } catch (error) {
      return "";
    }
  }

  function updateManageLink() {
    if (!manageLink) return;
    var url = trustedManageUrl(config.manageSubscriptionUrl);
    if (url) manageLink.setAttribute("href", url);
    manageLink.hidden = !url;
  }

  function updateDashboardLinks(lang) {
    var href =
      "../index.html" + (lang === I18N.DEFAULT_LANG ? "" : "?lang=" + encodeURIComponent(lang));
    document.querySelectorAll("[data-dashboard-link]").forEach(function (link) {
      link.setAttribute("href", href);
    });
  }

  function applyLanguage(lang, syncUrl) {
    var normalized = I18N.normalize(lang);
    I18N.setLang(normalized);
    document.documentElement.lang = normalized;
    document.title = I18N.t("checkout_thanks_page_title");
    I18N.applyStatic(document);
    if (languageSelect) languageSelect.value = normalized;
    updatePlan();
    updateDashboardLinks(normalized);

    if (syncUrl) {
      var params = new URLSearchParams(window.location.search);
      if (normalized === I18N.DEFAULT_LANG) {
        params.delete("lang");
      } else {
        params.set("lang", normalized);
      }
      var query = params.toString();
      window.history.replaceState(null, "", window.location.pathname + (query ? "?" + query : ""));
      persistLanguage(normalized);
    }
  }

  var params = new URLSearchParams(window.location.search);
  var requestedLanguage = params.get("lang");
  var initialLanguage = requestedLanguage
    ? I18N.normalize(requestedLanguage)
    : I18N.normalize(readStoredLanguage() || I18N.DEFAULT_LANG);
  applyLanguage(initialLanguage, false);
  updateManageLink();

  if (languageSelect) {
    languageSelect.addEventListener("change", function () {
      applyLanguage(languageSelect.value, true);
    });
  }
})();
