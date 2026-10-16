"""Offline tests for scripts/summarize_updates.py.

No API calls: these tests cover local AI-result application and validation only.
"""

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_public_data as bpd  # noqa: E402
import summarize_updates as su  # noqa: E402


def idle_workspace_client():
    """A client whose workspace has no batch running.

    The batch submission interlock asks the provider what is still in flight and
    fails closed when it cannot ask, so a bare object() now (correctly) blocks
    submission. These tests are about summarisation, not about the interlock, so
    they model the normal case: an idle workspace.
    """
    class _Batches:
        def list(self, limit=20):
            return []

    return types.SimpleNamespace(messages=types.SimpleNamespace(batches=_Batches()))



class TestSummarizeTitleCap(unittest.TestCase):
    def _item(self):
        return {
            "id": "raw-test",
            "title_en": "Rule-based title",
            "title_ja": "意見募集",
            "area": "Other",
            "stage": "Public Comment Open",
            "impact_level": "Low",
            "summary_en": "Template summary.",
            "business_impact_en": "Template impact.",
            "recommended_action_en": "Review the official source.",
            "source_name": "Test Source",
            "source_url": "https://example.go.jp/a",
            "published_at": "2026-06-16",
            "last_checked": "2026-06-16",
        }

    def _result(self, title):
        return {
            "title_en": title,
            "summary_en": "AI summary.",
            "business_impact_en": "AI impact.",
            "recommended_action_en": "Consider reviewing the official source.",
            "confidence": "medium",
            "ai_notes": "Limited metadata.",
        }

    def _ja_result(self):
        return {
            "summary_ja": "日本語の原文情報を基にした要約です。",
            "business_impact_ja": "事業への影響が生じる可能性があります。",
            "recommended_action_ja": "日本語の公式情報源を確認することが考えられます。",
        }

    def test_summary_model_default_and_overrides(self):
        self.assertEqual(su.DEFAULT_MODEL, "claude-sonnet-5-5")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(su.resolve_model(None), "claude-sonnet-5-5")
        with mock.patch.dict("os.environ", {"ANTHROPIC_SUMMARY_MODEL": "summary-env"}, clear=True):
            self.assertEqual(su.resolve_model(None), "summary-env")
        with mock.patch.dict(
            "os.environ",
            {"ANTHROPIC_SUMMARY_MODEL": "summary-env", "ANTHROPIC_MODEL": "legacy-env"},
            clear=True,
        ):
            self.assertEqual(su.resolve_model("cli-model"), "cli-model")
            self.assertEqual(su.resolve_model(None), "summary-env")
        with mock.patch.dict("os.environ", {"ANTHROPIC_MODEL": "legacy-env"}, clear=True):
            self.assertEqual(su.resolve_model(None), "legacy-env")

    def test_apply_result_shortens_ai_title_to_public_cap(self):
        item = self._item()
        su.apply_result(item, self._result("Long AI title " * 20), "2026-06-16T00:00:00Z", "model")

        self.assertLessEqual(len(item["title_en"]), bpd.TITLE_MAX_CHARS)
        self.assertEqual(item["summary_source"], "claude")
        self.assertFalse(bpd.contains_japanese(item["title_en"]))

    def test_apply_result_preserves_first_seen_at_and_does_not_backfill_legacy_items(self):
        detected = self._item()
        detected["first_seen_at"] = "2026-06-17"
        legacy = self._item()

        su.apply_result(detected, self._result("AI title"), "2026-06-18T00:00:00Z", "model")
        su.apply_result(legacy, self._result("AI title"), "2026-06-18T00:00:00Z", "model")

        self.assertEqual(detected["first_seen_at"], "2026-06-17")
        self.assertNotIn("first_seen_at", legacy)

    def test_apply_result_preserves_comment_deadline_and_provenance(self):
        item = self._item()
        item["comment_deadline"] = "2026-07-18T23:59:00+09:00"
        item["comment_deadline_source"] = "related_egov_item"
        item["comment_deadline_source_id"] = "raw-egov-source"
        item["comment_deadline_inherited"] = True

        su.apply_result(item, self._result("AI title"), "2026-06-18T00:00:00Z", "model")

        self.assertEqual(item["comment_deadline"], "2026-07-18T23:59:00+09:00")
        self.assertEqual(item["comment_deadline_source"], "related_egov_item")
        self.assertEqual(item["comment_deadline_source_id"], "raw-egov-source")
        self.assertIs(item["comment_deadline_inherited"], True)

    def test_restore_rule_based_preview_removes_stale_ai_text_and_provenance(self):
        item = self._item()
        su.apply_result(item, self._result("Stale AI title"), "2026-06-18T00:00:00Z", "model")

        su.restore_rule_based_english_preview(item)

        self.assertEqual(item["summary_source"], "rule_based")
        self.assertEqual(item["summary_en"], bpd.SUMMARY_EN)
        self.assertEqual(item["business_impact_en"], bpd.BUSINESS_IMPACT_EN)
        self.assertEqual(item["recommended_action_en"], bpd.RECOMMENDED_ACTION_EN)
        self.assertNotEqual(item["title_en"], "Stale AI title")
        for field in su.EN_PROVENANCE_FIELDS:
            self.assertNotIn(field, item)

    def test_japanese_summary_is_applied_without_changing_original_title(self):
        item = self._item()
        original_title = item["title_ja"]
        result = self._ja_result()

        su.apply_japanese_result(item, result, "2026-06-18T00:00:00Z", "model")

        self.assertEqual(item["title_ja"], original_title)
        for field in su.JA_AI_FIELDS:
            self.assertEqual(item[field], result[field])
        self.assertEqual(item["summary_ja_source"], "claude")
        self.assertEqual(item["ja_summarized_at"], "2026-06-18T00:00:00Z")
        self.assertEqual(item["ja_summary_model"], "model")
        self.assertTrue(su.valid_japanese_result(item))

        oversized = dict(result, summary_ja="あ" * (su.JA_FIELD_LIMITS["summary_ja"] + 1))
        self.assertFalse(su.valid_japanese_result(oversized))

    def test_japanese_prompt_uses_japanese_source_metadata_not_english_summary(self):
        item = self._item()
        item["summary_en"] = "ENGLISH SENTINEL MUST NOT BE TRANSLATED"
        params = su.japanese_summary_request_params(
            "model", item, {"raw_summary": "日本語の公表概要です。"}
        )

        content = params["messages"][0]["content"]
        self.assertIn("日本語の公表概要です。", content)
        self.assertIn(item["title_ja"], content)
        self.assertNotIn("ENGLISH SENTINEL", content)
        self.assertEqual(params["system"], su.SYSTEM_PROMPT_JA)

    def test_japanese_bad_request_is_not_retried_without_structured_output(self):
        item = self._item()
        calls = {"count": 0}

        class BadRequestError(Exception):
            status_code = 400

        def create(**_kwargs):
            calls["count"] += 1
            raise BadRequestError("credit balance is too low")

        client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
        with self.assertRaises(BadRequestError):
            su.request_japanese_summary(client, "claude-opus-4-8", item, {})

        self.assertEqual(calls["count"], 1)

    def test_response_usage_is_returned_and_costed(self):
        result = self._ja_result()
        block = types.SimpleNamespace(type="text", text=json.dumps(result))
        usage = types.SimpleNamespace(input_tokens=1000, output_tokens=200)
        message = types.SimpleNamespace(content=[block], model="claude-opus-4-8", usage=usage)

        parsed, model, counters = su.parse_summary_message(message, "fallback")

        self.assertEqual(parsed, result)
        self.assertEqual(model, "claude-opus-4-8")
        self.assertEqual(counters["input_tokens"], 1000)
        self.assertEqual(counters["output_tokens"], 200)
        self.assertAlmostEqual(su.estimate_usage_cost_usd(counters, model), 0.01)

    def test_validate_output_rejects_overlong_or_japanese_title_en(self):
        overlong = self._item()
        overlong["title_en"] = "A" * (bpd.TITLE_MAX_CHARS + 1)
        japanese = self._item()
        japanese["id"] = "raw-japanese"
        japanese["title_en"] = "Japanese title 個人情報"

        problems = su.validate_output(
            [overlong, japanese],
            {
                "raw-test": {"id": "raw-test", "source_url": "https://example.go.jp/a"},
                "raw-japanese": {"id": "raw-japanese", "source_url": "https://example.go.jp/a"},
            },
        )

        self.assertTrue(any("raw-test: title_en exceeds" in problem for problem in problems))
        self.assertTrue(any("raw-japanese: title_en contains Japanese" in problem for problem in problems))

    def test_caution_warning_ignores_explicitly_negated_legal_status(self):
        for sentence in (
            "The metadata does not indicate that the amendment has been enacted.",
            "This is not confirmation that the ordinance has been enacted.",
            "This is a consultation rather than confirmation that it has been enacted.",
        ):
            item = self._item()
            item["summary_source"] = "claude"
            item["summary_en"] = sentence
            self.assertNotIn("has been enacted", su.caution_phrases_in_item(item))

    def test_caution_warning_keeps_unqualified_definitive_status(self):
        item = self._item()
        item["summary_source"] = "claude"
        item["summary_en"] = "The amendment has been enacted."

        self.assertIn("has been enacted", su.caution_phrases_in_item(item))


class TestSummaryBatch(unittest.TestCase):
    def test_batch_uses_same_prompt_and_structured_output_contract(self):
        item = TestSummarizeTitleCap()._item()
        result = TestSummarizeTitleCap()._result("AI title")

        class Block:
            type = "text"
            text = json.dumps(result)

        message = types.SimpleNamespace(content=[Block()], model="claude-opus-4-8")
        captured = {}

        def fake_run(client, requests, *, timeout_seconds, logger, on_submit=None):
            captured["requests"] = requests
            captured["timeout"] = timeout_seconds
            return types.SimpleNamespace(
                batch_id="msgbatch_summary",
                results={"summary-0000": message},
            )

        with mock.patch.object(su, "run_message_batch", side_effect=fake_run):
            batch_id, outcomes = su.request_summary_batch(
                object(),
                "claude-opus-4-8",
                [(item, {"raw_summary": "Source metadata."})],
                timeout_seconds=12,
            )

        self.assertEqual(batch_id, "msgbatch_summary")
        self.assertEqual(outcomes[0][0]["title_en"], "AI title")
        params = captured["requests"][0]["params"]
        self.assertEqual(params["system"], su.SYSTEM_PROMPT)
        self.assertEqual(params["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(captured["timeout"], 12)

    def test_daily_workflow_uses_full_corpus_cost_capped_english_maintenance(self):
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-update.yml"
        ).read_text(encoding="utf-8")
        step = workflow[
            workflow.index("name: Maintain English summaries"):
            workflow.index("name: Maintain Japanese summaries")
        ]
        # gpt-6.1-sol on Flex since 2026-10-16: direct calls (a gpt-* model
        # refuses --batch), bounded parallel waves.
        invocation = step[step.index("python scripts/summarize_updates.py"):step.index("2>&1")]
        self.assertNotIn("--batch", invocation)
        self.assertIn("--parallel 5", invocation)
        self.assertIn("--all-items", step)
        self.assertIn("--english-only", step)
        self.assertIn("--api-limit 150", step)
        # The USD cap must survive the switch: it is the only spend brake besides
        # the call count, and it used to be rejected outright alongside --batch.
        # It also has to move WITH the limit -- see TestDailyBudgetCoversItsCallLimit.
        self.assertIn("--max-cost-usd 1.10", step)
        self.assertIn("English summary provider unavailable", workflow)
        self.assertIn("### English summary maintenance (gpt-6.1-sol, Flex)", workflow)

    def test_daily_workflow_reports_every_batch_stage_metric(self):
        """Phase 3A is an observation run: the Step Summary has to carry enough to
        judge discovery, the interlock, batch health, and estimate-vs-actual cost."""
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-update.yml"
        ).read_text(encoding="utf-8")
        batch_metrics = ("batch_id", "batch_succeeded", "batch_errored", "batch_expired",
                         "batch_missing", "input_tokens", "output_tokens",
                         "cache_read_input_tokens", "estimated_cost_usd",
                         "preflight_cost_usd")
        # The Japanese and zh-Hans steps run on OpenAI Flex (direct calls): no
        # batch to report, but cost, reasoning and the Flex confirmation are.
        flex_metrics = ("input_tokens", "output_tokens", "cache_read_input_tokens",
                        "reasoning_tokens", "flex_calls", "estimated_cost_usd",
                        "provider_status")
        for stage, marker, end, metrics in (
            ("english", "name: Maintain English summaries", "name: Maintain Japanese summaries", flex_metrics),
            ("japanese", "name: Maintain Japanese summaries", "name: Translate Simplified Chinese updates", flex_metrics),
            ("translate", "name: Translate Simplified Chinese updates", "name: Build yearly public archives", flex_metrics),
        ):
            step = workflow[workflow.index(marker):workflow.index(end)]
            for metric in metrics:
                self.assertIn(metric, step, f"{stage} stage is missing {metric}")

    def test_daily_workflow_has_an_explicit_job_timeout(self):
        """Three stages can each wait an hour for their batch; do not rely on the
        platform default as the only ceiling."""
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-update.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("timeout-minutes:", workflow)

    def test_only_the_daily_workflow_is_batched_in_phase_3a(self):
        """Backfills stay on direct calls so a Batch problem is easy to isolate."""
        root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        for name in ("english-summary-backfill.yml", "japanese-summary-backfill.yml",
                     "translation-backfill.yml"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("--batch", text, f"{name} must not be batched yet")

    def test_direct_call_path_remains_available_as_a_fallback(self):
        """Dropping --batch must restore the previous behaviour with no other edit."""
        source = (
            Path(__file__).resolve().parents[1] / "scripts" / "summarize_updates.py"
        ).read_text(encoding="utf-8")
        self.assertIn("if args.batch and pending:", source)
        self.assertIn("request_summary(client, model, it,", source)
        self.assertNotIn("--max-cost-usd is supported only for direct calls", source)

    def test_english_backfill_workflow_is_resumable_and_refreshes_chinese(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "english-summary-backfill.yml"
        ).read_text(encoding="utf-8")

        for snippet in (
            "workflow_dispatch:",
            "--all-items",
            "--english-only",
            '--api-limit "$BATCH_SIZE"',
            '--parallel "$PARALLELISM"',
            '--max-cost-usd "$SUMMARY_MAX_COST_USD"',
            "python scripts/translate_updates.py",
            '--limit "$total"',
            '--parallel "$PARALLELISM"',
            '--max-cost-usd "$TRANSLATION_MAX_COST_USD"',
            "--retry-rejected-title-without-ja-reference",
            "japanese_signature",
            'git push origin "HEAD:main"',
            "group: japan-legal-reform-data-writer",
            "Final semantic integrity gate",
            "Mark partial checkpoint incomplete",
            'translation_max_cost_usd:',
            '- "10.00"',
            'translation_status=${PIPESTATUS[0]}',
            'git restore --source=HEAD --',
            "save_english_cache_and_exit",
            "Checkpoint English summary cache after aborted checkpoint",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, workflow)
        self.assertNotIn("python scripts/fetch_updates.py", workflow)
        self.assertNotIn("python scripts/build_public_data.py", workflow)
        self.assertNotIn("python scripts/source_health.py", workflow)

    def test_daily_workflow_maintains_japanese_with_cost_and_call_caps(self):
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-update.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("name: Maintain Japanese summaries", workflow)
        self.assertIn("--all-items", workflow)
        self.assertIn("--japanese-only", workflow)
        self.assertIn("--api-limit 100", workflow)
        self.assertIn("--batch", workflow)
        self.assertIn("--max-cost-usd 0.80", workflow)
        self.assertIn("Japanese summary provider unavailable", workflow)
        self.assertIn("s/^provider_error_type *: //p", workflow)
        self.assertIn("estimated_cost_usd", workflow)
        self.assertLess(
            workflow.index("name: Maintain Japanese summaries"),
            workflow.index("name: Translate Simplified Chinese updates"),
        )

    def test_japanese_backfill_workflow_is_direct_resumable_and_serialized(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "japanese-summary-backfill.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("--all-items", workflow)
        self.assertIn("--japanese-only", workflow)
        self.assertIn("--api-limit \"$BATCH_SIZE\"", workflow)
        self.assertIn("--parallel \"$PARALLELISM\"", workflow)
        self.assertIn("--max-cost-usd \"$MAX_COST_USD\"", workflow)
        self.assertIn('default: "10"', workflow)
        self.assertIn('default: "0.50"', workflow)
        self.assertIn("git push origin \"HEAD:${GITHUB_REF_NAME}\"", workflow)
        self.assertIn("group: japan-legal-reform-data-writer", workflow)
        self.assertIn("python scripts/build_public_archives.py", workflow)
        self.assertNotIn("python scripts/fetch_updates.py", workflow)
        self.assertNotIn("python scripts/build_public_data.py", workflow)
        self.assertNotIn("python scripts/translate_updates.py", workflow)

    def test_all_items_rejects_accidental_full_english_summarization(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            su.main(["--all-items"])
        self.assertEqual(raised.exception.code, 2)

    def test_language_only_modes_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            su.main(["--all-items", "--english-only", "--japanese-only"])
        self.assertEqual(raised.exception.code, 2)

    def test_english_only_parallel_backfill_preserves_japanese_fields(self):
        items = []
        japanese = TestSummarizeTitleCap()._ja_result()
        for index in range(3):
            item = TestSummarizeTitleCap()._item()
            item["id"] = f"english-{index}"
            item["source_url"] = f"https://example.go.jp/english-{index}"
            item.update(japanese)
            item.update({
                "summary_ja_source": "claude",
                "ja_summarized_at": "2026-08-13T00:00:00Z",
                "ja_summary_model": "claude-opus-4-8",
            })
            items.append(item)
        japanese_before = [
            {field: item[field] for field in (*su.JA_AI_FIELDS, *su.JA_PROVENANCE_FIELDS)}
            for item in items
        ]
        english = TestSummarizeTitleCap()._result("Fresh English AI title")
        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            input_path = base / "legal_updates.json"
            cache_path = base / "summary_cache.json"
            raw_path = base / "raw_items.json"
            input_path.write_text(json.dumps(items), encoding="utf-8")
            cache_path.write_text("{}", encoding="utf-8")
            raw_path.write_text("[]", encoding="utf-8")
            patches = {
                "INPUT_PATH": input_path,
                "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json",
                "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path,
                "LOG_PATH": base / "summarize.log",
                "make_client": idle_workspace_client,
                "request_summary": lambda client, model, item, raw: (english, model, usage),
                "request_japanese_summary": mock.Mock(
                    side_effect=AssertionError("Japanese API must not run")
                ),
            }
            stdout = io.StringIO()
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False
            ), contextlib.redirect_stdout(stdout):
                rc = su.main([
                    "--all-items", "--english-only", "--api-limit", "2", "--parallel", "2"
                ])

            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()
            published = json.loads(input_path.read_text(encoding="utf-8"))
            self.assertEqual(rc, 0)
            self.assertEqual(
                [item["summary_source"] for item in published],
                ["claude", "claude", "rule_based"],
            )
            for index, item in enumerate(published):
                self.assertEqual(
                    {field: item[field] for field in (*su.JA_AI_FIELDS, *su.JA_PROVENANCE_FIELDS)},
                    japanese_before[index],
                )
            self.assertIn("english_only    : true", stdout.getvalue())
            self.assertIn("english_remaining: 1", stdout.getvalue())

    def test_english_cache_miss_over_budget_drops_carried_stale_ai_body(self):
        item = TestSummarizeTitleCap()._item()
        item.update(TestSummarizeTitleCap()._result("Stale cached AI title"))
        item.update({
            "summary_source": "claude",
            "summarized_at": "2026-08-12T00:00:00Z",
            "summary_model": "claude-opus-4-8",
        })
        raw = [{
            "id": item["id"],
            "title_ja": item["title_ja"],
            "source_url": item["source_url"],
            "raw_summary": "Changed official source content.",
            "raw_content_hash": "new-content-hash",
        }]

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            input_path = base / "legal_updates.json"
            cache_path = base / "summary_cache.json"
            raw_path = base / "raw_items.json"
            input_path.write_text(json.dumps([item]), encoding="utf-8")
            cache_path.write_text("{}", encoding="utf-8")
            raw_path.write_text(json.dumps(raw), encoding="utf-8")
            patches = {
                "INPUT_PATH": input_path,
                "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json",
                "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path,
                "LOG_PATH": base / "summarize.log",
                "make_client": mock.Mock(side_effect=AssertionError("API must not run")),
            }
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False
            ), contextlib.redirect_stdout(io.StringIO()):
                rc = su.main([
                    "--all-items", "--english-only", "--api-limit", "0", "--parallel", "4"
                ])

            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()
            published = json.loads(input_path.read_text(encoding="utf-8"))[0]
            self.assertEqual(rc, 0)
            self.assertEqual(published["summary_source"], "rule_based")
            self.assertEqual(published["summary_en"], bpd.SUMMARY_EN)
            self.assertEqual(published["business_impact_en"], bpd.BUSINESS_IMPACT_EN)
            self.assertEqual(published["recommended_action_en"], bpd.RECOMMENDED_ACTION_EN)
            self.assertNotEqual(published["title_en"], "Stale cached AI title")
            for field in su.EN_PROVENANCE_FIELDS:
                self.assertNotIn(field, published)

    def test_missing_api_key_applies_current_cache_and_cleans_stale_ai_without_calls(self):
        current = TestSummarizeTitleCap()._item()
        current["id"] = "current-cache"
        current["source_url"] = "https://example.go.jp/current-cache"
        current_cache = TestSummarizeTitleCap()._result("Current cached AI title")
        current_cache.update({
            "summarized_at": "2026-08-12T00:00:00Z",
            "summary_model": "claude-opus-4-8",
        })

        stale = TestSummarizeTitleCap()._item()
        stale["id"] = "stale-cache"
        stale["source_url"] = "https://example.go.jp/stale-cache"
        stale.update(TestSummarizeTitleCap()._result("Stale carried AI title"))
        stale.update({
            "summary_source": "claude",
            "summarized_at": "2026-08-11T00:00:00Z",
            "summary_model": "claude-opus-4-8",
        })
        uncached = TestSummarizeTitleCap()._item()
        uncached["id"] = "uncached"
        uncached["source_url"] = "https://example.go.jp/uncached"
        raw = [{
            "id": stale["id"],
            "title_ja": stale["title_ja"],
            "source_url": stale["source_url"],
            "raw_summary": "Changed official source content.",
            "raw_content_hash": "changed-source-content",
        }]
        cache = {su.cache_key(current, {}): current_cache}

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            input_path = base / "legal_updates.json"
            cache_path = base / "summary_cache.json"
            raw_path = base / "raw_items.json"
            input_path.write_text(json.dumps([current, stale, uncached]), encoding="utf-8")
            cache_path.write_text(json.dumps(cache), encoding="utf-8")
            raw_path.write_text(json.dumps(raw), encoding="utf-8")
            patches = {
                "INPUT_PATH": input_path,
                "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json",
                "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path,
                "LOG_PATH": base / "summarize.log",
                "make_client": mock.Mock(side_effect=AssertionError("API must not run")),
                "request_summary": mock.Mock(side_effect=AssertionError("API must not run")),
            }
            stdout = io.StringIO()
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {}, clear=True
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                rc = su.main([
                    "--all-items", "--english-only", "--api-limit", "1", "--parallel", "4"
                ])

            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()
            published = json.loads(input_path.read_text(encoding="utf-8"))
            self.assertEqual(rc, 0)
            self.assertEqual(published[0]["title_en"], "Current cached AI title")
            self.assertEqual(published[0]["summary_source"], "claude")
            self.assertEqual(published[1]["summary_source"], "rule_based")
            self.assertEqual(published[1]["summary_en"], bpd.SUMMARY_EN)
            self.assertNotEqual(published[1]["title_en"], "Stale carried AI title")
            self.assertEqual(published[2]["summary_source"], "rule_based")
            self.assertIn("provider_status : unavailable", stdout.getvalue())
            self.assertIn("provider_error_type: missing_api_key", stdout.getvalue())
            self.assertIn("api_calls       : 0", stdout.getvalue())
            self.assertIn("summarized_items: 0", stdout.getvalue())
            self.assertIn("provider_aborted_items: 1", stdout.getvalue())
            self.assertIn("skipped_api_budget: 1", stdout.getvalue())

    def test_main_applies_batch_result_and_persists_cache(self):
        item = TestSummarizeTitleCap()._item()
        item["relevance_score"] = 10
        result = TestSummarizeTitleCap()._result("AI title")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            input_path = base / "legal_updates.json"
            cache_path = base / "summary_cache.json"
            raw_path = base / "raw_items.json"
            input_path.write_text(json.dumps([item]), encoding="utf-8")
            raw_path.write_text(json.dumps([]), encoding="utf-8")

            def fake_batch(client, model, candidates, *, timeout_seconds, custom_ids=None):
                self.assertEqual(len(candidates), 1)
                return "msgbatch_main", [(result, model)]

            patches = {
                "INPUT_PATH": input_path,
                "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json",
                "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path,
                "LOG_PATH": base / "summarize.log",
                "make_client": idle_workspace_client,
                "request_summary_batch": fake_batch,
            }
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False
            ), contextlib.redirect_stdout(io.StringIO()):
                rc = su.main(["--limit", "1", "--batch"])

            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()
            published = json.loads(input_path.read_text(encoding="utf-8"))
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(rc, 0)
            self.assertEqual(published[0]["summary_source"], "claude")
            self.assertEqual(published[0]["title_en"], "AI title")
            self.assertEqual(len(cache), 1)

    def test_batch_creation_credit_failure_is_reported_once_without_phantom_calls(self):
        items = []
        for index in range(2):
            item = TestSummarizeTitleCap()._item()
            item["id"] = f"batch-credit-{index}"
            item["source_url"] = f"https://example.go.jp/batch-credit-{index}"
            item["relevance_score"] = 10 - index
            items.append(item)
        credit_error = RuntimeError("provider detail must not be logged")
        credit_error.status_code = 402

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            input_path = base / "legal_updates.json"
            cache_path = base / "summary_cache.json"
            raw_path = base / "raw_items.json"
            log_path = base / "summarize.log"
            input_path.write_text(json.dumps(items), encoding="utf-8")
            raw_path.write_text("[]", encoding="utf-8")
            patches = {
                "INPUT_PATH": input_path,
                "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json",
                "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path,
                "LOG_PATH": log_path,
                "make_client": idle_workspace_client,
                "request_summary_batch": mock.Mock(side_effect=credit_error),
            }
            stdout = io.StringIO()
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                rc = su.main(["--limit", "2", "--batch"])

            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()
            output = stdout.getvalue()
            log_text = log_path.read_text(encoding="utf-8")
            published = json.loads(input_path.read_text(encoding="utf-8"))
            self.assertEqual(rc, 0)
            self.assertIn("api_calls       : 0", output)
            self.assertIn("failed_items    : 1", output)
            self.assertIn("provider_aborted_items: 2", output)
            self.assertIn("provider_error_type: insufficient_credit", output)
            self.assertEqual(log_text.count("PROVIDER unavailable"), 1)
            self.assertNotIn("provider detail", log_text)
            self.assertTrue(all(item["summary_source"] == "rule_based" for item in published))

    def test_existing_english_cache_is_enriched_with_japanese_summary_only(self):
        item = TestSummarizeTitleCap()._item()
        item["relevance_score"] = 10
        english = TestSummarizeTitleCap()._result("Stable cached English title")
        japanese = TestSummarizeTitleCap()._ja_result()
        key = su.cache_key(item, {})
        cached = {
            **english,
            "summarized_at": "2026-06-18T00:00:00Z",
            "summary_model": "cached-model",
        }

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            input_path = base / "legal_updates.json"
            cache_path = base / "summary_cache.json"
            raw_path = base / "raw_items.json"
            input_path.write_text(json.dumps([item]), encoding="utf-8")
            cache_path.write_text(json.dumps({key: cached}), encoding="utf-8")
            raw_path.write_text(json.dumps([]), encoding="utf-8")

            def fake_japanese_batch(client, model, candidates, *, timeout_seconds, custom_ids=None):
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0][0]["title_en"], "Stable cached English title")
                return "msgbatch_ja", [(japanese, model)]

            patches = {
                "INPUT_PATH": input_path,
                "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json",
                "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path,
                "LOG_PATH": base / "summarize.log",
                "make_client": idle_workspace_client,
                "request_japanese_summary_batch": fake_japanese_batch,
            }
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False
            ), contextlib.redirect_stdout(io.StringIO()):
                rc = su.main(["--limit", "1", "--api-limit", "1", "--batch"])

            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()
            published = json.loads(input_path.read_text(encoding="utf-8"))[0]
            updated_cache = json.loads(cache_path.read_text(encoding="utf-8"))[key]
            self.assertEqual(rc, 0)
            self.assertEqual(published["title_en"], "Stable cached English title")
            self.assertEqual(published["summary_en"], english["summary_en"])
            for field in su.JA_AI_FIELDS:
                self.assertEqual(published[field], japanese[field])
                self.assertEqual(updated_cache[field], japanese[field])

    def test_unverified_published_japanese_fields_are_removed_when_cache_lacks_them(self):
        item = TestSummarizeTitleCap()._item()
        item["relevance_score"] = 10
        item.update(TestSummarizeTitleCap()._ja_result())
        english = TestSummarizeTitleCap()._result("Cached English title")
        key = su.cache_key(item, {})
        cached = {
            **english,
            "summarized_at": "2026-06-18T00:00:00Z",
            "summary_model": "cached-model",
        }

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            input_path = base / "legal_updates.json"
            cache_path = base / "summary_cache.json"
            raw_path = base / "raw_items.json"
            input_path.write_text(json.dumps([item]), encoding="utf-8")
            cache_path.write_text(json.dumps({key: cached}), encoding="utf-8")
            raw_path.write_text("[]", encoding="utf-8")
            patches = {
                "INPUT_PATH": input_path,
                "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json",
                "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path,
                "LOG_PATH": base / "summarize.log",
            }
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False
            ), contextlib.redirect_stdout(io.StringIO()):
                rc = su.main(["--limit", "1", "--api-limit", "0", "--batch"])

            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()
            published = json.loads(input_path.read_text(encoding="utf-8"))[0]
            self.assertEqual(rc, 0)
            self.assertEqual(published["summary_source"], "claude")
            for field in su.JA_AI_FIELDS:
                self.assertNotIn(field, published)

    def test_api_limit_caps_cache_misses_inside_larger_priority_pool(self):
        items = []
        for index in range(4):
            item = TestSummarizeTitleCap()._item()
            item["id"] = f"raw-{index}"
            item["source_url"] = f"https://example.go.jp/{index}"
            item["relevance_score"] = 100 - index
            items.append(item)
        result = TestSummarizeTitleCap()._result("AI title")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            input_path = base / "legal_updates.json"
            cache_path = base / "summary_cache.json"
            raw_path = base / "raw_items.json"
            input_path.write_text(json.dumps(items), encoding="utf-8")
            raw_path.write_text(json.dumps([]), encoding="utf-8")

            def fake_batch(client, model, candidates, *, timeout_seconds, custom_ids=None):
                self.assertEqual(len(candidates), 2)
                return "msgbatch_budget", [(result, model), (result, model)]

            patches = {
                "INPUT_PATH": input_path,
                "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json",
                "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path,
                "LOG_PATH": base / "summarize.log",
                "make_client": idle_workspace_client,
                "request_summary_batch": fake_batch,
            }
            stdout = io.StringIO()
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False
            ), contextlib.redirect_stdout(stdout):
                rc = su.main(["--limit", "4", "--api-limit", "2", "--batch"])

            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()
            published = json.loads(input_path.read_text(encoding="utf-8"))
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(rc, 0)
            self.assertEqual(
                [it["summary_source"] for it in published],
                ["claude", "claude", "rule_based", "rule_based"],
            )
            self.assertEqual(len(cache), 2)
            self.assertIn("skipped_api_budget: 2", stdout.getvalue())

    def test_api_limit_is_shared_by_english_and_japanese_batch_candidates(self):
        cached_item = TestSummarizeTitleCap()._item()
        cached_item["id"] = "cached"
        cached_item["source_url"] = "https://example.go.jp/cached"
        cached_item["relevance_score"] = 100
        english_item = TestSummarizeTitleCap()._item()
        english_item["id"] = "english"
        english_item["source_url"] = "https://example.go.jp/english"
        english_item["relevance_score"] = 99
        skipped_item = TestSummarizeTitleCap()._item()
        skipped_item["id"] = "skipped"
        skipped_item["source_url"] = "https://example.go.jp/skipped"
        skipped_item["relevance_score"] = 98
        items = [cached_item, english_item, skipped_item]
        english = TestSummarizeTitleCap()._result("Fresh English title")
        japanese = TestSummarizeTitleCap()._ja_result()
        cached_key = su.cache_key(cached_item, {})
        cached_result = {
            **TestSummarizeTitleCap()._result("Cached English title"),
            "summarized_at": "2026-06-18T00:00:00Z",
            "summary_model": "cached-model",
        }

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            input_path = base / "legal_updates.json"
            cache_path = base / "summary_cache.json"
            raw_path = base / "raw_items.json"
            input_path.write_text(json.dumps(items), encoding="utf-8")
            cache_path.write_text(json.dumps({cached_key: cached_result}), encoding="utf-8")
            raw_path.write_text("[]", encoding="utf-8")

            def fake_english_batch(client, model, candidates, *, timeout_seconds, custom_ids=None):
                self.assertEqual([item["id"] for item, _raw in candidates], ["english"])
                return "msgbatch_en_mixed", [(english, model)]

            def fake_japanese_batch(client, model, candidates, *, timeout_seconds, custom_ids=None):
                self.assertEqual([item["id"] for item, _raw in candidates], ["cached"])
                return "msgbatch_ja_mixed", [(japanese, model)]

            patches = {
                "INPUT_PATH": input_path,
                "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json",
                "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path,
                "LOG_PATH": base / "summarize.log",
                "make_client": idle_workspace_client,
                "request_summary_batch": fake_english_batch,
                "request_japanese_summary_batch": fake_japanese_batch,
            }
            stdout = io.StringIO()
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False
            ), contextlib.redirect_stdout(stdout):
                rc = su.main(["--limit", "3", "--api-limit", "2", "--batch"])

            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()
            published = json.loads(input_path.read_text(encoding="utf-8"))
            self.assertEqual(rc, 0)
            self.assertEqual([item["summary_source"] for item in published], ["claude", "claude", "rule_based"])
            self.assertEqual(published[0]["summary_ja"], japanese["summary_ja"])
            self.assertNotIn("summary_ja", published[1])
            self.assertIn("api_calls       : 2", stdout.getvalue())

    def test_japanese_only_all_items_backfill_does_not_relabel_english_preview(self):
        cached_item = TestSummarizeTitleCap()._item()
        cached_item["id"] = "cached-ja"
        cached_item["source_url"] = "https://example.go.jp/cached-ja"
        fresh_item = TestSummarizeTitleCap()._item()
        fresh_item["id"] = "fresh-ja"
        fresh_item["source_url"] = "https://example.go.jp/fresh-ja"
        skipped_item = TestSummarizeTitleCap()._item()
        skipped_item["id"] = "skipped-ja"
        skipped_item["source_url"] = "https://example.go.jp/skipped-ja"
        items = [cached_item, fresh_item, skipped_item]
        japanese = TestSummarizeTitleCap()._ja_result()
        cached_key = su.cache_key(cached_item, {})
        cached_result = {
            **japanese,
            "ja_summarized_at": "2026-06-18T00:00:00Z",
            "ja_summary_model": "cached-model",
        }

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            input_path = base / "legal_updates.json"
            cache_path = base / "summary_cache.json"
            raw_path = base / "raw_items.json"
            input_path.write_text(json.dumps(items), encoding="utf-8")
            cache_path.write_text(json.dumps({cached_key: cached_result}), encoding="utf-8")
            raw_path.write_text("[]", encoding="utf-8")

            def fake_japanese_batch(client, model, candidates, *, timeout_seconds, custom_ids=None):
                self.assertEqual([item["id"] for item, _raw in candidates], ["fresh-ja"])
                return "msgbatch_ja_backfill", [(japanese, model)]

            patches = {
                "INPUT_PATH": input_path,
                "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json",
                "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path,
                "LOG_PATH": base / "summarize.log",
                "make_client": idle_workspace_client,
                "request_japanese_summary_batch": fake_japanese_batch,
                "request_summary_batch": mock.Mock(side_effect=AssertionError("English API must not run")),
            }
            stdout = io.StringIO()
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False
            ), contextlib.redirect_stdout(stdout):
                rc = su.main(
                    ["--all-items", "--japanese-only", "--api-limit", "1", "--batch"]
                )

            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()
            published = json.loads(input_path.read_text(encoding="utf-8"))
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(rc, 0)
            self.assertEqual([item["summary_source"] for item in published], ["rule_based"] * 3)
            self.assertEqual(published[0]["summary_ja_source"], "claude")
            self.assertEqual(published[0]["ja_summary_model"], "cached-model")
            self.assertEqual(published[1]["summary_ja_source"], "claude")
            self.assertNotIn("summary_ja", published[2])
            self.assertEqual(len(cache), 2)
            self.assertEqual(cache[cached_key]["summary_ja_source"], "claude")
            self.assertEqual(cache[cached_key]["ja_summarized_at"], "2026-06-18T00:00:00Z")
            fresh_key = su.cache_key(fresh_item, {})
            self.assertEqual(cache[fresh_key]["summary_ja_source"], "claude")
            self.assertIn("japanese_cache_hits: 1", stdout.getvalue())
            self.assertIn("japanese_summarized_items: 1", stdout.getvalue())
            self.assertIn("japanese_remaining: 1", stdout.getvalue())

    def test_japanese_only_parallel_calls_respect_api_limit(self):
        items = []
        for index in range(3):
            item = TestSummarizeTitleCap()._item()
            item["id"] = f"parallel-{index}"
            item["source_url"] = f"https://example.go.jp/parallel-{index}"
            items.append(item)
        japanese = TestSummarizeTitleCap()._ja_result()

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            input_path = base / "legal_updates.json"
            cache_path = base / "summary_cache.json"
            raw_path = base / "raw_items.json"
            input_path.write_text(json.dumps(items), encoding="utf-8")
            cache_path.write_text("{}", encoding="utf-8")
            raw_path.write_text("[]", encoding="utf-8")
            patches = {
                "INPUT_PATH": input_path,
                "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json",
                "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path,
                "LOG_PATH": base / "summarize.log",
                "make_client": idle_workspace_client,
                "request_japanese_summary": lambda client, model, item, raw: (japanese, model),
            }
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False
            ), contextlib.redirect_stdout(io.StringIO()):
                rc = su.main(
                    ["--all-items", "--japanese-only", "--api-limit", "2", "--parallel", "2"]
                )

            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()
            published = json.loads(input_path.read_text(encoding="utf-8"))
            self.assertEqual(rc, 0)
            self.assertEqual(sum("summary_ja" in item for item in published), 2)
            self.assertNotIn("summary_ja", published[2])

    def test_parallel_backfill_stops_scheduling_after_credit_failure_wave(self):
        items = []
        for index in range(10):
            item = TestSummarizeTitleCap()._item()
            item["id"] = f"credit-{index}"
            item["source_url"] = f"https://example.go.jp/credit-{index}"
            items.append(item)

        class BadRequestError(Exception):
            status_code = 400

        calls = {"count": 0}

        def fail_credit(_client, _model, _item, _raw):
            calls["count"] += 1
            raise BadRequestError("credit balance is too low")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            input_path = base / "legal_updates.json"
            cache_path = base / "summary_cache.json"
            raw_path = base / "raw_items.json"
            input_path.write_text(json.dumps(items), encoding="utf-8")
            cache_path.write_text("{}", encoding="utf-8")
            raw_path.write_text("[]", encoding="utf-8")
            patches = {
                "INPUT_PATH": input_path,
                "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json",
                "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path,
                "LOG_PATH": base / "summarize.log",
                "make_client": idle_workspace_client,
                "request_japanese_summary": fail_credit,
            }
            stdout = io.StringIO()
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False
            ), contextlib.redirect_stdout(stdout):
                rc = su.main(
                    ["--all-items", "--japanese-only", "--api-limit", "10", "--parallel", "2"]
                )

            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()
            self.assertEqual(rc, 0)
            self.assertEqual(calls["count"], 2)
            self.assertIn("provider_status : unavailable", stdout.getvalue())
            self.assertIn("provider_error_type: insufficient_credit", stdout.getvalue())
            self.assertIn("provider_aborted_items: 8", stdout.getvalue())

    def test_measured_cost_cap_stops_new_direct_calls(self):
        items = []
        for index in range(10):
            item = TestSummarizeTitleCap()._item()
            item["id"] = f"cost-{index}"
            item["source_url"] = f"https://example.go.jp/cost-{index}"
            items.append(item)
        japanese = TestSummarizeTitleCap()._ja_result()
        usage = {"input_tokens": 1000, "output_tokens": 200,
                 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        calls = {"count": 0}

        def success(_client, model, _item, _raw):
            calls["count"] += 1
            return japanese, model, usage

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            input_path = base / "legal_updates.json"
            cache_path = base / "summary_cache.json"
            raw_path = base / "raw_items.json"
            input_path.write_text(json.dumps(items), encoding="utf-8")
            cache_path.write_text("{}", encoding="utf-8")
            raw_path.write_text("[]", encoding="utf-8")
            patches = {
                "INPUT_PATH": input_path,
                "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json",
                "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path,
                "LOG_PATH": base / "summarize.log",
                "make_client": idle_workspace_client,
                "request_japanese_summary": success,
            }
            stdout = io.StringIO()
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False
            ), contextlib.redirect_stdout(stdout):
                rc = su.main([
                    "--all-items", "--japanese-only", "--api-limit", "10",
                    "--parallel", "1", "--max-cost-usd", "0.015",
                    # The arithmetic below is priced at Opus 4.8 rates.
                    "--model", "claude-opus-4-8",
                ])

            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()
            self.assertEqual(rc, 0)
            self.assertEqual(calls["count"], 2)
            self.assertIn("cost_budget_skipped: 8", stdout.getvalue())
            self.assertIn("estimated_cost_usd: 0.020000", stdout.getvalue())


class TestBackfillPreservesPaidSummaryCache(unittest.TestCase):
    """Every abort after the paid Opus calls must keep data/summary_cache.json.

    The checkpoint step runs summarize (paid) and only commits at the very end.
    A bare `exit 1` in between destroys the runner workspace, so the paid
    responses are lost and a rerun pays for exactly the same work again.
    """

    def _checkpoint_step(self) -> str:
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "english-summary-backfill.yml"
        ).read_text(encoding="utf-8")
        return workflow[
            workflow.index("python scripts/summarize_updates.py"):
            workflow.index("- name: Request Pages build for checkpoint")
        ]

    def test_no_bare_exit_between_paid_calls_and_commit(self):
        step = self._checkpoint_step()
        post_payment = step[: step.index("git commit -m \"Backfill English summaries")]
        bare_exits = [
            line.strip()
            for line in post_payment.splitlines()
            # The recovery helper itself is allowed to exit; it saves the cache first.
            if line.strip() in ("exit 1", "exit \"$translation_status\"")
            and "refusing to save the recovery cache" not in post_payment[
                max(0, post_payment.index(line)) - 200: post_payment.index(line)
            ]
        ]
        self.assertEqual(
            bare_exits, [],
            f"post-payment abort path discards the paid summary cache: {bare_exits}",
        )

    def test_recovery_helper_restores_public_data_but_keeps_summary_cache(self):
        step = self._checkpoint_step()
        self.assertIn("save_english_cache_and_exit() {", step)
        # Public artifacts are reverted...
        for path in (
            "docs/data/legal_updates.json",
            "docs/data/legal_updates_manifest.json",
            "docs/data/archive",
            "data/translation_cache.json",
        ):
            self.assertIn(path, step)
        # ...while the paid English cache is committed on its own.
        self.assertIn("git add data/summary_cache.json", step)
        self.assertIn("Checkpoint English summary cache after aborted checkpoint", step)

    def test_every_integrity_guard_routes_through_the_recovery_helper(self):
        step = self._checkpoint_step()
        for guard in (
            "English coverage shrank",
            "No English-summary progress",
            "Japanese summary fields changed",
            "zh-Hans coverage shrank",
            "origin/main advanced during the checkpoint",
        ):
            line = next(l for l in step.splitlines() if guard in l)
            self.assertIn("save_english_cache_and_exit", line, guard)


class TestSummarizeBatchRecovery(unittest.TestCase):
    """Summarize must survive losing the runner mid-batch, like translate does.

    The daily workflow commits only at the very end, so a batch id written to the
    runner's filesystem is gone on the next run. Recovery therefore reads the
    batch back from the provider using self-describing custom_ids.
    """

    def _item(self, idx="raw-1"):
        it = TestSummarizeTitleCap()._item()
        it["id"] = idx
        return it

    def _client(self, rows, status="ended"):
        class Batches:
            def list(self, limit=20):
                return [type("B", (), {"id": "msgbatch_lost", "processing_status": status})()]

            def results(self, batch_id):
                return list(rows)

        return type("C", (), {"messages": type("M", (), {"batches": Batches()})()})()

    def _row(self, custom_id, payload):
        block = type("Blk", (), {"type": "text", "text": json.dumps(payload)})()
        message = type("M", (), {"content": [block], "model": "fake-batch", "usage": None})()
        result = type("R", (), {"type": "succeeded", "message": message})()
        return type("Row", (), {"custom_id": custom_id, "result": result})()

    def test_english_batch_is_recovered_into_the_cache_without_local_state(self):
        item = self._item()
        raw_by_id = {"raw-1": {"raw_content_hash": "abc123"}}
        key = su.cache_key(item, raw_by_id)
        custom_id = su.format_custom_id(su.BATCH_KIND_EN, "raw-1", key)
        payload = TestSummarizeTitleCap()._result("Recovered AI title")
        cache = {}
        with mock.patch.object(su, "make_client", lambda: self._client([self._row(custom_id, payload)])):
            stats = su.recover_unclaimed_batches([item], raw_by_id, cache, su.BATCH_KIND_EN)
        self.assertEqual(stats["recovered"], 1)
        self.assertEqual(cache[key]["title_en"], "Recovered AI title")
        self.assertTrue(su.valid_result(cache[key]))

    def test_recovery_is_skipped_when_the_source_content_changed(self):
        item = self._item()
        stale_key = su.cache_key(item, {"raw-1": {"raw_content_hash": "OLD"}})
        custom_id = su.format_custom_id(su.BATCH_KIND_EN, "raw-1", stale_key)
        raw_by_id = {"raw-1": {"raw_content_hash": "NEW"}}
        cache = {}
        payload = TestSummarizeTitleCap()._result("Stale AI title")
        with mock.patch.object(su, "make_client", lambda: self._client([self._row(custom_id, payload)])):
            stats = su.recover_unclaimed_batches([item], raw_by_id, cache, su.BATCH_KIND_EN)
        self.assertEqual(stats["recovered"], 0)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(cache, {})

    def test_japanese_and_english_kinds_do_not_cross_apply(self):
        item = self._item()
        raw_by_id = {"raw-1": {"raw_content_hash": "abc123"}}
        key = su.cache_key(item, raw_by_id)
        en_id = su.format_custom_id(su.BATCH_KIND_EN, "raw-1", key)
        payload = TestSummarizeTitleCap()._result("English title")
        cache = {}
        with mock.patch.object(su, "make_client", lambda: self._client([self._row(en_id, payload)])):
            stats = su.recover_unclaimed_batches([item], raw_by_id, cache, su.BATCH_KIND_JA)
        self.assertEqual(stats["recovered"], 0)
        self.assertEqual(cache, {})

    def test_recovery_is_idempotent(self):
        item = self._item()
        raw_by_id = {"raw-1": {"raw_content_hash": "abc123"}}
        key = su.cache_key(item, raw_by_id)
        custom_id = su.format_custom_id(su.BATCH_KIND_EN, "raw-1", key)
        payload = TestSummarizeTitleCap()._result("Recovered AI title")
        cache = {}
        client = self._client([self._row(custom_id, payload)])
        with mock.patch.object(su, "make_client", lambda: client):
            first = su.recover_unclaimed_batches([item], raw_by_id, cache, su.BATCH_KIND_EN)
            second = su.recover_unclaimed_batches([item], raw_by_id, cache, su.BATCH_KIND_EN)
        self.assertEqual((first["recovered"], second["recovered"]), (1, 0))

    def test_unfinished_batches_are_left_alone(self):
        item = self._item()
        raw_by_id = {"raw-1": {"raw_content_hash": "abc123"}}
        key = su.cache_key(item, raw_by_id)
        custom_id = su.format_custom_id(su.BATCH_KIND_EN, "raw-1", key)
        payload = TestSummarizeTitleCap()._result("Not ready")
        cache = {}
        client = self._client([self._row(custom_id, payload)], status="in_progress")
        with mock.patch.object(su, "make_client", lambda: client):
            stats = su.recover_unclaimed_batches([item], raw_by_id, cache, su.BATCH_KIND_EN)
        self.assertEqual(stats["recovered"], 0)
        self.assertEqual(cache, {})

    def test_batch_may_now_be_combined_with_a_dollar_cap(self):
        """The two used to be mutually exclusive, which silently removed the cap."""
        client = types.SimpleNamespace(
            messages=types.SimpleNamespace(
                count_tokens=lambda **kw: types.SimpleNamespace(input_tokens=1000)
            )
        )
        requests = [{"params": {"model": "claude-opus-4-8", "max_tokens": 1500,
                                "system": "s", "messages": []}} for _ in range(3)]
        pending = [(self._item(f"raw-{i}"), f"key{i}", {}) for i in range(3)]
        # Budgeted at EXPECTED_OUTPUT_TOKENS, not the 1500 ceiling:
        # 0.5 * (1000*1.10*$5 + 700*$25)/1e6 = $0.0115 per request.
        kept, kept_pending, bound, dropped, ceiling = su.trim_batch_to_budget(
            client, "claude-opus-4-8", requests, 0.025, pending
        )
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(kept_pending), 2)
        self.assertEqual(dropped, 1)
        self.assertLess(bound, 0.025)
        # The ceiling prices the same requests at MAX_TOKENS and must be higher,
        # so the run can report the true worst case next to what it scheduled.
        self.assertGreater(ceiling, bound)

    def test_preflight_refuses_to_submit_an_unpriced_model(self):
        client = types.SimpleNamespace(messages=types.SimpleNamespace())
        requests = [{"params": {"model": "x", "max_tokens": 10, "system": "s", "messages": []}}]
        kept, kept_pending, bound, dropped, ceiling = su.trim_batch_to_budget(
            client, "unpriced-model", requests, 1.0, [("i", "k", {})]
        )
        self.assertEqual(kept, [])
        self.assertEqual(dropped, 1)
        self.assertEqual((bound, ceiling), (0.0, 0.0))


class TestDailyBudgetCoversItsCallLimit(unittest.TestCase):
    """The USD cap must be able to pay for the call limit it is paired with.

    These two numbers are set in the workflow independently, and nothing linked
    them. With --api-limit 30 and --max-cost-usd 0.50 the pre-flight bound priced
    only 22 requests inside the cap, so every run silently trimmed 8 and reported
    `preflight_trimmed: 8` while actual spend sat at 43% of the cap. Coverage of
    the newest items fell behind and the cause was invisible unless someone read
    the Step Summary closely.

    Input sizes below are measured from production run 33569264923.
    """

    # Measured input tokens per call, from runs 37400327832 / 37551702064 /
    # 37709283488. The RATES are deliberately not hardcoded beside them: they are
    # looked up from whatever model the workflow sets, because this test was
    # written against Opus 4.8 and kept pricing at $5/$25 after production moved
    # to Sonnet 5.5 at $2/$10. That made it 2.7x too conservative and it would
    # have refused any English limit above 84 while the real bound allowed 228 --
    # a stale guard blocking a change it had no reason to block.
    MEASURED_INPUT_TOKENS = {"english": 1198, "japanese": 1113}

    def _rates(self) -> dict:
        model = re.search(
            r"ANTHROPIC_SUMMARY_MODEL:\s*(\S+)", self._daily()
        ).group(1)
        rates = su.model_pricing(model)
        self.assertIsNotNone(rates, f"{model} is not in MODEL_PRICING_USD_PER_MTOK")
        return rates

    def _daily(self) -> str:
        return (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-update.yml"
        ).read_text(encoding="utf-8")

    def _flags(self, step: str) -> dict:
        tokens = step.split()
        flags = {}
        for i, token in enumerate(tokens):
            if token.startswith("--") and i + 1 < len(tokens):
                nxt = tokens[i + 1]
                if not nxt.startswith("--") and not nxt.startswith("2>"):
                    flags[token] = nxt
        return flags

    def _budget_per_call(self, input_tokens, in_rate, out_rate) -> float:
        # Mirrors anthropic_batch.bounds_from_counts for a batch request.
        return 0.5 * (
            input_tokens * su_margin() * in_rate + su.EXPECTED_OUTPUT_TOKENS * out_rate
        ) / 1_000_000

    def test_each_summary_stage_can_afford_its_full_call_limit(self):
        workflow = self._daily()
        bounds = {
            "english": ("name: Maintain English summaries", "name: Maintain Japanese summaries"),
            "japanese": ("name: Maintain Japanese summaries", "name: Translate Simplified Chinese updates"),
        }
        for stage, (start, end) in bounds.items():
            with self.subTest(stage=stage):
                step = workflow[workflow.index(start):workflow.index(end)]
                flags = self._flags(step)
                limit = int(flags["--api-limit"])
                cap = float(flags["--max-cost-usd"])
                rates = self._rates()
                needed = limit * self._budget_per_call(
                    self.MEASURED_INPUT_TOKENS[stage], rates["input"], rates["output"]
                )
                self.assertLessEqual(
                    needed, cap,
                    f"{stage}: --api-limit {limit} budgets to ${needed:.4f}, "
                    f"over the --max-cost-usd {cap} cap, so requests will be trimmed",
                )

    def test_the_limits_keep_up_with_measured_item_arrival(self):
        """Arrivals doubled to ~82/day over 2026-09-28..10-08 (656 items / 8 days
        with runs, single days at 286). Japanese demand is arrivals only -- it is
        generated from the Japanese source metadata, not from the English -- so
        its limit alone has to clear that rate. At 50 it had slipped from 100%
        coverage to 90.6% and stopped recovering."""
        workflow = self._daily()
        step = workflow[
            workflow.index("name: Maintain Japanese summaries"):
            workflow.index("name: Translate Simplified Chinese updates")
        ]
        self.assertGreaterEqual(int(self._flags(step)["--api-limit"]), 82)

    def test_translation_limit_exceeds_the_summary_limit(self):
        """zh-Hans demand is new arrivals PLUS re-translation of every English
        upgrade the summary step just made, so it cannot be the smaller budget."""
        workflow = self._daily()
        english = self._flags(workflow[
            workflow.index("name: Maintain English summaries"):
            workflow.index("name: Maintain Japanese summaries")])
        translate = self._flags(workflow[
            workflow.index("name: Translate Simplified Chinese updates"):
            workflow.index("name: Build yearly public archives")])
        self.assertGreaterEqual(int(translate["--limit"]), int(english["--api-limit"]))


def su_margin() -> float:
    import anthropic_batch
    return anthropic_batch.DEFAULT_TOKEN_ESTIMATE_MARGIN


class TestOutputBudgetIsCalibrated(unittest.TestCase):
    """The budgeting figure must be well under the hard ceiling, and above reality."""

    def test_expected_output_is_far_below_the_hard_ceiling(self):
        self.assertLess(su.EXPECTED_OUTPUT_TOKENS, su.MAX_TOKENS)
        self.assertLessEqual(su.EXPECTED_OUTPUT_TOKENS, su.MAX_TOKENS / 2)

    def test_expected_output_has_headroom_over_observed_output(self):
        """Measured: 466 tokens/call English, 296 Japanese, max 427 in the corpus."""
        self.assertGreater(su.EXPECTED_OUTPUT_TOKENS, 466 * 1.4)

    def test_ceiling_is_still_priced_and_reported(self):
        import types
        client = types.SimpleNamespace(
            messages=types.SimpleNamespace(
                count_tokens=lambda **kw: types.SimpleNamespace(input_tokens=1000)
            )
        )
        requests = [{"params": {"model": "claude-opus-4-8", "max_tokens": su.MAX_TOKENS,
                                "system": "s", "messages": []}}]
        _kept, _pending, bound, _dropped, ceiling = su.trim_batch_to_budget(
            client, "claude-opus-4-8", requests, 10.0, [("i", "k", {})]
        )
        self.assertGreater(ceiling, bound, "the worst case must be reported, not hidden")
        expected_ratio = su.MAX_TOKENS / su.EXPECTED_OUTPUT_TOKENS
        self.assertGreater(expected_ratio, 2.0)



class TestModelPricingLookup(unittest.TestCase):
    """A model id can be a prefix of its own successor.

    `claude-opus-5-5` starts with `claude-opus-5`, so first-match-in-table-order
    priced it at $5/$25 instead of its own $4/$20 -- silently, and 25% wrong in
    both the cost report and the pre-flight bound that decides trimming. The
    table is supposed to fail closed on a model it does not know, not borrow a
    neighbour's rates.
    """

    def test_a_successor_id_does_not_inherit_its_prefixs_rates(self):
        five = su.model_pricing("claude-opus-5")
        five_five = su.model_pricing("claude-opus-5-5")
        self.assertIsNotNone(five_five)
        self.assertNotEqual(five_five, five)
        self.assertEqual((five_five["input"], five_five["output"]), (4.0, 20.0))
        self.assertEqual((five["input"], five["output"]), (5.0, 25.0))

    def test_longest_matching_prefix_wins_regardless_of_table_order(self):
        """Ordering the dict carefully would also work, and would break the next
        time someone appends a row. The lookup has to be order-independent."""
        original = dict(su.MODEL_PRICING_USD_PER_MTOK)
        try:
            su.MODEL_PRICING_USD_PER_MTOK.clear()
            # deliberately worst-case order: the short prefix first
            su.MODEL_PRICING_USD_PER_MTOK.update({
                "claude-opus-5": original["claude-opus-5"],
                "claude-opus-5-5": original["claude-opus-5-5"],
            })
            self.assertEqual(su.model_pricing("claude-opus-5-5")["input"], 4.0)
            su.MODEL_PRICING_USD_PER_MTOK.clear()
            su.MODEL_PRICING_USD_PER_MTOK.update({
                "claude-opus-5-5": original["claude-opus-5-5"],
                "claude-opus-5": original["claude-opus-5"],
            })
            self.assertEqual(su.model_pricing("claude-opus-5-5")["input"], 4.0)
        finally:
            su.MODEL_PRICING_USD_PER_MTOK.clear()
            su.MODEL_PRICING_USD_PER_MTOK.update(original)

    def test_an_unknown_model_still_fails_closed(self):
        for unknown in ("some-unreleased-model", "claude-fable-5-1", "gpt-4"):
            with self.subTest(unknown):
                self.assertIsNone(su.model_pricing(unknown))

    def test_opus_5_5_cache_read_is_the_published_rate_not_a_derived_one(self):
        """cache_read is 0.1x input on most rows, so it is tempting to derive it.
        Opus 5.5 is quoted at $0.20 against a $4 input -- 0.1x would be $0.40."""
        rates = su.model_pricing("claude-opus-5-5")
        self.assertEqual(rates["cache_read"], 0.20)
        self.assertNotEqual(rates["cache_read"], round(0.1 * rates["input"], 2))
        # Sonnet 5.5 is the same 0.05x: $0.10 against $2, where 0.1x would be $0.20.
        sonnet = su.model_pricing("claude-sonnet-5-5")
        self.assertEqual(sonnet["cache_read"], 0.10)
        self.assertNotEqual(sonnet["cache_read"], round(0.1 * sonnet["input"], 2))

    def test_cache_write_multipliers_hold_on_every_row(self):
        """These ones ARE derived: 5m = 1.25x input, 1h = 2x input.

        OpenAI has a single cache-write rate (1.25x, 30-minute TTL) and no 1h
        tier, so its rows carry 1.25x in both keys rather than an invented 2x."""
        for model, rates in su.MODEL_PRICING_USD_PER_MTOK.items():
            with self.subTest(model):
                self.assertAlmostEqual(rates["cache_write_5m"], 1.25 * rates["input"], places=4)
                expected_1h = 1.25 if model.startswith("gpt-") else 2.0
                self.assertAlmostEqual(rates["cache_write_1h"], expected_1h * rates["input"], places=4)

    def test_the_production_summary_model_is_unchanged(self):
        """Adding a row must not move production onto it. The switch is an
        env-only change (ANTHROPIC_SUMMARY_MODEL); production moved to Sonnet 5.5
        deliberately after an A/B evaluation, and Opus 5.5 is priced only."""
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-update.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("ANTHROPIC_SUMMARY_MODEL: claude-sonnet-5-5", workflow)
        self.assertNotIn("claude-opus-5-5", workflow)
        self.assertEqual(su.DEFAULT_MODEL, "claude-sonnet-5-5")


class TestStageTwoTitlePreservation(unittest.TestCase):
    """Stage 3 must not silently rewrite Stage 2's rule-based English.

    Stage 2 appends a `(published_at)` suffix to colliding rule-based titles
    (build_public_data.disambiguate_duplicate_titles). That pass is corpus-wide
    and cannot be reproduced per item, so regenerating title_en in Stage 3 drops
    the suffix — republishing indistinguishable duplicate cards and changing the
    Stage 4 translation source_hash, which forces paid re-translation of work
    already paid for. Pin the preservation.
    """

    def _rule_based_item(self):
        item = TestSummarizeTitleCap()._item()
        item["title_en"] = "Public Comment: Draft National Park Rules (2026-07-21)"
        item["summary_en"] = bpd.SUMMARY_EN
        item["business_impact_en"] = bpd.BUSINESS_IMPACT_EN
        item["recommended_action_en"] = bpd.RECOMMENDED_ACTION_EN
        item["summary_source"] = "rule_based"
        return item

    def test_restore_keeps_stage_two_disambiguated_title_for_non_ai_item(self):
        item = self._rule_based_item()

        su.restore_rule_based_english_preview(item)

        self.assertEqual(
            item["title_en"], "Public Comment: Draft National Park Rules (2026-07-21)"
        )
        self.assertEqual(item["summary_source"], "rule_based")

    def test_restore_keeps_stage_two_title_when_summary_source_is_absent(self):
        item = self._rule_based_item()
        item.pop("summary_source")

        su.restore_rule_based_english_preview(item)

        self.assertEqual(
            item["title_en"], "Public Comment: Draft National Park Rules (2026-07-21)"
        )
        self.assertEqual(item["summary_source"], "rule_based")

    def test_restore_still_strips_stale_ai_text_and_provenance(self):
        item = self._rule_based_item()
        su.apply_result(item, TestSummarizeTitleCap()._result("Stale AI title"), "2026-06-18T00:00:00Z", "m")

        su.restore_rule_based_english_preview(item)

        self.assertEqual(item["summary_source"], "rule_based")
        self.assertNotEqual(item["title_en"], "Stale AI title")
        self.assertEqual(item["summary_en"], bpd.SUMMARY_EN)
        for field in su.EN_PROVENANCE_FIELDS:
            self.assertNotIn(field, item)

    def test_restore_strips_ai_text_left_by_provenance_without_summary_source(self):
        item = self._rule_based_item()
        item["title_en"] = "Stale AI title"
        item["summary_en"] = "Stale AI summary."
        item["summarized_at"] = "2026-06-18T00:00:00Z"

        su.restore_rule_based_english_preview(item)

        self.assertNotEqual(item["title_en"], "Stale AI title")
        self.assertEqual(item["summary_en"], bpd.SUMMARY_EN)
        self.assertNotIn("summarized_at", item)


class TestResultValidationGuards(unittest.TestCase):
    def test_valid_result_rejects_japanese_title_per_item(self):
        """A Japanese title_en must fail per item, not abort the whole run.

        validate_output() rejects a Japanese title_en for the entire corpus and
        returns 2, discarding every other paid response in the run.
        """
        good = TestSummarizeTitleCap()._result("English only title")
        self.assertTrue(su.valid_result(good))

        for bad_title in ("特定外来生物 designation", "Amendment to 意見募集 rules", "ガイドライン draft"):
            bad = TestSummarizeTitleCap()._result(bad_title)
            self.assertFalse(su.valid_result(bad), bad_title)

    def test_valid_result_still_accepts_existing_over_length_cache_entries(self):
        """apply_result() shortens titles, so length must not be a cache-miss trigger."""
        long_title = "Long AI title " * 20
        self.assertGreater(len(long_title), bpd.TITLE_MAX_CHARS)
        self.assertTrue(su.valid_result(TestSummarizeTitleCap()._result(long_title)))


class TestCostCapFailsClosed(unittest.TestCase):
    def test_priced_models_cover_the_models_the_workflows_can_select(self):
        for model in ("claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"):
            self.assertIsNotNone(su.model_pricing(model), model)
        self.assertIsNone(su.model_pricing("some-unreleased-model"))

    def test_max_cost_usd_requires_a_priced_model(self):
        """Without pricing the cap silently never fires; fail closed instead."""
        stderr = io.StringIO()
        with mock.patch.dict(
            os.environ, {"ANTHROPIC_SUMMARY_MODEL": "some-unreleased-model"}, clear=False
        ), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                su.main(["--max-cost-usd", "0.50"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("requires a model with configured pricing", stderr.getvalue())


class TestValidationFailureKeepsPaidCache(unittest.TestCase):
    def test_cache_is_persisted_when_output_validation_fails(self):
        """A failed corpus-wide check must not throw away responses already paid for."""
        item = TestSummarizeTitleCap()._item()
        item["relevance_score"] = 10.0
        # A pre-existing, untouched corpus item that fails validate_output().
        broken = TestSummarizeTitleCap()._item()
        broken["id"] = "raw-broken"
        broken["relevance_score"] = 1.0
        broken["title_en"] = "意見募集の概要"

        result = TestSummarizeTitleCap()._result("Fresh AI title")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            input_path = base / "legal_updates.json"
            cache_path = base / "summary_cache.json"
            raw_path = base / "raw_items.json"
            input_path.write_text(json.dumps([item, broken]), encoding="utf-8")
            raw_path.write_text("[]", encoding="utf-8")
            cache_path.write_text("{}", encoding="utf-8")

            patches = {
                "INPUT_PATH": input_path,
                "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json",
                "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path,
                "LOG_PATH": base / "summarize.log",
                "make_client": idle_workspace_client,
                "request_summary": lambda *a, **k: (result, "claude-opus-4-8", su.message_usage(None)),
            }
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = su.main(["--limit", "1"])

            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()

            self.assertEqual(rc, 2)
            # The published file is still NOT written...
            published = json.loads(input_path.read_text(encoding="utf-8"))
            self.assertEqual(published[0]["title_en"], "Rule-based title")
            # ...but the paid response survives in the private cache.
            saved = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(next(iter(saved.values()))["title_en"], "Fresh AI title")



EGOV_LAW_SUMMARY = (
    "更新一覧日: 20260812; 対象法令: 特定複合観光施設区域整備法施行令; "
    "法令番号: 平成三十一年政令第七十二号; 改正法令: 関係政令の整備に関する政令; "
    "改正法令番号: 令和七年政令第三百四十一号; 公布日: 2025-10-01; 施行日: 2027-04-01"
)


class TestSourceEvidenceAndIdentifiers(unittest.TestCase):
    """Deterministic facts handed to the English prompt, and the confidence cap."""

    def test_identifiers_are_converted_and_carry_their_e_gov_role(self):
        ids = su.legal_identifiers({"title_ja": "関係政令の整備に関する政令"},
                                   {"raw_summary": EGOV_LAW_SUMMARY})
        self.assertEqual(
            [(i["instrument"], i["number"], i["gregorian_year"], i["era_year"], i["role"]) for i in ids],
            [("政令", 72, 2019, "平成 31", "target_law"), ("政令", 341, 2025, "令和 7", "amending_instrument")],
        )
        self.assertEqual(ids[1]["as_written"], "令和七年政令第三百四十一号")

    def test_identifiers_in_a_title_and_ministry_ordinances_are_found(self):
        ids = su.legal_identifiers(
            {"title_ja": "令和8年国税庁告示第4号及び平成十五年厚生労働省令第百号の改正"}, {})
        self.assertEqual([(i["instrument"], i["number"], i["role"]) for i in ids],
                         [("国税庁告示", 4, "mentioned"), ("厚生労働省令", 100, "mentioned")])
        self.assertEqual(su.legal_identifiers({"title_ja": "令和元年法律第一号"}, {})[0]["gregorian_year"], 2019)

    def test_numbered_things_that_are_not_instruments_are_ignored(self):
        for title in ("令和元年台風第19号による被害", "令和8年度病害虫発生予報第6号", "第三回検討会"):
            self.assertEqual(su.legal_identifiers({"title_ja": title}, {}), [], title)

    def test_source_evidence_classes(self):
        cases = [
            ({"title_ja": "法律案"}, {"raw_summary": ""}, "title_only"),
            ({"title_ja": "法律案"}, {"raw_summary": "報道発表"}, "title_only"),
            ({"title_ja": "PMDAの安全性情報"}, {"raw_summary": "PMDA 安全対策業務 新着情報"}, "title_only"),
            ({"title_ja": "消費者安全法施行規則の一部を改正する内閣府令(案)に関する意見募集について"},
             {"raw_summary": "「消費者安全法施行規則の一部を改正する内閣府令(案)」に関する意見募集について"},
             "title_only"),
            ({"title_ja": "政令"}, {"raw_summary": EGOV_LAW_SUMMARY}, "title_and_structured_metadata"),
            ({"title_ja": "案"}, {"raw_summary": "案の公示日：2026/06/16 受付締切日時：2026/07/15 23:59 "
                                                  "カテゴリー：林業"}, "title_and_structured_metadata"),
            ({"title_ja": "ガイダンス"},
             {"raw_summary": "経済産業省は、同族企業の統治に関する指針を取りまとめ、本日公表しました。"},
             "descriptive_text"),
        ]
        for item, raw, expected in cases:
            self.assertEqual(su.source_evidence(item, raw), expected, raw)

    def test_confidence_is_capped_by_evidence_and_never_raised(self):
        result = TestSummarizeTitleCap()._result("t")
        title_only = ({"title_ja": "法律案"}, {"raw_summary": ""})
        described = ({"title_ja": "指針"}, {"raw_summary": "経済産業省は、同族企業の統治に関する指針を取りまとめ、本日公表しました。"})
        self.assertEqual(su.calibrate_confidence({**result, "confidence": "high"}, *title_only)["confidence"], "medium")
        self.assertEqual(su.calibrate_confidence({**result, "confidence": "low"}, *title_only)["confidence"], "low")
        self.assertEqual(su.calibrate_confidence({**result, "confidence": "high"}, *described)["confidence"], "high")
        original = {**result, "confidence": "high"}
        su.calibrate_confidence(original, *title_only)
        self.assertEqual(original["confidence"], "high", "the caller's dict must not be mutated")

    def test_english_prompt_receives_derived_facts_japanese_only_the_numbers_as_written(self):
        item = {**TestSummarizeTitleCap()._item(), "title_ja": "関係政令の整備に関する政令"}
        raw = {"raw_summary": EGOV_LAW_SUMMARY}
        english = su.build_user_content(item, raw)
        self.assertIn('"source_evidence": "title_and_structured_metadata"', english)
        self.assertIn('"number": 341', english)
        japanese = su.japanese_summary_request_params("m", item, raw)["messages"][0]["content"]
        self.assertNotIn("source_evidence", japanese)
        # Since 2026-10-16 the Japanese prompt gets the instrument numbers too, in
        # their Japanese written form only (no converted English facts).
        self.assertIn('"as_written": "令和七年政令第三百四十一号"', japanese)
        self.assertNotIn('"gregorian_year"', japanese)

    def test_prompt_states_the_completeness_and_confidence_rules(self):
        prompt = " ".join(su.SYSTEM_PROMPT.split())
        for phrase in ("legal_identifiers", "source_evidence", "effective date",
                       "Never write an instrument number that is not in legal_identifiers",
                       "Do not pad", "lowered automatically",
                       "never mention the input's field names", "announced (published_at)",
                       "Keep every specific party, obligated entity",
                       "Do not add parties beyond those the source names",
                       "cannot be determined only when neither the title nor the named instrument"):
            self.assertIn(phrase, prompt)

    def test_every_fresh_english_result_path_applies_the_cap(self):
        source = Path(su.__file__).read_text(encoding="utf-8")
        # sequential, batch, parallel, and recovered-batch paths
        self.assertEqual(source.count("result = calibrate_confidence(result,"), 4)

    def test_batch_path_publishes_and_caches_the_capped_confidence(self):
        item = TestSummarizeTitleCap()._item()
        item["relevance_score"] = 10
        result = {**TestSummarizeTitleCap()._result("AI title"), "confidence": "high"}
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            input_path = base / "legal_updates.json"
            cache_path = base / "summary_cache.json"
            raw_path = base / "raw_items.json"
            input_path.write_text(json.dumps([item]), encoding="utf-8")
            raw_path.write_text(json.dumps([{"id": item["id"], "raw_summary": "報道発表"}]), encoding="utf-8")
            patches = {
                "INPUT_PATH": input_path, "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json", "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path, "LOG_PATH": base / "summarize.log",
                "make_client": idle_workspace_client,
                "request_summary_batch": lambda client, model, candidates, *, timeout_seconds, custom_ids=None:
                    ("msgbatch_cap", [(result, model)]),
            }
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False
            ), contextlib.redirect_stdout(io.StringIO()):
                rc = su.main(["--limit", "1", "--batch"])
            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()
            published = json.loads(input_path.read_text(encoding="utf-8"))
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertEqual(rc, 0)
        self.assertEqual(published[0]["confidence"], "medium")
        self.assertEqual(next(iter(cache.values()))["confidence"], "medium")



class TestEnglishRequeue(unittest.TestCase):
    """An audit can ask for specific English summaries to be redone, once."""

    def _run(self, tmp, api_limit, created_at="2025-01-01T00:00:00Z"):
        import requeue
        base = Path(tmp)
        item = TestSummarizeTitleCap()._item()
        item.update(TestSummarizeTitleCap()._result("Old AI title with Reiwa 15 error"))
        item.update(TestSummarizeTitleCap()._ja_result())
        item.update({"summary_source": "claude", "summarized_at": "2020-01-01T00:00:00Z",
                     "summary_model": "claude-opus-4-8", "summary_ja_source": "claude",
                     "ja_summarized_at": "2020-01-01T00:00:00Z", "ja_summary_model": "m"})
        key = su.cache_key(item, {})
        cache = {key: {**{f: item[f] for f in su.AI_FIELDS},
                       **{f: item[f] for f in su.JA_AI_FIELDS},
                       "summary_ja_source": "claude", "ja_summarized_at": "2020-01-01T00:00:00Z",
                       "ja_summary_model": "m",
                       "summarized_at": "2020-01-01T00:00:00Z", "summary_model": "claude-opus-4-8"}}
        paths = {name: base / name for name in ("legal_updates.json", "summary_cache.json", "raw.json", "requeue.json")}
        paths["legal_updates.json"].write_text(json.dumps([item]), encoding="utf-8")
        paths["summary_cache.json"].write_text(json.dumps(cache), encoding="utf-8")
        paths["raw.json"].write_text("[]", encoding="utf-8")
        paths["requeue.json"].write_text(json.dumps(
            {"created_at": created_at, "english": [item["id"]], "zh-Hans": []}), encoding="utf-8")
        fresh = TestSummarizeTitleCap()._result("Fresh AI title, Heisei 15")
        usage = {"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 0,
                 "cache_read_input_tokens": 0}
        calls = {"n": 0}

        def fake(client, model, it, raw):
            calls["n"] += 1
            return fresh, model, usage

        patches = {
            "INPUT_PATH": paths["legal_updates.json"], "OUTPUT_PATH": paths["legal_updates.json"],
            "BEFORE_AI_PATH": base / "before_ai.json", "CACHE_PATH": paths["summary_cache.json"],
            "RAW_PATH": paths["raw.json"], "LOG_PATH": base / "summarize.log",
            "make_client": idle_workspace_client, "request_summary": fake,
            "request_japanese_summary": mock.Mock(side_effect=AssertionError("Japanese must not run")),
        }
        saved = (requeue.REQUEUE_PATH, requeue.AUTO_REQUEUE_PATH)
        requeue.REQUEUE_PATH, requeue.AUTO_REQUEUE_PATH = paths["requeue.json"], base / "requeue_auto.json"
        out = io.StringIO()
        try:
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False
            ), contextlib.redirect_stdout(out):
                rc = su.main(["--all-items", "--english-only", "--api-limit", str(api_limit), "--parallel", "2"])
        finally:
            requeue.REQUEUE_PATH, requeue.AUTO_REQUEUE_PATH = saved
            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()
        published = json.loads(paths["legal_updates.json"].read_text(encoding="utf-8"))[0]
        return rc, calls["n"], published, item, out.getvalue()

    def test_a_listed_summary_is_redone_and_japanese_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, n, published, before, out = self._run(tmp, api_limit=5)
        self.assertEqual(rc, 0)
        self.assertEqual(n, 1)
        self.assertIn("requeued_items  : 1", out)
        self.assertEqual(published["title_en"], "Fresh AI title, Heisei 15")
        self.assertEqual(published["summary_source"], "claude")
        for field in (*su.JA_AI_FIELDS, *su.JA_PROVENANCE_FIELDS):
            self.assertEqual(published[field], before[field])

    def test_without_budget_the_current_summary_stays(self):
        with tempfile.TemporaryDirectory() as tmp:
            _rc, n, published, before, _ = self._run(tmp, api_limit=0)
        self.assertEqual(n, 0)
        self.assertEqual(published["title_en"], before["title_en"])
        self.assertEqual(published["summary_source"], "claude")

    def test_a_summary_newer_than_the_request_is_not_redone(self):
        with tempfile.TemporaryDirectory() as tmp:
            _rc, n, published, before, _ = self._run(tmp, api_limit=5, created_at="2019-01-01T00:00:00Z")
        self.assertEqual(n, 0)
        self.assertEqual(published["title_en"], before["title_en"])


class TestDeadlineInPayload(unittest.TestCase):
    def test_the_published_structured_deadline_reaches_both_prompts(self):
        item = {"title_ja": "告示案に係る意見募集", "comment_deadline": "2026-09-25T00:00:00+09:00",
                "comment_deadline_source": "related_egov_item", "comment_deadline_inherited": True}
        payload = su.source_payload(item, {})
        self.assertEqual(payload["comment_deadline"], "2026-09-25 00:00 JST", "readable, time and zone kept")
        self.assertIn("e-Gov", payload["comment_deadline_basis"])
        self.assertNotIn("comment_deadline", su.source_payload({"title_ja": "x"}, {}))


class TestJapaneseRequeue(unittest.TestCase):
    """The `japanese` requeue section redoes a Japanese summary in --japanese-only runs only."""

    def _run(self, tmp, api_limit, created_at="2025-01-01T00:00:00Z"):
        import requeue
        base = Path(tmp)
        item = TestSummarizeTitleCap()._item()
        item.update(TestSummarizeTitleCap()._result("AI title"))
        item.update({"summary_ja": "農林水産省は意見募集を行っています。", "business_impact_ja": "影響の可能性があります。",
                     "recommended_action_ja": "原文を確認してください。", "summary_source": "claude",
                     "summarized_at": "2020-01-01T00:00:00Z", "summary_model": "m",
                     "summary_ja_source": "claude", "ja_summarized_at": "2020-01-01T00:00:00Z",
                     "ja_summary_model": "m"})
        key = su.cache_key(item, {})
        cache = {key: {**{f: item[f] for f in su.AI_FIELDS}, **{f: item[f] for f in su.JA_AI_FIELDS},
                       "summary_ja_source": "claude", "ja_summarized_at": "2020-01-01T00:00:00Z",
                       "ja_summary_model": "m", "summarized_at": "2020-01-01T00:00:00Z", "summary_model": "m"}}
        paths = {n: base / n for n in ("legal_updates.json", "summary_cache.json", "raw.json", "requeue.json")}
        paths["legal_updates.json"].write_text(json.dumps([item]), encoding="utf-8")
        paths["summary_cache.json"].write_text(json.dumps(cache), encoding="utf-8")
        paths["raw.json"].write_text("[]", encoding="utf-8")
        paths["requeue.json"].write_text(json.dumps({"created_at": created_at, "japanese": [item["id"]]}),
                                         encoding="utf-8")
        fresh = {"summary_ja": "農林水産省は2026年7月30日に案を公示し、意見募集を行いました。",
                 "business_impact_ja": "影響の可能性があります。", "recommended_action_ja": "原文を確認してください。"}
        usage = {"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 0,
                 "cache_read_input_tokens": 0}
        calls = {"n": 0}

        def fake(client, model, it, raw):
            calls["n"] += 1
            return fresh, model, usage

        patches = {
            "INPUT_PATH": paths["legal_updates.json"], "OUTPUT_PATH": paths["legal_updates.json"],
            "BEFORE_AI_PATH": base / "before_ai.json", "CACHE_PATH": paths["summary_cache.json"],
            "RAW_PATH": paths["raw.json"], "LOG_PATH": base / "summarize.log",
            "make_client": idle_workspace_client, "request_japanese_summary": fake,
            "request_summary": mock.Mock(side_effect=AssertionError("English must not run")),
        }
        saved = (requeue.REQUEUE_PATH, requeue.AUTO_REQUEUE_PATH)
        requeue.REQUEUE_PATH, requeue.AUTO_REQUEUE_PATH = paths["requeue.json"], base / "none.json"
        out = io.StringIO()
        try:
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False
            ), contextlib.redirect_stdout(out):
                rc = su.main(["--all-items", "--japanese-only", "--api-limit", str(api_limit)])
        finally:
            requeue.REQUEUE_PATH, requeue.AUTO_REQUEUE_PATH = saved
            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()
        published = json.loads(paths["legal_updates.json"].read_text(encoding="utf-8"))[0]
        return rc, calls["n"], published, item

    def test_a_listed_japanese_summary_is_redone_and_english_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, n, published, before = self._run(tmp, api_limit=5)
        self.assertEqual((rc, n), (0, 1))
        self.assertIn("意見募集を行いました", published["summary_ja"])
        for field in (*su.AI_FIELDS, "summary_source", "summarized_at"):
            self.assertEqual(published.get(field), before.get(field))

    def test_without_budget_or_after_regeneration_nothing_is_called(self):
        with tempfile.TemporaryDirectory() as tmp:
            _rc, n, published, before = self._run(tmp, api_limit=0)
        self.assertEqual(n, 0)
        self.assertEqual(published["summary_ja"], before["summary_ja"], "the current text stays, never a gap")
        with tempfile.TemporaryDirectory() as tmp:
            _rc, n, _p, _b = self._run(tmp, api_limit=5, created_at="2019-01-01T00:00:00Z")
        self.assertEqual(n, 0)

    def test_the_prompts_ask_for_time_neutral_consultation_wording(self):
        self.assertIn("never state whether comments are being accepted now", " ".join(su.SYSTEM_PROMPT.split()))
        self.assertIn("募集中", su.SYSTEM_PROMPT_JA)
        self.assertNotIn("'out for public comment'", su.SYSTEM_PROMPT)


class TestCourtCaseNumbers(unittest.TestCase):
    def test_case_codes_are_romanized_by_rule(self):
        item = {"title_ja": "令和7(許)23 手数料還付申立て却下決定に対する許可抗告事件"}
        raw = {"raw_summary": "令和8年9月30日 最高裁判所第二小法廷 決定 破棄自判 東京高等裁判所 令和7(ラ)184"}
        got = [(c["era_year"], c["code_romanized"], c["number"], c["gregorian_year"])
               for c in su.court_case_numbers(item, raw)]
        self.assertEqual(got, [("令和 7", "Kyo", 23, 2025), ("令和 7", "Ra", 184, 2025)])

    def test_every_code_seen_in_the_corpus_romanizes(self):
        for code, expected in (("あ", "A"), ("ク", "Ku"), ("し", "Shi"), ("ラ", "Ra"), ("ネ", "Ne"),
                               ("受", "Ju"), ("オ", "O"), ("行ツ", "Gyo-Tsu"), ("行ヒ", "Gyo-Hi"),
                               ("ム", "Mu"), ("う", "U"), ("ワ", "Wa")):
            self.assertEqual(su.romanize_case_code(code), expected, code)
        self.assertIsNone(su.romanize_case_code("X"))

    def test_the_english_payload_and_prompt_carry_them(self):
        item = {**TestSummarizeTitleCap()._item(), "title_ja": "令和8(ク)407 宗教法人解散命令申立事件"}
        content = su.build_user_content(item, {"raw_summary": "東京高等裁判所 令和7(ラ)1003"})
        self.assertIn('"code_romanized": "Ra"', content)
        self.assertIn('"code_romanized": "Ku"', content)
        prompt = " ".join(su.SYSTEM_PROMPT.split())
        self.assertIn("use code_romanized exactly", prompt)
        self.assertIn("court_case_numbers", prompt)



class TestOfficialStatuteNames(unittest.TestCase):
    def test_official_english_names_reach_the_prompt_keyed_by_exact_title(self):
        prompt = su.SYSTEM_PROMPT
        for ja, en in (("資金決済に関する法律", "Payment Services Act"),
                       ("租税特別措置法", "Act on Special Measures Concerning Taxation"),
                       ("労働安全衛生規則", "Ordinance on Industrial Safety and Health"),
                       ("外国為替及び外国貿易法", "Foreign Exchange and Foreign Trade Act")):
            self.assertEqual(su.OFFICIAL_EN_STATUTE_NAMES[ja], en)
            self.assertIn(f"{ja} -> {en}", prompt)
        self.assertIn("renamed or predecessor law", " ".join(prompt.split()))


class TestRequeueFile(unittest.TestCase):
    def test_the_committed_request_is_well_formed(self):
        import requeue
        path = requeue.REQUEUE_PATH
        if not path.exists():
            self.skipTest("no requeue request committed")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertRegex(data["created_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        for section in requeue.SECTIONS:
            ids = data.get(section, [])
            self.assertEqual(len(ids), len(set(ids)), f"{section} lists an id twice")
            self.assertTrue(all(i.startswith("raw-") for i in ids))
        self.assertFalse(set(data.get("english", [])) & set(data.get("zh-Hans", [])),
                         "an English regeneration already re-translates; do not list it twice")
        self.assertFalse(set(data.get("english", [])) & set(data.get("english_title", [])),
                         "a full English regeneration already rewrites the title")



class TestTitleOnlyRequeue(unittest.TestCase):
    """Truncated titles are redone alone; the summary body is never touched."""

    TRUNCATED = "Ministerial Ordinance Partially Amending the Enforcement Regulation of the Act on Domestic Animal Infectious..."

    def _run(self, tmp, new_title, api_limit=5, created_at="2025-01-01T00:00:00Z", confidence="medium"):
        import requeue
        base = Path(tmp)
        item = TestSummarizeTitleCap()._item()
        item.update(TestSummarizeTitleCap()._result(self.TRUNCATED))
        item.update(TestSummarizeTitleCap()._ja_result())
        item.update({"summary_source": "claude", "summarized_at": "2020-01-01T00:00:00Z",
                     "summary_model": "claude-opus-4-8", "summary_ja_source": "claude",
                     "ja_summarized_at": "2020-01-01T00:00:00Z", "ja_summary_model": "m",
                     "confidence": confidence})
        key = su.cache_key(item, {})
        cache = {key: {**{f: item[f] for f in su.AI_FIELDS},
                       **{f: item[f] for f in su.JA_AI_FIELDS},
                       "summary_ja_source": "claude", "ja_summarized_at": "2020-01-01T00:00:00Z",
                       "ja_summary_model": "m",
                       "summarized_at": "2020-01-01T00:00:00Z", "summary_model": "claude-opus-4-8"}}
        paths = {n: base / n for n in ("legal_updates.json", "summary_cache.json", "raw.json", "requeue.json")}
        paths["legal_updates.json"].write_text(json.dumps([item]), encoding="utf-8")
        paths["summary_cache.json"].write_text(json.dumps(cache), encoding="utf-8")
        paths["raw.json"].write_text("[]", encoding="utf-8")
        paths["requeue.json"].write_text(json.dumps(
            {"created_at": created_at, "english": [], "english_title": [item["id"]], "zh-Hans": []}),
            encoding="utf-8")
        usage = {"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 0,
                 "cache_read_input_tokens": 0}
        calls = {"n": 0}

        def fake_title(client, model, it, raw):
            calls["n"] += 1
            return {"title_en": new_title}, model, usage

        patches = {
            "INPUT_PATH": paths["legal_updates.json"], "OUTPUT_PATH": paths["legal_updates.json"],
            "BEFORE_AI_PATH": base / "before_ai.json", "CACHE_PATH": paths["summary_cache.json"],
            "RAW_PATH": paths["raw.json"], "LOG_PATH": base / "summarize.log",
            "make_client": idle_workspace_client, "request_title": fake_title,
            "request_summary": mock.Mock(side_effect=AssertionError("the body must not be regenerated")),
            "request_japanese_summary": mock.Mock(side_effect=AssertionError("Japanese must not run")),
        }
        saved = (requeue.REQUEUE_PATH, requeue.AUTO_REQUEUE_PATH)
        requeue.REQUEUE_PATH, requeue.AUTO_REQUEUE_PATH = paths["requeue.json"], base / "requeue_auto.json"
        out = io.StringIO()
        try:
            with mock.patch.multiple(su, **patches), mock.patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False
            ), contextlib.redirect_stdout(out):
                rc = su.main(["--all-items", "--english-only", "--api-limit", str(api_limit), "--parallel", "2"])
        finally:
            requeue.REQUEUE_PATH, requeue.AUTO_REQUEUE_PATH = saved
            for handler in list(su.logger.handlers):
                handler.close()
            su.logger.handlers.clear()
        published = json.loads(paths["legal_updates.json"].read_text(encoding="utf-8"))[0]
        saved_cache = json.loads(paths["summary_cache.json"].read_text(encoding="utf-8"))[key]
        return rc, calls["n"], published, item, saved_cache, out.getvalue()

    def test_only_the_title_changes(self):
        new = "Ministerial Ordinance Amending Domestic Animal Infectious Diseases Control Act Regulations"
        with tempfile.TemporaryDirectory() as tmp:
            rc, n, published, before, cache, out = self._run(tmp, new)
        self.assertEqual((rc, n), (0, 1))
        self.assertEqual(published["title_en"], new)
        self.assertEqual(cache["title_en"], new)
        self.assertIn("title_regenerated_at", cache)
        for field in ("summary_en", "business_impact_en", "recommended_action_en", "summarized_at",
                      *su.JA_AI_FIELDS, *su.JA_PROVENANCE_FIELDS):
            self.assertEqual(published[field], before[field], field)
        self.assertIn("titles_regenerated: 1", out)

    def test_an_incomplete_or_japanese_title_is_rejected_and_the_old_one_kept(self):
        for bad in ("A" * 121, "Ordinance Amending the Act on...", "家畜伝染病予防法施行規則 amendment"):
            with self.subTest(bad=bad[:30]), tempfile.TemporaryDirectory() as tmp:
                _rc, n, published, before, cache, out = self._run(tmp, bad)
                self.assertEqual(n, 1)
                self.assertEqual(published["title_en"], before["title_en"])
                self.assertNotIn("title_regenerated_at", cache)
                self.assertIn("titles_failed   : 1", out)

    def test_not_redone_once_newer_and_not_without_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            _rc, n, *_ = self._run(tmp, "x", created_at="2019-01-01T00:00:00Z")
        self.assertEqual(n, 0)
        with tempfile.TemporaryDirectory() as tmp:
            _rc, n, published, before, *_ = self._run(tmp, "x", api_limit=0)
        self.assertEqual(n, 0)
        self.assertEqual(published["title_en"], before["title_en"])

    def test_cached_confidence_is_capped_without_an_api_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            _rc, _n, published, *_ = self._run(tmp, "Complete title", created_at="2019-01-01T00:00:00Z",
                                               confidence="high")
        self.assertEqual(published["confidence"], "medium", "title-only evidence cannot support high")

    def test_title_prompt_keeps_the_title_consistent_with_the_summary(self):
        prompt = " ".join(su.TITLE_SYSTEM_PROMPT.split())
        self.assertIn("must agree with the current English summary", prompt)
        self.assertIn("no ellipsis", prompt)
        content = su.build_user_content(TestSummarizeTitleCap()._item(), {})
        self.assertNotIn("current_english_summary", content, "the full-summary prompt is unchanged")
        title_content = su.build_title_user_content({**TestSummarizeTitleCap()._item(), "summary_en": "S."}, {})
        self.assertIn('"current_english_summary": "S."', title_content)


class TestOfficialOfficeNames(unittest.TestCase):
    def test_offices_named_in_the_contact_field_get_their_official_english_name(self):
        raw = {"raw_summary": "案の公示日：2026/09/01 問合せ先（所管省庁・部局名等）：厚生労働省医薬局医薬品審査管理課"}
        self.assertEqual(su.official_office_names({"title_ja": "x"}, raw),
                         [{"ja": "厚生労働省医薬局",
                           "official_en": "Pharmaceutical Safety Bureau, Ministry of Health, Labour and Welfare"}])
        self.assertEqual(su.official_office_names({"title_ja": "x"}, {"raw_summary": "厚生労働省健康・生活衛生局"}), [],
                         "an office whose official English name could not be confirmed is not listed")

    def test_the_prompt_forbids_substituting_another_or_former_office(self):
        prompt = " ".join(su.SYSTEM_PROMPT.split())
        self.assertIn("never substitute a different office, a broader or narrower one, or a former name", prompt)
        payload = su.english_source_payload({"title_ja": "x"}, {"raw_summary": "国土交通省都市局"})
        self.assertEqual(payload["official_office_names"][0]["official_en"].split(",")[0], "City Bureau")



class TestOfficeNamesRespectTheReorganization(unittest.TestCase):
    def test_the_pre_2023_bureau_is_not_mapped_to_the_new_name(self):
        """医薬・生活衛生局 (until 2023-08) and 医薬局 (from 2023-09) are different
        offices; an older notice keeps its own bureau name."""
        old = {"raw_summary": "問合せ先（所管省庁・部局名等）：厚生労働省医薬・生活衛生局医薬安全対策課"}
        self.assertEqual(su.official_office_names({"title_ja": "x"}, old), [])
        new = {"raw_summary": "問合せ先（所管省庁・部局名等）：厚生労働省医薬局医薬安全対策課"}
        self.assertEqual(len(su.official_office_names({"title_ja": "x"}, new)), 1)


class TestRequeuePreviewIsReadOnly(unittest.TestCase):
    def test_preview_writes_only_under_data_eval_and_needs_confirmation(self):
        source = (SCRIPTS_DIR / "preview_requeue.py").read_text(encoding="utf-8")
        self.assertNotIn("save_json(", source)
        self.assertNotIn("OUTPUT_PATH", source)
        # The only writes are the two preview files under data/eval.
        self.assertEqual(source.count("write_text("), 2)
        self.assertEqual(source.count("OUT_JSON.write_text(") + source.count("OUT_MD.write_text("), 2)
        self.assertIn('"data" / "eval"', source)
        import preview_requeue
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(
            preview_requeue.su, "provider_client", side_effect=AssertionError("no API without confirmation")
        ):
            self.assertEqual(preview_requeue.main(["--full", "1", "--titles", "1"]), 0)
        self.assertIn("Nothing was called", out.getvalue())

    def test_preview_workflow_is_read_only_and_matches_the_daily_models(self):
        text = (SCRIPTS_DIR.parent / ".github" / "workflows" / "openai-flex-canary.yml").read_text(encoding="utf-8")
        self.assertIn("contents: read", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("git push", text)
        step = text[text.index("name: Requeue preview on gpt-6.1-sol"):]
        self.assertIn("if: ${{ inputs.requeue_preview }}", step)
        self.assertIn("ANTHROPIC_SUMMARY_MODEL: gpt-6.1-sol", step)
        self.assertIn("ANTHROPIC_TRANSLATION_MODEL: gpt-6.1-sol", step)
        self.assertIn("OPENAI_SERVICE_TIER: flex", text)

    def test_sampling_prefers_the_defect_items_and_is_deterministic(self):
        import preview_requeue
        ids = [f"raw-{n:03d}" for n in range(50)]
        self.assertEqual(preview_requeue.pick(ids, 3, {"raw-040"}), ["raw-040", "raw-000", "raw-001"])
        self.assertEqual(preview_requeue.pick(ids[:2], 5, set()), ids[:2])


if __name__ == "__main__":
    unittest.main()
