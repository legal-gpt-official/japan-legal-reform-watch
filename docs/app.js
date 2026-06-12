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
    quickFilterButtons,
    cardsEl,
    emptyEl,
    errorEl,
    metaEl,
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
    quickFilterButtons = Array.from(document.querySelectorAll("[data-quick-filter]"));
    cardsEl = $("#cards");
    emptyEl = $("#empty-state");
    errorEl = $("#error-state");
    metaEl = $("#results-meta");
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

  function restoreFiltersFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const area = params.get("area") || "";
    const stage = params.get("stage") || "";
    const source = getSourceNameFromSlug(params.get("source") || "");
    const impact = params.get("impact") || "";

    filters.search = params.get("q") || "";
    filters.area = hasOption(filterArea, area) ? area : "";
    filters.stage = hasOption(filterStage, stage) ? stage : "";
    filters.source = source || "";
    filters.impact = hasOption(filterImpact, impact) ? impact : "";
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
    filters.search = "";
    filters.aiSummaryOnly = false;
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
            <a class="source-button" href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">
              View Original Japanese Source &rarr;
            </a>
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

  function maxLastChecked(updates) {
    const dates = updates
      .map((u) => (typeof u.last_checked === "string" ? u.last_checked.trim() : ""))
      .filter((v) => /^\d{4}-\d{2}-\d{2}$/.test(v));
    if (dates.length === 0) return "";
    dates.sort();
    return dates[dates.length - 1];
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
    const filtered = applyFilters();
    updateQuickFilterState();
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
