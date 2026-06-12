/* =============================================================
   Japan Legal Reform Watch by LegalOS
   Client-side dashboard logic — vanilla JS, no dependencies.
   ============================================================= */

(function () {
  "use strict";

  const STORAGE_KEY = "jlrw_disclaimer_accepted_v1";
  // Data lives inside docs/ so the published site (GitHub Pages: /docs) is self-contained.
  const DATA_URL = "./data/legal_updates.json";

  // Preferred display order for impact level.
  const IMPACT_ORDER = ["High", "Medium", "Low"];
  const DEFAULT_SORT = "relevance";
  const SORT_VALUES = ["relevance", "published", "checked"];
  const SORT_LABELS = {
    relevance: "Relevance",
    published: "Published date",
    checked: "Last checked",
  };

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
  };

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
      renderDataStatus();
      restoreFiltersFromUrl();
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

  function setMobileFiltersOpen(isOpen) {
    if (!mobileFiltersToggle || !filterPanel) return;
    filterPanel.classList.toggle("is-open", isOpen);
    mobileFiltersToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    mobileFiltersToggle.textContent = isOpen ? "Hide filters" : "Filters & Search";
  }

  function shortenSummaryPart(text, maxLength) {
    if (text.length <= maxLength) return text;
    return text.slice(0, maxLength - 1).trim() + "...";
  }

  function activeFilterSummaryText() {
    const parts = [];
    const query = filters.search.trim();

    if (query) parts.push('Search: "' + shortenSummaryPart(query, 24) + '"');
    if (filters.area) parts.push("Area: " + filters.area);
    if (filters.stage) parts.push("Stage: " + filters.stage);
    if (filters.source) parts.push("Source: " + formatSourceDisplayName(filters.source));
    if (filters.impact) parts.push("Impact: " + filters.impact);
    if (filters.sort !== DEFAULT_SORT) parts.push("Sort: " + sortLabel(filters.sort));
    if (filters.aiSummaryOnly) parts.push("AI Summary");

    if (parts.length === 0) return "No active filters";
    if (parts.length <= 3) return "Active: " + parts.join(" · ");
    return "Active: " + parts.length + " filters";
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
        label: "AI Summary",
        className: "badge-summary-ai",
        title:
          "This item includes an AI-generated English summary. It is not an official translation or legal advice.",
      };
    }
    return {
      label: "Rule-based Preview",
      className: "badge-summary-rule",
      title: "This item has not yet been summarized by AI and uses a rule-based placeholder.",
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

  function sourceUrlForCopy(update) {
    const sourceUrl = safeUrl(update.source_url);
    return sourceUrl === "#" ? "" : sourceUrl;
  }

  function copySection(label, value) {
    return label + ":\n" + plainText(value);
  }

  function buildCopySummaryText(update) {
    const sourceUrl = sourceUrlForCopy(update);
    return [
      copySection("Title", update.title_en),
      copySection("Original title", update.title_ja),
      copySection("Area", update.area),
      copySection("Stage", update.stage),
      copySection("Impact", update.impact_level),
      copySection("Source", formatSourceDisplayName(update.source_name)),
      copySection("Published", update.published_at),
      copySection("Summary", update.summary_en),
      copySection("Business impact", update.business_impact_en),
      copySection("Recommended action", update.recommended_action_en),
      copySection("Official Japanese source", sourceUrl),
      copySection(
        "Note",
        "This is an English monitoring aid. The original Japanese official source remains authoritative."
      ),
    ].join("\n\n");
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
    let successMessage = "Copied";
    if (action === "summary") {
      text = buildCopySummaryText(update);
      successMessage = "Summary copied";
    } else if (action === "source-link") {
      text = sourceUrlForCopy(update);
      successMessage = "Source link copied";
    } else {
      return;
    }

    const copied = await copyTextToClipboard(text);
    if (!copied) {
      console.warn("[JLRW] Copy action failed.");
    }
    showCopyFeedback(button, copied ? successMessage : "Copy failed", copied);
  }

  function summarySourceLabel(value) {
    return value === "claude" ? "AI Summary" : "Rule-based Preview";
  }

  function csvPlainText(value) {
    return plainText(value).replace(/[\r\n]+/g, " ");
  }

  function protectCsvFormula(value) {
    const text = csvPlainText(value);
    if (/^[=+\-@]/.test(text)) return "'" + text;
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
      count === 0 ? "No matching updates to export" : "Export " + count + " matching updates to CSV";
    exportCsvBtn.setAttribute("aria-label", label);
    exportCsvBtn.setAttribute("title", label);
  }

  function exportCurrentCsv() {
    const filtered = currentFilteredUpdates();
    updateExportState(filtered);
    if (filtered.length === 0) {
      showExportFeedback("No matching updates to export", false);
      return;
    }

    try {
      const csvText = buildCsvText(filtered);
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
      showExportFeedback("Exported " + filtered.length + " updates", true);
    } catch (e) {
      console.warn("[JLRW] CSV export failed.");
      showExportFeedback("Export failed", false);
    }
  }

  function renderCard(u) {
    // Treat every record field as untrusted. All interpolated values are passed
    // through escapeHtml(); source_url is additionally scheme-checked via safeUrl()
    // AND attribute-escaped before being placed in the href.
    const sourceUrl = safeUrl(u.source_url);
    const summaryMeta = summarySourceMeta(u.summary_source);
    const summaryBadge = `<span class="badge badge-summary ${escapeHtml(
      summaryMeta.className
    )}" title="${escapeHtml(summaryMeta.title)}">${escapeHtml(summaryMeta.label)}</span>`;
    return `
      <article class="card ${impactClass(u.impact_level)}" data-id="${escapeHtml(u.id)}">
        <header class="card-header">
          <div class="card-badges">
            <span class="badge badge-area">${escapeHtml(u.area)}</span>
            <span class="badge badge-stage">${escapeHtml(u.stage)}</span>
            <span class="badge badge-impact">${escapeHtml(u.impact_level)} Impact</span>
            ${summaryBadge}
          </div>
          <h2 class="card-title">${escapeHtml(u.title_en)}</h2>
          <p class="card-title-ja" lang="ja">${escapeHtml(u.title_ja)}</p>
        </header>
        <div class="card-body">
          <section>
            <h3>Summary</h3>
            <p>${escapeHtml(u.summary_en)}</p>
          </section>
          <section>
            <h3>Business Impact</h3>
            <p>${escapeHtml(u.business_impact_en)}</p>
          </section>
          <section>
            <h3>Recommended Action</h3>
            <p>${escapeHtml(u.recommended_action_en)}</p>
          </section>
        </div>
        <footer class="card-footer">
          <div class="source">
            <div class="source-meta">
              <span class="source-label">Source name</span>
              <span class="source-name" title="${escapeHtml(u.source_name)}">${escapeHtml(
      formatSourceDisplayName(u.source_name)
    )}</span>
            </div>
            <div class="source-actions">
              <a class="source-button" href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">
                View Original Japanese Source &rarr;
              </a>
              <div class="copy-actions" aria-label="Copy actions">
                <button type="button" class="copy-button" data-copy-action="summary" data-copy-id="${escapeHtml(
                  u.id
                )}" data-copy-label="Copy summary">Copy summary</button>
                <button type="button" class="copy-button" data-copy-action="source-link" data-copy-id="${escapeHtml(
                  u.id
                )}" data-copy-label="Copy source link">Copy source link</button>
              </div>
            </div>
            <span class="copy-status" aria-live="polite" role="status"></span>
            <p class="source-note">Original Japanese source remains authoritative.</p>
          </div>
          <div class="dates">
            <span>Published: ${escapeHtml(u.published_at)}</span>
            <span>Last checked: ${escapeHtml(u.last_checked)}</span>
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
      if (q) {
        const hay = (
          (u.title_en || "") +
          " " +
          (u.summary_en || "")
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
    if (typeof value !== "string") return 0;
    const trimmed = value.trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) return 0;
    const time = Date.parse(trimmed + "T00:00:00Z");
    return Number.isNaN(time) ? 0 : time;
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
      ["Updates", String(allUpdates.length)],
      ["Sources represented", String(distinctCount(allUpdates, "source_name"))],
      ["AI summaries", String(allUpdates.filter((u) => u.summary_source === "claude").length)],
      [
        "Open public comments",
        String(allUpdates.filter((u) => u.stage === "Public Comment Open").length),
      ],
      ["Latest checked", maxLastChecked(allUpdates) || "Unknown"],
    ];

    dataStatusList.innerHTML = "";
    stats.forEach(([label, value]) => {
      dataStatusList.appendChild(makeStatusChip(label, value));
    });
  }

  function renderDataStatusUnavailable() {
    if (!dataStatusList) return;
    dataStatusList.innerHTML = "";
    dataStatusList.appendChild(makeStatusChip("Data status", "Unavailable"));
  }

  function renderResultsMeta(filtered) {
    // AI summary count and last-checked date cover the FULL filtered set,
    // not only the cards currently rendered on screen.
    const shown = Math.min(visibleCount, filtered.length);
    const aiSummaryCount = filtered.filter((u) => u.summary_source === "claude").length;
    const lastChecked = maxLastChecked(filtered);
    const parts = [
      "Showing " + shown + " of " + filtered.length + " matching updates",
      allUpdates.length + " total updates",
      "AI summaries: " + aiSummaryCount,
    ];
    if (lastChecked) {
      parts.push("Last checked: " + lastChecked);
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
      // Render only the visible page window — never all (up to 1000) cards at once.
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
    cardsEl.addEventListener("click", handleCopyAction);
    window.addEventListener("popstate", () => {
      restoreFiltersFromUrl();
      render();
    });
  }

  // -------- Init --------
  document.addEventListener("DOMContentLoaded", () => {
    cacheDom();
    initModal();
    wireEvents();
    loadData();
  });
})();
