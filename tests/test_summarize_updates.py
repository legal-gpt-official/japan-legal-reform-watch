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

    def test_japanese_summary_is_applied_without_changing_original_title(self):
        item = self._item()
        original_title = item["title_ja"]
        result = self._ja_result()

        su.apply_japanese_result(item, result)

        self.assertEqual(item["title_ja"], original_title)
        for field in su.JA_AI_FIELDS:
            self.assertEqual(item[field], result[field])
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

        def fake_run(client, requests, *, timeout_seconds, logger):
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

    def test_daily_workflow_uses_batch_summarization(self):
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-update.yml"
        ).read_text(encoding="utf-8")
        command = next(
            line.strip() for line in workflow.splitlines()
            if "python scripts/summarize_updates.py" in line
        )
        self.assertIn("--batch", command)
        self.assertIn("--limit 100", command)
        self.assertIn("--api-limit 30", command)

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

            def fake_batch(client, model, candidates, *, timeout_seconds):
                self.assertEqual(len(candidates), 1)
                return "msgbatch_main", [(result, model)]

            patches = {
                "INPUT_PATH": input_path,
                "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json",
                "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path,
                "LOG_PATH": base / "summarize.log",
                "make_client": lambda: object(),
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

            def fake_japanese_batch(client, model, candidates, *, timeout_seconds):
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
                "make_client": lambda: object(),
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

            def fake_batch(client, model, candidates, *, timeout_seconds):
                self.assertEqual(len(candidates), 2)
                return "msgbatch_budget", [(result, model), (result, model)]

            patches = {
                "INPUT_PATH": input_path,
                "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json",
                "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path,
                "LOG_PATH": base / "summarize.log",
                "make_client": lambda: object(),
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

            def fake_english_batch(client, model, candidates, *, timeout_seconds):
                self.assertEqual([item["id"] for item, _raw in candidates], ["english"])
                return "msgbatch_en_mixed", [(english, model)]

            def fake_japanese_batch(client, model, candidates, *, timeout_seconds):
                self.assertEqual([item["id"] for item, _raw in candidates], ["cached"])
                return "msgbatch_ja_mixed", [(japanese, model)]

            patches = {
                "INPUT_PATH": input_path,
                "OUTPUT_PATH": input_path,
                "BEFORE_AI_PATH": base / "before_ai.json",
                "CACHE_PATH": cache_path,
                "RAW_PATH": raw_path,
                "LOG_PATH": base / "summarize.log",
                "make_client": lambda: object(),
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


if __name__ == "__main__":
    unittest.main()
