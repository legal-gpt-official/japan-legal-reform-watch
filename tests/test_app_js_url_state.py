"""Static checks for docs/app.js URL filter state, copy actions, and source mappings.

These are lightweight string/regex checks because the dashboard intentionally
has no JavaScript test runner or build step.
"""

import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_updates as fu  # noqa: E402

APP_JS = (REPO_ROOT / "docs" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (REPO_ROOT / "docs" / "index.html").read_text(encoding="utf-8")
STYLE_CSS = (REPO_ROOT / "docs" / "style.css").read_text(encoding="utf-8")


def object_body(name: str) -> str:
    match = re.search(rf"const {name}\s*=\s*\{{(?P<body>.*?)\n\s*\}};", APP_JS, re.S)
    if not match:
        raise AssertionError(f"{name} object not found in docs/app.js")
    return match.group("body")


class TestAppJsUrlState(unittest.TestCase):
    def test_source_slug_mapping_exists(self):
        self.assertIn("const SOURCE_SLUGS", APP_JS)
        self.assertIn("function getSourceSlug", APP_JS)
        self.assertIn("function getSourceNameFromSlug", APP_JS)

    def test_representative_source_slugs_exist(self):
        slug_body = object_body("SOURCE_SLUGS")
        for slug in (
            "egov", "jftc", "ppc", "moe", "fsa", "mhlw", "digital-agency", "meti", "caa", "mlit", "maff",
        ):
            with self.subTest(slug=slug):
                self.assertRegex(slug_body, rf'"{re.escape(slug)}"\s*:')

    def test_source_slug_keys_are_unique(self):
        slug_body = object_body("SOURCE_SLUGS")
        slugs = re.findall(r'"([a-z0-9-]+)"\s*:', slug_body)
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_fetch_sources_have_display_names_and_slugs(self):
        display_body = object_body("SOURCE_DISPLAY_NAMES")
        slug_body = object_body("SOURCE_SLUGS")
        for source in fu.SOURCES:
            source_name = source["name"]
            with self.subTest(source=source_name):
                self.assertIn(source_name, display_body)
                self.assertIn(source_name, slug_body)

    def test_url_state_functions_exist(self):
        for snippet in (
            "function restoreFiltersFromUrl",
            "function updateUrlFromFilters",
            "new URLSearchParams",
            "window.history.replaceState",
            'params.set("q"',
            'params.set("area"',
            'params.set("stage"',
            'params.set("source"',
            'params.set("impact"',
            'params.set("sort"',
            'params.set("ai", "1")',
            'window.addEventListener("popstate"',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)

    def test_sort_control_and_url_state_exist(self):
        for snippet in (
            'id="filter-sort"',
            'value="relevance">Relevance',
            'value="published">Published date',
            'value="checked">Last checked',
            'value="detected">First detected',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, INDEX_HTML)

        for snippet in (
            'const DEFAULT_SORT = "relevance"',
            '"published"',
            '"checked"',
            '"detected"',
            'detected: "First detected"',
            'params.get("sort")',
            'function sortUpdates',
            "relevance_score",
            "published_at",
            "last_checked",
            "firstSeenDateValue",
            "dateValue",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)

    def test_copy_actions_exist(self):
        for snippet in (
            "Copy summary",
            "Copy source link",
            'data-copy-action="summary"',
            'data-copy-action="source-link"',
            "function buildCopySummaryText",
            "function sourceUrlForCopy",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)

    def test_clipboard_copy_support_exists(self):
        for snippet in (
            "navigator.clipboard.writeText",
            'document.execCommand("copy")',
            "function fallbackCopyText",
            "function copyTextToClipboard",
            'aria-live="polite"',
            "Summary copied",
            "Source link copied",
            "Copy failed",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)

    def test_copy_action_styles_exist(self):
        for snippet in (
            ".source-actions",
            ".copy-actions",
            ".copy-button",
            ".copy-status",
            ".copy-button.is-copy-success",
            ".copy-button.is-copy-error",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, STYLE_CSS)

    def test_mobile_filter_controls_exist(self):
        for snippet in (
            'id="mobile-filters-toggle"',
            'aria-expanded="false"',
            'aria-controls="filter-panel"',
            'id="active-filter-summary"',
            'id="filter-panel"',
            "newly-detected-20260618",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, INDEX_HTML)

        for snippet in (
            "function setMobileFiltersOpen",
            "function activeFilterSummaryText",
            "function updateActiveFilterSummary",
            "Hide filters",
            "Filters & Search",
            "No active filters",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)

        for snippet in (
            ".mobile-controls-bar",
            ".mobile-filters-toggle",
            ".filter-panel.is-open",
            ".active-filter-summary",
            "@media (max-width: 720px)",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, STYLE_CSS)

    def test_data_status_controls_exist(self):
        for snippet in (
            "Data status",
            'id="data-status-list"',
            "Sources represented",
            "Open public comments",
            "Latest checked",
            "Newly detected (7d)",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, INDEX_HTML + APP_JS)

        for snippet in (
            "function renderDataStatus",
            "function distinctCount",
            'u.summary_source === "claude"',
            'u.stage === "Public Comment Open"',
            "isNewlyDetected(u)",
            "maxLastChecked(allUpdates)",
            "last_checked",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)

        for snippet in (
            ".data-status",
            ".data-status-list",
            ".data-status-chip",
            ".data-status-note",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, STYLE_CSS)

    def test_data_status_avoids_overclaims(self):
        combined = INDEX_HTML + APP_JS + STYLE_CSS
        for forbidden in (
            "All sources checked successfully",
            "Complete coverage",
            "Real-time",
            "No missed updates",
            "Official translation",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)

    def test_search_haystack_includes_japanese_title(self):
        """Search must cover title_en, title_ja, and summary_en (M-2)."""
        for snippet in (
            '(u.title_en || "")',
            '(u.title_ja || "")',
            '(u.summary_en || "")',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)
        # title_ja sits inside the same haystack expression as title_en.
        hay = re.search(r"const hay = \((?P<body>.*?)\)\.toLowerCase\(\)", APP_JS, re.S)
        self.assertIsNotNone(hay)
        self.assertIn("u.title_ja", hay.group("body"))

    def test_modal_focus_trap_exists(self):
        """aria-modal behavior is backed by a real Tab focus trap (M-4)."""
        for snippet in (
            "function trapModalFocus",
            'document.addEventListener("keydown", trapModalFocus, true)',
            'event.key !== "Tab"',
            "event.shiftKey",
            "modalEl.contains(active)",
            "event.preventDefault()",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)
        # Escape must still NOT close the modal.
        self.assertNotIn('"Escape"', APP_JS)

    def test_csv_formula_protection_covers_tab_and_cr(self):
        # = + - @ plus tab/CR-prefixed payloads are neutralized with a leading quote (L-5).
        self.assertIn("/^[=+\\-@\\t\\r]/", APP_JS)
        self.assertIn('return "\'" + text', APP_JS)

    def test_newly_detected_filter_badge_url_and_summary_exist(self):
        for snippet in (
            'data-quick-filter="newly-detected"',
            "Newly detected",
            'params.get("new") === "7"',
            'params.set("new", "7")',
            "filters.newlyDetectedOnly",
            "isNewlyDetected",
            "NEWLY_DETECTED_DAYS = 7",
            "ageDays >= 0 && ageDays < NEWLY_DETECTED_DAYS",
            "parseIsoDateValue",
            "Date.UTC",
            "currentJstDateValue",
            "firstSeenDateValue",
            "firstSeenDisplay",
            "badge-newly-detected",
            "First detected by this dashboard on",
            "First detected by this dashboard",
            "First detected:",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS + INDEX_HTML + STYLE_CSS)

    def test_newly_detected_rejects_unknown_url_values_and_future_dates(self):
        self.assertIn('params.get("new") === "7"', APP_JS)
        self.assertNotIn('params.get("new") === "true"', APP_JS)
        self.assertIn("ageDays >= 0", APP_JS)
        self.assertIn("ageDays < NEWLY_DETECTED_DAYS", APP_JS)
        self.assertIn("return 0;", APP_JS)

    def test_filter_and_sort_changes_reset_render_window(self):
        self.assertIn("const PAGE_SIZE = 50", APP_JS)
        self.assertRegex(APP_JS, r"function resetVisibleCount\(\) \{\s*visibleCount = PAGE_SIZE;")
        self.assertRegex(APP_JS, r"function applyFilterChange\(\) \{\s*resetVisibleCount\(\);")
        self.assertIn('filterSort.addEventListener("change"', APP_JS)
        self.assertIn('loadMoreBtn.addEventListener("click"', APP_JS)
        self.assertNotIn('params.set("visible"', APP_JS)
        self.assertNotIn('params.set("page"', APP_JS)

    def test_csv_export_failure_logs_error_object(self):
        self.assertIn('console.warn("[JLRW] CSV export failed.", e)', APP_JS)

    def test_csv_headers_exact_order(self):
        match = re.search(r"const headers = \[(?P<body>.*?)\];", APP_JS, re.S)
        self.assertIsNotNone(match, "CSV headers array not found")
        headers = re.findall(r'"([^"]+)"', match.group("body"))
        self.assertEqual(
            headers,
            [
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
            ],
        )
        self.assertEqual(headers[-1], "Internal ID")

    def test_csv_export_controls_exist(self):
        for snippet in (
            "Export CSV",
            'id="export-csv"',
            'id="export-status"',
            "Export matching updates to CSV",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, INDEX_HTML)

        for snippet in (
            "function buildCsvText",
            "function csvCell",
            "function protectCsvFormula",
            "function exportCurrentCsv",
            "function currentFilteredUpdates",
            "text/csv;charset=utf-8",
            "new Blob",
            "URL.createObjectURL",
            "URL.revokeObjectURL",
            "link.download",
            "\\uFEFF",
            "formatSourceDisplayName(update.source_name)",
            "safeUrl(update.source_url)",
            "summarySourceLabel",
            "firstSeenDisplay(update)",
            '"English title"',
            '"Original Japanese title"',
            '"Official source URL"',
            '"First detected"',
            '"Summary type"',
            '"Ranking score"',
            '"Internal ID"',
            '"Ranking score",\n      "Internal ID"',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)

        for snippet in (
            ".export-actions",
            ".export-csv-button",
            ".export-status",
            ".controls-footer",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, STYLE_CSS)


if __name__ == "__main__":
    unittest.main()
