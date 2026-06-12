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
        for slug in ("egov", "jftc", "ppc", "moe", "fsa", "mhlw", "digital-agency", "meti", "caa"):
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
            'params.set("ai", "1")',
            'window.addEventListener("popstate"',
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


if __name__ == "__main__":
    unittest.main()
