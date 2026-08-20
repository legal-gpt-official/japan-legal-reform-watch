"""Offline tests for scripts/summarize_updates.py.

No API calls: these tests cover local AI-result application and validation only.
"""

import contextlib
import io
import json
import os
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
        self.assertEqual(su.DEFAULT_MODEL, "claude-opus-4-8")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(su.resolve_model(None), "claude-opus-4-8")
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
        # Phase 3A: batched. --parallel is rejected together with --batch, so it
        # must be gone from the invocation itself (comments may still mention it).
        invocation = step[step.index("python scripts/summarize_updates.py"):step.index("2>&1")]
        self.assertIn("--batch", invocation)
        self.assertNotIn("--parallel", invocation)
        self.assertIn("--all-items", step)
        self.assertIn("--english-only", step)
        self.assertIn("--api-limit 30", step)
        # The USD cap must survive the switch: it is the only spend brake besides
        # the call count, and it used to be rejected outright alongside --batch.
        self.assertIn("--max-cost-usd 0.50", step)
        self.assertIn("English summary provider unavailable", workflow)
        self.assertIn("### English summary maintenance (batch)", workflow)

    def test_daily_workflow_reports_every_batch_stage_metric(self):
        """Phase 3A is an observation run: the Step Summary has to carry enough to
        judge discovery, the interlock, batch health, and estimate-vs-actual cost."""
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-update.yml"
        ).read_text(encoding="utf-8")
        for stage, marker, end in (
            ("english", "name: Maintain English summaries", "name: Maintain Japanese summaries"),
            ("japanese", "name: Maintain Japanese summaries", "name: Translate Simplified Chinese updates"),
            ("translate", "name: Translate Simplified Chinese updates", "name: Build yearly public archives"),
        ):
            step = workflow[workflow.index(marker):workflow.index(end)]
            for metric in ("batch_id", "batch_succeeded", "batch_errored", "batch_expired",
                           "batch_missing", "input_tokens", "output_tokens",
                           "cache_read_input_tokens", "estimated_cost_usd",
                           "preflight_cost_usd"):
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
            "--retry-kana-title-without-ja-reference",
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
        self.assertIn("--api-limit 30", workflow)
        self.assertIn("--batch", workflow)
        self.assertIn("--max-cost-usd 0.50", workflow)
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
        # 0.5 * (1000*1.10*$5 + 1500*$25)/1e6 = $0.021 per request.
        kept, kept_pending, bound, dropped = su.trim_batch_to_budget(
            client, "claude-opus-4-8", requests, 0.045, pending
        )
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(kept_pending), 2)
        self.assertEqual(dropped, 1)
        self.assertLess(bound, 0.045)

    def test_preflight_refuses_to_submit_an_unpriced_model(self):
        client = types.SimpleNamespace(messages=types.SimpleNamespace())
        requests = [{"params": {"model": "x", "max_tokens": 10, "system": "s", "messages": []}}]
        kept, kept_pending, bound, dropped = su.trim_batch_to_budget(
            client, "unpriced-model", requests, 1.0, [("i", "k", {})]
        )
        self.assertEqual(kept, [])
        self.assertEqual(dropped, 1)


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


if __name__ == "__main__":
    unittest.main()
