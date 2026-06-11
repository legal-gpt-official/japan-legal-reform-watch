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

  // -------- State --------
  let allUpdates = [];
  const filters = {
    area: "",
    stage: "",
    impact: "",
    search: "",
  };

  // -------- DOM helpers --------
  const $ = (sel) => document.querySelector(sel);

  let modalEl,
    acceptBtn,
    searchInput,
    filterArea,
    filterStage,
    filterImpact,
    cardsEl,
    emptyEl,
    errorEl,
    metaEl;

  function cacheDom() {
    modalEl = $("#disclaimer-modal");
    acceptBtn = $("#accept-disclaimer");
    searchInput = $("#search-input");
    filterArea = $("#filter-area");
    filterStage = $("#filter-stage");
    filterImpact = $("#filter-impact");
    cardsEl = $("#cards");
    emptyEl = $("#empty-state");
    errorEl = $("#error-state");
    metaEl = $("#results-meta");
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

  function populateFilterOptions() {
    const areas = unique(allUpdates, "area");
    const stages = unique(allUpdates, "stage");
    const impacts = unique(allUpdates, "impact_level");

    areas.forEach((a) => filterArea.appendChild(new Option(a, a)));
    stages.forEach((s) => filterStage.appendChild(new Option(s, s)));

    // Impact levels in canonical order (High > Medium > Low) when present.
    const orderedImpacts = IMPACT_ORDER.filter((i) => impacts.includes(i)).concat(
      impacts.filter((i) => !IMPACT_ORDER.includes(i))
    );
    orderedImpacts.forEach((i) => filterImpact.appendChild(new Option(i, i)));
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
            <span class="source-label">Source</span>
            <a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(
      u.source_name
    )}</a>
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
      if (filters.impact && u.impact_level !== filters.impact) return false;
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
    // AI summary count and last-checked date are based on the currently displayed set.
    const aiSummaryCount = filtered.filter((u) => u.summary_source === "claude").length;
    const lastChecked = maxLastChecked(filtered);
    const parts = [
      "Showing " + filtered.length + " of " + allUpdates.length + " updates",
      "AI summaries: " + aiSummaryCount,
    ];
    if (lastChecked) {
      parts.push("Last checked: " + lastChecked);
    }
    metaEl.textContent = parts.join(" · ");
  }

  function render() {
    const filtered = applyFilters();
    errorEl.hidden = true;
    if (filtered.length === 0) {
      cardsEl.innerHTML = "";
      emptyEl.hidden = false;
    } else {
      cardsEl.innerHTML = filtered.map(renderCard).join("");
      emptyEl.hidden = true;
    }
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
      render();
    }, 120);
    searchInput.addEventListener("input", onSearch);

    filterArea.addEventListener("change", (e) => {
      filters.area = e.target.value;
      render();
    });
    filterStage.addEventListener("change", (e) => {
      filters.stage = e.target.value;
      render();
    });
    filterImpact.addEventListener("change", (e) => {
      filters.impact = e.target.value;
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
