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
I18N_JS = (REPO_ROOT / "docs" / "i18n.js").read_text(encoding="utf-8")
INDEX_HTML = (REPO_ROOT / "docs" / "index.html").read_text(encoding="utf-8")
STYLE_CSS = (REPO_ROOT / "docs" / "style.css").read_text(encoding="utf-8")

# English UI strings now live in docs/i18n.js (English is the canonical default),
# so dynamic-string assertions search app.js + i18n.js together.
UI_JS = APP_JS + I18N_JS
CACHE_BUSTER = "i18n-zh-hans-20260618"
# i18n.js is busted independently so dictionary-only changes ship without
# re-fetching app.js / style.css.
I18N_CACHE_BUSTER = "zh-hans-v3-20260618"


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
                self.assertIn(snippet, UI_JS)

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
                self.assertIn(snippet, UI_JS)

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
            CACHE_BUSTER,
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
                self.assertIn(snippet, UI_JS)

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
                self.assertIn(snippet, INDEX_HTML + APP_JS + I18N_JS)

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

    def test_public_comment_closed_flows_through_stage_driven_ui(self):
        # Stage correction happens in the generated data. Every UI consumer
        # must continue to use the corrected stage rather than a display-only
        # deadline calculation.
        self.assertIn('filters.stage === "Public Comment Open"', APP_JS)
        self.assertIn('filters.stage = filters.stage === "Public Comment Open" ? "" : "Public Comment Open"', APP_JS)
        self.assertIn('u.stage === "Public Comment Open"', APP_JS)
        self.assertIn("update.stage,", APP_JS)
        self.assertIn("I18N.stageLabel(update.stage)", APP_JS)
        self.assertIn('"Public Comment Closed": "公开征求意见已截止"', I18N_JS)

    def test_data_status_avoids_overclaims(self):
        combined = INDEX_HTML + APP_JS + STYLE_CSS + I18N_JS
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
            "First detected",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS + INDEX_HTML + STYLE_CSS + I18N_JS)

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


class TestSimplifiedChineseI18n(unittest.TestCase):
    def test_i18n_loaded_before_app_with_cache_buster(self):
        self.assertIn("i18n.js?v=" + I18N_CACHE_BUSTER, INDEX_HTML)
        self.assertIn("app.js?v=" + CACHE_BUSTER, INDEX_HTML)
        self.assertIn("style.css?v=" + CACHE_BUSTER, INDEX_HTML)
        # i18n.js must be parsed before app.js so window.JLRW_I18N exists.
        self.assertLess(INDEX_HTML.index("i18n.js?v="), INDEX_HTML.index("app.js?v="))
        # The old cache buster must be fully replaced.
        self.assertNotIn("newly-detected-20260618", INDEX_HTML)

    def test_i18n_namespace_and_api(self):
        for snippet in (
            "window.JLRW_I18N",
            "applyStatic",
            "areaLabel",
            "stageLabel",
            "impactLabel",
            "sourceLabel",
            "summaryTypeLabel",
            "normalize",
            "STORAGE_KEY",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, I18N_JS)

    def test_localstorage_key_is_namespaced(self):
        self.assertIn('"jlrw-language"', I18N_JS)

    def test_supported_locales_are_en_and_zh_hans(self):
        self.assertIn('"en"', I18N_JS)
        self.assertIn('"zh-Hans"', I18N_JS)

    def test_language_selector_markup(self):
        for snippet in (
            'id="language-select"',
            'value="en"',
            'value="zh-Hans"',
            "简体中文",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, INDEX_HTML)

    def test_language_preference_precedence_url_localstorage_english(self):
        for snippet in (
            'params.get("lang")',
            'params.set("lang"',
            "I18N.normalize",
            "function readStoredLang",
            "function persistLang",
            "I18N.STORAGE_KEY",
            "function handleLanguageChange",
            "document.documentElement.lang = filters.lang",
            'document.title = I18N.t("document_title")',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)
        # Default English is the absence of the param (clean URLs).
        self.assertIn('filters.lang !== I18N.DEFAULT_LANG) params.set("lang"', APP_JS)

    def test_language_change_keeps_load_more_window(self):
        match = re.search(
            r"function handleLanguageChange\(nextLang\) \{(?P<body>.*?)\n  \}",
            APP_JS,
            re.S,
        )
        self.assertIsNotNone(match, "handleLanguageChange not found")
        body = match.group("body")
        self.assertIn("render()", body)
        # Language change must NOT reset the Load more window or wipe filters.
        self.assertNotIn("resetVisibleCount", body)
        self.assertNotIn("resetFilters", body)

    def test_card_translation_resolver_and_fallback(self):
        for snippet in (
            "function translatedField",
            "function englishCanonical",
            "function hasTranslation",
            "function localeBlock",
            'TRANSLATION_LOCALE = "zh-Hans"',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)
        # Translated values stay inside the escapeHtml() boundary.
        self.assertRegex(APP_JS, r"escapeHtml\(\s*translatedField")
        # Source URL still passes safeUrl().
        self.assertIn("safeUrl(u.source_url)", APP_JS)

    def test_card_translation_badge_and_notes_localized(self):
        # The CSS class lives in app.js (renderCard) + style.css.
        self.assertIn("badge-ai-translation", APP_JS)
        self.assertIn("badge-ai-translation", STYLE_CSS)
        self.assertIn("translation-note", STYLE_CSS)
        # The localized strings live in i18n.js.
        for snippet in (
            "AI翻译",
            "本译文由AI生成，仅用于信息监测。应以日文原文为准。",
            "中文翻译暂不可用，以下显示英文。",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, I18N_JS)

    def test_search_covers_chinese_english_japanese(self):
        hay = re.search(r"const hay = \((?P<body>.*?)\)\.toLowerCase\(\)", APP_JS, re.S)
        self.assertIsNotNone(hay)
        body = hay.group("body")
        for snippet in ("u.title_en", "u.title_ja", "u.summary_en", "tr.title", "tr.summary"):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, body)
        # The Chinese translation block is read regardless of active language.
        self.assertIn("u.translations[TRANSLATION_LOCALE]", APP_JS)

    def test_reliability_and_disclaimer_have_chinese(self):
        for snippet in (
            "并非官方译文",  # reliability note / trust body
            "本仪表板汇总日本政府及监管机构的官方信息",
            "重要提示 — 使用前请阅读",  # modal title
            "我已理解并同意",  # modal accept
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, I18N_JS)

    def test_newly_detected_chinese_text(self):
        self.assertIn("新近收录", I18N_JS)

    def test_static_strings_use_data_i18n_hooks(self):
        for snippet in (
            'data-i18n="modal_title"',
            'data-i18n="trust_body"',
            'data-i18n="ctl_search"',
            'data-i18n-placeholder="ctl_search_placeholder"',
            'data-i18n="qf_reset"',
            'data-i18n="export_button"',
            'data-i18n="footer_bottom"',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, INDEX_HTML)


class TestChineseCsvExport(unittest.TestCase):
    def _zh_headers(self):
        # The 17-column Chinese layout is defined in i18n.js (CSV_HEADERS_ZH);
        # app.js references it via I18N.csvHeadersZh().
        self.assertIn("const ZH_CSV_HEADERS = I18N.csvHeadersZh();", APP_JS)
        match = re.search(r"var CSV_HEADERS_ZH = \[(?P<body>.*?)\];", I18N_JS, re.S)
        self.assertIsNotNone(match, "CSV_HEADERS_ZH not found in i18n.js")
        return re.findall(r'"([^"]+)"', match.group("body"))

    def test_chinese_csv_column_order_is_fixed(self):
        self.assertEqual(
            self._zh_headers(),
            [
                "中文标题",
                "英文参考标题",
                "日文原题",
                "领域",
                "阶段",
                "影响程度",
                "来源",
                "日文官方来源URL",
                "发布日期",
                "首次收录日期",
                "最后确认日期",
                "摘要类型",
                "摘要",
                "业务影响",
                "建议措施",
                "排序分数",
                "内部ID",
            ],
        )

    def test_internal_id_is_last_chinese_column(self):
        self.assertEqual(self._zh_headers()[-1], "内部ID")

    def test_chinese_csv_keeps_english_reference_and_japanese_columns(self):
        headers = self._zh_headers()
        self.assertIn("英文参考标题", headers)
        self.assertIn("日文原题", headers)

    def test_chinese_csv_uses_fallback_and_protections(self):
        for snippet in (
            "function csvRowZh",
            "function buildCsvTextZh",
            'translatedField(update, "title")',
            "ZH_CSV_HEADERS.map(csvCell)",
            ".map(csvCell)",  # cells go through formula-injection protection
            "csvSourceUrl(update)",  # safe URL
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)

    def test_english_csv_is_dispatched_for_english(self):
        # English keeps the existing buildCsvText; zh-Hans uses buildCsvTextZh.
        self.assertIn("buildCsvTextZh(filtered) : buildCsvText(filtered)", APP_JS)

    def test_english_csv_headers_unchanged(self):
        # The original English header constant must be intact (see also
        # TestAppJsUrlState.test_csv_headers_exact_order).
        self.assertIn("const headers = [", APP_JS)
        self.assertIn('"English title"', APP_JS)


class TestChineseUiTerminologyV3(unittest.TestCase):
    def test_unified_official_source_and_impact_terms(self):
        for snippet in (
            "中等影响",
            "高影响",
            "低影响",
            "公开征求意见中",
            "查看日文官方来源",
            "以日文官方来源（原文）为准",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, I18N_JS)

    def test_superseded_zh_strings_removed(self):
        for snippet in (
            "征求意见进行中",
            "查看日文原始来源",
            "以日文原始来源为准",
            "{level}影响",
        ):
            with self.subTest(snippet=snippet):
                self.assertNotIn(snippet, I18N_JS)

    def test_english_ui_strings_unchanged(self):
        for snippet in (
            '"{level} Impact"',
            '"Public Comment Open"',
            "View Original Japanese Source →",
            "Original Japanese source remains authoritative.",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, I18N_JS)

    def test_language_switch_url_state_and_focus_trap_intact(self):
        # v3 must not disturb the language selector, URL lang state, or focus trap.
        self.assertIn('id="language-select"', INDEX_HTML)
        self.assertIn('params.set("lang"', APP_JS)
        self.assertIn("function trapModalFocus", APP_JS)


if __name__ == "__main__":
    unittest.main()
