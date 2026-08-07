"""Offline tests for scripts/translate_updates.py.

No network and no Anthropic SDK: API-path tests patch translate_updates.make_client
and translate_updates.request_translation. Cache / fallback tests use --no-api or
an unset API key. All file IO is redirected to a temp directory.
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
        "comment_deadline": "2026-07-18T23:59:00+09:00",
        "comment_deadline_source": "related_egov_item",
        "comment_deadline_source_id": "raw-egov-source",
        "comment_deadline_inherited": True,
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
            "request_translation_batch": tu.request_translation_batch,
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

    def install_api_returning(self, title):
        """Patch the API so each candidate returns a good body but a given title."""
        return self.install_api_returning_fields(title=title)

    def install_api_returning_fields(self, **overrides):
        """Patch the API so each candidate returns good_translation() with overrides."""
        calls = {"n": 0}

        def fake_request(client, model, item, locale):
            calls["n"] += 1
            d = good_translation()
            d.update(overrides)
            return d, "fake-model"

        tu.make_client = lambda: object()
        tu.request_translation = fake_request
        return calls

    def run_main(self, argv):
        """Run tu.main capturing stdout; return (rc, stdout)."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = tu.main(argv)
        return rc, buf.getvalue()


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
            tu.compute_source_hash(base, LOCALE, "zh-hans-v1"),
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


class TestTranslationModelSelection(unittest.TestCase):
    def test_default_translation_model_is_haiku_45(self):
        self.assertEqual(tu.DEFAULT_TRANSLATION_MODEL, "claude-haiku-4-5-20251001")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(tu.resolve_model(None), "claude-haiku-4-5-20251001")

    def test_translation_model_env_override(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_TRANSLATION_MODEL": "env-model"}, clear=True):
            self.assertEqual(tu.resolve_model(None), "env-model")

    def test_cli_model_overrides_translation_env(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_TRANSLATION_MODEL": "env-model"}, clear=True):
            self.assertEqual(tu.resolve_model("cli-model"), "cli-model")

    def test_generic_anthropic_model_does_not_override_translation_default(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_MODEL": "legacy-high-model"}, clear=True):
            self.assertEqual(tu.resolve_model(None), "claude-haiku-4-5-20251001")

    def test_model_change_does_not_change_source_hash(self):
        item = sample_item()
        before = tu.compute_source_hash(item, LOCALE, tu.PROMPT_VERSION)
        with mock.patch.dict(os.environ, {"ANTHROPIC_TRANSLATION_MODEL": "other-model"}, clear=True):
            self.assertEqual(tu.compute_source_hash(item, LOCALE, tu.PROMPT_VERSION), before)


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

    def test_batch_mode_submits_only_budgeted_misses_and_applies_results(self):
        cached = sample_item(id="raw-cached", title_en="Already cached title.")
        candidates = [
            sample_item(id=f"raw-{i}", title_en=f"Distinct English title {i}.")
            for i in range(3)
        ]
        self.write_input([cached, *candidates])
        self.write_cache({
            "schema_version": 1,
            "entries": {LOCALE: {cached["id"]: make_cache_entry(cached)}},
        })
        captured = {}

        def fake_batch(client, model, items, locale, *, timeout_seconds):
            captured["ids"] = [item["id"] for item in items]
            captured["timeout"] = timeout_seconds
            return "msgbatch_test", [(good_translation(), "fake-batch-model") for _ in items]

        tu.make_client = lambda: object()
        tu.request_translation_batch = fake_batch
        rc, out = self.run_main(["--locale", LOCALE, "--limit", "2", "--batch"])

        self.assertEqual(rc, 0)
        self.assertEqual(captured["ids"], ["raw-0", "raw-1"])
        translated = [it for it in self.read_output() if LOCALE in it.get("translations", {})]
        self.assertEqual(len(translated), 3)  # one cache hit + two batch results
        summary = parse_summary(out)
        self.assertEqual(summary["batch_mode"], "true")
        self.assertEqual(summary["batch_id"], "msgbatch_test")
        self.assertEqual(summary["api_calls"], "2")
        self.assertEqual(summary["skipped_no_budget"], "1")

    def test_batch_creation_credit_failure_is_counted_once_and_aborts_unbilled_items(self):
        items = [sample_item(id=f"raw-{i}", title_en=f"Title {i}.") for i in range(3)]
        self.write_input(items)
        self.write_cache(tu.default_cache())

        def fake_batch(*args, **kwargs):
            raise make_provider_error(CREDIT_MESSAGE, 402)

        tu.make_client = lambda: object()
        tu.request_translation_batch = fake_batch
        rc, out = self.run_main([
            "--locale", LOCALE, "--limit", "3", "--batch",
            "--provider-failure-mode", "warn",
        ])

        summary = parse_summary(out)
        self.assertEqual(rc, 0)
        self.assertEqual(summary["api_calls"], "0")
        self.assertEqual(summary["failed_items"], "1")
        self.assertEqual(summary["provider_aborted_items"], "3")
        self.assertEqual(summary["provider_error_type"], "insufficient_credit")
        self.assertEqual(out.count("PROVIDER_UNAVAILABLE"), 1)

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

    def test_cache_entry_records_selected_model_when_provider_echoes_it(self):
        item = sample_item()
        self.write_input([item])
        self.write_cache(tu.default_cache())

        def fake(client, model, item, locale):
            return good_translation(), model

        tu.make_client = lambda: object()
        tu.request_translation = fake

        rc, _ = self.run_main(["--locale", LOCALE, "--limit", "30", "--model", "cli-model"])

        self.assertEqual(rc, 0)
        entry = self.read_cache()["entries"][LOCALE][item["id"]]
        self.assertEqual(entry["model"], "cli-model")

    def test_summary_prints_selected_model(self):
        item = sample_item()
        self.write_input([item])
        self.write_cache(tu.default_cache())

        rc, out = self.run_main(["--locale", LOCALE, "--limit", "30", "--no-api", "--model", "summary-model"])

        self.assertEqual(rc, 0)
        self.assertEqual(parse_summary(out)["model"], "summary-model")

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
            "first_seen_at", "comment_deadline", "comment_deadline_source",
            "comment_deadline_source_id", "comment_deadline_inherited",
            "relevance_score", "summary_source", "confidence",
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


def parse_summary(stdout):
    """Parse the 'label : value' summary block into a dict (spacing-robust)."""
    d = {}
    for line in stdout.splitlines():
        if ":" in line and not line.startswith("("):
            key, value = line.split(":", 1)
            d[key.strip()] = value.strip()
    return d


# A clean 90-character title: a period-10 sequence has no 2-8 char immediate
# self-repeat, no kana, no brackets, and no stage phrase, so it is otherwise valid.
VALID_90 = "甲乙丙丁戊己庚辛壬癸" * 9


class TestPromptVersionV3(TranslateTestBase):
    def test_prompt_version_is_v3(self):
        self.assertEqual(tu.PROMPT_VERSION, "zh-hans-v3")

    def test_source_hash_changes_between_v2_and_v3(self):
        item = sample_item()
        self.assertNotEqual(
            tu.compute_source_hash(item, LOCALE, "zh-hans-v2"),
            tu.compute_source_hash(item, LOCALE, tu.PROMPT_VERSION),
        )

    def test_older_version_cache_entry_is_a_miss(self):
        # A v1 or v2 entry (and any stale published translation) is removed under v3.
        for old_version in ("zh-hans-v1", "zh-hans-v2"):
            with self.subTest(version=old_version):
                item = sample_item()
                old_entry = {
                    "source_hash": tu.compute_source_hash(item, LOCALE, old_version),
                    "prompt_version": old_version,
                    "translated_at": "2026-06-18T00:00:00Z",
                    "model": "claude-opus-4-8",
                    **{k: good_translation()[k] for k in tu.TRANSLATION_FIELDS},
                }
                item["translations"] = {LOCALE: {k: old_entry[k] for k in tu.TRANSLATION_FIELDS}}
                self.write_input([item])
                self.write_cache({"schema_version": 1, "entries": {LOCALE: {item["id"]: old_entry}}})
                rc, _ = self.run_main(["--locale", LOCALE, "--limit", "30", "--no-api"])
                self.assertEqual(rc, 0)
                self.assertNotIn("translations", self.read_output()[0])

    def test_v3_cache_entry_is_a_hit(self):
        item = sample_item()
        v3_entry = make_cache_entry(item)  # built with PROMPT_VERSION (v3) + valid title
        self.write_input([item])
        self.write_cache({"schema_version": 1, "entries": {LOCALE: {item["id"]: v3_entry}}})
        calls = self.install_counting_api()
        rc, _ = self.run_main(["--locale", LOCALE, "--limit", "30"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls["n"], 0, "v3 cache entry must be a hit (no API call)")
        self.assertIn(LOCALE, self.read_output()[0]["translations"])


class TestSourceHashV3(unittest.TestCase):
    def test_japanese_context_changes_hash(self):
        base = sample_item()
        for field in ("title_ja", "stage", "source_name"):
            changed = sample_item(**{field: "DIFFERENT VALUE"})
            with self.subTest(field=field):
                self.assertNotEqual(
                    tu.compute_source_hash(base, LOCALE, tu.PROMPT_VERSION),
                    tu.compute_source_hash(changed, LOCALE, tu.PROMPT_VERSION),
                )

    def test_hash_stable_when_english_and_context_unchanged(self):
        self.assertEqual(
            tu.compute_source_hash(sample_item(id="raw-1"), LOCALE, tu.PROMPT_VERSION),
            tu.compute_source_hash(sample_item(id="raw-2"), LOCALE, tu.PROMPT_VERSION),
        )


class TestPromptContext(TranslateTestBase):
    def test_reference_context_included_but_marked_reference_only(self):
        item = sample_item()
        content = tu.build_user_content(item, LOCALE)
        self.assertIn(item["title_ja"], content)
        self.assertIn(item["stage"], content)
        self.assertIn(item["source_name"], content)
        self.assertIn("REFERENCE_CONTEXT", content)
        self.assertIn("do NOT return", content)
        self.assertIn("UNTRUSTED_ENGLISH_JSON", content)

    def test_api_metadata_is_not_accepted(self):
        item = sample_item()
        original_ja = item["title_ja"]
        self.write_input([item])
        self.write_cache(tu.default_cache())

        def fake(client, model, it, locale):
            d = good_translation()
            d["title_ja"] = "HACKED"  # the model tries to return reference fields
            d["stage"] = "HACKED"
            d["source_name"] = "HACKED"
            return d, "fake-model"

        tu.make_client = lambda: object()
        tu.request_translation = fake

        rc, _ = self.run_main(["--locale", LOCALE, "--limit", "30"])
        self.assertEqual(rc, 0)
        out = self.read_output()[0]
        self.assertEqual(out["title_ja"], original_ja)  # not overwritten
        self.assertEqual(set(out["translations"][LOCALE]), set(tu.TRANSLATION_FIELDS))  # only 4 keys
        entry = self.read_cache()["entries"][LOCALE][item["id"]]
        self.assertNotIn("stage", entry)
        self.assertNotIn("source_name", entry)


class TestGlossaryV3(unittest.TestCase):
    def test_japan_specific_terms_present(self):
        values = set(tu.GLOSSARY_ZH_HANS.values())
        self.assertIn("特定外来生物造成生态系统等损害防止法", values)
        self.assertIn("育成就业", values)  # Training and Employment
        self.assertTrue(any("育成就业" in v for v in values))

    def test_known_mistranslation_not_used(self):
        self.assertFalse(any("开发与雇佣" in v for v in tu.GLOSSARY_ZH_HANS.values()))

    def test_prior_terms_preserved(self):
        values = set(tu.GLOSSARY_ZH_HANS.values())
        self.assertIn("课征金缴纳命令", values)
        self.assertIn("长期优良住宅", values)


class TestTitleQualityV3(unittest.TestCase):
    def test_known_mistranslated_statute_titles_invalid(self):
        self.assertFalse(tu.valid_title("公开征求意见：《外国人开发与雇佣适当实施及保护法》修订草案"))
        self.assertFalse(tu.valid_title("公开征求意见：外来入侵物种法施行令修订草案"))

    def test_preferred_statute_titles_valid(self):
        self.assertTrue(tu.valid_title("公开征求意见：《特定外来生物造成生态系统等损害防止法》施行令修订草案"))
        self.assertTrue(
            tu.valid_title("公开征求意见：《外国人育成就业适当实施及育成就业外国人保护法》林业领域标准告示草案")
        )


class TestDateNormalization(TranslateTestBase):
    def test_slash_and_dot_dates_normalized(self):
        self.assertEqual(tu.normalize_dates("截止日期为2026/07/16。"), "截止日期为2026-07-16。")
        self.assertEqual(tu.normalize_dates("2026.07.16"), "2026-07-16")
        self.assertEqual(tu.normalize_dates("2026/7/6"), "2026-07-06")

    def test_ambiguous_format_unchanged(self):
        self.assertEqual(tu.normalize_dates("07/16/2026"), "07/16/2026")
        self.assertEqual(tu.normalize_dates("no date here"), "no date here")

    def test_applied_to_translation_only_not_metadata(self):
        item = sample_item()
        before = json.loads(json.dumps(item))
        self.write_input([item])
        self.write_cache(tu.default_cache())
        self.install_api_returning_fields(summary="公示截止日期为2026/07/16，请留意。")

        rc, _ = self.run_main(["--locale", LOCALE, "--limit", "30"])
        self.assertEqual(rc, 0)
        out = self.read_output()[0]
        self.assertEqual(out["translations"][LOCALE]["summary"], "公示截止日期为2026-07-16，请留意。")
        # Metadata dates and the Japanese original are untouched.
        self.assertEqual(out["published_at"], before["published_at"])
        self.assertEqual(out["last_checked"], before["last_checked"])
        self.assertEqual(out["title_ja"], before["title_ja"])


class TestTitleQuality(unittest.TestCase):
    VALID = "公开征求意见：公寓管理与长期优良住宅相关施行规则修订草案"

    def test_normal_title_valid(self):
        self.assertTrue(tu.valid_title(self.VALID))

    def test_90_chars_valid_91_invalid(self):
        self.assertEqual(len(VALID_90), 90)
        self.assertTrue(tu.valid_title(VALID_90))
        self.assertFalse(tu.valid_title(VALID_90 + "甲"))

    def test_empty_invalid(self):
        self.assertFalse(tu.valid_title("   "))

    def test_ellipsis_invalid(self):
        self.assertFalse(tu.valid_title("公开征求意见：施行规则修订草案……"))
        self.assertFalse(tu.valid_title("公开征求意见：施行规则修订草案..."))

    def test_fragment_duplication_invalid(self):
        self.assertFalse(tu.valid_title("公开征求意见：公寓管理及长期优良住宅的施行规则、则的省令草案"))

    def test_word_duplication_invalid(self):
        self.assertFalse(tu.valid_title("公开征求意见：施行规则修订修订草案"))
        self.assertFalse(tu.valid_title("公开征求意见：施行规则草案草案"))

    def test_stage_phrase_repetition_invalid(self):
        self.assertFalse(tu.valid_title("关于公寓管理的公开征求意见，公开征求意见：施行规则修订草案"))

    def test_single_results_phrase_valid(self):
        self.assertTrue(tu.valid_title("公开征求意见结果：电气通信事业法施行规则修订"))

    def test_kana_invalid(self):
        self.assertFalse(tu.valid_title("公开征求意见：あ施行规则修订草案"))  # hiragana
        self.assertFalse(tu.valid_title("公开征求意见：ア施行规则修订草案"))  # katakana
        self.assertFalse(tu.valid_title("公开征求意见：ｱ施行规则修订草案"))  # half-width katakana

    def test_unbalanced_brackets_invalid(self):
        self.assertFalse(tu.valid_title("公开征求意见：《电气通信事业法施行规则修订草案"))
        self.assertFalse(tu.valid_title("公开征求意见：（施行规则修订草案"))

    def test_newline_invalid(self):
        self.assertFalse(tu.valid_title("公开征求意见：施行规则\n修订草案"))

    def test_html_and_markdown_invalid(self):
        self.assertFalse(tu.valid_title("<b>公开征求意见</b>：施行规则修订草案"))
        self.assertFalse(tu.valid_title("```公开征求意见：施行规则修订草案```"))

    def test_legitimate_compounds_not_false_flagged(self):
        for title in (
            "公开征求意见：信息通信技术相关施行规则修订草案",
            "公开征求意见：个人信息保护相关施行规则修订草案",
            "公开征求意见：行政机关相关施行令修订草案",
        ):
            with self.subTest(title=title):
                self.assertTrue(tu.valid_title(title))


class TestTitleQualityFallback(TranslateTestBase):
    BAD_TITLE = "公开征求意见：公寓管理及长期优良住宅的施行规则、则的省令草案"

    def test_bad_title_not_applied_or_cached(self):
        item = sample_item()
        self.write_input([item])
        self.write_cache(tu.default_cache())
        self.install_api_returning(self.BAD_TITLE)

        rc, out = self.run_main(["--locale", LOCALE, "--limit", "30"])
        self.assertEqual(rc, 0)
        self.assertNotIn("translations", self.read_output()[0])  # English fallback
        self.assertNotIn(item["id"], self.read_cache()["entries"][LOCALE])  # not cached
        summary = parse_summary(out)
        self.assertEqual(summary["quality_rejected_items"], "1")
        self.assertEqual(summary["failed_items"], "0")  # not double-counted

    def test_bad_title_keeps_english_canonical(self):
        item = sample_item()
        before = json.loads(json.dumps(item))
        self.write_input([item])
        self.write_cache(tu.default_cache())
        self.install_api_returning(self.BAD_TITLE)

        self.run_main(["--locale", LOCALE, "--limit", "30"])
        out = self.read_output()[0]
        for key in ("title_en", "summary_en", "business_impact_en", "recommended_action_en"):
            self.assertEqual(out[key], before[key])

    def test_processing_continues_after_bad_title(self):
        bad = sample_item(id="raw-bad")
        good = sample_item(id="raw-good", title_en="A different English title.")
        self.write_input([bad, good])
        self.write_cache(tu.default_cache())

        def fake(client, model, item, locale):
            d = good_translation()
            if item.get("id") == "raw-bad":
                d["title"] = self.BAD_TITLE
            return d, "fake-model"

        tu.make_client = lambda: object()
        tu.request_translation = fake

        rc, _ = self.run_main(["--locale", LOCALE, "--limit", "30"])
        self.assertEqual(rc, 0)
        out = {it["id"]: it for it in self.read_output()}
        self.assertNotIn("translations", out["raw-bad"])
        self.assertIn(LOCALE, out["raw-good"]["translations"])

    def test_stale_v1_removed_and_counted(self):
        item = sample_item()
        item["translations"] = {LOCALE: {k: good_translation()[k] for k in tu.TRANSLATION_FIELDS}}
        v1_entry = {
            "source_hash": tu.compute_source_hash(item, LOCALE, "zh-hans-v1"),
            "prompt_version": "zh-hans-v1",
            "translated_at": "2026-06-18T00:00:00Z",
            "model": "claude-opus-4-8",
            **{k: good_translation()[k] for k in tu.TRANSLATION_FIELDS},
        }
        self.write_input([item])
        self.write_cache({"schema_version": 1, "entries": {LOCALE: {item["id"]: v1_entry}}})

        rc, out = self.run_main(["--locale", LOCALE, "--limit", "30", "--no-api"])
        self.assertEqual(rc, 0)
        self.assertNotIn("translations", self.read_output()[0])
        self.assertEqual(parse_summary(out)["stale_translations_removed"], "1")


class TestRegressionV2(unittest.TestCase):
    def test_default_limit_is_30(self):
        self.assertEqual(tu.DEFAULT_LIMIT, 30)

    def test_field_caps_unchanged_for_body(self):
        self.assertEqual(tu.FIELD_LIMITS["summary"], 800)
        self.assertEqual(tu.FIELD_LIMITS["business_impact"], 500)
        self.assertEqual(tu.FIELD_LIMITS["recommended_action"], 500)

    def test_title_cap_is_90(self):
        self.assertEqual(tu.TITLE_MAX_CHARS, 90)
        self.assertEqual(tu.FIELD_LIMITS["title"], 90)


def valid_cache_entry(source_hash="h1", prompt_version=None):
    entry = good_translation()
    entry.update(
        source_hash=source_hash,
        prompt_version=prompt_version or tu.PROMPT_VERSION,
        translated_at="2026-06-18T00:00:00Z",
        model="m",
    )
    return entry


class TestCandidateReason(unittest.TestCase):
    def test_missing_cache(self):
        self.assertEqual(tu.candidate_reason(None, "h", tu.PROMPT_VERSION), "missing_cache")

    def test_hash_mismatch_on_hash(self):
        entry = valid_cache_entry(source_hash="other")
        self.assertEqual(tu.candidate_reason(entry, "h", tu.PROMPT_VERSION), "hash_mismatch")

    def test_hash_mismatch_on_prompt_version(self):
        entry = valid_cache_entry(source_hash="h", prompt_version="zh-hans-v2")
        self.assertEqual(tu.candidate_reason(entry, "h", tu.PROMPT_VERSION), "hash_mismatch")

    def test_invalid_cache(self):
        entry = valid_cache_entry(source_hash="h")
        del entry["summary"]  # field validation fails
        self.assertEqual(tu.candidate_reason(entry, "h", tu.PROMPT_VERSION), "invalid_cache")

    def test_invalid_title(self):
        entry = valid_cache_entry(source_hash="h")
        entry["title"] = "公开征求意见：外来入侵物种法施行令修订草案"  # known mistranslation
        self.assertEqual(tu.candidate_reason(entry, "h", tu.PROMPT_VERSION), "invalid_title")

    def test_valid_entry_is_not_a_candidate(self):
        entry = valid_cache_entry(source_hash="h")
        self.assertIsNone(tu.candidate_reason(entry, "h", tu.PROMPT_VERSION))


class TestIntegrityGate(unittest.TestCase):
    def _healthy(self, **overrides):
        stats = {
            "translated_items": 30,
            "new_cache_entries": 30,
            "updated_cache_entries": 0,
            "published_added": 30,
            "published_updated": 0,
            "cache_entries_before": 133,
            "cache_entries_after": 163,
            "items_with_locale": 163,
            "published_after": 163,
            "saved_published_count": 163,
            "saved_cache_count": 163,
        }
        stats.update(overrides)
        return stats

    def test_healthy_run_passes(self):
        self.assertEqual(tu.integrity_gate_failures(self._healthy()), [])

    def test_translated_but_nothing_persisted_fails(self):
        bad = self._healthy(
            new_cache_entries=0, updated_cache_entries=0, published_added=0, published_updated=0,
            cache_entries_after=133, published_after=133, items_with_locale=133,
            saved_published_count=133, saved_cache_count=133,
        )
        failures = tu.integrity_gate_failures(bad)
        self.assertTrue(failures)
        self.assertTrue(any("no new/updated cache" in f for f in failures))
        self.assertTrue(any("no published translations" in f for f in failures))

    def test_cache_shrink_fails(self):
        self.assertTrue(tu.integrity_gate_failures(self._healthy(cache_entries_after=100)))

    def test_saved_count_mismatch_fails(self):
        self.assertTrue(tu.integrity_gate_failures(self._healthy(saved_cache_count=133)))


def _cached_item(i):
    item = sample_item(id=f"raw-c{i}", title_en=f"Cached English title number {i}.")
    item["translations"] = {LOCALE: {k: good_translation()[k] for k in tu.TRANSLATION_FIELDS}}
    return item


class TestSaveIntegrityCounters(TranslateTestBase):
    def test_new_cache_entries_before_133_after_163(self):
        cached = [_cached_item(i) for i in range(133)]
        candidates = [
            sample_item(id=f"raw-n{i}", title_en=f"New English title number {i}.")
            for i in range(30)
        ]
        cache = tu.default_cache()
        for it in cached:
            cache["entries"][LOCALE][it["id"]] = make_cache_entry(it)
        self.write_input(cached + candidates)
        self.write_cache(cache)
        self.install_counting_api()

        rc, out = self.run_main(["--locale", LOCALE, "--limit", "30"])
        self.assertEqual(rc, 0)
        s = parse_summary(out)
        self.assertEqual(s["cache_entries_before"], "133")
        self.assertEqual(s["cache_entries_after"], "163")
        self.assertEqual(s["new_cache_entries"], "30")
        self.assertEqual(s["translated_items"], "30")
        self.assertEqual(s["published_added"], "30")

    def test_updated_one_when_english_changed(self):
        item = sample_item(id="raw-x", title_en="NEW English canonical title.")
        stale = sample_item(id="raw-x", title_en="OLD English canonical title.")
        cache = tu.default_cache()
        cache["entries"][LOCALE]["raw-x"] = make_cache_entry(stale)  # hash from OLD English
        item["translations"] = {LOCALE: {
            "title": "旧标题", "summary": "旧摘要",
            "business_impact": "旧影响", "recommended_action": "旧建议",
        }}
        self.write_input([item])
        self.write_cache(cache)
        self.install_counting_api()

        rc, out = self.run_main(["--locale", LOCALE, "--limit", "30"])
        self.assertEqual(rc, 0)
        s = parse_summary(out)
        self.assertEqual(s["new_cache_entries"], "0")
        self.assertEqual(s["updated_cache_entries"], "1")
        self.assertEqual(s["translated_items"], "1")
        self.assertEqual(s["published_updated"], "1")

    def test_cache_hit_makes_no_semantic_change(self):
        item = sample_item()
        entry = make_cache_entry(item)
        item["translations"] = {LOCALE: {k: entry[k] for k in tu.TRANSLATION_FIELDS}}
        self.write_input([item])
        self.write_cache({"schema_version": 1, "entries": {LOCALE: {item["id"]: entry}}})
        self.install_counting_api()

        rc, out = self.run_main(["--locale", LOCALE, "--limit", "30"])
        self.assertEqual(rc, 0)
        s = parse_summary(out)
        self.assertEqual(s["cache_hits"], "1")
        self.assertEqual(s["translated_items"], "0")  # cache hit not counted as translated
        self.assertEqual(s["new_cache_entries"], "0")
        self.assertEqual(s["updated_cache_entries"], "0")
        self.assertEqual(s["published_updated"], "0")

    def test_main_returns_1_when_integrity_gate_trips(self):
        item = sample_item()
        self.write_input([item])
        self.write_cache(tu.default_cache())
        self.install_counting_api()
        original = tu.integrity_gate_failures
        tu.integrity_gate_failures = lambda stats: ["forced failure for test"]
        try:
            rc, out = self.run_main(["--locale", LOCALE, "--limit", "30"])
        finally:
            tu.integrity_gate_failures = original
        self.assertEqual(rc, 1)
        self.assertIn("INTEGRITY GATE FAILED", out)


class TestBackfillWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        cls.backfill = (root / "translation-backfill.yml").read_text(encoding="utf-8")
        cls.daily = (root / "daily-update.yml").read_text(encoding="utf-8")

    def test_backfill_has_no_fetch_build_summarize_or_health(self):
        for forbidden in ("fetch_updates", "build_public_data", "summarize_updates", "source_health"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.backfill)

    def test_backfill_is_manual_only(self):
        self.assertIn("workflow_dispatch:", self.backfill)
        self.assertNotIn("schedule:", self.backfill)

    def test_backfill_commit_targets_translation_files_and_browser_archives(self):
        self.assertIn(
            "git add data/translation_cache.json docs/data/legal_updates.json "
            "docs/data/legal_updates_manifest.json docs/data/archive",
            self.backfill,
        )
        for forbidden in ("data/raw_items.json", "data/summary_cache.json", "data/source_health_state.json"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.backfill)

    def test_backfill_requests_pages_only_after_a_translation_commit(self):
        self.assertIn("permissions:\n  contents: write\n  pages: write", self.backfill)
        commit_pos = self.backfill.index("name: Commit and push translations and archives")
        pages_pos = self.backfill.index("name: Request Pages build for committed translations")
        self.assertLess(commit_pos, pages_pos)
        self.assertIn("id: translation_commit", self.backfill)
        self.assertIn(
            "steps.translation_commit.outputs.committed == 'true'", self.backfill
        )
        self.assertIn("GH_TOKEN: ${{ github.token }}", self.backfill)
        self.assertIn('"repos/${GITHUB_REPOSITORY}/pages/builds"', self.backfill)

    def test_backfill_integrity_gate_allows_cache_only_history(self):
        self.assertIn("cache-only stale/history entries allowed", self.backfill)
        self.assertIn("translator.compute_source_hash", self.backfill)
        self.assertIn("translator.valid_translation", self.backfill)
        self.assertIn("translator.valid_title", self.backfill)
        self.assertIn("published/cache content mismatch", self.backfill)
        self.assertNotIn('if [ "$after_cache" != "$after_pub" ]', self.backfill)

    def test_backfill_runs_translate_with_limit_30(self):
        self.assertIn(
            "python scripts/translate_updates.py --locale zh-Hans --limit 30", self.backfill
        )

    def test_backfill_rebuilds_yearly_archives_after_translation(self):
        translate_pos = self.backfill.index("name: Translate Simplified Chinese updates")
        archive_pos = self.backfill.index("name: Build yearly public archives")
        commit_pos = self.backfill.index("name: Commit and push translations and archives")
        self.assertLess(translate_pos, archive_pos)
        self.assertLess(archive_pos, commit_pos)
        self.assertIn("python scripts/build_public_archives.py", self.backfill)

    def test_backfill_sets_translation_model(self):
        self.assertIn("ANTHROPIC_TRANSLATION_MODEL: claude-sonnet-5", self.backfill)

    def test_backfill_presync_and_no_force_push(self):
        self.assertIn("git fetch origin main", self.backfill)
        self.assertNotIn("--force", self.backfill)
        self.assertNotIn("push -f", self.backfill)

    def test_action_pins_preserved(self):
        self.assertIn("uses: actions/checkout@v5", self.backfill)
        self.assertIn("uses: actions/setup-python@v6", self.backfill)

    def test_both_workflows_share_concurrency_group(self):
        for name, wf in (("backfill", self.backfill), ("daily", self.daily)):
            with self.subTest(workflow=name):
                self.assertIn("group: japan-legal-reform-data-writer", wf)
                self.assertIn("cancel-in-progress: false", wf)


def make_provider_error(message="", status_code=None):
    """A provider-like exception carrying an HTTP status_code (and message)."""
    exc = Exception(message)
    if status_code is not None:
        exc.status_code = status_code
    return exc


CREDIT_MESSAGE = "Your credit balance is too low to access the Anthropic API"


class TestProviderErrorClassification(unittest.TestCase):
    def test_insufficient_credit(self):
        self.assertEqual(
            tu.classify_provider_error(make_provider_error(CREDIT_MESSAGE, 400)),
            "insufficient_credit",
        )

    def test_authentication_error(self):
        self.assertEqual(tu.classify_provider_error(make_provider_error("nope", 401)), "authentication_error")

    def test_permission_error(self):
        self.assertEqual(tu.classify_provider_error(make_provider_error("nope", 403)), "permission_error")

    def test_rate_limit_is_not_fatal(self):
        rl = tu.classify_provider_error(make_provider_error("slow down", 429))
        self.assertEqual(rl, "rate_limit")
        self.assertNotIn(rl, tu.FATAL_PROVIDER_ERRORS)

    def test_temporary_server_error(self):
        self.assertEqual(tu.classify_provider_error(make_provider_error("oops", 503)), "temporary_server_error")

    def test_network_error(self):
        self.assertEqual(tu.classify_provider_error(ConnectionError("down")), "network_error")

    def test_unknown_provider_error_for_plain_400(self):
        self.assertEqual(tu.classify_provider_error(make_provider_error("bad field", 400)), "unknown_provider_error")

    def test_item_validation_error(self):
        self.assertEqual(tu.classify_error(ValueError("bad json")), "item_validation_error")

    def test_fatal_set_is_exactly_the_three(self):
        self.assertEqual(
            set(tu.FATAL_PROVIDER_ERRORS),
            {"insufficient_credit", "authentication_error", "permission_error"},
        )


class TestProviderFailFast(TranslateTestBase):
    def _install_credit_failure(self):
        calls = {"n": 0}

        def fake(client, model, item, locale):
            calls["n"] += 1
            raise make_provider_error(CREDIT_MESSAGE, 400)

        tu.make_client = lambda: object()
        tu.request_translation = fake
        return calls

    def _thirty_candidates(self):
        items = [sample_item(id=f"raw-{i}", title_en=f"English title number {i}.") for i in range(30)]
        self.write_input(items)
        self.write_cache(tu.default_cache())

    def test_insufficient_credit_aborts_after_first_call_fail_mode(self):
        self._thirty_candidates()
        calls = self._install_credit_failure()
        rc, out = self.run_main(["--locale", LOCALE, "--limit", "30", "--provider-failure-mode", "fail"])
        self.assertEqual(calls["n"], 1, "only one API call before fail-fast")
        s = parse_summary(out)
        self.assertEqual(s["api_calls"], "1")
        self.assertEqual(s["failed_items"], "1")          # not 30
        self.assertEqual(s["provider_aborted_items"], "29")
        self.assertEqual(s["api_calls_avoided"], "29")
        self.assertEqual(s["provider_status"], "unavailable")
        self.assertEqual(s["provider_error_type"], "insufficient_credit")
        self.assertEqual(s["provider_error_detected"], "true")
        self.assertEqual(rc, 1)                            # fail mode

    def test_warn_mode_exits_zero(self):
        self._thirty_candidates()
        self._install_credit_failure()
        rc, out = self.run_main(["--locale", LOCALE, "--limit", "30", "--provider-failure-mode", "warn"])
        self.assertEqual(rc, 0)                            # daily pipeline keeps going
        s = parse_summary(out)
        self.assertEqual(s["provider_status"], "unavailable")
        self.assertEqual(s["provider_error_type"], "insufficient_credit")
        self.assertEqual(s["provider_aborted_items"], "29")

    def test_provider_error_marker_emitted_once_not_per_item(self):
        self._thirty_candidates()
        self._install_credit_failure()
        rc, out = self.run_main(["--locale", LOCALE, "--limit", "30", "--provider-failure-mode", "warn"])
        self.assertEqual(out.count("PROVIDER_UNAVAILABLE"), 1)
        self.assertEqual(parse_summary(out)["failed_items"], "1")  # not 30 failures

    def test_authentication_error_is_also_fatal(self):
        self._thirty_candidates()

        def fake(client, model, item, locale):
            raise make_provider_error("auth", 401)

        tu.make_client = lambda: object()
        tu.request_translation = fake
        rc, out = self.run_main(["--locale", LOCALE, "--limit", "30", "--provider-failure-mode", "warn"])
        s = parse_summary(out)
        self.assertEqual(s["api_calls"], "1")
        self.assertEqual(s["provider_error_type"], "authentication_error")
        self.assertEqual(rc, 0)

    def test_per_item_validation_error_does_not_abort(self):
        bad = sample_item(id="raw-bad", title_en="Bad English title.")
        good = sample_item(id="raw-good", title_en="Good English title.")
        self.write_input([bad, good])
        self.write_cache(tu.default_cache())

        def fake(client, model, item, locale):
            if item.get("id") == "raw-bad":
                return {"title": "x"}, "fake-model"  # missing fields -> item_validation_error
            return good_translation(), "fake-model"

        tu.make_client = lambda: object()
        tu.request_translation = fake
        rc, out = self.run_main(["--locale", LOCALE, "--limit", "30", "--provider-failure-mode", "fail"])
        self.assertEqual(rc, 0)
        s = parse_summary(out)
        self.assertEqual(s["provider_status"], "healthy")
        self.assertEqual(s["failed_items"], "1")
        out_items = {it["id"]: it for it in self.read_output()}
        self.assertNotIn("translations", out_items["raw-bad"])
        self.assertIn(LOCALE, out_items["raw-good"]["translations"])

    def test_rate_limit_is_per_item_not_fatal(self):
        self._thirty_candidates()

        def fake(client, model, item, locale):
            raise make_provider_error("rate limited", 429)

        tu.make_client = lambda: object()
        tu.request_translation = fake
        rc, out = self.run_main(["--locale", LOCALE, "--limit", "30", "--provider-failure-mode", "fail"])
        s = parse_summary(out)
        # Not fatal: every candidate within budget is attempted (no fail-fast abort).
        self.assertEqual(s["api_calls"], "30")
        self.assertEqual(s["failed_items"], "30")
        self.assertEqual(s["provider_status"], "healthy")
        self.assertEqual(s["provider_aborted_items"], "0")
        self.assertEqual(rc, 0)


class TestProviderFailureWorkflowPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        cls.daily = (root / "daily-update.yml").read_text(encoding="utf-8")
        cls.backfill = (root / "translation-backfill.yml").read_text(encoding="utf-8")

    def test_daily_uses_warn_mode(self):
        self.assertIn("--provider-failure-mode warn", self.daily)
        self.assertNotIn("--provider-failure-mode fail", self.daily)

    def test_backfill_uses_fail_mode(self):
        self.assertIn("--provider-failure-mode fail", self.backfill)
        self.assertNotIn("--provider-failure-mode warn", self.backfill)

    def test_daily_surfaces_provider_warning(self):
        self.assertIn("Translation provider unavailable", self.daily)

    def test_daily_still_commits_legal_data(self):
        self.assertIn("Daily update legal reform data", self.daily)
        for path in ("data/raw_items.json", "docs/data/legal_updates.json", "data/translation_cache.json"):
            with self.subTest(path=path):
                self.assertIn(path, self.daily)

    def test_daily_limit_30_preserved(self):
        self.assertIn("--limit 30", self.daily)

    def test_daily_sets_translation_model(self):
        self.assertIn("ANTHROPIC_TRANSLATION_MODEL: claude-sonnet-5", self.daily)

    def test_backfill_remains_translate_only(self):
        for forbidden in ("fetch_updates", "build_public_data", "summarize_updates", "source_health"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.backfill)

    def test_both_share_concurrency_group(self):
        for name, wf in (("daily", self.daily), ("backfill", self.backfill)):
            with self.subTest(workflow=name):
                self.assertIn("group: japan-legal-reform-data-writer", wf)


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

    def test_yearly_archives_run_after_translate_and_before_change_check(self):
        translate_pos = self.workflow.index("name: Translate Simplified Chinese updates")
        archive_pos = self.workflow.index("name: Build yearly public archives")
        check_pos = self.workflow.index("name: Check data changes")
        self.assertLess(translate_pos, archive_pos)
        self.assertLess(archive_pos, check_pos)
        self.assertIn("scripts/build_public_archives.py", self.workflow)
        self.assertGreaterEqual(self.workflow.count("docs/data/legal_updates_manifest.json"), 2)
        self.assertGreaterEqual(self.workflow.count("docs/data/archive"), 2)

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
        self.assertIn("  pages: write", self.workflow)
        fetch_pos = self.workflow.index("name: Fetch raw updates")
        evaluate_pos = self.workflow.index("name: Evaluate source health")
        build_pos = self.workflow.index("name: Build public data")
        commit_pos = self.workflow.index("name: Commit and push updated data")
        pages_pos = self.workflow.index("name: Request Pages build for committed data")
        gate_pos = self.workflow.index("name: Enforce source health gate")
        self.assertLess(fetch_pos, evaluate_pos)
        self.assertLess(evaluate_pos, build_pos)
        self.assertLess(commit_pos, pages_pos)
        self.assertLess(pages_pos, gate_pos)
        self.assertIn("github.event_name == 'schedule'", self.workflow)


class TestRequestTranslationCallShape(unittest.TestCase):
    def test_disables_thinking_and_omits_sampling_params(self):
        # Stub the SDK so this offline test needs no anthropic install; capture the
        # kwargs the translator passes to messages.create.
        fake_anthropic = types.ModuleType("anthropic")

        class _BadRequestError(Exception):
            pass

        fake_anthropic.BadRequestError = _BadRequestError

        captured = {}

        class _Block:
            type = "text"
            text = json.dumps(good_translation(), ensure_ascii=False)

        class _Resp:
            content = [_Block()]
            model = "claude-sonnet-5"

        class _Messages:
            def create(self, **kwargs):
                captured.update(kwargs)
                return _Resp()

        class _Client:
            def __init__(self):
                self.messages = _Messages()

        saved = sys.modules.get("anthropic")
        sys.modules["anthropic"] = fake_anthropic
        try:
            data, model_used = tu.request_translation(_Client(), "claude-sonnet-5", sample_item(), LOCALE)
        finally:
            if saved is None:
                sys.modules.pop("anthropic", None)
            else:
                sys.modules["anthropic"] = saved

        # Thinking is explicitly disabled (Sonnet 5 would otherwise run adaptive
        # thinking by default and could truncate the JSON at MAX_TOKENS).
        self.assertEqual(captured.get("thinking"), {"type": "disabled"})
        self.assertEqual(
            captured.get("system"),
            [{
                "type": "text",
                "text": tu.SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
        )
        # No sampling params — they are rejected (400) on Sonnet 5 / Opus 4.8.
        for param in ("temperature", "top_p", "top_k"):
            self.assertNotIn(param, captured)
        self.assertEqual(captured.get("model"), "claude-sonnet-5")  # resolved model passes through
        self.assertEqual(model_used, "claude-sonnet-5")
        self.assertEqual(data["title"], good_translation()["title"])

    def test_batch_params_use_one_hour_prompt_cache(self):
        params = tu.translation_request_params(
            "claude-sonnet-5",
            sample_item(),
            LOCALE,
            cache_ttl="1h",
        )

        self.assertEqual(
            params["system"][0]["cache_control"],
            {"type": "ephemeral", "ttl": "1h"},
        )
        self.assertEqual(params["system"][0]["text"], tu.SYSTEM_PROMPT)


class TestTranslationModelOverride(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        cls.daily = (root / "daily-update.yml").read_text(encoding="utf-8")
        cls.backfill = (root / "translation-backfill.yml").read_text(encoding="utf-8")

    def test_both_workflows_set_translation_model_to_sonnet_5(self):
        for name, wf in (("daily", self.daily), ("backfill", self.backfill)):
            with self.subTest(workflow=name):
                self.assertIn("ANTHROPIC_TRANSLATION_MODEL: claude-sonnet-5", wf)

    def test_both_workflows_use_discounted_batch_mode(self):
        for name, wf in (("daily", self.daily), ("backfill", self.backfill)):
            with self.subTest(workflow=name):
                command = next(
                    line.strip() for line in wf.splitlines()
                    if "python scripts/translate_updates.py" in line
                )
                self.assertIn("--batch", command)

    def test_override_is_translate_only(self):
        # The model-override *assignment* appears exactly once per workflow (the
        # translate step), so summarization is not switched to Sonnet. (The bare
        # token also appears in an explanatory comment, hence matching the value.)
        self.assertEqual(self.daily.count("ANTHROPIC_TRANSLATION_MODEL: claude-sonnet-5"), 1)
        self.assertEqual(self.backfill.count("ANTHROPIC_TRANSLATION_MODEL: claude-sonnet-5"), 1)

    def test_switch_is_env_only(self):
        # The Sonnet-5 switch is env-only: the translation code default is
        # unchanged (still Haiku 4.5), and the ANTHROPIC_TRANSLATION_MODEL
        # override selects Sonnet 5. (Summarization stays on Opus 4.8.)
        self.assertEqual(tu.DEFAULT_TRANSLATION_MODEL, "claude-haiku-4-5-20251001")
        with mock.patch.dict(os.environ, {"ANTHROPIC_TRANSLATION_MODEL": "claude-sonnet-5"}, clear=True):
            self.assertEqual(tu.resolve_model(None), "claude-sonnet-5")


if __name__ == "__main__":
    unittest.main()
