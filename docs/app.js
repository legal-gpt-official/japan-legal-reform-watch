/* =============================================================
   Japan Legal Reform Watch by LegalOS
   Client-side dashboard logic — vanilla JS, no dependencies.
   ============================================================= */

(function () {
  "use strict";

  // i18n layer (docs/i18n.js, loaded first). English is canonical; zh-Hans is an
  // optional overlay. All user-facing strings route through this namespace.
  const I18N = window.JLRW_I18N;
  const ALERTS_CONFIG = window.JLRW_ALERTS_CONFIG || {};

  const STORAGE_KEY = "jlrw_disclaimer_accepted_v1";
  const SAVED_SEARCHES_STORAGE_KEY = "jlrw-saved-searches-v1";
  const MAX_SAVED_SEARCHES = 5;
  const SAVED_SEARCH_QUERY_KEYS = [
    "q",
    "area",
    "stage",
    "source",
    "impact",
    "sort",
    "ai",
    "new",
    "year",
  ];
  // The canonical file is the uncapped all-years dataset used only when the
  // visitor explicitly selects All years. Normal browsing loads one yearly
  // shard from the manifest, keeping initial transfer and parsing bounded.
  const DATA_URL = "./data/legal_updates.json";
  const DATA_MANIFEST_URL = "./data/legal_updates_manifest.json";
  const ALL_PERIODS = "all";
  const UNDATED_PERIOD = "undated";

  // Preferred display order for impact level.
  const IMPACT_ORDER = ["High", "Medium", "Low"];
  // Monitoring views default to the newest official publication date. The
  // Relevance option preserves the build-ranked JSON order, which already
  // includes stage, impact, and recency adjustments.
  const DEFAULT_SORT = "published";
  const SORT_VALUES = ["relevance", "published", "checked", "detected"];
  const SORT_LABELS = {
    relevance: "Relevance",
    published: "Published date",
    checked: "Last checked",
    detected: "First detected",
  };
  const NEWLY_DETECTED_DAYS = 7;
  const DAY_MS = 24 * 60 * 60 * 1000;

  // Cards render in pages: 50 on load, +50 per "Load more" click. Filters and
  // search always evaluate the full selected period, not just rendered cards.
  const PAGE_SIZE = 50;

  // English-first display labels for sources (audience: non-Japanese-reading
  // professionals). Keys are the EXACT `source_name` values produced by
  // scripts/fetch_updates.py — those internal values stay unchanged and remain
  // the filter <option> values; only the visible label is translated.
  // When adding a source to fetch_updates.py, add its display name here too.
  const SOURCE_DISPLAY_NAMES = {
    "e-Gov Public Comment (意見募集案件一覧)": "e-Gov Public Comment",
    "House of Representatives (衆議院) 議案情報": "House of Representatives — Diet Bills",
    "e-Gov Law Search (法令更新一覧)": "e-Gov Law Search — Updated Laws",
    "Japan Exchange Group (JPX) Public Comments": "Japan Exchange Group (JPX) — Public Comments",
    "Tokyo Stock Exchange (JPX) Rule Revisions": "Tokyo Stock Exchange (JPX) — Rule Revisions",
    "Pharmaceuticals and Medical Devices Agency (PMDA) Safety Updates": "Pharmaceuticals and Medical Devices Agency (PMDA) — Safety Updates",
    "Japan Securities Dealers Association (JSDA) Public Comments": "Japan Securities Dealers Association (JSDA) — Public Comments",
    "Japan Securities Dealers Association (JSDA) Public Comment Results": "Japan Securities Dealers Association (JSDA) — Public Comment Results",
    "Courts in Japan (裁判所) Recent Supreme Court Decisions": "Courts in Japan — Recent Supreme Court Decisions",
    "Securities and Exchange Surveillance Commission (SESC) Enforcement Updates": "Securities and Exchange Surveillance Commission (SESC) — Enforcement Updates",
    "Financial Services Agency (金融庁) 新着情報": "Financial Services Agency (FSA)",
    "経済産業省 (METI) ニュースリリース": "Ministry of Economy, Trade and Industry (METI)",
    "Ministry of Health, Labour and Welfare (厚生労働省) 新着情報": "Ministry of Health, Labour and Welfare (MHLW)",
    "Digital Agency (デジタル庁) 新着・更新": "Digital Agency",
    "消費者庁 (CAA) 新着情報": "Consumer Affairs Agency (CAA)",
    "個人情報保護委員会 (PPC) 新着情報": "Personal Information Protection Commission (PPC)",
    "公正取引委員会 (JFTC) 報道発表": "Japan Fair Trade Commission (JFTC)",
    "法務省 (MOJ) 新着情報": "Ministry of Justice (MOJ)",
    "環境省 (MOE) 報道発表": "Ministry of the Environment (MOE)",
    "財務省 (MOF) 新着情報": "Ministry of Finance (MOF)",
    "国税庁 (NTA) 新着・通達": "National Tax Agency (NTA)",
    "総務省 (MIC) 新着情報": "Ministry of Internal Affairs and Communications (MIC)",
    "国土交通省 (MLIT) 報道発表": "Ministry of Land, Infrastructure, Transport and Tourism (MLIT)",
    "農林水産省 (MAFF) 報道発表": "Ministry of Agriculture, Forestry and Fisheries (MAFF)",
  };

  // Compact URL slugs for shareable filter state. Values are the exact internal
  // source_name strings; do not change those internal names.
  const SOURCE_SLUGS = {
    "egov": "e-Gov Public Comment (意見募集案件一覧)",
    "shugiin-bills": "House of Representatives (衆議院) 議案情報",
    "egov-laws": "e-Gov Law Search (法令更新一覧)",
    "jpx-comments": "Japan Exchange Group (JPX) Public Comments",
    "jpx-rules": "Tokyo Stock Exchange (JPX) Rule Revisions",
    "pmda": "Pharmaceuticals and Medical Devices Agency (PMDA) Safety Updates",
    "jsda-comments": "Japan Securities Dealers Association (JSDA) Public Comments",
    "jsda-results": "Japan Securities Dealers Association (JSDA) Public Comment Results",
    "courts-supreme": "Courts in Japan (裁判所) Recent Supreme Court Decisions",
    "sesc": "Securities and Exchange Surveillance Commission (SESC) Enforcement Updates",
    "fsa": "Financial Services Agency (金融庁) 新着情報",
    "mhlw": "Ministry of Health, Labour and Welfare (厚生労働省) 新着情報",
    "digital-agency": "Digital Agency (デジタル庁) 新着・更新",
    "meti": "経済産業省 (METI) ニュースリリース",
    "caa": "消費者庁 (CAA) 新着情報",
    "ppc": "個人情報保護委員会 (PPC) 新着情報",
    "jftc": "公正取引委員会 (JFTC) 報道発表",
    "moj": "法務省 (MOJ) 新着情報",
    "moe": "環境省 (MOE) 報道発表",
    "mof": "財務省 (MOF) 新着情報",
    "nta": "国税庁 (NTA) 新着・通達",
    "mic": "総務省 (MIC) 新着情報",
    "mlit": "国土交通省 (MLIT) 報道発表",
    "maff": "農林水産省 (MAFF) 報道発表",
  };

  const SOURCE_SLUGS_BY_NAME = Object.keys(SOURCE_SLUGS).reduce((acc, slug) => {
    acc[SOURCE_SLUGS[slug]] = slug;
    return acc;
  }, {});

  // Untrusted-safe: returns a display label for known sources, otherwise the
  // raw value unchanged. Output is always escaped at render time regardless.
  function formatSourceDisplayName(sourceName) {
    return SOURCE_DISPLAY_NAMES[sourceName] || sourceName;
  }

  function getSourceSlug(sourceName) {
    return SOURCE_SLUGS_BY_NAME[sourceName] || "";
  }

  function getSourceNameFromSlug(slug) {
    if (typeof slug !== "string") return "";
    return SOURCE_SLUGS[slug.trim().toLowerCase()] || "";
  }

  // -------- State --------
  let allUpdates = [];
  let archiveManifest = null;
  const datasetCache = new Map();
  let visibleCount = PAGE_SIZE;
  let savedSearches = [];
  let savedSearchDialogOpener = null;
  const filters = {
    period: "",
    area: "",
    stage: "",
    source: "",
    impact: "",
    sort: DEFAULT_SORT,
    search: "",
    aiSummaryOnly: false,
    newlyDetectedOnly: false,
    lang: I18N.DEFAULT_LANG,
  };

  // The locale whose translations.<locale> block the card/search/CSV/copy layers
  // read. English remains the canonical fallback for every field.
  const TRANSLATION_LOCALE = "zh-Hans";
  let savedSearchStatusKey = "";
  let savedSearchStatusIsError = false;
  let alertPilotStatusKey = "";
  let alertPilotStatusState = "";
  let alertPilotIsSubmitting = false;

  // -------- Language preference (URL > localStorage > English) --------
  function readStoredLang() {
    try {
      return localStorage.getItem(I18N.STORAGE_KEY);
    } catch (e) {
      return null;
    }
  }

  function persistLang(lang) {
    try {
      if (lang === I18N.DEFAULT_LANG) {
        localStorage.removeItem(I18N.STORAGE_KEY);
      } else {
        localStorage.setItem(I18N.STORAGE_KEY, lang);
      }
    } catch (e) {
      // localStorage may be unavailable (private mode, file://). URL still carries lang.
    }
  }

  // -------- DOM helpers --------
  const $ = (sel) => document.querySelector(sel);

  let modalEl,
    acceptBtn,
    searchInput,
    filterArea,
    filterStage,
    filterSource,
    filterImpact,
    filterSort,
    filterPeriod,
    languageSelect,
    mobileFiltersToggle,
    filterPanel,
    activeFilterSummary,
    quickFilterButtons,
    cardsEl,
    emptyEl,
    errorEl,
    metaEl,
    dataStatusList,
    saveSearchBtn,
    manageSavedSearchesBtn,
    savedSearchDialog,
    closeSavedSearchDialogBtn,
    savedSearchForm,
    savedSearchNameInput,
    saveCurrentSearchBtn,
    savedSearchCurrentSummary,
    savedSearchStatus,
    savedSearchCapacity,
    savedSearchList,
    openAlertPilotFormBtn,
    alertPilotFormWrap,
    alertPilotForm,
    alertPilotNameInput,
    alertPilotEmailInput,
    alertPilotCompanyInput,
    alertPilotPlanSelect,
    alertPilotPlanButtons,
    alertPilotPlanCards,
    alertPilotFrequencySelect,
    alertPilotFocusInput,
    alertPilotScopeWarning,
    alertPilotConsentInput,
    alertPilotHoneypotInput,
    alertPilotSubmitBtn,
    alertPilotStatus,
    alertPilotReference,
    alertPilotReferenceValue,
    alertPilotFallback,
    alertPilotCheckout,
    alertPilotPrivacyLink,
    exportCsvBtn,
    exportStatusEl,
    loadMoreWrap,
    loadMoreBtn;

  function cacheDom() {
    modalEl = $("#disclaimer-modal");
    acceptBtn = $("#accept-disclaimer");
    searchInput = $("#search-input");
    filterArea = $("#filter-area");
    filterStage = $("#filter-stage");
    filterSource = $("#filter-source");
    filterImpact = $("#filter-impact");
    filterSort = $("#filter-sort");
    filterPeriod = $("#filter-period");
    languageSelect = $("#language-select");
    mobileFiltersToggle = $("#mobile-filters-toggle");
    filterPanel = $("#filter-panel");
    activeFilterSummary = $("#active-filter-summary");
    quickFilterButtons = Array.from(document.querySelectorAll("[data-quick-filter]"));
    cardsEl = $("#cards");
    emptyEl = $("#empty-state");
    errorEl = $("#error-state");
    metaEl = $("#results-meta");
    dataStatusList = $("#data-status-list");
    saveSearchBtn = $("#save-search");
    manageSavedSearchesBtn = $("#manage-saved-searches");
    savedSearchDialog = $("#saved-search-dialog");
    closeSavedSearchDialogBtn = $("#close-saved-search-dialog");
    savedSearchForm = $("#saved-search-form");
    savedSearchNameInput = $("#saved-search-name");
    saveCurrentSearchBtn = $("#save-current-search");
    savedSearchCurrentSummary = $("#saved-search-current-summary");
    savedSearchStatus = $("#saved-search-status");
    savedSearchCapacity = $("#saved-search-capacity");
    savedSearchList = $("#saved-search-list");
    openAlertPilotFormBtn = $("#open-alert-pilot-form");
    alertPilotFormWrap = $("#alert-pilot-form-wrap");
    alertPilotForm = $("#alert-pilot-form");
    alertPilotNameInput = $("#alert-pilot-name");
    alertPilotEmailInput = $("#alert-pilot-email");
    alertPilotCompanyInput = $("#alert-pilot-company");
    alertPilotPlanSelect = $("#alert-pilot-plan");
    alertPilotPlanButtons = Array.from(document.querySelectorAll("[data-alert-plan]"));
    alertPilotPlanCards = Array.from(document.querySelectorAll("[data-alert-plan-card]"));
    alertPilotFrequencySelect = $("#alert-pilot-frequency");
    alertPilotFocusInput = $("#alert-pilot-focus");
    alertPilotScopeWarning = $("#alert-pilot-scope-warning");
    alertPilotConsentInput = $("#alert-pilot-consent");
    alertPilotHoneypotInput = $("#alert-pilot-website");
    alertPilotSubmitBtn = $("#submit-alert-pilot");
    alertPilotStatus = $("#alert-pilot-status");
    alertPilotReference = $("#alert-pilot-reference");
    alertPilotReferenceValue = $("#alert-pilot-reference-value");
    alertPilotFallback = $("#alert-pilot-fallback");
    alertPilotCheckout = $("#alert-pilot-checkout");
    alertPilotPrivacyLink = $("#alert-pilot-privacy-link");
    exportCsvBtn = $("#export-csv");
    exportStatusEl = $("#export-status");
    loadMoreWrap = $("#load-more-wrap");
    loadMoreBtn = $("#load-more");
  }

  // Any filter/search change restarts paging at the first 50 matches.
  function resetVisibleCount() {
    visibleCount = PAGE_SIZE;
  }

  // -------- Disclaimer Modal --------
  function showModal() {
    modalEl.hidden = false;
    document.body.classList.add("modal-open");
    // Move focus to the accept button so keyboard users can act immediately.
    window.setTimeout(() => acceptBtn && acceptBtn.focus(), 0);
  }

  function hideModal() {
    modalEl.hidden = true;
    document.body.classList.remove("modal-open");
  }

  // Keep keyboard focus inside the dialog while it is open, so the behavior
  // matches aria-modal="true". Escape intentionally does NOT close the modal.
  function trapModalFocus(event) {
    if (event.key !== "Tab" || !modalEl || modalEl.hidden) return;
    const focusables = modalEl.querySelectorAll(
      'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'
    );
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement;
    const insideModal = modalEl.contains(active);

    if (event.shiftKey) {
      if (!insideModal || active === first) {
        event.preventDefault();
        last.focus();
      }
    } else if (!insideModal || active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function initModal() {
    let accepted = null;
    try {
      accepted = localStorage.getItem(STORAGE_KEY);
    } catch (e) {
      // localStorage may be unavailable (private mode, file://, etc.). Show modal each visit.
      accepted = null;
    }

    if (!accepted) {
      showModal();
    }

    document.addEventListener("keydown", trapModalFocus, true);

    acceptBtn.addEventListener("click", () => {
      try {
        localStorage.setItem(STORAGE_KEY, new Date().toISOString());
      } catch (e) {
        // Silent fail — user can still dismiss for this session.
      }
      hideModal();
    });
  }

  // -------- Data loading --------
  async function fetchJson(url) {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }

  function validPeriodValue(value) {
    return value === ALL_PERIODS || value === UNDATED_PERIOD || /^\d{4}$/.test(value || "");
  }

  function validateArchiveManifest(value) {
    if (!value || value.schema_version !== 1 || !Array.isArray(value.periods)) {
      throw new Error("Unexpected archive manifest shape.");
    }
    const seen = new Set();
    let periodTotal = 0;
    value.periods.forEach((entry) => {
      if (
        !entry ||
        !validPeriodValue(entry.value) ||
        entry.value === ALL_PERIODS ||
        entry.file !== "./data/archive/" + entry.value + ".json" ||
        !Number.isInteger(entry.count) ||
        entry.count < 0 ||
        seen.has(entry.value)
      ) {
        throw new Error("Invalid archive manifest period.");
      }
      seen.add(entry.value);
      periodTotal += entry.count;
    });
    if (
      !Number.isInteger(value.total_items) ||
      value.total_items < 0 ||
      periodTotal !== value.total_items ||
      (value.periods.length > 0 && !seen.has(value.latest_period))
    ) {
      throw new Error("Invalid archive manifest totals.");
    }
    return value;
  }

  function periodEntry(value) {
    if (!archiveManifest) return null;
    return archiveManifest.periods.find((entry) => entry.value === value) || null;
  }

  function resolvePeriod(value) {
    if (value === ALL_PERIODS) return ALL_PERIODS;
    return periodEntry(value) ? value : archiveManifest.latest_period;
  }

  function requestedPeriodFromUrl() {
    const params = new URLSearchParams(window.location.search);
    return resolvePeriod(params.get("year") || "");
  }

  function periodDisplayLabel(value) {
    if (value === ALL_PERIODS) return I18N.t("period_all");
    if (value === UNDATED_PERIOD) return I18N.t("period_undated");
    if (archiveManifest && value === archiveManifest.latest_period) {
      return I18N.t("period_latest", { year: value });
    }
    return value;
  }

  function populatePeriodOptions() {
    if (!filterPeriod || !archiveManifest) return;
    filterPeriod.innerHTML = "";
    archiveManifest.periods.forEach((entry) => {
      filterPeriod.appendChild(new Option(periodDisplayLabel(entry.value), entry.value));
    });
    filterPeriod.appendChild(new Option(periodDisplayLabel(ALL_PERIODS), ALL_PERIODS));
    filterPeriod.value = filters.period;
  }

  async function datasetForPeriod(period) {
    if (datasetCache.has(period)) return datasetCache.get(period).slice();
    const entry = period === ALL_PERIODS ? null : periodEntry(period);
    const url = period === ALL_PERIODS ? DATA_URL : entry && entry.file;
    if (!url) throw new Error("Archive period is unavailable: " + period);
    const data = await fetchJson(url);
    if (!Array.isArray(data)) {
      throw new Error("Unexpected data shape: expected an array.");
    }
    if (entry && data.length !== entry.count) {
      throw new Error("Archive count does not match manifest: " + period);
    }
    if (period === ALL_PERIODS && data.length !== archiveManifest.total_items) {
      throw new Error("All-years count does not match manifest.");
    }
    datasetCache.set(period, data.slice());
    return data.slice();
  }

  function setPeriodLoading(isLoading) {
    if (filterPeriod) filterPeriod.disabled = isLoading;
    if (!isLoading) return;
    metaEl.textContent = I18N.t("period_loading");
    updateExportState([]);
  }

  async function installPeriodDataset(period) {
    setPeriodLoading(true);
    try {
      const data = await datasetForPeriod(period);
      // Preserve the published order within each shard. Stage 2 owns relevance ranking.
      allUpdates = data;
      filters.period = period;
      populateFilterOptions();
    } finally {
      setPeriodLoading(false);
    }
  }

  async function handlePeriodChange(nextPeriod) {
    const resolved = resolvePeriod(nextPeriod);
    if (resolved === filters.period) return;
    const previous = filters.period;
    try {
      await installPeriodDataset(resolved);
      resetVisibleCount();
      applyLanguageDom();
      renderDataStatus();
      render();
      // Data-dependent filters may have been cleared if the new period does not
      // contain their selected values, so write the URL only after installation.
      updateUrlFromFilters();
    } catch (err) {
      filters.period = previous;
      populatePeriodOptions();
      console.error("[JLRW] Failed to load archive period:", err);
      showError();
    }
  }

  async function loadData() {
    try {
      archiveManifest = validateArchiveManifest(await fetchJson(DATA_MANIFEST_URL));
      filters.period = requestedPeriodFromUrl();
      populatePeriodOptions();
      await installPeriodDataset(filters.period);
      restoreFiltersFromUrl(); // resolves language (URL > localStorage > English)
      applyLanguageDom(); // localize chrome, filter options, <title>, <html lang>
      renderDataStatus();
      render();
      setSavedSearchControlsAvailable(true);
    } catch (err) {
      console.error("[JLRW] Failed to load data:", err);
      showError();
    }
  }

  function showError() {
    if (filterPeriod) filterPeriod.disabled = false;
    cardsEl.innerHTML = "";
    emptyEl.hidden = true;
    errorEl.hidden = false;
    metaEl.textContent = "";
    renderDataStatusUnavailable();
    updateExportState([]);
    setSavedSearchControlsAvailable(false);
  }

  // -------- Filter options --------
  function unique(arr, key) {
    const set = new Set();
    arr.forEach((x) => {
      if (x && x[key] != null && x[key] !== "") set.add(x[key]);
    });
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }

  function uniqueByCount(arr, key) {
    const counts = new Map();
    arr.forEach((x) => {
      if (x && x[key] != null && x[key] !== "") {
        counts.set(x[key], (counts.get(x[key]) || 0) + 1);
      }
    });
    return Array.from(counts.keys()).sort((a, b) => {
      const countDiff = counts.get(b) - counts.get(a);
      return countDiff || a.localeCompare(b);
    });
  }

  function populateFilterOptions() {
    [filterArea, filterStage, filterSource, filterImpact].forEach((selectEl) => {
      while (selectEl.options.length > 1) selectEl.remove(1);
    });
    const areas = unique(allUpdates, "area");
    const stages = unique(allUpdates, "stage");
    const sources = uniqueByCount(allUpdates, "source_name");
    const impacts = unique(allUpdates, "impact_level");

    areas.forEach((a) => filterArea.appendChild(new Option(a, a)));
    stages.forEach((s) => filterStage.appendChild(new Option(s, s)));
    // Label is the English-first display name; the option VALUE stays the raw
    // source_name so filtering still matches records exactly.
    sources.forEach((s) => filterSource.appendChild(new Option(formatSourceDisplayName(s), s)));

    // Impact levels in canonical order (High > Medium > Low) when present.
    const orderedImpacts = IMPACT_ORDER.filter((i) => impacts.includes(i)).concat(
      impacts.filter((i) => !IMPACT_ORDER.includes(i))
    );
    orderedImpacts.forEach((i) => filterImpact.appendChild(new Option(i, i)));
    filters.area = hasOption(filterArea, filters.area) ? filters.area : "";
    filters.stage = hasOption(filterStage, filters.stage) ? filters.stage : "";
    filters.source = hasOption(filterSource, filters.source) ? filters.source : "";
    filters.impact = hasOption(filterImpact, filters.impact) ? filters.impact : "";
    syncFilterControls();
  }

  function hasOption(selectEl, value) {
    if (!value) return false;
    return Array.from(selectEl.options).some((option) => option.value === value);
  }

  function ensureSourceOption(sourceName) {
    if (!sourceName || hasOption(filterSource, sourceName)) return;
    filterSource.appendChild(new Option(formatSourceDisplayName(sourceName), sourceName));
  }

  function isValidSort(value) {
    return SORT_VALUES.includes(value);
  }

  function sortLabel(value) {
    return SORT_LABELS[value] || SORT_LABELS[DEFAULT_SORT];
  }

  const SORT_I18N_KEYS = {
    relevance: "sort_relevance",
    published: "sort_published",
    checked: "sort_checked",
    detected: "sort_detected",
  };

  function localizedSortLabel(value) {
    return I18N.t(SORT_I18N_KEYS[value] || SORT_I18N_KEYS[DEFAULT_SORT]);
  }

  function restoreFiltersFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const area = params.get("area") || "";
    const stage = params.get("stage") || "";
    const source = getSourceNameFromSlug(params.get("source") || "");
    const impact = params.get("impact") || "";
    const sort = params.get("sort") || DEFAULT_SORT;

    filters.period = requestedPeriodFromUrl();
    filters.search = params.get("q") || "";
    filters.area = hasOption(filterArea, area) ? area : "";
    filters.stage = hasOption(filterStage, stage) ? stage : "";
    filters.source = source || "";
    filters.impact = hasOption(filterImpact, impact) ? impact : "";
    filters.sort = isValidSort(sort) ? sort : DEFAULT_SORT;
    filters.aiSummaryOnly = params.get("ai") === "1";
    filters.newlyDetectedOnly = params.get("new") === "7";

    // Language precedence: URL > localStorage > English. Unknown values fall back.
    const urlLang = params.get("lang");
    const lang = urlLang
      ? I18N.normalize(urlLang)
      : I18N.normalize(readStoredLang() || I18N.DEFAULT_LANG);
    filters.lang = lang;
    I18N.setLang(lang);

    if (filters.source) {
      ensureSourceOption(filters.source);
    }
    resetVisibleCount();
    syncFilterControls();
  }

  function buildFilterParams(includeLanguage) {
    const params = new URLSearchParams();
    const query = filters.search.trim();
    const sourceSlug = getSourceSlug(filters.source);

    if (query) params.set("q", query);
    if (filters.area) params.set("area", filters.area);
    if (filters.stage) params.set("stage", filters.stage);
    if (sourceSlug) params.set("source", sourceSlug);
    if (filters.impact) params.set("impact", filters.impact);
    if (filters.sort !== DEFAULT_SORT) params.set("sort", filters.sort);
    if (filters.aiSummaryOnly) params.set("ai", "1");
    if (filters.newlyDetectedOnly) params.set("new", "7");
    // The latest year is the default and stays absent from clean shared URLs.
    if (archiveManifest && filters.period !== archiveManifest.latest_period) {
      params.set("year", filters.period);
    }
    // Default English is the absence of the param, keeping shared URLs clean.
    if (includeLanguage && filters.lang && filters.lang !== I18N.DEFAULT_LANG) {
      params.set("lang", filters.lang);
    }
    return params;
  }

  function updateUrlFromFilters() {
    if (!window.history || !window.history.replaceState) return;

    const params = buildFilterParams(true);
    const queryString = params.toString();
    const nextUrl =
      window.location.pathname +
      (queryString ? "?" + queryString : "") +
      window.location.hash;
    window.history.replaceState(null, "", nextUrl);
  }

  function applyFilterChange() {
    resetVisibleCount();
    syncFilterControls();
    render();
    updateUrlFromFilters();
  }

  function syncFilterControls() {
    searchInput.value = filters.search;
    filterArea.value = filters.area;
    filterStage.value = filters.stage;
    filterSource.value = filters.source;
    filterImpact.value = filters.impact;
    filterSort.value = filters.sort;
    if (filterPeriod) filterPeriod.value = filters.period;
  }

  function syncLanguageSelector() {
    if (languageSelect) languageSelect.value = filters.lang;
  }

  // Relabel data-driven <option>s (Area/Stage/Impact/Source) for the active
  // language WITHOUT changing their values. The "All ..." defaults carry their
  // own data-i18n keys and are handled by I18N.applyStatic().
  function relabelOptions(selectEl, labeller) {
    if (!selectEl) return;
    Array.from(selectEl.options).forEach((opt) => {
      if (opt.value === "") return;
      opt.textContent = labeller(opt.value);
    });
  }

  function relabelFilterOptions() {
    relabelOptions(filterArea, (v) => I18N.areaLabel(v));
    relabelOptions(filterStage, (v) => I18N.stageLabel(v));
    relabelOptions(filterImpact, (v) => I18N.impactLabel(v));
    relabelOptions(filterSource, (v) => I18N.sourceLabel(v, formatSourceDisplayName(v)));
    if (filterPeriod) {
      Array.from(filterPeriod.options).forEach((option) => {
        option.textContent = periodDisplayLabel(option.value);
      });
    }
  }

  function refreshMobileToggleLabel() {
    if (!mobileFiltersToggle) return;
    const isOpen = mobileFiltersToggle.getAttribute("aria-expanded") === "true";
    mobileFiltersToggle.textContent = isOpen
      ? I18N.t("controls_hide_filters")
      : I18N.t("controls_filters_search");
  }

  // Apply the current language to page chrome (static strings, <html lang>,
  // <title>, selector state). Does NOT re-render cards — callers render separately.
  function applyLanguageDom() {
    I18N.setLang(filters.lang);
    I18N.applyStatic(document);
    relabelFilterOptions();
    document.documentElement.lang = filters.lang;
    document.title = I18N.t("document_title");
    syncLanguageSelector();
    refreshMobileToggleLabel();
    refreshSavedSearchDialog();
    syncAlertPilotCheckoutLabel();
    setSavedSearchStatus(savedSearchStatusKey, savedSearchStatusIsError);
    setAlertPilotStatus(alertPilotStatusKey, alertPilotStatusState);
    setAlertPilotSubmitting(alertPilotIsSubmitting);
  }

  // User changed language: keep every filter / sort / quick-filter state AND the
  // Load more window; only swap the display language and reflect it in URL +
  // localStorage. Intentionally does NOT call resetVisibleCount().
  function handleLanguageChange(nextLang) {
    const lang = I18N.normalize(nextLang);
    if (lang === filters.lang) return;
    filters.lang = lang;
    persistLang(lang);
    applyLanguageDom();
    updateUrlFromFilters();
    renderDataStatus(); // re-localize the Data status chips (built from the full dataset)
    render();
  }

  function setMobileFiltersOpen(isOpen) {
    if (!mobileFiltersToggle || !filterPanel) return;
    filterPanel.classList.toggle("is-open", isOpen);
    mobileFiltersToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    mobileFiltersToggle.textContent = isOpen
      ? I18N.t("controls_hide_filters")
      : I18N.t("controls_filters_search");
  }

  function shortenSummaryPart(text, maxLength) {
    if (text.length <= maxLength) return text;
    return text.slice(0, maxLength - 1).trim() + "...";
  }

  function activeFilterSummaryText() {
    const parts = [];
    const query = filters.search.trim();

    if (query) parts.push(I18N.t("af_search", { q: shortenSummaryPart(query, 24) }));
    if (filters.area) parts.push(I18N.t("af_area", { v: I18N.areaLabel(filters.area) }));
    if (filters.stage) parts.push(I18N.t("af_stage", { v: I18N.stageLabel(filters.stage) }));
    if (filters.source)
      parts.push(
        I18N.t("af_source", {
          v: I18N.sourceLabel(filters.source, formatSourceDisplayName(filters.source)),
        })
      );
    if (filters.impact) parts.push(I18N.t("af_impact", { v: I18N.impactLabel(filters.impact) }));
    if (filters.sort !== DEFAULT_SORT)
      parts.push(I18N.t("af_sort", { v: localizedSortLabel(filters.sort) }));
    if (filters.aiSummaryOnly) parts.push(I18N.t("af_ai"));
    if (filters.newlyDetectedOnly) parts.push(I18N.t("af_newly"));
    if (archiveManifest && filters.period !== archiveManifest.latest_period) {
      parts.push(I18N.t("af_period", { v: periodDisplayLabel(filters.period) }));
    }

    if (parts.length === 0) return I18N.t("af_none");
    if (parts.length <= 3) return I18N.t("af_active_prefix") + parts.join(" · ");
    return I18N.t("af_count", { n: parts.length });
  }

  function updateActiveFilterSummary() {
    if (!activeFilterSummary) return;
    activeFilterSummary.textContent = activeFilterSummaryText();
  }

  // -------- Saved searches (local-only demand-validation MVP) --------
  // Search definitions contain no personal data and remain in this browser.
  // Email delivery and billing are deliberately outside the static dashboard;
  // the pilot CTA routes to the existing Legal GPT inquiry form.
  function normalizeSavedSearchQuery(value) {
    if (typeof value !== "string" || value.length > 2000) return null;
    const incoming = new URLSearchParams(value);
    const normalized = new URLSearchParams();
    SAVED_SEARCH_QUERY_KEYS.forEach((key) => {
      const entry = incoming.get(key);
      if (entry && entry.length <= 500) normalized.set(key, entry);
    });
    return normalized.toString();
  }

  function normalizeSavedSearch(value) {
    if (!value || typeof value !== "object") return null;
    const id = typeof value.id === "string" ? value.id.trim().toLowerCase() : "";
    const name = typeof value.name === "string" ? plainText(value.name).slice(0, 80) : "";
    const query = normalizeSavedSearchQuery(value.query);
    if (!/^[a-z0-9-]{6,80}$/.test(id) || !name || query === null) return null;
    return {
      id: id,
      name: name,
      query: query,
      saved_at: typeof value.saved_at === "string" ? value.saved_at : "",
    };
  }

  function readSavedSearches() {
    try {
      const raw = localStorage.getItem(SAVED_SEARCHES_STORAGE_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      const seen = new Set();
      return parsed
        .map(normalizeSavedSearch)
        .filter((entry) => {
          if (!entry || seen.has(entry.id)) return false;
          seen.add(entry.id);
          return true;
        })
        .slice(0, MAX_SAVED_SEARCHES);
    } catch (e) {
      return [];
    }
  }

  function writeSavedSearches(nextSearches) {
    try {
      localStorage.setItem(SAVED_SEARCHES_STORAGE_KEY, JSON.stringify(nextSearches));
      savedSearches = nextSearches.slice(0, MAX_SAVED_SEARCHES);
      return true;
    } catch (e) {
      return false;
    }
  }

  function makeSavedSearchId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID().toLowerCase();
    }
    return "search-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function currentSavedSearchQuery() {
    return buildFilterParams(false).toString();
  }

  function inquirySafeSearchText(value) {
    return plainText(value).replace(/[<>&]/g, "");
  }

  function savedSearchDescription(query) {
    const params = new URLSearchParams(query || "");
    const parts = [];
    const search = inquirySafeSearchText(params.get("q") || "");
    const area = params.get("area") || "";
    const stage = params.get("stage") || "";
    const sourceName = getSourceNameFromSlug(params.get("source") || "");
    const impact = params.get("impact") || "";
    const sort = params.get("sort") || DEFAULT_SORT;
    const year = params.get("year") || "";

    if (search) parts.push(I18N.t("af_search", { q: shortenSummaryPart(search, 28) }));
    if (area) parts.push(I18N.t("af_area", { v: I18N.areaLabel(area) }));
    if (stage) parts.push(I18N.t("af_stage", { v: I18N.stageLabel(stage) }));
    if (sourceName) {
      parts.push(
        I18N.t("af_source", {
          v: I18N.sourceLabel(sourceName, formatSourceDisplayName(sourceName)),
        })
      );
    }
    if (impact) parts.push(I18N.t("af_impact", { v: I18N.impactLabel(impact) }));
    if (sort !== DEFAULT_SORT && isValidSort(sort)) {
      parts.push(I18N.t("af_sort", { v: localizedSortLabel(sort) }));
    }
    if (params.get("ai") === "1") parts.push(I18N.t("af_ai"));
    if (params.get("new") === "7") parts.push(I18N.t("af_newly"));
    if (year && validPeriodValue(year)) {
      parts.push(I18N.t("af_period", { v: periodDisplayLabel(year) }));
    }
    return parts.length ? parts.join(" · ") : I18N.t("af_none");
  }

  function defaultSavedSearchName() {
    const query = filters.search.trim();
    if (query) return shortenSummaryPart(query, 80);
    if (filters.area) return I18N.areaLabel(filters.area);
    if (filters.source) {
      return I18N.sourceLabel(filters.source, formatSourceDisplayName(filters.source));
    }
    if (filters.stage) return I18N.stageLabel(filters.stage);
    return I18N.t("saved_search_default_name", { count: savedSearches.length + 1 });
  }

  function setSavedSearchStatus(key, isError) {
    savedSearchStatusKey = key || "";
    savedSearchStatusIsError = !!isError;
    if (!savedSearchStatus) return;
    savedSearchStatus.textContent = savedSearchStatusKey
      ? I18N.t(savedSearchStatusKey, { max: MAX_SAVED_SEARCHES })
      : "";
    savedSearchStatus.classList.toggle("is-error", savedSearchStatusIsError);
  }

  function renderSavedSearches() {
    if (manageSavedSearchesBtn) {
      manageSavedSearchesBtn.textContent = I18N.t("saved_searches_count", {
        count: savedSearches.length,
      });
    }
    if (saveSearchBtn) saveSearchBtn.textContent = I18N.t("saved_search_open");
    if (savedSearchCapacity) {
      savedSearchCapacity.textContent = I18N.t("saved_search_capacity", {
        count: savedSearches.length,
        max: MAX_SAVED_SEARCHES,
      });
    }
    if (!savedSearchList) return;

    savedSearchList.replaceChildren();
    if (savedSearches.length === 0) {
      const empty = document.createElement("p");
      empty.className = "saved-search-empty";
      empty.textContent = I18N.t("saved_search_empty");
      savedSearchList.appendChild(empty);
      return;
    }

    savedSearches.forEach((saved) => {
      const item = document.createElement("article");
      item.className = "saved-search-item";

      const copy = document.createElement("div");
      const name = document.createElement("p");
      name.className = "saved-search-item-name";
      name.textContent = saved.name;
      const summary = document.createElement("p");
      summary.className = "saved-search-item-summary";
      summary.textContent = savedSearchDescription(saved.query);
      copy.append(name, summary);

      const actions = document.createElement("div");
      actions.className = "saved-search-item-actions";
      const load = document.createElement("button");
      load.type = "button";
      load.className = "saved-search-item-button";
      load.dataset.savedSearchAction = "load";
      load.dataset.savedSearchId = saved.id;
      load.textContent = I18N.t("saved_search_load");
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "saved-search-item-button saved-search-item-delete";
      remove.dataset.savedSearchAction = "delete";
      remove.dataset.savedSearchId = saved.id;
      remove.textContent = I18N.t("saved_search_delete");
      actions.append(load, remove);

      item.append(copy, actions);
      savedSearchList.appendChild(item);
    });
  }

  function refreshSavedSearchDialog() {
    renderSavedSearches();
    if (savedSearchCurrentSummary) {
      savedSearchCurrentSummary.textContent = activeFilterSummaryText();
    }
    syncAlertPilotScopeWarning();
  }

  function openSavedSearches(preferNameInput) {
    if (!savedSearchDialog || typeof savedSearchDialog.showModal !== "function") return;
    savedSearchDialogOpener = document.activeElement;
    setSavedSearchStatus("", false);
    refreshSavedSearchDialog();
    if (savedSearchNameInput) {
      savedSearchNameInput.value = preferNameInput ? defaultSavedSearchName() : "";
    }
    if (!savedSearchDialog.open) savedSearchDialog.showModal();
    window.setTimeout(() => {
      if (preferNameInput && savedSearchNameInput) savedSearchNameInput.focus();
      else if (closeSavedSearchDialogBtn) closeSavedSearchDialogBtn.focus();
    }, 0);
  }

  function closeSavedSearches() {
    if (savedSearchDialog && savedSearchDialog.open) savedSearchDialog.close();
  }

  function saveCurrentSearch() {
    if (!archiveManifest || !savedSearchNameInput) return;
    const query = currentSavedSearchQuery();
    const enteredName = plainText(savedSearchNameInput.value).slice(0, 80);
    const name = enteredName || defaultSavedSearchName();
    const existing = savedSearches.find((saved) => saved.query === query);

    let next;
    let statusKey;
    if (existing) {
      next = savedSearches.map((saved) =>
        saved.id === existing.id
          ? { id: saved.id, name: name, query: query, saved_at: new Date().toISOString() }
          : saved
      );
      statusKey = "saved_search_updated";
    } else {
      if (savedSearches.length >= MAX_SAVED_SEARCHES) {
        setSavedSearchStatus("saved_search_limit", true);
        return;
      }
      next = [
        {
          id: makeSavedSearchId(),
          name: name,
          query: query,
          saved_at: new Date().toISOString(),
        },
      ].concat(savedSearches);
      statusKey = "saved_search_saved";
    }

    if (!writeSavedSearches(next)) {
      setSavedSearchStatus("saved_search_storage_error", true);
      return;
    }
    savedSearchNameInput.value = name;
    renderSavedSearches();
    setSavedSearchStatus(statusKey, false);
  }

  function deleteSavedSearch(id) {
    const next = savedSearches.filter((saved) => saved.id !== id);
    if (next.length === savedSearches.length) {
      setSavedSearchStatus("saved_search_unavailable", true);
      return;
    }
    if (!writeSavedSearches(next)) {
      setSavedSearchStatus("saved_search_storage_error", true);
      return;
    }
    renderSavedSearches();
    setSavedSearchStatus("saved_search_deleted", false);
  }

  async function loadSavedSearch(id) {
    const saved = savedSearches.find((entry) => entry.id === id);
    if (!saved || !archiveManifest || !window.history || !window.history.replaceState) {
      setSavedSearchStatus("saved_search_unavailable", true);
      return;
    }
    const params = new URLSearchParams(saved.query);
    if (filters.lang && filters.lang !== I18N.DEFAULT_LANG) params.set("lang", filters.lang);
    const queryString = params.toString();
    const nextUrl =
      window.location.pathname +
      (queryString ? "?" + queryString : "") +
      window.location.hash;
    window.history.replaceState(null, "", nextUrl);

    try {
      await restoreStateFromLocation();
      closeSavedSearches();
    } catch (err) {
      console.error("[JLRW] Failed to load saved search:", err);
      setSavedSearchStatus("saved_search_unavailable", true);
    }
  }

  function setSavedSearchControlsAvailable(isAvailable) {
    if (saveSearchBtn) saveSearchBtn.disabled = !isAvailable;
    if (saveCurrentSearchBtn) saveCurrentSearchBtn.disabled = !isAvailable;
  }

  function initSavedSearches() {
    savedSearches = readSavedSearches();
    renderSavedSearches();
  }

  // -------- Paid alert-pilot inquiry --------
  // This is a lead-capture bridge, not account provisioning. Contact Form 7
  // receives the request; no fee is charged until a separate checkout occurs.
  function trustedIntegrationUrl(value, expectedHost, expectedPathPrefix) {
    if (typeof value !== "string" || !value.trim()) return "";
    try {
      const parsed = new URL(value.trim());
      if (
        parsed.protocol !== "https:" ||
        parsed.host !== expectedHost ||
        parsed.username ||
        parsed.password
      ) {
        return "";
      }
      if (expectedPathPrefix && !parsed.pathname.startsWith(expectedPathPrefix)) return "";
      parsed.hash = "";
      return parsed.href;
    } catch (e) {
      return "";
    }
  }

  function alertPilotEndpoint() {
    return trustedIntegrationUrl(
      ALERTS_CONFIG.inquiryEndpoint,
      "legal-gpt.com",
      "/wp-json/contact-form-7/v1/contact-forms/"
    );
  }

  function validAlertPilotRequestId(value) {
    return typeof value === "string" && /^jlrw_[a-z0-9]+_[a-z0-9]+$/.test(value) && value.length <= 200
      ? value
      : "";
  }

  function createAlertPilotRequestId() {
    const timestamp = Date.now().toString(36);
    let randomPart = "";
    if (window.crypto && typeof window.crypto.getRandomValues === "function") {
      const bytes = new Uint8Array(8);
      window.crypto.getRandomValues(bytes);
      randomPart = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
    }
    return randomPart ? "jlrw_" + timestamp + "_" + randomPart : "";
  }

  function alertPilotCheckoutUrl(plan, requestId) {
    const links = ALERTS_CONFIG.stripePaymentLinks;
    const value = links && typeof links === "object" ? links[plan === "team" ? "team" : "pro"] : "";
    const trusted = trustedIntegrationUrl(value, "buy.stripe.com", "/");
    const reference = validAlertPilotRequestId(requestId);
    if (!trusted || !reference) return "";
    const checkoutUrl = new URL(trusted);
    checkoutUrl.searchParams.set("client_reference_id", reference);
    return trustedIntegrationUrl(checkoutUrl.href, "buy.stripe.com", "/");
  }

  function alertPilotFallbackUrl() {
    return trustedIntegrationUrl(ALERTS_CONFIG.fallbackContactUrl, "legal-gpt.com", "/contact/");
  }

  function alertPilotPrivacyUrl() {
    return trustedIntegrationUrl(ALERTS_CONFIG.privacyPolicyUrl, "legal-gpt.com", "/privacy-policy/");
  }

  function validAlertPilotUnitTag(value) {
    return typeof value === "string" && /^wpcf7-f\d+-p\d+-o\d+$/.test(value)
      ? value
      : "";
  }

  function validAlertPilotNumericId(value) {
    return typeof value === "string" && /^\d+$/.test(value) ? value : "";
  }

  function setAlertPilotStatus(key, state) {
    alertPilotStatusKey = key || "";
    alertPilotStatusState = state || "";
    if (!alertPilotStatus) return;
    alertPilotStatus.textContent = alertPilotStatusKey ? I18N.t(alertPilotStatusKey) : "";
    alertPilotStatus.classList.toggle("is-success", alertPilotStatusState === "success");
    alertPilotStatus.classList.toggle("is-error", alertPilotStatusState === "error");
  }

  function setAlertPilotReference(requestId) {
    if (!alertPilotReference || !alertPilotReferenceValue) return;
    const reference = validAlertPilotRequestId(requestId);
    alertPilotReferenceValue.textContent = reference;
    alertPilotReference.hidden = !reference;
  }

  function setAlertPilotSubmitting(isSubmitting) {
    alertPilotIsSubmitting = !!isSubmitting;
    if (!alertPilotSubmitBtn) return;
    alertPilotSubmitBtn.disabled = alertPilotIsSubmitting;
    alertPilotSubmitBtn.textContent = I18N.t(
      alertPilotIsSubmitting ? "alert_pilot_submitting" : "alert_pilot_submit"
    );
  }

  function clearAlertPilotHoneypot() {
    if (alertPilotHoneypotInput) alertPilotHoneypotInput.value = "";
  }

  function resetAlertPilotOutcome() {
    setAlertPilotStatus("", "");
    setAlertPilotReference("");
    if (alertPilotFallback) alertPilotFallback.hidden = true;
    if (alertPilotCheckout) {
      alertPilotCheckout.hidden = true;
      alertPilotCheckout.removeAttribute("href");
      delete alertPilotCheckout.dataset.plan;
    }
  }

  function openAlertPilotForm() {
    if (!alertPilotFormWrap || !openAlertPilotFormBtn) return;
    syncAlertPilotScopeWarning();
    setAlertPilotPlanLocked(false);
    alertPilotFormWrap.hidden = false;
    openAlertPilotFormBtn.setAttribute("aria-expanded", "true");
    clearAlertPilotHoneypot();
    resetAlertPilotOutcome();
    syncAlertPilotCheckoutLabel();
    window.setTimeout(() => {
      alertPilotFormWrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
      if (alertPilotNameInput) alertPilotNameInput.focus();
    }, 0);
  }

  function hasActiveMonitoringFilter() {
    const params = new URLSearchParams(currentSavedSearchQuery());
    return ["q", "area", "stage", "source", "impact", "ai", "new"].some((key) =>
      Boolean(params.get(key))
    );
  }

  function syncAlertPilotScopeWarning() {
    if (!alertPilotScopeWarning) return;
    alertPilotScopeWarning.hidden = hasActiveMonitoringFilter();
  }

  function syncAlertPilotPlanChoice() {
    const selectedPlan = alertPilotPlanSelect && alertPilotPlanSelect.value === "team" ? "team" : "pro";
    alertPilotPlanButtons.forEach((button) => {
      const isSelected = button.dataset.alertPlan === selectedPlan;
      button.setAttribute("aria-pressed", isSelected ? "true" : "false");
    });
    alertPilotPlanCards.forEach((card) => {
      card.classList.toggle("is-selected", card.dataset.alertPlanCard === selectedPlan);
    });
  }

  function setAlertPilotPlanLocked(isLocked) {
    if (alertPilotPlanSelect) alertPilotPlanSelect.disabled = isLocked;
    alertPilotPlanButtons.forEach((button) => {
      button.disabled = isLocked;
    });
  }

  function syncAlertPilotCheckoutLabel() {
    if (!alertPilotCheckout) return;
    const plan = alertPilotCheckout.dataset.plan;
    if (plan === "pro" || plan === "team") {
      const labelKey = plan === "team" ? "alert_pilot_plan_team" : "alert_pilot_plan_pro";
      alertPilotCheckout.textContent = I18N.t("alert_pilot_checkout_plan", {
        plan: I18N.t(labelKey),
      });
      return;
    }
    alertPilotCheckout.textContent = I18N.t("alert_pilot_checkout");
  }

  function selectAlertPilotPlan(event) {
    if (!alertPilotPlanSelect) return;
    alertPilotPlanSelect.value = event.currentTarget.dataset.alertPlan === "team" ? "team" : "pro";
    syncAlertPilotPlanChoice();
    if (alertPilotNameInput) alertPilotNameInput.focus({ preventScroll: true });
  }

  function alertPilotPlanLabel(value) {
    if (value === "team") return "Team — US$149/month";
    return "Pro — US$29/month";
  }

  function alertPilotFrequencyLabel(value) {
    return value === "weekly" ? "Weekly digest" : "Daily digest";
  }

  function currentMonitoringUrl() {
    const params = buildFilterParams(true).toString();
    return (
      window.location.origin +
      window.location.pathname +
      (params ? "?" + params : "")
    );
  }

  function buildAlertPilotMessage(values) {
    const query = currentSavedSearchQuery();
    const lines = [
      "Japan Regulatory Alert Pilot request",
      "",
      "Request ID: " + values.requestId,
      "Name: " + values.name,
      "Company / organization: " + values.company,
      "Work email: " + values.email,
      "Plan: " + alertPilotPlanLabel(values.plan),
      "Preferred frequency: " + alertPilotFrequencyLabel(values.frequency),
      "Monitoring focus / business context: " + values.focus,
      "Monitoring criteria: " + savedSearchDescription(query),
      "Filter query: " + (query || "Latest updates / no additional filters"),
      "Dashboard URL: " + currentMonitoringUrl(),
      "Display language: " + filters.lang,
      "",
      "This is a request for a regulatory-monitoring pilot, not a request for legal advice.",
    ];
    return lines.join("\n");
  }

  async function submitAlertPilotRequest() {
    resetAlertPilotOutcome();
    if (!alertPilotForm || !alertPilotForm.checkValidity()) {
      setAlertPilotStatus("alert_pilot_validation", "error");
      if (alertPilotForm) alertPilotForm.reportValidity();
      return;
    }

    // Fail closed without sending. Clear the field so browser autofill or a
    // password manager cannot silently poison every later attempt in this dialog.
    if (alertPilotHoneypotInput && alertPilotHoneypotInput.value) {
      clearAlertPilotHoneypot();
      setAlertPilotStatus("alert_pilot_failed", "error");
      if (alertPilotFallback) alertPilotFallback.hidden = false;
      return;
    }

    const values = {
      name: plainText(alertPilotNameInput.value).slice(0, 120),
      email: plainText(alertPilotEmailInput.value).slice(0, 254),
      company: plainText(alertPilotCompanyInput.value).slice(0, 160),
      plan: alertPilotPlanSelect.value === "team" ? "team" : "pro",
      frequency: alertPilotFrequencySelect.value === "weekly" ? "weekly" : "daily",
      focus: plainText(alertPilotFocusInput.value).slice(0, 500),
      requestId: createAlertPilotRequestId(),
    };
    if (!values.name || !values.company || values.focus.length < 10) {
      if (!values.name || !values.company) {
        setAlertPilotStatus("alert_pilot_validation", "error");
        if (!values.name) alertPilotNameInput.focus();
        else alertPilotCompanyInput.focus();
      } else {
        setAlertPilotStatus("alert_pilot_focus_validation", "error");
        alertPilotFocusInput.focus();
      }
      return;
    }
    if (!validAlertPilotRequestId(values.requestId)) {
      setAlertPilotStatus("alert_pilot_failed", "error");
      if (alertPilotFallback) alertPilotFallback.hidden = false;
      return;
    }

    const endpoint = alertPilotEndpoint();
    const unitTag = validAlertPilotUnitTag(ALERTS_CONFIG.inquiryUnitTag);
    const formId = validAlertPilotNumericId(ALERTS_CONFIG.inquiryFormId);
    const containerPost = validAlertPilotNumericId(ALERTS_CONFIG.inquiryContainerPost);
    if (!endpoint || !unitTag || !formId || !containerPost) {
      setAlertPilotStatus("alert_pilot_failed", "error");
      if (alertPilotFallback) alertPilotFallback.hidden = false;
      return;
    }

    const data = new FormData();
    data.append("_wpcf7", formId);
    data.append("_wpcf7_locale", "ja");
    data.append("_wpcf7_unit_tag", unitTag);
    data.append("_wpcf7_container_post", containerPost);
    data.append("_wpcf7_posted_data_hash", "");
    data.append("your-name", values.name);
    data.append("your-email", values.email);
    data.append(
      "your-subject",
      "[JLRW Alert Pilot " + values.requestId + "] " + alertPilotPlanLabel(values.plan)
    );
    data.append("your-message", buildAlertPilotMessage(values));

    setAlertPilotSubmitting(true);
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        body: data,
        mode: "cors",
        credentials: "omit",
        referrerPolicy: "origin",
        headers: { Accept: "application/json" },
      });
      const result = await response.json();
      if (!response.ok || !result || result.status !== "mail_sent") {
        const providerStatus = result && typeof result.status === "string" ? result.status : "unknown";
        console.warn("[JLRW] Alert pilot request was not accepted. status=" + providerStatus);
        throw new Error("Inquiry was not accepted.");
      }

      const checkoutUrl = alertPilotCheckoutUrl(values.plan, values.requestId);
      alertPilotNameInput.value = "";
      alertPilotEmailInput.value = "";
      alertPilotCompanyInput.value = "";
      alertPilotFocusInput.value = "";
      alertPilotConsentInput.checked = false;
      clearAlertPilotHoneypot();
      alertPilotPlanSelect.value = values.plan;
      alertPilotFrequencySelect.value = values.frequency;
      syncAlertPilotPlanChoice();
      setAlertPilotReference(values.requestId);
      if (checkoutUrl && alertPilotCheckout) {
        setAlertPilotStatus("alert_pilot_success_checkout", "success");
        alertPilotCheckout.href = checkoutUrl;
        alertPilotCheckout.dataset.plan = values.plan;
        syncAlertPilotCheckoutLabel();
        alertPilotCheckout.hidden = false;
        setAlertPilotPlanLocked(true);
      } else {
        setAlertPilotStatus("alert_pilot_success_manual", "success");
      }
    } catch (err) {
      console.warn("[JLRW] Alert pilot request failed.", err);
      setAlertPilotStatus("alert_pilot_failed", "error");
      if (alertPilotFallback) alertPilotFallback.hidden = false;
    } finally {
      setAlertPilotSubmitting(false);
    }
  }

  function initAlertPilot() {
    const fallbackUrl = alertPilotFallbackUrl();
    const privacyUrl = alertPilotPrivacyUrl();
    if (fallbackUrl && alertPilotFallback) alertPilotFallback.href = fallbackUrl;
    if (privacyUrl && alertPilotPrivacyLink) alertPilotPrivacyLink.href = privacyUrl;
  }

  function setQuickButtonState(button, active) {
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  }

  function updateQuickFilterState() {
    quickFilterButtons.forEach((button) => {
      const action = button.getAttribute("data-quick-filter");
      if (action === "reset") {
        setQuickButtonState(button, false);
      } else if (action === "public-comment-open") {
        setQuickButtonState(button, filters.stage === "Public Comment Open");
      } else if (action === "ai-summary") {
        setQuickButtonState(button, filters.aiSummaryOnly);
      } else if (action === "newly-detected") {
        setQuickButtonState(button, filters.newlyDetectedOnly);
      } else if (action === "medium-impact") {
        setQuickButtonState(button, filters.impact === "Medium");
      }
    });
  }

  async function resetFilters() {
    const defaultPeriod = archiveManifest.latest_period;
    filters.area = "";
    filters.stage = "";
    filters.source = "";
    filters.impact = "";
    filters.sort = DEFAULT_SORT;
    filters.search = "";
    filters.aiSummaryOnly = false;
    filters.newlyDetectedOnly = false;
    setMobileFiltersOpen(false);
    if (filters.period !== defaultPeriod) {
      try {
        await installPeriodDataset(defaultPeriod);
      } catch (err) {
        console.error("[JLRW] Failed to reset archive period:", err);
        showError();
        return;
      }
    }
    applyFilterChange();
  }

  function handleQuickFilter(action) {
    if (action === "reset") {
      resetFilters();
      return;
    }
    if (action === "public-comment-open") {
      filters.stage = filters.stage === "Public Comment Open" ? "" : "Public Comment Open";
    } else if (action === "ai-summary") {
      filters.aiSummaryOnly = !filters.aiSummaryOnly;
    } else if (action === "newly-detected") {
      filters.newlyDetectedOnly = !filters.newlyDetectedOnly;
    } else if (action === "medium-impact") {
      filters.impact = filters.impact === "Medium" ? "" : "Medium";
    }
    applyFilterChange();
  }

  // -------- Rendering --------
  function impactClass(level) {
    if (level === "High") return "impact-high";
    if (level === "Medium") return "impact-medium";
    if (level === "Low") return "impact-low";
    return "";
  }

  function summarySourceMeta(source) {
    if (source === "claude") {
      return {
        label: I18N.t("badge_ai_summary"),
        className: "badge-summary-ai",
        title: I18N.t("badge_ai_summary_title"),
      };
    }
    return {
      label: I18N.t("badge_rule_based"),
      className: "badge-summary-rule",
      title: I18N.t("badge_rule_based_title"),
    };
  }

  function escapeHtml(str) {
    if (str == null) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function safeUrl(url) {
    if (typeof url !== "string") return "#";
    const trimmed = url.trim();
    if (/^https?:\/\//i.test(trimmed)) return trimmed;
    return "#";
  }

  function plainText(value) {
    if (value == null) return "";
    return String(value).replace(/\s+/g, " ").trim();
  }

  // -------- Localized record fields (English canonical fallback per field) --------
  const TRANSLATABLE = {
    title: "title_en",
    summary: "summary_en",
    business_impact: "business_impact_en",
    recommended_action: "recommended_action_en",
  };

  function englishCanonical(update, field) {
    return update[TRANSLATABLE[field]];
  }

  function localeBlock(update) {
    if (filters.lang === I18N.DEFAULT_LANG) return null;
    const translations = update && update.translations;
    if (!translations || typeof translations !== "object") return null;
    const block = translations[filters.lang];
    return block && typeof block === "object" ? block : null;
  }

  // Returns the localized value for a translatable field, falling back to the
  // English canonical field whenever the translation is missing or empty.
  function translatedField(update, field) {
    const block = localeBlock(update);
    if (block) {
      const value = block[field];
      if (typeof value === "string" && value.trim()) return value;
    }
    return englishCanonical(update, field);
  }

  // True when the item carries a usable translation for the active language.
  function hasTranslation(update) {
    const block = localeBlock(update);
    return !!(block && typeof block.title === "string" && block.title.trim());
  }

  function sourceUrlForCopy(update) {
    const sourceUrl = safeUrl(update.source_url);
    return sourceUrl === "#" ? "" : sourceUrl;
  }

  function copySection(label, value) {
    return label + ":\n" + plainText(value);
  }

  function parseIsoDateValue(value) {
    if (typeof value !== "string") return 0;
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
    if (!match) return 0;
    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    const time = Date.UTC(year, month - 1, day);
    const parsed = new Date(time);
    if (
      parsed.getUTCFullYear() !== year ||
      parsed.getUTCMonth() !== month - 1 ||
      parsed.getUTCDate() !== day
    ) {
      return 0;
    }
    return time;
  }

  function currentJstDateValue(now) {
    const base = now instanceof Date ? now : new Date();
    const jst = new Date(base.getTime() + 9 * 60 * 60 * 1000);
    return Date.UTC(jst.getUTCFullYear(), jst.getUTCMonth(), jst.getUTCDate());
  }

  function firstSeenDateValue(update) {
    return parseIsoDateValue(update && update.first_seen_at);
  }

  function firstSeenDisplay(update) {
    return firstSeenDateValue(update) ? update.first_seen_at.trim() : "";
  }

  function isNewlyDetected(update, todayValue) {
    const firstSeenValue = firstSeenDateValue(update);
    if (!firstSeenValue) return false;
    const today = todayValue || currentJstDateValue();
    const ageDays = Math.floor((today - firstSeenValue) / DAY_MS);
    return ageDays >= 0 && ageDays < NEWLY_DETECTED_DAYS;
  }

  function buildCopySummaryText(update) {
    const sourceUrl = sourceUrlForCopy(update);
    const sections = [
      copySection("Title", update.title_en),
      copySection("Original title", update.title_ja),
      copySection("Area", update.area),
      copySection("Stage", update.stage),
      copySection("Impact", update.impact_level),
      copySection("Source", formatSourceDisplayName(update.source_name)),
      copySection("Published", update.published_at),
    ];
    const firstSeen = firstSeenDisplay(update);
    if (firstSeen) {
      sections.push(copySection("First detected by this dashboard", firstSeen));
    }
    sections.push(
      copySection("Summary", update.summary_en),
      copySection("Business impact", update.business_impact_en),
      copySection("Recommended action", update.recommended_action_en),
      copySection("Official Japanese source", sourceUrl),
      copySection(
        "Note",
        "This is an English monitoring aid. The original Japanese official source remains authoritative."
      )
    );
    return sections.join("\n\n");
  }

  // Chinese copy summary: localized labels, translated values with per-field
  // English fallback, an always-present English reference title and Japanese
  // original title, and a clear unofficial-AI-translation note. The English
  // builder above is left exactly as-is for the English UI.
  function buildCopySummaryTextLocalized(update) {
    const sourceUrl = sourceUrlForCopy(update);
    const translationMissing = !localeBlock(update);
    const sections = [
      copySection(I18N.t("cs_title"), translatedField(update, "title")),
      copySection(I18N.t("cs_en_ref_title"), update.title_en),
      copySection(I18N.t("cs_ja_title"), update.title_ja),
      copySection(I18N.t("cs_area"), I18N.areaLabel(update.area)),
      copySection(I18N.t("cs_stage"), I18N.stageLabel(update.stage)),
      copySection(I18N.t("cs_impact"), I18N.impactLabel(update.impact_level)),
      copySection(
        I18N.t("cs_source"),
        I18N.sourceLabel(update.source_name, formatSourceDisplayName(update.source_name))
      ),
      copySection(I18N.t("cs_published"), update.published_at),
    ];
    const firstSeen = firstSeenDisplay(update);
    if (firstSeen) {
      sections.push(copySection(I18N.t("cs_first_seen"), firstSeen));
    }
    sections.push(
      copySection(I18N.t("cs_summary"), translatedField(update, "summary")),
      copySection(I18N.t("cs_business"), translatedField(update, "business_impact")),
      copySection(I18N.t("cs_action"), translatedField(update, "recommended_action")),
      copySection(I18N.t("cs_official_source"), sourceUrl),
      plainText(I18N.t("cs_note"))
    );
    if (translationMissing) {
      sections.push(plainText(I18N.t("cs_fallback")));
    }
    return sections.join("\n\n");
  }

  function fallbackCopyText(text) {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.top = "-9999px";
    textarea.style.left = "-9999px";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);

    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (e) {
      copied = false;
    } finally {
      document.body.removeChild(textarea);
    }
    return copied;
  }

  async function copyTextToClipboard(text) {
    if (!text) return false;
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (e) {
        // Fall back below for browsers or contexts that block the async Clipboard API.
      }
    }
    return fallbackCopyText(text);
  }

  function showCopyFeedback(button, message, isSuccess) {
    const defaultLabel = button.getAttribute("data-copy-label") || button.textContent.trim();
    const card = button.closest(".card");
    const statusEl = card ? card.querySelector(".copy-status") : null;

    window.clearTimeout(button._copyResetTimer);
    button.textContent = message;
    button.classList.toggle("is-copy-success", isSuccess);
    button.classList.toggle("is-copy-error", !isSuccess);
    if (statusEl) {
      statusEl.textContent = message;
    }

    button._copyResetTimer = window.setTimeout(() => {
      button.textContent = defaultLabel;
      button.classList.remove("is-copy-success", "is-copy-error");
      if (statusEl && statusEl.textContent === message) {
        statusEl.textContent = "";
      }
    }, 1800);
  }

  async function handleCopyAction(event) {
    const button = event.target.closest("[data-copy-action]");
    if (!button || !cardsEl.contains(button)) return;

    const action = button.getAttribute("data-copy-action");
    const id = button.getAttribute("data-copy-id");
    const update = allUpdates.find((item) => String(item.id) === id);
    if (!update) {
      showCopyFeedback(button, "Copy failed", false);
      return;
    }

    let text = "";
    let successMessage = "";
    if (action === "summary") {
      text =
        filters.lang !== I18N.DEFAULT_LANG
          ? buildCopySummaryTextLocalized(update)
          : buildCopySummaryText(update);
      successMessage = I18N.t("copy_summary_success");
    } else if (action === "source-link") {
      text = sourceUrlForCopy(update);
      successMessage = I18N.t("copy_source_success");
    } else {
      return;
    }

    const copied = await copyTextToClipboard(text);
    if (!copied) {
      console.warn("[JLRW] Copy action failed.");
    }
    showCopyFeedback(button, copied ? successMessage : I18N.t("copy_failed"), copied);
  }

  function summarySourceLabel(value) {
    return value === "claude" ? "AI Summary" : "Rule-based Preview";
  }

  function csvPlainText(value) {
    return plainText(value).replace(/[\r\n]+/g, " ");
  }

  function protectCsvFormula(value) {
    const text = csvPlainText(value);
    // Neutralize spreadsheet formula triggers, including tab/CR-prefixed
    // variants. Normal sentences, dates, and https URLs are unaffected.
    if (/^[=+\-@\t\r]/.test(text)) return "'" + text;
    return text;
  }

  function csvCell(value) {
    const text = protectCsvFormula(value).replace(/"/g, '""');
    return '"' + text + '"';
  }

  function csvSourceUrl(update) {
    const sourceUrl = safeUrl(update.source_url);
    return sourceUrl === "#" ? "" : sourceUrl;
  }

  function csvRow(update) {
    return [
      update.title_en,
      update.title_ja,
      update.area,
      update.stage,
      update.impact_level,
      formatSourceDisplayName(update.source_name),
      csvSourceUrl(update),
      update.published_at,
      firstSeenDisplay(update),
      update.last_checked,
      summarySourceLabel(update.summary_source),
      update.summary_en,
      update.business_impact_en,
      update.recommended_action_en,
      update.relevance_score,
      update.id,
    ].map(csvCell);
  }

  function buildCsvText(updates) {
    const headers = [
      "English title",
      "Original Japanese title",
      "Area",
      "Stage",
      "Impact level",
      "Source",
      "Official source URL",
      "Published date",
      "First detected",
      "Last checked",
      "Summary type",
      "Summary",
      "Business impact",
      "Recommended action",
      "Ranking score",
      "Internal ID",
    ];
    const rows = [headers.map(csvCell)];
    updates.forEach((update) => {
      rows.push(csvRow(update));
    });
    return "\uFEFF" + rows.map((row) => row.join(",")).join("\r\n");
  }

  // Chinese CSV: fixed 17-column layout (defined in i18n.js). Adds an English
  // reference-title column and keeps the Japanese original title; translated
  // cells fall back to English per field. Internal ID stays last. The English
  // CSV above is unchanged.
  const ZH_CSV_HEADERS = I18N.csvHeadersZh();

  function csvRowZh(update) {
    return [
      translatedField(update, "title"),
      update.title_en,
      update.title_ja,
      I18N.areaLabel(update.area),
      I18N.stageLabel(update.stage),
      I18N.impactLabel(update.impact_level),
      I18N.sourceLabel(update.source_name, formatSourceDisplayName(update.source_name)),
      csvSourceUrl(update),
      update.published_at,
      firstSeenDisplay(update),
      update.last_checked,
      I18N.summaryTypeLabel(update.summary_source),
      translatedField(update, "summary"),
      translatedField(update, "business_impact"),
      translatedField(update, "recommended_action"),
      update.relevance_score,
      update.id,
    ].map(csvCell);
  }

  function buildCsvTextZh(updates) {
    const rows = [ZH_CSV_HEADERS.map(csvCell)];
    updates.forEach((update) => {
      rows.push(csvRowZh(update));
    });
    return "\uFEFF" + rows.map((row) => row.join(",")).join("\r\n");
  }

  function csvFilename() {
    const datePart = maxLastChecked(allUpdates) || new Date().toISOString().slice(0, 10);
    const periodPart = validPeriodValue(filters.period) ? filters.period : "latest";
    return "japan-legal-reform-watch-" + periodPart + "-" + datePart + ".csv";
  }

  function showExportFeedback(message, isSuccess) {
    if (!exportStatusEl) return;
    window.clearTimeout(exportStatusEl._exportResetTimer);
    exportStatusEl.textContent = message;
    exportStatusEl.classList.toggle("is-export-success", isSuccess);
    exportStatusEl.classList.toggle("is-export-error", !isSuccess);
    exportStatusEl._exportResetTimer = window.setTimeout(() => {
      if (exportStatusEl.textContent === message) {
        exportStatusEl.textContent = "";
        exportStatusEl.classList.remove("is-export-success", "is-export-error");
      }
    }, 2200);
  }

  function currentFilteredUpdates() {
    return sortUpdates(applyFilters());
  }

  function updateExportState(filtered) {
    if (!exportCsvBtn) return;
    const count = filtered.length;
    exportCsvBtn.disabled = count === 0;
    const label =
      count === 0 ? I18N.t("export_none") : I18N.t("export_label", { count: count });
    exportCsvBtn.setAttribute("aria-label", label);
    exportCsvBtn.setAttribute("title", label);
  }

  function exportCurrentCsv() {
    const filtered = currentFilteredUpdates();
    updateExportState(filtered);
    if (filtered.length === 0) {
      showExportFeedback(I18N.t("export_none"), false);
      return;
    }

    try {
      // English keeps the existing 16-column CSV exactly; zh-Hans uses the
      // 17-column Chinese layout with English fallback per cell.
      const csvText =
        filters.lang !== I18N.DEFAULT_LANG ? buildCsvTextZh(filtered) : buildCsvText(filtered);
      const blob = new Blob([csvText], { type: "text/csv;charset=utf-8" });
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = csvFilename();
      link.style.display = "none";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(objectUrl);
      showExportFeedback(I18N.t("export_done", { count: filtered.length }), true);
    } catch (e) {
      console.warn("[JLRW] CSV export failed.", e);
      showExportFeedback(I18N.t("export_failed"), false);
    }
  }

  function renderCard(u) {
    // Treat every record field as untrusted. All interpolated values are passed
    // through escapeHtml(); source_url is additionally scheme-checked via safeUrl()
    // AND attribute-escaped before being placed in the href. Localized fields fall
    // back to English per field; the original Japanese title is always preserved.
    const sourceUrl = safeUrl(u.source_url);
    const summaryMeta = summarySourceMeta(u.summary_source);
    const summaryBadge = `<span class="badge badge-summary ${escapeHtml(
      summaryMeta.className
    )}" title="${escapeHtml(summaryMeta.title)}">${escapeHtml(summaryMeta.label)}</span>`;
    const firstSeen = firstSeenDisplay(u);
    const newlyDetected = isNewlyDetected(u);
    const newlyDetectedBadge =
      newlyDetected && firstSeen
        ? `<span class="badge badge-newly-detected" title="${escapeHtml(
            I18N.t("newly_detected_title", { date: firstSeen })
          )}" aria-label="${escapeHtml(
            I18N.t("newly_detected_aria", { date: firstSeen })
          )}">${escapeHtml(I18N.t("newly_detected"))}</span>`
        : "";

    // Translation badge + notice apply only for a non-English active language.
    const translating = filters.lang !== I18N.DEFAULT_LANG;
    const translated = hasTranslation(u);
    const translationBadge =
      translating && translated
        ? `<span class="badge badge-ai-translation" title="${escapeHtml(
            I18N.t("translation_note")
          )}">${escapeHtml(I18N.t("badge_ai_translation"))}</span>`
        : "";
    const translationNote = translating
      ? translated
        ? `<p class="translation-note">${escapeHtml(I18N.t("translation_note"))}</p>`
        : `<p class="translation-note translation-note-missing">${escapeHtml(
            I18N.t("translation_unavailable")
          )}</p>`
      : "";
    // Body language for screen readers: the translated language only when a
    // translation is actually shown; otherwise the English fallback.
    const cardLang = translating && translated ? filters.lang : "en";

    const firstSeenDateLine = firstSeen
      ? `<span>${escapeHtml(I18N.t("date_first_detected"))}: ${escapeHtml(firstSeen)}</span>`
      : "";
    return `
      <article class="card ${impactClass(u.impact_level)}" data-id="${escapeHtml(u.id)}">
        <header class="card-header">
          <div class="card-badges">
            <span class="badge badge-area">${escapeHtml(I18N.areaLabel(u.area))}</span>
            <span class="badge badge-stage">${escapeHtml(I18N.stageLabel(u.stage))}</span>
            <span class="badge badge-impact">${escapeHtml(
              I18N.t("impact_badge", { level: I18N.impactLabel(u.impact_level) })
            )}</span>
            ${newlyDetectedBadge}
            ${summaryBadge}
            ${translationBadge}
          </div>
          <h2 class="card-title" lang="${escapeHtml(cardLang)}">${escapeHtml(
      translatedField(u, "title")
    )}</h2>
          <p class="card-title-ja" lang="ja">${escapeHtml(u.title_ja)}</p>
          ${translationNote}
        </header>
        <div class="card-body" lang="${escapeHtml(cardLang)}">
          <section>
            <h3>${escapeHtml(I18N.t("card_summary_heading"))}</h3>
            <p>${escapeHtml(translatedField(u, "summary"))}</p>
          </section>
          <section>
            <h3>${escapeHtml(I18N.t("card_business_impact_heading"))}</h3>
            <p>${escapeHtml(translatedField(u, "business_impact"))}</p>
          </section>
          <section>
            <h3>${escapeHtml(I18N.t("card_recommended_action_heading"))}</h3>
            <p>${escapeHtml(translatedField(u, "recommended_action"))}</p>
          </section>
        </div>
        <footer class="card-footer">
          <div class="source">
            <div class="source-meta">
              <span class="source-label">${escapeHtml(I18N.t("card_source_name_label"))}</span>
              <span class="source-name" title="${escapeHtml(u.source_name)}">${escapeHtml(
      I18N.sourceLabel(u.source_name, formatSourceDisplayName(u.source_name))
    )}</span>
            </div>
            <div class="source-actions">
              <a class="source-button" href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">
                ${escapeHtml(I18N.t("card_view_source"))}
              </a>
              <div class="copy-actions" aria-label="Copy actions">
                <button type="button" class="copy-button" data-copy-action="summary" data-copy-id="${escapeHtml(
                  u.id
                )}" data-copy-label="${escapeHtml(I18N.t("copy_summary_label"))}">${escapeHtml(
      I18N.t("copy_summary_label")
    )}</button>
                <button type="button" class="copy-button" data-copy-action="source-link" data-copy-id="${escapeHtml(
                  u.id
                )}" data-copy-label="${escapeHtml(I18N.t("copy_source_link_label"))}">${escapeHtml(
      I18N.t("copy_source_link_label")
    )}</button>
              </div>
            </div>
            <span class="copy-status" aria-live="polite" role="status"></span>
            <p class="source-note">${escapeHtml(I18N.t("card_source_note"))}</p>
          </div>
          <div class="dates">
            <span>${escapeHtml(I18N.t("date_published"))}: ${escapeHtml(u.published_at)}</span>
            ${firstSeenDateLine}
            <span>${escapeHtml(I18N.t("date_last_checked"))}: ${escapeHtml(u.last_checked)}</span>
          </div>
        </footer>
      </article>
    `;
  }

  function searchHaystack(update) {
    const tr = (update.translations && update.translations[TRANSLATION_LOCALE]) || {};
    const values = [update.title_en, update.title_ja, tr.title];
    if (update.summary_source === "claude") {
      values.push(
        update.summary_en,
        tr.summary,
        tr.business_impact,
        tr.recommended_action
      );
    }
    return values.filter((value) => typeof value === "string").join(" ").toLowerCase();
  }

  function applyFilters() {
    const q = filters.search.trim().toLowerCase();
    return allUpdates.filter((u) => {
      if (filters.area && u.area !== filters.area) return false;
      if (filters.stage && u.stage !== filters.stage) return false;
      if (filters.source && u.source_name !== filters.source) return false;
      if (filters.impact && u.impact_level !== filters.impact) return false;
      if (filters.aiSummaryOnly && u.summary_source !== "claude") return false;
      if (filters.newlyDetectedOnly && !isNewlyDetected(u)) return false;
      if (q) {
        // Titles remain searchable for every item. AI-authored summaries and
        // their Chinese translations are searchable too; rule-based template
        // bodies are excluded so boilerplate such as "by AI" cannot create
        // broad false-positive monitoring matches.
        if (!searchHaystack(u).includes(q)) return false;
      }
      return true;
    });
  }

  function numberValue(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function dateValue(value) {
    return parseIsoDateValue(value);
  }

  function compareDesc(a, b) {
    return b - a;
  }

  function originalOrder(a, b) {
    return allUpdates.indexOf(a) - allUpdates.indexOf(b);
  }

  function sortUpdates(updates) {
    const sorted = updates.slice();
    sorted.sort((a, b) => {
      if (filters.sort === "published") {
        return (
          compareDesc(dateValue(a.published_at), dateValue(b.published_at)) ||
          compareDesc(numberValue(a.relevance_score), numberValue(b.relevance_score)) ||
          originalOrder(a, b)
        );
      }
      if (filters.sort === "checked") {
        return (
          compareDesc(dateValue(a.last_checked), dateValue(b.last_checked)) ||
          compareDesc(dateValue(a.published_at), dateValue(b.published_at)) ||
          compareDesc(numberValue(a.relevance_score), numberValue(b.relevance_score)) ||
          originalOrder(a, b)
        );
      }
      if (filters.sort === "detected") {
        return (
          compareDesc(firstSeenDateValue(a), firstSeenDateValue(b)) ||
          compareDesc(dateValue(a.published_at), dateValue(b.published_at)) ||
          compareDesc(numberValue(a.relevance_score), numberValue(b.relevance_score)) ||
          originalOrder(a, b)
        );
      }
      // build_public_data.py owns the composite relevance ranking. Re-sorting
      // here by the public relevance_score alone would discard its Open/Closed,
      // impact, and recency adjustments.
      return originalOrder(a, b);
    });
    return sorted;
  }

  function maxLastChecked(updates) {
    const dates = updates
      .map((u) => (typeof u.last_checked === "string" ? u.last_checked.trim() : ""))
      .filter((v) => /^\d{4}-\d{2}-\d{2}$/.test(v));
    if (dates.length === 0) return "";
    dates.sort();
    return dates[dates.length - 1];
  }

  function distinctCount(updates, key) {
    const values = new Set();
    updates.forEach((u) => {
      if (u && typeof u[key] === "string" && u[key].trim() !== "") {
        values.add(u[key].trim());
      }
    });
    return values.size;
  }

  function makeStatusChip(label, value) {
    const chip = document.createElement("span");
    chip.className = "data-status-chip";

    const labelEl = document.createElement("span");
    labelEl.className = "data-status-chip-label";
    labelEl.textContent = label + ":";

    const valueEl = document.createElement("span");
    valueEl.className = "data-status-chip-value";
    valueEl.textContent = value;

    chip.append(labelEl, valueEl);
    return chip;
  }

  function renderDataStatus() {
    if (!dataStatusList) return;
    const stats = [
      [I18N.t("ds_period"), periodDisplayLabel(filters.period)],
      [I18N.t("ds_updates"), String(allUpdates.length)],
      [I18N.t("ds_archive_total"), String(archiveManifest.total_items)],
      [I18N.t("ds_sources"), String(distinctCount(allUpdates, "source_name"))],
      [I18N.t("ds_ai_summaries"), String(allUpdates.filter((u) => u.summary_source === "claude").length)],
      [
        I18N.t("ds_open_pc"),
        String(allUpdates.filter((u) => u.stage === "Public Comment Open").length),
      ],
      [I18N.t("ds_newly_detected"), String(allUpdates.filter((u) => isNewlyDetected(u)).length)],
      [I18N.t("ds_latest_checked"), maxLastChecked(allUpdates) || I18N.t("ds_unknown")],
    ];

    dataStatusList.innerHTML = "";
    stats.forEach(([label, value]) => {
      dataStatusList.appendChild(makeStatusChip(label, value));
    });
  }

  function renderDataStatusUnavailable() {
    if (!dataStatusList) return;
    dataStatusList.innerHTML = "";
    dataStatusList.appendChild(makeStatusChip(I18N.t("ds_status"), I18N.t("ds_unavailable")));
  }

  function renderResultsMeta(filtered) {
    // AI summary count and last-checked date cover the FULL filtered set,
    // not only the cards currently rendered on screen.
    const shown = Math.min(visibleCount, filtered.length);
    const aiSummaryCount = filtered.filter((u) => u.summary_source === "claude").length;
    const lastChecked = maxLastChecked(filtered);
    const parts = [
      I18N.t("meta_showing", { shown: shown, total: filtered.length }),
      I18N.t("meta_total", { count: allUpdates.length }),
      I18N.t("meta_ai", { count: aiSummaryCount }),
    ];
    if (lastChecked) {
      parts.push(I18N.t("meta_last_checked", { date: lastChecked }));
    }
    metaEl.textContent = parts.join(" · ");
  }

  function updateLoadMore(matchingCount) {
    if (!loadMoreWrap) return;
    // Hidden when everything that matches is already rendered (or nothing matches).
    loadMoreWrap.hidden = matchingCount === 0 || visibleCount >= matchingCount;
  }

  function render() {
    const filtered = currentFilteredUpdates();
    updateQuickFilterState();
    updateActiveFilterSummary();
    updateExportState(filtered);
    errorEl.hidden = true;
    if (filtered.length === 0) {
      cardsEl.innerHTML = "";
      emptyEl.hidden = false;
    } else {
      // Render only the visible page window — never every matching card at once.
      cardsEl.innerHTML = filtered.slice(0, visibleCount).map(renderCard).join("");
      emptyEl.hidden = true;
    }
    updateLoadMore(filtered.length);
    renderResultsMeta(filtered);
  }

  // -------- Events --------
  function debounce(fn, ms) {
    let t;
    return function () {
      const args = arguments;
      window.clearTimeout(t);
      t = window.setTimeout(() => fn.apply(null, args), ms);
    };
  }

  async function restoreStateFromLocation() {
    const requestedPeriod = requestedPeriodFromUrl();
    if (requestedPeriod !== filters.period) {
      await installPeriodDataset(requestedPeriod);
    }
    restoreFiltersFromUrl();
    applyLanguageDom();
    renderDataStatus();
    render();
  }

  function wireEvents() {
    const onSearch = debounce((e) => {
      filters.search = e.target.value;
      applyFilterChange();
    }, 120);
    searchInput.addEventListener("input", onSearch);

    filterArea.addEventListener("change", (e) => {
      filters.area = e.target.value;
      applyFilterChange();
    });
    filterStage.addEventListener("change", (e) => {
      filters.stage = e.target.value;
      applyFilterChange();
    });
    filterSource.addEventListener("change", (e) => {
      filters.source = e.target.value;
      applyFilterChange();
    });
    filterImpact.addEventListener("change", (e) => {
      filters.impact = e.target.value;
      applyFilterChange();
    });
    filterSort.addEventListener("change", (e) => {
      filters.sort = isValidSort(e.target.value) ? e.target.value : DEFAULT_SORT;
      applyFilterChange();
    });
    if (filterPeriod) {
      filterPeriod.addEventListener("change", (e) => {
        handlePeriodChange(e.target.value);
      });
    }
    if (mobileFiltersToggle) {
      mobileFiltersToggle.addEventListener("click", () => {
        const isOpen = mobileFiltersToggle.getAttribute("aria-expanded") === "true";
        setMobileFiltersOpen(!isOpen);
      });
    }
    quickFilterButtons.forEach((button) => {
      button.addEventListener("click", () => {
        handleQuickFilter(button.getAttribute("data-quick-filter"));
      });
    });
    if (loadMoreBtn) {
      loadMoreBtn.addEventListener("click", () => {
        visibleCount += PAGE_SIZE;
        render();
      });
    }
    if (exportCsvBtn) {
      exportCsvBtn.addEventListener("click", exportCurrentCsv);
    }
    if (saveSearchBtn) {
      saveSearchBtn.addEventListener("click", () => openSavedSearches(true));
    }
    if (manageSavedSearchesBtn) {
      manageSavedSearchesBtn.addEventListener("click", () => openSavedSearches(false));
    }
    if (closeSavedSearchDialogBtn) {
      closeSavedSearchDialogBtn.addEventListener("click", closeSavedSearches);
    }
    if (savedSearchForm) {
      savedSearchForm.addEventListener("submit", (event) => {
        event.preventDefault();
        saveCurrentSearch();
      });
    }
    if (savedSearchList) {
      savedSearchList.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-saved-search-action]");
        if (!button || !savedSearchList.contains(button)) return;
        const id = button.dataset.savedSearchId || "";
        if (button.dataset.savedSearchAction === "delete") {
          deleteSavedSearch(id);
        } else if (button.dataset.savedSearchAction === "load") {
          loadSavedSearch(id);
        }
      });
    }
    if (savedSearchDialog) {
      savedSearchDialog.addEventListener("close", () => {
        if (alertPilotFormWrap) alertPilotFormWrap.hidden = true;
        if (openAlertPilotFormBtn) openAlertPilotFormBtn.setAttribute("aria-expanded", "false");
        clearAlertPilotHoneypot();
        resetAlertPilotOutcome();
        setAlertPilotPlanLocked(false);
        syncAlertPilotCheckoutLabel();
        if (savedSearchDialogOpener && typeof savedSearchDialogOpener.focus === "function") {
          savedSearchDialogOpener.focus();
        }
        savedSearchDialogOpener = null;
      });
    }
    if (openAlertPilotFormBtn) {
      openAlertPilotFormBtn.addEventListener("click", openAlertPilotForm);
    }
    alertPilotPlanButtons.forEach((button) => {
      button.addEventListener("click", selectAlertPilotPlan);
    });
    if (alertPilotPlanSelect) {
      alertPilotPlanSelect.addEventListener("change", syncAlertPilotPlanChoice);
      syncAlertPilotPlanChoice();
    }
    if (alertPilotForm) {
      alertPilotForm.addEventListener("submit", (event) => {
        event.preventDefault();
        submitAlertPilotRequest();
      });
    }
    if (languageSelect) {
      languageSelect.addEventListener("change", (e) => {
        handleLanguageChange(e.target.value);
      });
    }
    cardsEl.addEventListener("click", handleCopyAction);
    window.addEventListener("popstate", async () => {
      try {
        await restoreStateFromLocation();
      } catch (err) {
        console.error("[JLRW] Failed to restore URL state:", err);
        showError();
      }
    });
  }

  // Resolve language (URL > localStorage > English) without validating filters,
  // so the disclaimer modal and page chrome can render localized before data loads.
  function initLanguage() {
    const params = new URLSearchParams(window.location.search);
    const urlLang = params.get("lang");
    const lang = urlLang
      ? I18N.normalize(urlLang)
      : I18N.normalize(readStoredLang() || I18N.DEFAULT_LANG);
    filters.lang = lang;
    I18N.setLang(lang);
  }

  // -------- Init --------
  document.addEventListener("DOMContentLoaded", () => {
    cacheDom();
    initLanguage();
    applyLanguageDom();
    initModal();
    initSavedSearches();
    initAlertPilot();
    wireEvents();
    loadData();
  });
})();
