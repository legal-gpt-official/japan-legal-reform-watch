/* =============================================================
   Japan Legal Reform Watch by LegalOS
   Client-side dashboard logic — vanilla JS, no dependencies.
   ============================================================= */

(function () {
  "use strict";

  // i18n layer (docs/i18n.js, loaded first). English is canonical; zh-Hans is an
  // optional overlay. All user-facing strings route through this namespace.
  const I18N = window.JLRW_I18N;

  const STORAGE_KEY = "jlrw_disclaimer_accepted_v1";
  // Data lives inside docs/ so the published site (GitHub Pages: /docs) is self-contained.
  const DATA_URL = "./data/legal_updates.json";

  // Preferred display order for impact level.
  const IMPACT_ORDER = ["High", "Medium", "Low"];
  const DEFAULT_SORT = "relevance";
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
  // search always evaluate the FULL public dataset, not just rendered cards.
  const PAGE_SIZE = 50;

  // English-first display labels for sources (audience: non-Japanese-reading
  // professionals). Keys are the EXACT `source_name` values produced by
  // scripts/fetch_updates.py — those internal values stay unchanged and remain
  // the filter <option> values; only the visible label is translated.
  // When adding a source to fetch_updates.py, add its display name here too.
  const SOURCE_DISPLAY_NAMES = {
    "e-Gov Public Comment (意見募集案件一覧)": "e-Gov Public Comment",
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
    "総務省 (MIC) 新着情報": "Ministry of Internal Affairs and Communications (MIC)",
    "国土交通省 (MLIT) 報道発表": "Ministry of Land, Infrastructure, Transport and Tourism (MLIT)",
    "農林水産省 (MAFF) 報道発表": "Ministry of Agriculture, Forestry and Fisheries (MAFF)",
  };

  // Compact URL slugs for shareable filter state. Values are the exact internal
  // source_name strings; do not change those internal names.
  const SOURCE_SLUGS = {
    "egov": "e-Gov Public Comment (意見募集案件一覧)",
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
  let visibleCount = PAGE_SIZE;
  const filters = {
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
  async function loadData() {
    try {
      const res = await fetch(DATA_URL, { cache: "no-store" });
      if (!res.ok) {
        throw new Error("HTTP " + res.status);
      }
      const data = await res.json();
      if (!Array.isArray(data)) {
        throw new Error("Unexpected data shape: expected an array.");
      }
      // Preserve the published JSON order. build_public_data.py owns relevance ranking.
      allUpdates = data.slice();
      populateFilterOptions();
      restoreFiltersFromUrl(); // resolves language (URL > localStorage > English)
      applyLanguageDom(); // localize chrome, filter options, <title>, <html lang>
      renderDataStatus();
      render();
    } catch (err) {
      console.error("[JLRW] Failed to load data:", err);
      showError();
    }
  }

  function showError() {
    cardsEl.innerHTML = "";
    emptyEl.hidden = true;
    errorEl.hidden = false;
    metaEl.textContent = "";
    renderDataStatusUnavailable();
    updateExportState([]);
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

  function updateUrlFromFilters() {
    if (!window.history || !window.history.replaceState) return;

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
    // Default English is the absence of the param, keeping shared URLs clean.
    if (filters.lang && filters.lang !== I18N.DEFAULT_LANG) params.set("lang", filters.lang);

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

    if (parts.length === 0) return I18N.t("af_none");
    if (parts.length <= 3) return I18N.t("af_active_prefix") + parts.join(" · ");
    return I18N.t("af_count", { n: parts.length });
  }

  function updateActiveFilterSummary() {
    if (!activeFilterSummary) return;
    activeFilterSummary.textContent = activeFilterSummaryText();
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

  function resetFilters() {
    filters.area = "";
    filters.stage = "";
    filters.source = "";
    filters.impact = "";
    filters.sort = DEFAULT_SORT;
    filters.search = "";
    filters.aiSummaryOnly = false;
    filters.newlyDetectedOnly = false;
    setMobileFiltersOpen(false);
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
    return "japan-legal-reform-watch-" + datePart + ".csv";
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
        // Search covers English title/summary, the original Japanese title, and —
        // regardless of the active UI language — the Chinese translation fields,
        // so English, Japanese, and Chinese keywords all match.
        const tr = (u.translations && u.translations[TRANSLATION_LOCALE]) || {};
        const hay = (
          (u.title_en || "") +
          " " +
          (u.title_ja || "") +
          " " +
          (u.summary_en || "") +
          " " +
          (tr.title || "") +
          " " +
          (tr.summary || "") +
          " " +
          (tr.business_impact || "") +
          " " +
          (tr.recommended_action || "")
        ).toLowerCase();
        if (!hay.includes(q)) return false;
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
      return (
        compareDesc(numberValue(a.relevance_score), numberValue(b.relevance_score)) ||
        compareDesc(dateValue(a.published_at), dateValue(b.published_at)) ||
        originalOrder(a, b)
      );
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
      [I18N.t("ds_updates"), String(allUpdates.length)],
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
      // Render only the visible page window — never all (up to 3000) cards at once.
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
    if (languageSelect) {
      languageSelect.addEventListener("change", (e) => {
        handleLanguageChange(e.target.value);
      });
    }
    cardsEl.addEventListener("click", handleCopyAction);
    window.addEventListener("popstate", () => {
      restoreFiltersFromUrl();
      applyLanguageDom();
      renderDataStatus();
      render();
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
    wireEvents();
    loadData();
  });
})();
