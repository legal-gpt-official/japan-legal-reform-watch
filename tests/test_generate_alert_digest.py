"""Tests for the review-required alert digest draft generator."""

from __future__ import annotations

import json
import io
import re
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import generate_alert_digest as gad  # noqa: E402


PPC_SOURCE = "個人情報保護委員会 (PPC) 新着情報"
EGOV_SOURCE = "e-Gov Public Comment (意見募集案件一覧)"


def make_item(item_id: str, **overrides):
    item = {
        "id": item_id,
        "title_en": f"English title {item_id}",
        "title_ja": f"日本語タイトル {item_id}",
        "area": "Data / Privacy / AI",
        "stage": "Public Comment Open",
        "impact_level": "Medium",
        "summary_en": "English monitoring summary.",
        "business_impact_en": "Review possible business relevance.",
        "recommended_action_en": "Review the official Japanese source.",
        "source_name": PPC_SOURCE,
        "source_url": f"https://example.go.jp/{item_id}",
        "published_at": "2026-08-08",
        "last_checked": "2026-08-10",
        "first_seen_at": "2026-08-09",
        "relevance_score": 10,
        "summary_source": "claude",
    }
    item.update(overrides)
    return item


class DigestFixture(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        data_dir = self.repo_root / "docs" / "data"
        archive_dir = data_dir / "archive"
        archive_dir.mkdir(parents=True)

        self.items_2026 = [
            make_item(
                "privacy-ai",
                translations={
                    "zh-Hans": {
                        "title": "人工智能隐私规则",
                        "summary": "公开征求意见",
                        "business_impact": "审查数据处理",
                        "recommended_action": "检查官方来源",
                    }
                },
                relevance_score=12,
            ),
            make_item(
                "older-detection",
                title_en="Older detected update",
                published_at="2026-08-10",
                first_seen_at="2026-07-20",
                relevance_score=20,
            ),
            make_item(
                "egov-low",
                source_name=EGOV_SOURCE,
                area="Other",
                impact_level="Low",
                summary_source="rule_based",
                published_at="2026-08-07",
                first_seen_at="2026-08-08",
                relevance_score=4,
            ),
            make_item(
                "no-first-seen",
                published_at="2026-08-09",
                first_seen_at=None,
                relevance_score=8,
            ),
        ]
        self.items_2025 = [
            make_item(
                "archive-item",
                published_at="2025-12-20",
                first_seen_at="2026-08-10",
            )
        ]
        all_items = self.items_2026 + self.items_2025
        manifest = {
            "schema_version": 1,
            "total_items": len(all_items),
            "latest_period": "2026",
            "periods": [
                {
                    "value": "2026",
                    "label": "2026",
                    "file": "./data/archive/2026.json",
                    "count": len(self.items_2026),
                },
                {
                    "value": "2025",
                    "label": "2025",
                    "file": "./data/archive/2025.json",
                    "count": len(self.items_2025),
                },
            ],
        }
        (data_dir / "legal_updates_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        (data_dir / "legal_updates.json").write_text(
            json.dumps(all_items, ensure_ascii=False), encoding="utf-8"
        )
        (archive_dir / "2026.json").write_text(
            json.dumps(self.items_2026, ensure_ascii=False), encoding="utf-8"
        )
        (archive_dir / "2025.json").write_text(
            json.dumps(self.items_2025, ensure_ascii=False), encoding="utf-8"
        )

    def tearDown(self):
        self.temp_dir.cleanup()


class TestDigestFiltering(DigestFixture):
    def test_dashboard_url_filters_match_multilingual_search_and_source_slug(self):
        result = gad.build_digest(
            repo_root=self.repo_root,
            dashboard_url=(
                "https://example.test/?q=人工智能&area=Data%20%2F%20Privacy%20%2F%20AI"
                "&stage=Public%20Comment%20Open&source=ppc&impact=Medium"
                "&ai=1&new=7&sort=detected&year=all&lang=zh-Hans"
            ),
            since=date(2026, 8, 4),
            until=date(2026, 8, 10),
        )
        self.assertEqual(result.filters.period, "all")
        self.assertEqual(result.filters.source, PPC_SOURCE)
        self.assertEqual(result.filters.sort, "detected")
        self.assertTrue(result.filters.ai_only)
        self.assertTrue(result.filters.newly_detected_only)
        self.assertEqual([item["id"] for item in result.items], ["privacy-ai"])

    def test_unknown_url_values_fall_back_like_dashboard(self):
        result = gad.build_digest(
            repo_root=self.repo_root,
            dashboard_url="q=&area=Unknown&stage=Unknown&source=unknown&impact=Urgent&sort=bad&year=2099",
            since=date(2026, 8, 8),
            until=date(2026, 8, 10),
        )
        self.assertEqual(result.filters.period, "2026")
        self.assertEqual(result.filters.area, "")
        self.assertEqual(result.filters.source, "")
        self.assertEqual(result.filters.sort, "published")
        self.assertEqual(result.total_in_period, 4)

    def test_first_seen_window_does_not_guess_from_published_date(self):
        result = gad.build_digest(
            repo_root=self.repo_root,
            dashboard_url="https://example.test/",
            since=date(2026, 8, 8),
            until=date(2026, 8, 10),
        )
        ids = [item["id"] for item in result.items]
        self.assertIn("privacy-ai", ids)
        self.assertIn("egov-low", ids)
        self.assertNotIn("older-detection", ids)
        self.assertNotIn("no-first-seen", ids)

    def test_published_window_is_available_for_manual_backfill(self):
        result = gad.build_digest(
            repo_root=self.repo_root,
            dashboard_url="https://example.test/",
            since=date(2026, 8, 9),
            until=date(2026, 8, 10),
            date_field="published",
        )
        self.assertEqual(
            [item["id"] for item in result.items],
            ["older-detection", "no-first-seen"],
        )

    def test_limit_reports_omitted_matches(self):
        result = gad.build_digest(
            repo_root=self.repo_root,
            dashboard_url="https://example.test/?sort=detected",
            since=date(2026, 8, 8),
            until=date(2026, 8, 10),
            max_items=1,
        )
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.window_match_count, 2)
        self.assertEqual(result.omitted_count, 1)

    def test_rule_based_boilerplate_is_not_searchable(self):
        rule_based = make_item(
            "rule-based",
            title_en="Cloud services update",
            title_ja="クラウドサービス更新",
            summary_source="rule_based",
            summary_en="This has not yet been reviewed or summarized by AI.",
            translations={
                "zh-Hans": {
                    "title": "云服务动态",
                    "summary": "尚未由人工智能总结",
                    "business_impact": "人工智能影响占位文本",
                    "recommended_action": "人工智能建议占位文本",
                }
            },
        )
        haystack = gad._search_haystack(rule_based)
        self.assertIn("cloud services update", haystack)
        self.assertIn("云服务动态", haystack)
        self.assertNotIn("by ai", haystack)
        self.assertNotIn("人工智能影响", haystack)

        ai_summary = dict(rule_based, summary_source="claude")
        ai_haystack = gad._search_haystack(ai_summary)
        self.assertIn("by ai", ai_haystack)
        self.assertIn("人工智能影响", ai_haystack)


class TestDigestCommandDefaults(unittest.TestCase):
    def test_default_window_depends_on_frequency(self):
        until = date(2026, 8, 10)
        self.assertEqual(gad._default_since(until, "daily"), until)
        self.assertEqual(gad._default_since(until, "weekly"), date(2026, 8, 4))

    def test_stdout_is_reconfigured_for_utf8_when_supported(self):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="cp932")
        try:
            with mock.patch.object(gad.sys, "stdout", stream):
                gad._configure_stdout_utf8()
                self.assertEqual(stream.encoding.lower(), "utf-8")
                stream.write("—日本語")
                stream.flush()
            self.assertEqual(buffer.getvalue().decode("utf-8"), "—日本語")
        finally:
            stream.detach()


class TestDigestRendering(DigestFixture):
    def test_html_and_markdown_escape_untrusted_fields_and_reject_unsafe_url(self):
        unsafe = make_item(
            "unsafe",
            title_en='<script>alert("x")</script> [click](javascript:alert(2))',
            summary_en='<img src=x onerror="alert(1)">',
            source_url="javascript:alert(1)",
        )
        self.items_2026[0] = unsafe
        data_dir = self.repo_root / "docs" / "data"
        (data_dir / "archive" / "2026.json").write_text(
            json.dumps(self.items_2026, ensure_ascii=False), encoding="utf-8"
        )
        result = gad.build_digest(
            repo_root=self.repo_root,
            dashboard_url="https://example.test/",
            since=date(2026, 8, 8),
            until=date(2026, 8, 10),
        )
        markdown = gad.render_markdown(result, frequency="weekly", digest_title="Test")
        rendered_html = gad.render_html(result, frequency="weekly", digest_title="Test")
        self.assertNotIn("<script>", markdown)
        self.assertIn("&lt;script&gt;", markdown)
        self.assertNotIn("](javascript:", markdown)
        self.assertNotIn("<script>", rendered_html)
        self.assertNotIn("<img", rendered_html)
        self.assertNotIn('href="javascript:', rendered_html)
        self.assertIn("URL unavailable", rendered_html)

    def test_only_canonical_dashboard_url_is_linked_in_draft(self):
        result = gad.build_digest(
            repo_root=self.repo_root,
            dashboard_url="https://untrusted.example/?source=ppc",
            since=date(2026, 8, 8),
            until=date(2026, 8, 10),
        )
        markdown = gad.render_markdown(result, frequency="weekly", digest_title="Test")
        rendered_html = gad.render_html(result, frequency="weekly", digest_title="Test")
        self.assertNotIn("untrusted.example", markdown)
        self.assertNotIn("untrusted.example", rendered_html)

        canonical = gad.build_digest(
            repo_root=self.repo_root,
            dashboard_url=(
                "https://legal-gpt-official.github.io/japan-legal-reform-watch/?source=ppc"
            ),
            since=date(2026, 8, 8),
            until=date(2026, 8, 10),
        )
        canonical_markdown = gad.render_markdown(
            canonical, frequency="weekly", digest_title="Test"
        )
        self.assertIn("legal-gpt-official.github.io", canonical_markdown)

    def test_draft_contains_review_gate_scope_counts_and_trust_notice(self):
        result = gad.build_digest(
            repo_root=self.repo_root,
            dashboard_url="https://example.test/?source=ppc&sort=detected",
            since=date(2026, 8, 8),
            until=date(2026, 8, 10),
            max_items=1,
        )
        markdown = gad.render_markdown(
            result, frequency="weekly", digest_title="Japan Regulatory Alert"
        )
        self.assertIn("DRAFT — HUMAN REVIEW REQUIRED", markdown)
        self.assertIn(
            "Source: Personal Information Protection Commission \\(PPC\\)", markdown
        )
        self.assertIn("1 shown of 1", markdown)
        self.assertIn("not legal advice", markdown)
        self.assertIn("Original Japanese official sources remain authoritative", markdown)

    def test_write_digest_files_writes_both_formats(self):
        result = gad.build_digest(
            repo_root=self.repo_root,
            dashboard_url="https://example.test/",
            since=date(2026, 8, 8),
            until=date(2026, 8, 10),
        )
        output_dir = self.repo_root / "private-output"
        markdown_path, html_path = gad.write_digest_files(
            result,
            output_dir=output_dir,
            file_prefix="customer-001-weekly",
            frequency="weekly",
            digest_title="Japan Regulatory Alert",
        )
        self.assertTrue(markdown_path.is_file())
        self.assertTrue(html_path.is_file())
        self.assertIn("Weekly regulatory alert", markdown_path.read_text(encoding="utf-8"))
        self.assertIn("<!doctype html>", html_path.read_text(encoding="utf-8"))

    def test_unsafe_file_prefix_is_rejected(self):
        result = gad.build_digest(
            repo_root=self.repo_root,
            dashboard_url="https://example.test/",
            since=date(2026, 8, 8),
            until=date(2026, 8, 10),
        )
        with self.assertRaisesRegex(ValueError, "file_prefix"):
            gad.write_digest_files(
                result,
                output_dir=self.repo_root,
                file_prefix="../outside",
                frequency="weekly",
                digest_title="Test",
            )

    def test_public_docs_output_is_rejected(self):
        result = gad.build_digest(
            repo_root=self.repo_root,
            dashboard_url="https://example.test/",
            since=date(2026, 8, 8),
            until=date(2026, 8, 10),
        )
        with self.assertRaisesRegex(ValueError, "public docs"):
            gad.write_digest_files(
                result,
                output_dir=self.repo_root / "docs" / "alert-drafts",
                file_prefix="weekly",
                frequency="weekly",
                digest_title="Test",
                public_docs_root=self.repo_root / "docs",
            )


class TestDigestConfigurationParity(unittest.TestCase):
    def test_source_slugs_are_kept_in_sync_with_dashboard(self):
        app_js = (REPO_ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        match = re.search(r"const SOURCE_SLUGS = \{(?P<body>.*?)\n  \};", app_js, re.S)
        self.assertIsNotNone(match)
        dashboard_slugs = dict(re.findall(r'^\s+"([^"]+)": "([^"]+)",?$', match.group("body"), re.M))
        self.assertEqual(gad.SOURCE_SLUGS, dashboard_slugs)

    def test_generator_has_no_email_sending_or_credentials(self):
        source = (REPO_ROOT / "scripts" / "generate_alert_digest.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("smtplib", "sendgrid", "mailgun", "sk_live_", "api_key"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source.lower())
        self.assertIn("This is an operator tool, not an email sender.", source)


if __name__ == "__main__":
    unittest.main()
