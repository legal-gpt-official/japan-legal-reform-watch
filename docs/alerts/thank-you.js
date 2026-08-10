/* global window, document, localStorage, URLSearchParams */

(function () {
  "use strict";

  var I18N = window.JLRW_I18N;
  if (!I18N) return;

  var languageSelect = document.getElementById("completion-language-select");
  var planWrap = document.getElementById("completion-plan");
  var planValue = document.getElementById("completion-plan-value");

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
    if (plan === "pro" || plan === "team") return plan;
    return "";
  }

  function updatePlan() {
    var plan = selectedPlan();
    if (!plan || !planWrap || !planValue) {
      if (planWrap) planWrap.hidden = true;
      return;
    }
    planValue.textContent = I18N.t(
      plan === "team" ? "checkout_thanks_plan_team" : "checkout_thanks_plan_pro"
    );
    planWrap.hidden = false;
  }

  function updateDashboardLinks(lang) {
    var href = "../index.html" + (lang === "zh-Hans" ? "?lang=zh-Hans" : "");
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

  if (languageSelect) {
    languageSelect.addEventListener("change", function () {
      applyLanguage(languageSelect.value, true);
    });
  }
})();
