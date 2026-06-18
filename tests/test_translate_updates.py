"""Offline tests for scripts/translate_updates.py.

No network and no Anthropic SDK: API-path tests patch translate_updates.make_client
and translate_updates.request_translation. Cache / fallback tests use --no-api or
an unset API key. All file IO is redirected to a temp directory.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import translate_updates as tu  # noqa: E402

LOCALE = "zh-Hans"


def sample_item(**overrides):
    item = {
        "id": "raw-aaa111",
        "title_en": "Public Comment: Draft Amendment to Pharmacy Guidelines",
        "title_ja": "薬局調剤指針の一部改正（案）に関する意見募集について",
        "area": "Healthcare / Pharmaceuticals",
        "stage": "Public Comment Open",
        "impact_level": "Medium",
        "summary_en": "This is a public comment on a draft amendment.",
        "business_impact_en": "Pharmacies may be affected if adopted.",
        "recommended_action_en": "Review the official Japanese source.",
        "source_name": "e-Gov Public Comment (意見募集案件一覧)",
        "source_url": "https://public-comment.e-gov.go.jp/servlet/Public?id=1",
        "published_at": "2026-06-16",
        "last_checked": "2026-06-16",
        "relevance_score": 57.0,
        "summary_source": "claude",
        "confidence": "medium",
        "ai_notes": "Limited metadata.",
        "summarized_at": "2026-06-16T02:17:40Z",
        "summary_model": "claude-opus-4-8",
        "first_seen_at": "2026-06-16",
    }
    item.update(overrides)
    return item


def good_translation():
    return {
        "title": "公开征求意见：药店调剂指南修订草案",
        "summary": "这是针对一份修订草案的公开征求意见。",
        "business_impact": "若获通过，药店可能受到影响。",
        "recommended_action": "请查阅日文官方来源。",
    }


def make_cache_entry(item, locale=LOCALE, fields=None, **overrides):
    fields = fields or good_translation()
    entry = tu.cache_entry(
        tu.compute_source_hash(item, locale, tu.PROMPT_VERSION),
        tu.PROMPT_VERSION,
        "2026-06-17T00:00:00Z",
        "claude-opus-4-8",
        fields,
    )
    entry.update(overrides)
    return entry


class TranslateTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self._saved = {
            "INPUT_PATH": tu.INPUT_PATH,
            "OUTPUT_PATH": tu.OUTPUT_PATH,
            "CACHE_PATH": tu.CACHE_PATH,
            "LOG_PATH": tu.LOG_PATH,
            "make_client": tu.make_client,
            "request_translation": tu.request_translation,
        }
        self.input_path = base / "legal_updates.json"
        self.cache_path = base / "translation_cache.json"
        tu.INPUT_PATH = self.input_path
        tu.OUTPUT_PATH = self.input_path
        tu.CACHE_PATH = self.cache_path
        tu.LOG_PATH = base / "logs" / "translate.log"
        self._saved_key = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "test-key"

    def tearDown(self):
        for handler in list(tu.logger.handlers):
            handler.close()
        tu.logger.handlers.clear()
        for name, value in self._saved.items():
            setattr(tu, name, value)
        if self._saved_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._saved_key
        self._tmp.cleanup()

    # -- helpers --
    def write_input(self, items):
        self.input_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    def write_cache(self, cache):
        self.cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    def read_output(self):
        return json.loads(self.input_path.read_text(encoding="utf-8"))

    def read_cache(self):
        return json.loads(self.cache_path.read_text(encoding="utf-8"))

    def install_counting_api(self, fields=None):
        """Patch the API so each candidate returns a valid translation; count calls."""
        calls = {"n": 0}

        def fake_request(client, model, item, locale):
            calls["n"] += 1
            return (fields or good_translation()), "fake-model"

        tu.make_client = lambda: object()
        tu.request_translation = fake_request
        return calls


class TestHashAndValidation(unittest.TestCase):
    def test_source_hash_is_stable_and_excludes_id(self):
        a = sample_item(id="raw-1")
        b = sample_item(id="raw-2")  # different id, same English
        self.assertEqual(
            tu.compute_source_hash(a, LOCALE, tu.PROMPT_VERSION),
            tu.compute_source_hash(b, LOCALE, tu.PROMPT_VERSION),
        )

    def test_source_hash_changes_on_english_change(self):
        base = sample_item()
        for field in ("title_en", "summary_en", "business_impact_en", "recommended_action_en"):
            changed = sample_item(**{field: "Different English text."})
            with self.subTest(field=field):
                self.assertNotEqual(
                    tu.compute_source_hash(base, LOCALE, tu.PROMPT_VERSION),
                    tu.compute_source_hash(changed, LOCALE, tu.PROMPT_VERSION),
                )

    def test_source_hash_changes_on_locale_or_prompt_version(self):
        base = sample_item()
        self.assertNotEqual(
            tu.compute_source_hash(base, LOCALE, tu.PROMPT_VERSION),
            tu.compute_source_hash(base, "zh-Hant", tu.PROMPT_VERSION),
        )
        self.assertNotEqual(
            tu.compute_source_hash(base, LOCALE, tu.PROMPT_VERSION),
            tu.compute_source_hash(base, LOCALE, "zh-hans-v2"),
        )

    def test_valid_translation_accepts_good(self):
        self.assertTrue(tu.valid_translation(good_translation()))

    def test_valid_translation_rejects_empty_or_missing(self):
        bad = good_translation()
        bad["summary"] = "   "
        self.assertFalse(tu.valid_translation(bad))
        missing = good_translation()
        del missing["title"]
        self.assertFalse(tu.valid_translation(missing))

    def test_valid_translation_rejects_html_and_code_fence(self):
        html = good_translation()
        html["title"] = "<b>标题</b>"
        self.assertFalse(tu.valid_translation(html))
        fence = good_translation()
        fence["summary"] = "```代码```"
        self.assertFalse(tu.valid_translation(fence))

    def test_valid_translation_rejects_overlength_without_truncating(self):
        too_long = good_translation()
        too_long["title"] = "标" * (tu.FIELD_LIMITS["title"] + 1)
        self.assertFalse(tu.valid_translation(too_long))

    def test_valid_translation_ignores_extra_metadata_keys(self):
        entry = make_cache_entry(sample_item())  # carries metadata keys
        self.assertTrue(tu.valid_translation(entry))


class TestApplyRemove(unittest.TestCase):
    def test_apply_translation_sets_only_four_fields(self):
        item = sample_item()
        tu.apply_translation(item, good_translation(), LOCALE)
        block = item["translations"][LOCALE]
        self.assertEqual(set(block.keys()), set(tu.TRANSLATION_FIELDS))

    def test_remove_translation_drops_empty_block(self):
        item = sample_item()
        tu.apply_translation(item, good_translation(), LOCALE)
        tu.remove_translation(item, LOCALE)
        self.assertNotIn("translations", item)


class TestCacheHitBehaviour(TranslateTestBase):
    def test_cache_hit_applies_without_changing_translated_at_or_calling_api(self):
        item = sample_item()
        entry = make_cache_entry(item)
        self.write_input([item])
        self.write_cache({"schema_version": 1, "entries": {LOCALE: {item["id"]: entry}}})
        calls = self.install_counting_api()

        rc = tu.main(["--locale", LOCALE, "--limit", "30"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls["n"], 0, "cache hit must not call the API")

        out = self.read_output()[0]
        self.assertEqual(out["translations"][LOCALE]["title"], entry["title"])
        cache_entry_after = self.read_cache()["entries"][LOCALE][item["id"]]
        self.assertEqual(cache_entry_after["translated_at"], "2026-06-17T00:00:00Z")

    def test_cache_hit_does_not_consume_limit(self):
        cached_item = sample_item(id="raw-cached")
        candidate = sample_item(
            id="raw-candidate",
            title_en="Another English title that has no cache yet.",
        )
        entry = make_cache_entry(cached_item)
        self.write_input([cached_item, candidate])
        self.write_cache({"schema_version": 1, "entries": {LOCALE: {cached_item["id"]: entry}}})
        calls = self.install_counting_api()

        rc = tu.main(["--locale", LOCALE, "--limit", "1"])
        self.assertEqual(rc, 0)
        # Only the candidate consumes the single API call; the cache hit is free.
        self.assertEqual(calls["n"], 1)
        out = {it["id"]: it for it in self.read_output()}
        self.assertIn(LOCALE, out["raw-cached"]["translations"])
        self.assertIn(LOCALE, out["raw-candidate"]["translations"])


class TestLimitAndApi(TranslateTestBase):
    def test_limit_caps_new_api_calls(self):
        items = [
            sample_item(id=f"raw-{i}", title_en=f"Distinct English title number {i}.")
            for i in range(5)
        ]
        self.write_input(items)
        self.write_cache(tu.default_cache())
        calls = self.install_counting_api()

        rc = tu.main(["--locale", LOCALE, "--limit", "2"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls["n"], 2, "must stop after --limit new API calls")
        translated = [it for it in self.read_output() if LOCALE in it.get("translations", {})]
        self.assertEqual(len(translated), 2)

    def test_new_translation_is_cached_with_metadata(self):
        item = sample_item()
        self.write_input([item])
        self.write_cache(tu.default_cache())
        self.install_counting_api()

        tu.main(["--locale", LOCALE, "--limit", "30"])
        entry = self.read_cache()["entries"][LOCALE][item["id"]]
        self.assertEqual(entry["source_hash"], tu.compute_source_hash(item, LOCALE, tu.PROMPT_VERSION))
        self.assertEqual(entry["prompt_version"], tu.PROMPT_VERSION)
        self.assertEqual(entry["model"], "fake-model")
        self.assertTrue(entry["translated_at"])
        for field in tu.TRANSLATION_FIELDS:
            self.assertIn(field, entry)

    def test_invalid_api_result_keeps_english_fallback(self):
        item = sample_item()
        self.write_input([item])
        self.write_cache(tu.default_cache())
        tu.make_client = lambda: object()
        tu.request_translation = lambda *a, **k: ({"title": "x"}, "fake-model")  # missing fields

        rc = tu.main(["--locale", LOCALE, "--limit", "30"])
        self.assertEqual(rc, 0)
        out = self.read_output()[0]
        self.assertNotIn("translations", out)  # English fallback
        self.assertNotIn(item["id"], self.read_cache()["entries"][LOCALE])  # not cached


class TestStaleRemoval(TranslateTestBase):
    def test_stale_translation_removed_from_published_json(self):
        # Published item already carries a zh translation, but the English text has
        # changed since (no matching cache) -> the stale translation must be removed.
        item = sample_item()
        item["translations"] = {LOCALE: {
            "title": "陈旧标题",
            "summary": "陈旧摘要",
            "business_impact": "陈旧影响",
            "recommended_action": "陈旧建议",
        }}
        self.write_input([item])
        self.write_cache(tu.default_cache())  # no cache entry

        rc = tu.main(["--locale", LOCALE, "--limit", "30", "--no-api"])
        self.assertEqual(rc, 0)
        out = self.read_output()[0]
        self.assertNotIn("translations", out)

    def test_stale_cache_hash_mismatch_is_not_adopted(self):
        item = sample_item()
        stale_source = sample_item(summary_en="OLD English summary now changed.")
        entry = make_cache_entry(stale_source)  # hash computed from OLD English
        item["translations"] = {LOCALE: {k: entry[k] for k in tu.TRANSLATION_FIELDS}}
        self.write_input([item])
        self.write_cache({"schema_version": 1, "entries": {LOCALE: {item["id"]: entry}}})

        rc = tu.main(["--locale", LOCALE, "--limit", "30", "--no-api"])
        self.assertEqual(rc, 0)
        out = self.read_output()[0]
        self.assertNotIn("translations", out)


class TestNoApiAndNoKey(TranslateTestBase):
    def test_no_api_applies_valid_cache_only(self):
        item = sample_item()
        entry = make_cache_entry(item)
        self.write_input([item])
        self.write_cache({"schema_version": 1, "entries": {LOCALE: {item["id"]: entry}}})
        tu.make_client = lambda: (_ for _ in ()).throw(AssertionError("must not create client"))

        rc = tu.main(["--locale", LOCALE, "--limit", "30", "--no-api"])
        self.assertEqual(rc, 0)
        out = self.read_output()[0]
        self.assertEqual(out["translations"][LOCALE]["summary"], entry["summary"])

    def test_missing_api_key_applies_cache_and_exits_zero(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        item = sample_item()
        entry = make_cache_entry(item)
        self.write_input([item])
        self.write_cache({"schema_version": 1, "entries": {LOCALE: {item["id"]: entry}}})
        tu.make_client = lambda: (_ for _ in ()).throw(AssertionError("must not create client without key"))

        rc = tu.main(["--locale", LOCALE, "--limit", "30"])
        self.assertEqual(rc, 0)
        out = self.read_output()[0]
        self.assertIn(LOCALE, out["translations"])


class TestInvariants(TranslateTestBase):
    def test_metadata_and_english_canonical_unchanged(self):
        item = sample_item()
        before = json.loads(json.dumps(item))
        self.write_input([item])
        self.write_cache(tu.default_cache())
        self.install_counting_api()

        tu.main(["--locale", LOCALE, "--limit", "30"])
        out = self.read_output()[0]
        protected = (
            "id", "title_en", "title_ja", "source_name", "source_url",
            "area", "stage", "impact_level", "published_at", "last_checked",
            "first_seen_at", "relevance_score", "summary_source", "confidence",
            "ai_notes", "summarized_at", "summary_model",
            "summary_en", "business_impact_en", "recommended_action_en",
        )
        for key in protected:
            with self.subTest(field=key):
                self.assertEqual(out[key], before[key])
        # The only addition is the optional translations block.
        self.assertEqual(set(out) - set(before), {"translations"})

    def test_cache_structure_is_entries_locale_itemid(self):
        item = sample_item()
        self.write_input([item])
        self.write_cache(tu.default_cache())
        self.install_counting_api()

        tu.main(["--locale", LOCALE, "--limit", "30"])
        cache = self.read_cache()
        self.assertEqual(cache["schema_version"], 1)
        self.assertIn(LOCALE, cache["entries"])
        self.assertIn(item["id"], cache["entries"][LOCALE])


class TestWorkflowTranslateStep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-update.yml"
        ).read_text(encoding="utf-8")

    def test_translate_step_present_with_locale_and_limit(self):
        self.assertIn("name: Translate Simplified Chinese updates", self.workflow)
        self.assertIn("python scripts/translate_updates.py --locale zh-Hans --limit 30", self.workflow)

    def test_translate_runs_after_summarize_and_before_check_changes(self):
        summarize_pos = self.workflow.index("name: Summarize top updates")
        translate_pos = self.workflow.index("name: Translate Simplified Chinese updates")
        check_pos = self.workflow.index("name: Check data changes")
        self.assertLess(summarize_pos, translate_pos)
        self.assertLess(translate_pos, check_pos)

    def test_translate_compiled_in_offline_gate(self):
        self.assertIn("scripts/translate_updates.py", self.workflow.split("name: Fetch raw updates")[0])

    def test_translation_cache_in_diff_and_commit(self):
        # Appears in both the change check and the git add list.
        self.assertGreaterEqual(self.workflow.count("data/translation_cache.json"), 2)

    def test_api_key_only_exposed_to_summarize_and_translate(self):
        self.assertEqual(
            self.workflow.count("ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}"), 2
        )

    def test_source_health_and_action_pins_preserved(self):
        self.assertIn("uses: actions/checkout@v5", self.workflow)
        self.assertIn("uses: actions/setup-python@v6", self.workflow)
        self.assertIn("permissions:\n  contents: write", self.workflow)
        fetch_pos = self.workflow.index("name: Fetch raw updates")
        evaluate_pos = self.workflow.index("name: Evaluate source health")
        build_pos = self.workflow.index("name: Build public data")
        commit_pos = self.workflow.index("name: Commit and push updated data")
        gate_pos = self.workflow.index("name: Enforce source health gate")
        self.assertLess(fetch_pos, evaluate_pos)
        self.assertLess(evaluate_pos, build_pos)
        self.assertLess(commit_pos, gate_pos)
        self.assertIn("github.event_name == 'schedule'", self.workflow)


if __name__ == "__main__":
    unittest.main()
