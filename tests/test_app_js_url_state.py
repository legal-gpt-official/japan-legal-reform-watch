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
ALERTS_CONFIG_JS = (REPO_ROOT / "docs" / "alerts-config.js").read_text(encoding="utf-8")
INDEX_HTML = (REPO_ROOT / "docs" / "index.html").read_text(encoding="utf-8")
STYLE_CSS = (REPO_ROOT / "docs" / "style.css").read_text(encoding="utf-8")
THANK_YOU_HTML = (REPO_ROOT / "docs" / "alerts" / "thank-you.html").read_text(encoding="utf-8")
THANK_YOU_JS = (REPO_ROOT / "docs" / "alerts" / "thank-you.js").read_text(encoding="utf-8")
THANK_YOU_CSS = (REPO_ROOT / "docs" / "alerts" / "thank-you.css").read_text(encoding="utf-8")

# English UI strings now live in docs/i18n.js (English is the canonical default),
# so dynamic-string assertions search app.js + i18n.js together.
UI_JS = APP_JS + I18N_JS
CACHE_BUSTER = "self-serve-alerts-20260925"
APP_CACHE_BUSTER = "self-serve-alerts-20260925"
# i18n.js is busted independently so dictionary-only changes ship without
# re-fetching app.js / style.css.
I18N_CACHE_BUSTER = "self-serve-alerts-20260925"


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
            'params.set("year"',
            'window.addEventListener("popstate"',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)

    def test_yearly_archive_selector_and_lazy_loading_exist(self):
        for snippet in (
            'id="filter-period"',
            'data-i18n="ctl_period"',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, INDEX_HTML)

        for snippet in (
            'const DATA_MANIFEST_URL = "./data/legal_updates_manifest.json"',
            'const ALL_PERIODS = "all"',
            'const UNDATED_PERIOD = "undated"',
            'entry.file !== "./data/archive/" + entry.value + ".json"',
            "const datasetCache = new Map()",
            "async function datasetForPeriod",
            "async function handlePeriodChange",
            'params.get("year")',
            'params.set("year", filters.period)',
            "filters.period !== archiveManifest.latest_period",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)

    def test_latest_period_is_default_and_reset_target(self):
        self.assertIn("return periodEntry(value) ? value : archiveManifest.latest_period", APP_JS)
        self.assertIn("const defaultPeriod = archiveManifest.latest_period", APP_JS)
        self.assertIn("await installPeriodDataset(defaultPeriod)", APP_JS)

    def test_all_years_uses_uncapped_canonical_file(self):
        self.assertIn('const DATA_URL = "./data/legal_updates.json"', APP_JS)
        self.assertIn("period === ALL_PERIODS ? DATA_URL", APP_JS)
        self.assertIn("data.length !== archiveManifest.total_items", APP_JS)

    def test_manifest_period_counts_must_equal_total(self):
        self.assertIn("let periodTotal = 0", APP_JS)
        self.assertIn("periodTotal += entry.count", APP_JS)
        self.assertIn("periodTotal !== value.total_items", APP_JS)

    def test_period_change_and_popstate_install_dataset_before_render(self):
        self.assertIn('filterPeriod.addEventListener("change"', APP_JS)
        self.assertIn('window.addEventListener("popstate", async () =>', APP_JS)
        self.assertIn("await installPeriodDataset(requestedPeriod)", APP_JS)

    def test_period_status_and_csv_filename_are_scoped(self):
        for snippet in (
            'I18N.t("ds_period")',
            'I18N.t("ds_archive_total")',
            'I18N.t("period_all")',
            'I18N.t("period_undated")',
            '"japan-legal-reform-watch-" + periodPart + "-" + datePart + ".csv"',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)

    def test_sort_control_and_url_state_exist(self):
        for snippet in (
            'id="filter-sort"',
            'value="relevance">Relevance',
            'value="published" selected>Published date',
            'value="checked">Last checked',
            'value="detected">First detected',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, INDEX_HTML)

        for snippet in (
            'const DEFAULT_SORT = "published"',
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

    def test_default_sort_is_published_and_relevance_preserves_build_order(self):
        self.assertIn(
            'value="published" selected>Published date',
            INDEX_HTML,
        )
        match = re.search(
            r"function sortUpdates\(updates\) \{(?P<body>.*?)\n  \}\n",
            APP_JS,
            re.S,
        )
        self.assertIsNotNone(match, "sortUpdates function not found")
        body = match.group("body")
        self.assertRegex(
            body,
            re.compile(
                r'filters\.sort === "published".*?published_at.*?relevance_score',
                re.S,
            ),
        )
        self.assertRegex(
            body,
            re.compile(
                r"build_public_data\.py owns the composite relevance ranking.*?"
                r"return originalOrder\(a, b\);",
                re.S,
            ),
        )

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
            "localizedCoverageMetric(allUpdates)",
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

    def test_content_coverage_metric_is_explicit_for_each_language(self):
        for snippet in (
            "function hasCompleteTranslation",
            "function localizedCoverageMetric",
            'dataStatusKey: "ds_english_ai_summaries"',
            'metaKey: "meta_english_ai"',
            'dataStatusKey: "ds_japanese_ai_summaries"',
            'metaKey: "meta_japanese_ai"',
            'dataStatusKey: "ds_chinese_translations"',
            'metaKey: "meta_chinese_translations"',
            "hasJapaneseSummary(update)",
            "hasCompleteTranslation(update)",
            'update.summary_source === "claude"',
            "I18N.t(coverage.dataStatusKey)",
            "I18N.t(coverage.metaKey, { count: coverage.count })",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)

        self.assertNotIn('I18N.t("ds_ai_summaries")', APP_JS)
        self.assertNotIn('I18N.t("meta_ai"', APP_JS)

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

    def test_search_haystack_includes_titles_and_only_ai_summary_bodies(self):
        """Titles are universal; only reviewed AI summary bodies are searchable."""
        for snippet in (
            "update.title_en",
            "update.title_ja",
            "block.title",
            'update.summary_source === "claude"',
            "update.summary_en",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)
        hay = re.search(
            r"function searchHaystack\(update\) \{(?P<body>.*?)\n  \}\n\n  function applyFilters",
            APP_JS,
            re.S,
        )
        self.assertIsNotNone(hay)
        self.assertIn("update.title_ja", hay.group("body"))

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

    def test_saved_search_local_mvp_exists(self):
        for snippet in (
            'id="save-search"',
            'id="manage-saved-searches"',
            'id="saved-search-dialog"',
            'id="saved-search-form"',
            'id="saved-search-list"',
            'const SAVED_SEARCHES_STORAGE_KEY = "jlrw-saved-searches-v1"',
            "const MAX_SAVED_SEARCHES = 5",
            "function readSavedSearches",
            "function writeSavedSearches",
            "function saveCurrentSearch",
            "async function loadSavedSearch",
            "function deleteSavedSearch",
            "buildFilterParams(false).toString()",
            "await restoreStateFromLocation()",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS + INDEX_HTML)

    def test_saved_search_copy_is_local_only_and_localized(self):
        for snippet in (
            "Saved searches stay on this device.",
            "They do not create an account or email subscription.",
            "搜索条件仅保存在本设备中",
            'saved_searches_count: "Saved searches ({count})"',
            'saved_searches_count: "已保存的搜索（{count}）"',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, I18N_JS + INDEX_HTML)

    def test_saved_search_rendering_uses_dom_text_not_html(self):
        self.assertIn("savedSearchList.replaceChildren()", APP_JS)
        self.assertIn("name.textContent = saved.name", APP_JS)
        self.assertIn("summary.textContent = savedSearchDescription(saved.query)", APP_JS)
        self.assertNotIn("savedSearchList.innerHTML", APP_JS)

    def test_alert_plan_is_self_serve_without_an_inquiry_form(self):
        for snippet in (
            'id="alert-plan-actions" class="alert-plan-actions" hidden',
            'id="alert-subscribe-monthly"',
            'id="alert-subscribe-yearly"',
            'id="alert-plan-unavailable"',
            'id="alert-manage-subscription"',
            'rel="noopener noreferrer"',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, INDEX_HTML)
        # The retired pilot collected contact details through Contact Form 7 and
        # relied on manual activation. Nothing personal is collected any more.
        combined = INDEX_HTML + APP_JS + ALERTS_CONFIG_JS + I18N_JS
        for retired in ("alert-pilot", "alertPilot", "alert_pilot", "wpcf7", "contact-forms", "client_reference_id"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, combined)
        dialog = INDEX_HTML[INDEX_HTML.index('<dialog'):INDEX_HTML.index("</dialog>")]
        self.assertNotIn('type="email"', dialog)
        self.assertNotIn("<textarea", dialog)

    def test_alert_plan_copy_is_localized_scoped_and_non_authoritative(self):
        for snippet in (
            "US$19/month",
            "or US$190/year",
            "月額19米ドル",
            "每月19美元",
            "when structured official data is available",
            "Alert emails are monitoring aids, not legal advice",
            "does not mean a new law or regulation",
            "cancel the current subscription in the subscription portal",
            "A cancellation takes effect at the end of the current billing period.",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, INDEX_HTML + I18N_JS)
        self.assertNotIn("non-refundable", (INDEX_HTML + I18N_JS).lower())
        self.assertNotIn("comprehensive coverage", (INDEX_HTML + I18N_JS).lower())

    def test_alert_config_is_public_only_and_stripe_hosted(self):
        for snippet in ("window.JLRW_ALERTS_CONFIG", "checkoutLinks: Object.freeze", "manageSubscriptionUrl:"):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, ALERTS_CONFIG_JS)
        for forbidden in ("sk_live_", "sk_test_", "rk_live_", "rk_test_", "whsec_", "api_key", "apiSecret"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, ALERTS_CONFIG_JS)
        self.assertIsNone(re.search(r"\bre_[A-Za-z0-9]{8,}", ALERTS_CONFIG_JS), "Resend key in public config")
        for plan, url in re.findall(r'(monthly|yearly): "([^"]*)"', ALERTS_CONFIG_JS):
            with self.subTest(plan=plan):
                self.assertTrue(url == "" or url.startswith("https://buy.stripe.com/"), url)
        manage = re.search(r'manageSubscriptionUrl: "([^"]*)"', ALERTS_CONFIG_JS)
        self.assertIsNotNone(manage)
        self.assertTrue(
            manage.group(1) == "" or manage.group(1).startswith("https://billing.stripe.com/p/login/"),
            manage.group(1),
        )

    def test_alert_links_only_trust_stripe_hosts(self):
        for snippet in (
            'trustedIntegrationUrl(plan === "yearly" ? links.yearly : links.monthly, "buy.stripe.com", "/")',
            'trustedIntegrationUrl(ALERTS_CONFIG.manageSubscriptionUrl, "billing.stripe.com", "/p/login/")',
            "if (alertPlanActions) alertPlanActions.hidden = !available;",
            "if (alertPlanUnavailable) alertPlanUnavailable.hidden = available;",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)

    def test_alert_integration_urls_reject_ports_credentials_and_fragments(self):
        trusted = re.search(
            r"function trustedIntegrationUrl\(value, expectedHost, expectedPathPrefix\) \{(?P<body>.*?)\n  \}",
            APP_JS,
            re.S,
        )
        self.assertIsNotNone(trusted)
        body = trusted.group("body")
        for snippet in (
            "parsed.host !== expectedHost",
            "parsed.username",
            "parsed.password",
            'parsed.hash = ""',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, body)
        self.assertNotIn("parsed.hostname !== expectedHost", body)

    def test_feed_selector_matches_alert_channels_and_allow_lists_paths(self):
        import alert_common

        options = re.findall(
            r'<option value="([a-z]+)"(?: data-area="([^"]+)")?',
            INDEX_HTML[INDEX_HTML.index('id="alert-feed-channel"'):],
        )
        options = options[: len(alert_common.CHANNELS)]
        self.assertEqual([value for value, _ in options], [c.value for c in alert_common.CHANNELS])
        for (value, area), channel in zip(options, alert_common.CHANNELS):
            with self.subTest(channel=value):
                self.assertEqual(area or None, channel.area)
        self.assertIn("known && /^[a-z]+$/.test(value)", APP_JS)
        self.assertIn('"./feeds/" + channel + ".xml"', APP_JS)
        self.assertIn('"./feeds/" + channel + ".ics"', APP_JS)
        self.assertIn("I18N.areaLabel(option.dataset.area)", APP_JS)

    def test_saved_search_status_relocalizes(self):
        for snippet in (
            "savedSearchStatusKey = key || \"\"",
            "setSavedSearchStatus(savedSearchStatusKey, savedSearchStatusIsError)",
            'aria-live="polite"',
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS + INDEX_HTML)

    def test_saved_search_description_strips_markup_characters(self):
        sanitizer = re.search(
            r"function inquirySafeSearchText\(value\) \{(?P<body>.*?)\n  \}", APP_JS, re.S
        )
        self.assertIsNotNone(sanitizer)
        self.assertIn('replace(/[<>&]/g, "")', sanitizer.group("body"))
        self.assertIn('const search = inquirySafeSearchText(params.get("q") || "")', APP_JS)

    def test_alert_config_never_logs(self):
        self.assertNotIn("console.log", ALERTS_CONFIG_JS)
        self.assertNotIn("console.log", THANK_YOU_JS)

    def test_checkout_follow_up_page_is_safe_and_non_authoritative(self):
        for snippet in (
            'content="noindex, nofollow"',
            'id="completion-plan"',
            'id="completion-manage"',
            "data-dashboard-link",
            "This page does not confirm payment",
            "monitoring aids, not legal advice",
            "本页面不代表付款确认",
            "このページは支払いを確認するものではありません",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, THANK_YOU_HTML + I18N_JS)
        self.assertLess(THANK_YOU_HTML.index("../i18n.js?v="), THANK_YOU_HTML.index("../alerts-config.js?v="))
        self.assertLess(THANK_YOU_HTML.index("../alerts-config.js?v="), THANK_YOU_HTML.index("thank-you.js?v="))
        self.assertNotIn("Thank you for subscribing", THANK_YOU_HTML + I18N_JS)
        self.assertNotIn("感谢您的订阅", THANK_YOU_HTML + I18N_JS)
        self.assertNotIn("inquiry=jlrw-alert-pilot", THANK_YOU_HTML)

    def test_all_static_i18n_keys_exist_in_all_dictionaries(self):
        html = INDEX_HTML + THANK_YOU_HTML
        keys = set(
            re.findall(
                r'data-i18n(?:-(?:placeholder|aria-label|title))?="([a-z0-9_]+)"',
                html,
            )
        )
        self.assertTrue(keys)
        for key in sorted(keys):
            with self.subTest(key=key):
                definitions = re.findall(rf"^\s+{re.escape(key)}\s*:", I18N_JS, re.M)
                self.assertEqual(len(definitions), 3)

    def test_all_dynamic_i18n_keys_match_across_languages(self):
        en = I18N_JS[I18N_JS.index("    en: {"):I18N_JS.index("    ja: {")]
        ja = I18N_JS[I18N_JS.index("    ja: {"):I18N_JS.index('    "zh-Hans": {')]
        zh = I18N_JS[
            I18N_JS.index('    "zh-Hans": {'):
            I18N_JS.index("  // -------- Controlled-vocabulary")
        ]

        def dictionary_keys(block):
            return set(re.findall(r"^\s{6}([a-z0-9_]+):", block, re.M))

        self.assertEqual(dictionary_keys(en), dictionary_keys(ja))
        self.assertEqual(dictionary_keys(en), dictionary_keys(zh))

    def test_checkout_page_uses_current_shared_cache_buster(self):
        for asset in ("thank-you.css", "../i18n.js", "../alerts-config.js", "thank-you.js"):
            with self.subTest(asset=asset):
                self.assertIn(asset + "?v=" + CACHE_BUSTER, THANK_YOU_HTML)

    def test_checkout_follow_up_plan_and_language_are_allow_listed(self):
        for snippet in (
            'plan === "monthly" || plan === "yearly"',
            "planValue.textContent = I18N.t(",
            'parsed.host !== "billing.stripe.com"',
            'parsed.pathname.indexOf("/p/login/") !== 0',
            'params.set("lang", normalized)',
            "I18N.normalize(readStoredLanguage() || I18N.DEFAULT_LANG)",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, THANK_YOU_JS)
        self.assertNotIn("innerHTML", THANK_YOU_JS)
        self.assertIn("[hidden]", THANK_YOU_CSS)
        self.assertIn("@media (max-width: 680px)", THANK_YOU_CSS)

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
        self.assertIn("app.js?v=" + APP_CACHE_BUSTER, INDEX_HTML)
        self.assertIn("style.css?v=" + CACHE_BUSTER, INDEX_HTML)
        # i18n.js must be parsed before app.js so window.JLRW_I18N exists.
        self.assertLess(INDEX_HTML.index("i18n.js?v="), INDEX_HTML.index("app.js?v="))
        self.assertIn("alerts-config.js?v=" + CACHE_BUSTER, INDEX_HTML)
        self.assertLess(INDEX_HTML.index("alerts-config.js?v="), INDEX_HTML.index("app.js?v="))
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

    def test_supported_locales_are_en_ja_and_zh_hans(self):
        self.assertIn('"en"', I18N_JS)
        self.assertIn('"ja"', I18N_JS)
        self.assertIn('"zh-Hans"', I18N_JS)

    def test_language_selector_markup(self):
        for snippet in (
            'id="language-select"',
            'value="en"',
            'value="ja"',
            "日本語",
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
            "SEARCH_TRANSLATION_LOCALES",
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
        hay = re.search(
            r"function searchHaystack\(update\) \{(?P<body>.*?)\n  \}\n\n  function applyFilters",
            APP_JS,
            re.S,
        )
        self.assertIsNotNone(hay)
        body = hay.group("body")
        for snippet in (
            "update.title_en",
            "update.title_ja",
            "update.summary_en",
            "block.title",
            "block.summary",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, body)
        self.assertIn('update.summary_source === "claude"', body)
        self.assertIn("searchHaystack(u).includes(q)", APP_JS)
        # The Chinese translation block is read regardless of active language.
        self.assertIn("translations[locale]", APP_JS)

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
            'data-i18n="ctl_period"',
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
        # app.js selects localized headers through the i18n layer.
        self.assertIn("I18N.csvHeadersLocalized(filters.lang)", APP_JS)
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
            "function csvRowLocalized",
            "function buildCsvTextLocalized",
            'translatedField(update, "title")',
            "headers.map(csvCell)",
            ".map(csvCell)",  # cells go through formula-injection protection
            "csvSourceUrl(update)",  # safe URL
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APP_JS)

    def test_english_csv_is_dispatched_for_english(self):
        # English keeps the existing buildCsvText; non-English uses localized CSV.
        self.assertIn("buildCsvTextLocalized(filtered)", APP_JS)
        self.assertIn(": buildCsvText(filtered)", APP_JS)

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


class TestJapaneseI18n(unittest.TestCase):
    def test_japanese_ui_has_core_trust_and_navigation_copy(self):
        for snippet in (
            "日本法改正ウォッチ by LegalOS",
            "情報の信頼性について",
            "日本語の公式情報源が優先します",
            "重要事項 — ご利用前にお読みください",
            "内容を確認し、同意します",
            "絞り込み・検索",
            "CSVを出力",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, I18N_JS)
        self.assertIn("data-localized-disclaimer", INDEX_HTML)
        self.assertIn("I18N.disclaimerPath()", APP_JS)

    def test_japanese_title_uses_original_before_ai_translation_exists(self):
        self.assertIn('if (filters.lang === "ja")', APP_JS)
        self.assertIn('field === "title"', APP_JS)
        self.assertIn("return update.title_ja", APP_JS)
        self.assertIn('const titleLang = filters.lang === "ja" ? "ja" : cardLang;', APP_JS)

    def test_japanese_body_uses_stage3_source_summaries_not_translation_block(self):
        for field in ("summary_ja", "business_impact_ja", "recommended_action_ja"):
            self.assertIn(field, APP_JS)
        self.assertIn("function hasJapaneseSummary", APP_JS)
        self.assertIn("japaneseField && hasJapaneseSummary(update)", APP_JS)
        self.assertIn("function activeSummarySource", APP_JS)
        self.assertIn('return update.summary_ja_source || "claude";', APP_JS)
        self.assertIn('activeSummarySource(u) !== "claude"', APP_JS)
        self.assertIn("if (hasJapaneseSummary(update))", APP_JS)
        self.assertIn("日本語の原文メタデータを基にAIが直接作成", I18N_JS)
        self.assertIn("日本語AI要約はまだ生成されていない", I18N_JS)
        self.assertNotIn('translations["ja"]', APP_JS)

    def test_japanese_csv_column_order_is_fixed(self):
        match = re.search(r"var CSV_HEADERS_JA = \[(?P<body>.*?)\];", I18N_JS, re.S)
        self.assertIsNotNone(match, "CSV_HEADERS_JA not found in i18n.js")
        headers = re.findall(r'"([^"]+)"', match.group("body"))
        self.assertEqual(
            headers,
            [
                "日本語原題",
                "英語参考タイトル",
                "分野",
                "段階",
                "影響度",
                "情報源",
                "日本語公式情報源URL",
                "公表日",
                "初回検出日",
                "最終確認日",
                "要約種別",
                "要約",
                "事業への影響",
                "推奨対応",
                "ランキングスコア",
                "内部ID",
            ],
        )
        self.assertEqual(len(headers), 16)
        self.assertIn('filters.lang === "ja"', APP_JS)
        self.assertIn("[update.title_ja, update.title_en]", APP_JS)

    def test_checkout_follow_up_keeps_any_supported_non_default_language(self):
        self.assertIn('encodeURIComponent(lang)', THANK_YOU_JS)
        self.assertNotIn('lang === "zh-Hans" ? "?lang=zh-Hans"', THANK_YOU_JS)

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
