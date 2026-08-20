"""Offline tests for field-level translation memoization and batch idempotency.

No network and no API: every Claude entry point is patched. These pin the
second-stage optimizations:

  * identical English is translated once and reused (dedup)
  * distinct English never collides on one cache key
  * the Japanese reference context still separates otherwise-identical titles
  * a prompt-version bump invalidates every memoized field
  * an in-flight batch is recorded before waiting and reclaimed afterwards
  * batch results are matched by custom_id, not by position
  * partial batch failure keeps the successful, already-billed results
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import anthropic_batch  # noqa: E402
import translate_updates as tu  # noqa: E402

LOCALE = "zh-Hans"

# The three fixed rule-based English sentences share one value across the whole
# corpus; only the title varies. That is the case dedup exists for.
BOILERPLATE = {
    "summary_en": "This is a rule-based preview and has not yet been reviewed by AI.",
    "business_impact_en": "The business impact has not yet been assessed.",
    "recommended_action_en": "Review the original Japanese source.",
}

ZH = {
    "title": "公开征求意见：药店调剂指南修订草案",
    "summary": "这是基于规则的预览，尚未经过AI审阅。",
    "business_impact": "业务影响尚未评估。",
    "recommended_action": "建议查阅日文原始来源。",
}


def item(idx, title_en=None, title_ja=None, **overrides):
    base = {
        "id": f"raw-{idx}",
        "title_en": title_en or f"MOE Update: Regulatory announcement {idx}",
        "title_ja": title_ja or f"環境省告示第{idx}号",
        "area": "Other",
        "stage": "In Force",
        "impact_level": "Low",
        "source_name": "環境省 報道発表",
        "source_url": f"https://www.env.go.jp/{idx}",
        "published_at": "2026-06-16",
        "last_checked": "2026-06-16",
        "summary_source": "rule_based",
        **BOILERPLATE,
    }
    base.update(overrides)
    return base


def _idle_workspace_client():
    """A client whose workspace has no batch running.

    The batch interlock refuses to submit while anything is still in flight, and
    fails closed when the batch list cannot be read at all, so every batch test
    needs a client that can answer `list()`. This models the normal case: an idle
    workspace.
    """
    import types as _types

    class _Batches:
        def list(self, limit=20):
            return []

    return _types.SimpleNamespace(messages=_types.SimpleNamespace(batches=_Batches()))



def recovery_stats():
    """The stats dict the recovery/apply helpers expect, in one place.

    Kept as a helper because every new counter would otherwise have to be added
    to a dozen inline copies.
    """
    return {
        "usage_totals": tu.message_usage(None),
        "estimated_cost_usd": 0.0,
        "cost_estimate_complete": True,
        "translated": 0,
        "quality_rejected": 0,
        "partial_field_requests": 0,
        "field_cache_added": 0,
        "reclaimed": 0,
        "reclaim_skipped": 0,
        "reclaim_failed": 0,
        "batch_outcomes": {bucket: 0 for bucket in tu.BATCH_OUTCOME_BUCKETS},
    }



class FieldCacheBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.published = base / "legal_updates.json"
        self.cache_path = base / "translation_cache.json"
        self._orig = (tu.INPUT_PATH, tu.OUTPUT_PATH, tu.CACHE_PATH, tu.LOG_PATH,
                      tu.request_translation, tu.request_translation_batch, tu.make_client)
        tu.INPUT_PATH = tu.OUTPUT_PATH = self.published
        tu.CACHE_PATH = self.cache_path
        tu.LOG_PATH = base / "translate.log"
        tu.make_client = _idle_workspace_client
        self.cache_path.write_text(json.dumps(tu.default_cache()), encoding="utf-8")

    def tearDown(self):
        for handler in list(tu.logger.handlers):
            handler.close()
        tu.logger.handlers.clear()
        (tu.INPUT_PATH, tu.OUTPUT_PATH, tu.CACHE_PATH, tu.LOG_PATH,
         tu.request_translation, tu.request_translation_batch, tu.make_client) = self._orig
        self._tmp.cleanup()

    def write_items(self, items):
        self.published.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    def read_items(self):
        return json.loads(self.published.read_text(encoding="utf-8"))

    def read_cache(self):
        return json.loads(self.cache_path.read_text(encoding="utf-8"))

    def install_recorder(self):
        """Patch the API, recording which fields each call actually requested."""
        seen = []

        def fake(client, model, it, locale, fields=tu.TRANSLATION_FIELDS):
            seen.append({"id": it.get("id"), "fields": tuple(fields)})
            return {f: ZH[f] for f in fields}, "fake-model"

        tu.request_translation = fake
        return seen

    def run_main(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), unittest.mock.patch.dict(
            "os.environ", {"ANTHROPIC_API_KEY": "test-key"}, clear=False
        ):
            rc = tu.main(argv)
        return rc, buf.getvalue()


import unittest.mock  # noqa: E402  (used by run_main above)


class TestFieldDeduplication(FieldCacheBase):
    def test_identical_english_body_is_translated_once(self):
        """Five items sharing the boilerplate must request the bodies only once."""
        self.write_items([item(i) for i in range(5)])
        seen = self.install_recorder()

        rc, _ = self.run_main(["--locale", LOCALE, "--limit", "10"])
        self.assertEqual(rc, 0)

        self.assertEqual(len(seen), 5, "each distinct title still needs its own call")
        self.assertEqual(seen[0]["fields"], tu.TRANSLATION_FIELDS)
        for call in seen[1:]:
            self.assertEqual(call["fields"], ("title",), "bodies must come from the field cache")

        out = self.read_items()
        bodies = {tuple(o["translations"][LOCALE][f] for f in
                        ("summary", "business_impact", "recommended_action")) for o in out}
        self.assertEqual(len(bodies), 1, "identical English must render identically")
        for o in out:
            self.assertEqual(o["translations"][LOCALE]["summary"], ZH["summary"])

    def test_fully_memoized_item_costs_no_api_call(self):
        """An item identical to one already translated needs no request at all."""
        shared_title = "MOE Update: Regulatory announcement"
        shared_ja = "環境省告示"
        first = item(1, title_en=shared_title, title_ja=shared_ja)
        second = item(2, title_en=shared_title, title_ja=shared_ja)
        self.write_items([first, second])
        seen = self.install_recorder()

        rc, out = self.run_main(["--locale", LOCALE, "--limit", "10"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(seen), 1, "the second item is fully memoized")
        self.assertIn("field_cache_hits          : 1", out)

        published = self.read_items()
        self.assertEqual(
            published[0]["translations"][LOCALE], published[1]["translations"][LOCALE]
        )
        # Both still get their own item-level cache entry.
        self.assertEqual(len(self.read_cache()["entries"][LOCALE]), 2)

    def test_full_memoization_does_not_consume_the_api_limit(self):
        """A free assembly must not eat the budget a real candidate needs."""
        shared = dict(title_en="MOE Update: Same", title_ja="環境省告示")
        items = [item(1, **shared), item(2, **shared), item(3, title_en="MOE Update: Different")]
        self.write_items(items)
        seen = self.install_recorder()

        rc, _ = self.run_main(["--locale", LOCALE, "--limit", "2"])
        self.assertEqual(rc, 0)
        # item 1 (full) + item 3 (title only) = 2 calls; item 2 was free.
        self.assertEqual([c["id"] for c in seen], ["raw-1", "raw-3"])
        for it in self.read_items():
            self.assertIn(LOCALE, it["translations"])


class TestFieldCacheKeying(FieldCacheBase):
    def test_distinct_english_never_shares_a_field_key(self):
        a = item(1, title_en="Title A")
        b = item(2, title_en="Title B")
        self.assertNotEqual(
            tu.compute_field_hash(a, "title", LOCALE, tu.PROMPT_VERSION),
            tu.compute_field_hash(b, "title", LOCALE, tu.PROMPT_VERSION),
        )
        # ...while identical body text does share one key.
        self.assertEqual(
            tu.compute_field_hash(a, "summary", LOCALE, tu.PROMPT_VERSION),
            tu.compute_field_hash(b, "summary", LOCALE, tu.PROMPT_VERSION),
        )

    def test_title_key_separates_different_japanese_originals(self):
        """Same English title, different statute: must NOT reuse one Chinese title."""
        a = item(1, title_en="e-Gov Law Update: In force", title_ja="種苗法施行規則の一部を改正する省令")
        b = item(2, title_en="e-Gov Law Update: In force", title_ja="地方自治法施行規則の一部を改正する省令")
        self.assertNotEqual(
            tu.compute_field_hash(a, "title", LOCALE, tu.PROMPT_VERSION),
            tu.compute_field_hash(b, "title", LOCALE, tu.PROMPT_VERSION),
        )

    def test_title_key_ignores_japanese_context_for_body_fields(self):
        a = item(1, title_ja="環境省告示第1号", stage="In Force")
        b = item(2, title_ja="全く別の日本語原題", stage="Public Comment Open")
        for field in ("summary", "business_impact", "recommended_action"):
            self.assertEqual(
                tu.compute_field_hash(a, field, LOCALE, tu.PROMPT_VERSION),
                tu.compute_field_hash(b, field, LOCALE, tu.PROMPT_VERSION),
                field,
            )

    def test_prompt_version_bump_invalidates_every_memoized_field(self):
        entries = {}
        key = tu.compute_field_hash(item(1), "summary", LOCALE, tu.PROMPT_VERSION)
        tu.store_field_translation(entries, key, "summary", ZH["summary"],
                                   tu.PROMPT_VERSION, "2026-01-01T00:00:00Z", "m")
        self.assertIsNotNone(
            tu.field_cache_lookup(entries, key, "summary", tu.PROMPT_VERSION)
        )
        self.assertIsNone(
            tu.field_cache_lookup(entries, key, "summary", "zh-hans-v4"),
            "an older-version entry must be a cache miss",
        )

    def test_english_change_invalidates_only_the_changed_field(self):
        before = item(1)
        after = item(1, summary_en="Completely different English summary text.")
        self.assertNotEqual(
            tu.compute_field_hash(before, "summary", LOCALE, tu.PROMPT_VERSION),
            tu.compute_field_hash(after, "summary", LOCALE, tu.PROMPT_VERSION),
        )
        self.assertEqual(
            tu.compute_field_hash(before, "title", LOCALE, tu.PROMPT_VERSION),
            tu.compute_field_hash(after, "title", LOCALE, tu.PROMPT_VERSION),
        )

    def test_invalid_or_oversize_text_is_never_memoized(self):
        entries = {}
        key = tu.compute_field_hash(item(1), "summary", LOCALE, tu.PROMPT_VERSION)
        for bad in ("", "   ", "<b>markup</b>", "x" * (tu.FIELD_LIMITS["summary"] + 1)):
            self.assertFalse(
                tu.store_field_translation(entries, key, "summary", bad,
                                           tu.PROMPT_VERSION, "t", "m"),
                repr(bad[:20]),
            )
        self.assertEqual(entries, {})


class TestFieldCacheSeeding(FieldCacheBase):
    def test_seeding_reuses_paid_translations_and_is_deterministic(self):
        """Upgrading to a field cache must cost zero API calls."""
        items = [item(i) for i in range(3)]
        entries = {}
        for i, it in enumerate(items):
            fields = dict(ZH)
            # Independent sampling produced slightly different boilerplate wording.
            fields["summary"] = ZH["summary"] + ("。" * i)
            entries[it["id"]] = tu.cache_entry(
                tu.compute_source_hash(it, LOCALE, tu.PROMPT_VERSION),
                tu.PROMPT_VERSION, "2026-06-17T00:00:00Z", "claude-sonnet-5", fields,
            )
        first, second = {}, {}
        n1 = tu.seed_field_cache(first, entries, items, LOCALE)
        n2 = tu.seed_field_cache(second, entries, items, LOCALE)
        self.assertGreater(n1, 0)
        self.assertEqual(n1, n2)
        self.assertEqual(
            {k: v["text"] for k, v in first.items()},
            {k: v["text"] for k, v in second.items()},
            "seeding must be deterministic across runs",
        )

    def test_seeding_ignores_entries_whose_english_has_changed(self):
        it = item(1)
        stale = tu.cache_entry("not-the-current-hash", tu.PROMPT_VERSION,
                               "2026-06-17T00:00:00Z", "m", dict(ZH))
        entries = {it["id"]: stale}
        seeded = {}
        self.assertEqual(tu.seed_field_cache(seeded, entries, [it], LOCALE), 0)
        self.assertEqual(seeded, {})

    def test_upgrade_from_schema_v1_adds_the_field_section(self):
        legacy = {"schema_version": 1, "entries": {LOCALE: {}}}
        upgraded = tu.ensure_cache_shape(legacy, LOCALE)
        self.assertEqual(upgraded["schema_version"], tu.CACHE_SCHEMA_VERSION)
        self.assertEqual(upgraded["fields"][LOCALE], {})
        self.assertIn(LOCALE, upgraded["entries"])


class FakeBatches:
    """Minimal stand-in for client.messages.batches."""

    def __init__(self, results, status="ended", fail_create=None):
        self._results = results
        self._status = status
        self._fail_create = fail_create
        self.created = []
        self.cancelled = []

    def create(self, requests):
        if self._fail_create:
            raise self._fail_create
        self.created.append(requests)
        return type("B", (), {"id": "msgbatch_fake", "processing_status": self._status})()

    def retrieve(self, batch_id):
        return type("B", (), {"id": batch_id, "processing_status": self._status})()

    def cancel(self, batch_id):
        self.cancelled.append(batch_id)

    def results(self, batch_id):
        return list(self._results)


def batch_row(custom_id, fields=tu.TRANSLATION_FIELDS, *, error=None):
    if error:
        result = type("R", (), {"type": "errored",
                                "error": type("E", (), {"error": type("I", (), {"type": error, "message": ""})()})()})()
    else:
        block = type("Blk", (), {"type": "text", "text": json.dumps({f: ZH[f] for f in fields})})()
        message = type("M", (), {"content": [block], "model": "fake-batch", "usage": None})()
        result = type("R", (), {"type": "succeeded", "message": message})()
    return type("Row", (), {"custom_id": custom_id, "result": result})()


class TestBatchIdempotency(FieldCacheBase):
    def test_results_are_matched_by_custom_id_not_position(self):
        rows = [batch_row("translate-0001", ("title",)), batch_row("translate-0000")]
        client = type("C", (), {"messages": type("M", (), {"batches": FakeBatches(rows)})()})()
        run = anthropic_batch.run_message_batch(
            client,
            [{"custom_id": "translate-0000", "params": {}},
             {"custom_id": "translate-0001", "params": {}}],
        )
        self.assertEqual(set(run.results), {"translate-0000", "translate-0001"})
        for value in run.results.values():
            self.assertNotIsInstance(value, Exception)

    def test_missing_and_errored_results_become_per_item_errors(self):
        rows = [batch_row("translate-0000"), batch_row("translate-0001", error="rate_limit_error")]
        client = type("C", (), {"messages": type("M", (), {"batches": FakeBatches(rows)})()})()
        run = anthropic_batch.run_message_batch(
            client,
            [{"custom_id": f"translate-{i:04d}", "params": {}} for i in range(3)],
        )
        self.assertNotIsInstance(run.results["translate-0000"], Exception)
        self.assertIsInstance(run.results["translate-0001"], anthropic_batch.BatchItemError)
        self.assertEqual(run.results["translate-0001"].error_type, "rate_limit_error")
        missing = run.results["translate-0002"]
        self.assertIsInstance(missing, anthropic_batch.BatchItemError)
        self.assertEqual(missing.error_type, "missing_result")

    def test_batch_id_is_persisted_before_waiting(self):
        """A run killed during the wait must leave a reclaimable record."""
        seen = {}
        rows = [batch_row("translate-0000")]
        client = type("C", (), {"messages": type("M", (), {"batches": FakeBatches(rows)})()})()
        anthropic_batch.run_message_batch(
            client, [{"custom_id": "translate-0000", "params": {}}],
            on_submit=lambda bid: seen.setdefault("id", bid),
        )
        self.assertEqual(seen["id"], "msgbatch_fake")

    def test_pending_record_round_trips_and_clears(self):
        cache = tu.default_cache()
        self.assertIsNone(tu.pending_batch_record(cache, LOCALE))
        candidates = [(item(1), "hash-1", False, {}, tu.TRANSLATION_FIELDS, {})]
        tu.set_pending_batch(
            cache, LOCALE, tu.build_pending_record("msgbatch_x", "m", candidates, "2026-06-18T00:00:00Z")
        )
        record = tu.pending_batch_record(cache, LOCALE)
        self.assertEqual(record["batch_id"], "msgbatch_x")
        self.assertEqual(record["requests"]["translate-0000"]["item_id"], "raw-1")
        self.assertEqual(record["requests"]["translate-0000"]["source_hash"], "hash-1")
        tu.clear_pending_batch(cache, LOCALE)
        self.assertIsNone(tu.pending_batch_record(cache, LOCALE))

    def test_pending_record_is_ignored_after_a_prompt_version_bump(self):
        cache = tu.default_cache()
        candidates = [(item(1), "hash-1", False, {}, tu.TRANSLATION_FIELDS, {})]
        record = tu.build_pending_record("msgbatch_x", "m", candidates, "2026-06-18T00:00:00Z")
        record["prompt_version"] = "zh-hans-v1"
        tu.set_pending_batch(cache, LOCALE, record)
        self.assertNotEqual(
            tu.pending_batch_record(cache, LOCALE)["prompt_version"], tu.PROMPT_VERSION
        )

    def test_reclaim_applies_results_and_skips_changed_english(self):
        unchanged, changed = item(1), item(2)
        entries, field_entries, stats = {}, {}, recovery_stats()
        record = {
            "batch_id": "msgbatch_old", "prompt_version": tu.PROMPT_VERSION, "model": "m",
            "requests": {
                "translate-0000": {
                    "item_id": "raw-1", "source_hash": tu.compute_source_hash(unchanged, LOCALE, tu.PROMPT_VERSION),
                    "fields": list(tu.TRANSLATION_FIELDS),
                },
                "translate-0001": {
                    "item_id": "raw-2", "source_hash": "stale-hash-from-older-english",
                    "fields": list(tu.TRANSLATION_FIELDS),
                },
            },
        }
        rows = [batch_row("translate-0000"), batch_row("translate-0001")]
        tu.make_client = lambda: type("C", (), {"messages": type("M", (), {"batches": FakeBatches(rows)})()})()
        tu.reclaim_pending_batch([unchanged, changed], entries, field_entries, LOCALE, record, stats, 60.0)

        self.assertEqual(stats["reclaimed"], 1)
        self.assertEqual(stats["reclaim_skipped"], 1, "changed English must not be published")
        self.assertIn("raw-1", entries)
        self.assertNotIn("raw-2", entries)
        self.assertIn(LOCALE, unchanged["translations"])
        self.assertNotIn("translations", changed)

    def test_canceling_status_keeps_polling_instead_of_raising(self):
        """A cancelled-but-finishing batch still has billed results to collect."""
        states = iter(["canceling", "ended"])
        rows = [batch_row("translate-0000")]

        class Batches(FakeBatches):
            def retrieve(self, batch_id):
                return type("B", (), {"id": batch_id, "processing_status": next(states)})()

        batches = Batches(rows, status="canceling")
        client = type("C", (), {"messages": type("M", (), {"batches": batches})()})()
        run = anthropic_batch.collect_message_batch(
            client, "msgbatch_fake", ["translate-0000"], poll_seconds=0, timeout_seconds=30
        )
        self.assertNotIsInstance(run.results["translate-0000"], Exception)
        self.assertEqual(batches.cancelled, [], "must not cancel a batch it is collecting")


class TestRunnerLossRecovery(FieldCacheBase):
    """The batch must be recoverable with NO local state at all.

    On GitHub Actions the daily workflow commits only at the very end, so a
    runner that dies while waiting takes data/translation_cache.json with it and
    the next run starts from a fresh checkout. Recovery therefore cannot depend
    on anything written locally — it has to come from the provider.
    """

    def _client_with_ended_batch(self, rows):
        batches = FakeBatches(rows)
        batches.listed = [type("B", (), {"id": "msgbatch_lost", "processing_status": "ended"})()]
        batches.list = lambda limit=20: batches.listed
        return type("C", (), {"messages": type("M", (), {"batches": batches})()})()

    def test_batch_is_recovered_from_the_provider_with_no_local_record(self):
        it = item(0, title_en="Only title")
        source_hash = tu.compute_source_hash(it, LOCALE, tu.PROMPT_VERSION)
        custom_id = tu.batch_custom_id(it, source_hash, tu.TRANSLATION_FIELDS)
        tu.make_client = lambda: self._client_with_ended_batch([batch_row(custom_id)])

        entries, field_entries = {}, {}
        stats = recovery_stats()

        # Note: NO pending_batch record is supplied anywhere.
        tu.discover_and_reclaim_batches([it], entries, field_entries, LOCALE, stats)

        self.assertEqual(stats["reclaimed"], 1, "paid results must be recovered without local state")
        self.assertIn("raw-0", entries)
        self.assertIn(LOCALE, it["translations"])

    def test_recovered_result_is_discarded_when_the_english_has_changed(self):
        it = item(0, title_en="Original title")
        stale_hash = tu.compute_source_hash(it, LOCALE, tu.PROMPT_VERSION)
        custom_id = tu.batch_custom_id(it, stale_hash, tu.TRANSLATION_FIELDS)
        it["title_en"] = "The English was rewritten after the batch was submitted"
        tu.make_client = lambda: self._client_with_ended_batch([batch_row(custom_id)])

        entries, field_entries = {}, {}
        stats = recovery_stats()
        tu.discover_and_reclaim_batches([it], entries, field_entries, LOCALE, stats)

        self.assertEqual(stats["reclaimed"], 0)
        self.assertEqual(stats["reclaim_skipped"], 1)
        self.assertNotIn("raw-0", entries)
        self.assertNotIn("translations", it)

    def test_another_tools_batch_in_the_same_workspace_is_ignored(self):
        it = item(0)
        tu.make_client = lambda: self._client_with_ended_batch(
            [batch_row("some-other-tool-request-0001")]
        )
        entries, field_entries = {}, {}
        stats = recovery_stats()
        tu.discover_and_reclaim_batches([it], entries, field_entries, LOCALE, stats)
        self.assertEqual((stats["reclaimed"], stats["reclaim_skipped"]), (0, 0))
        self.assertEqual(entries, {})

    def test_recovery_is_idempotent_across_repeated_runs(self):
        it = item(0, title_en="Only title")
        source_hash = tu.compute_source_hash(it, LOCALE, tu.PROMPT_VERSION)
        custom_id = tu.batch_custom_id(it, source_hash, tu.TRANSLATION_FIELDS)
        tu.make_client = lambda: self._client_with_ended_batch([batch_row(custom_id)])
        entries, field_entries = {}, {}

        def fresh_stats():
            return recovery_stats()

        first = fresh_stats()
        tu.discover_and_reclaim_batches([it], entries, field_entries, LOCALE, first)
        second = fresh_stats()
        tu.discover_and_reclaim_batches([it], entries, field_entries, LOCALE, second)
        self.assertEqual(first["reclaimed"], 1)
        self.assertEqual(second["reclaimed"], 0, "an already-applied batch must not be re-applied")

    def test_field_mask_survives_the_round_trip(self):
        for fields in (tu.TRANSLATION_FIELDS, ("title",),
                       ("summary", "business_impact", "recommended_action")):
            mask = tu.fields_mask(fields)
            self.assertEqual(tu.mask_fields(mask), tuple(fields), fields)
        # A corrupt mask degrades to "all four" rather than losing fields.
        self.assertEqual(tu.mask_fields("zz"), tu.TRANSLATION_FIELDS)


class TestPartialBatchFailure(FieldCacheBase):
    def test_successful_results_survive_a_partially_failed_batch(self):
        """One errored request must not discard the other paid results."""
        items = [item(i, title_en=f"Distinct title {i}") for i in range(3)]
        self.write_items(items)
        rows = [
            batch_row("translate-0000"),
            batch_row("translate-0001", error="rate_limit_error"),
            batch_row("translate-0002", ("title",)),
        ]

        def fake_batch(client, model, cands, locale, *, timeout_seconds,
                       field_sets=None, on_submit=None, custom_ids=None):
            if on_submit:
                on_submit("msgbatch_partial")
            sets = field_sets or [tu.TRANSLATION_FIELDS] * len(cands)
            decoded = []
            for i, fields in enumerate(sets):
                row = rows[i]
                if getattr(row.result, "type", None) != "succeeded":
                    decoded.append(anthropic_batch.BatchItemError("rate_limit_error"))
                else:
                    decoded.append(({f: ZH[f] for f in fields}, "fake-batch"))
            return "msgbatch_partial", decoded

        tu.request_translation_batch = fake_batch
        rc, out = self.run_main(["--locale", LOCALE, "--limit", "10", "--batch",
                                 "--provider-failure-mode", "warn"])

        published = {it["id"]: it for it in self.read_items()}
        self.assertIn(LOCALE, published["raw-0"]["translations"])
        self.assertNotIn("translations", published["raw-1"])
        self.assertIn(LOCALE, published["raw-2"]["translations"])
        cached = self.read_cache()["entries"][LOCALE]
        self.assertIn("raw-0", cached)
        self.assertNotIn("raw-1", cached, "a failed request must not be cached")
        self.assertIn("raw-2", cached)
        self.assertIn("failed_items              : 1", out)
        self.assertEqual(rc, 0)

    def test_pending_record_is_cleared_once_results_are_applied(self):
        items = [item(0, title_en="Only title")]
        self.write_items(items)

        def fake_batch(client, model, cands, locale, *, timeout_seconds,
                       field_sets=None, on_submit=None, custom_ids=None):
            if on_submit:
                on_submit("msgbatch_done")
            sets = field_sets or [tu.TRANSLATION_FIELDS] * len(cands)
            return "msgbatch_done", [({f: ZH[f] for f in s}, "fake-batch") for s in sets]

        tu.request_translation_batch = fake_batch
        rc, _ = self.run_main(["--locale", LOCALE, "--limit", "10", "--batch"])
        self.assertEqual(rc, 0)
        self.assertIsNone(
            tu.pending_batch_record(self.read_cache(), LOCALE),
            "a completed batch must not stay recorded as outstanding",
        )


class TestHumanReviewedCanonicalText(FieldCacheBase):
    """The seeded canonical text is machine-generated, not human-verified.

    One string stands in for ~1,700 published cards, so a person must be able to
    read it and pin a corrected version.
    """

    def test_reviewed_entry_is_never_replaced_by_seeding_or_a_fresh_call(self):
        it = item(1)
        key = tu.compute_field_hash(it, "summary", LOCALE, tu.PROMPT_VERSION)
        entries = {key: {"field": "summary", "text": "人手で確認した訳文です。",
                         "prompt_version": tu.PROMPT_VERSION,
                         "translated_at": "2026-08-20T00:00:00Z",
                         "model": "human-reviewed", "reviewed": True}}
        self.assertFalse(
            tu.store_field_translation(entries, key, "summary", "机器生成的新译文",
                                       tu.PROMPT_VERSION, "now", "claude-sonnet-5")
        )
        self.assertEqual(entries[key]["text"], "人手で確認した訳文です。")

    def test_a_changed_english_source_still_needs_fresh_review(self):
        before = item(1)
        after = item(1, summary_en="A different English sentence entirely.")
        self.assertNotEqual(
            tu.compute_field_hash(before, "summary", LOCALE, tu.PROMPT_VERSION),
            tu.compute_field_hash(after, "summary", LOCALE, tu.PROMPT_VERSION),
            "a reviewed pin must not silently carry over to different English",
        )

    def test_shared_field_report_surfaces_the_boilerplate_first(self):
        items = [item(i) for i in range(6)]
        entries = {}
        tu.seed_field_cache(entries, {}, items, LOCALE)  # nothing to seed yet
        # Memoize as if one item had been translated.
        for field in tu.TRANSLATION_FIELDS:
            tu.store_field_translation(
                entries, tu.compute_field_hash(items[0], field, LOCALE, tu.PROMPT_VERSION),
                field, ZH[field], tu.PROMPT_VERSION, "now", "m",
            )
        rows = tu.shared_field_usage(items, entries, LOCALE)
        self.assertTrue(rows, "boilerplate shared by 6 items must be reported")
        # The title is unique per item, so it is not "shared" and must not appear.
        self.assertNotIn("title", {r["field"] for r in rows})
        for row in rows:
            self.assertEqual(row["items"], 6)
            self.assertFalse(row["reviewed"])

    def test_report_marks_reviewed_entries(self):
        items = [item(i) for i in range(3)]
        key = tu.compute_field_hash(items[0], "summary", LOCALE, tu.PROMPT_VERSION)
        entries = {key: {"field": "summary", "text": "已审阅译文。",
                         "prompt_version": tu.PROMPT_VERSION, "translated_at": "t",
                         "model": "human", "reviewed": True}}
        rows = [r for r in tu.shared_field_usage(items, entries, LOCALE) if r["field"] == "summary"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["reviewed"])


class TestBatchInterlock(FieldCacheBase):
    """An unfinished batch cannot be identified, so it must block submission.

    `batches.list()` exposes ids and statuses, but custom_ids are only readable
    once a batch has ENDED. A rerun minutes after a lost runner therefore sees an
    in-flight batch it cannot recognise as its own — "daily, so it will not
    happen" is not a guarantee when someone reruns a failed workflow.
    """

    def _client(self, statuses, *, listable=True):
        class Batches:
            def results(self, batch_id):
                return []

            if listable:
                def list(self, limit=20):
                    return [type("B", (), {"id": "msgbatch_%d" % i, "processing_status": st})()
                            for i, st in enumerate(statuses)]

        return type("C", (), {"messages": type("M", (), {"batches": Batches()})()})()

    def _run_batch(self, statuses, *, listable=True, extra_args=()):
        self.write_items([item(i, title_en="Distinct %d" % i) for i in range(2)])
        submitted = {"n": 0}

        def fake_batch(client, model, cands, locale, *, timeout_seconds,
                       field_sets=None, on_submit=None, custom_ids=None):
            submitted["n"] += 1
            sets = field_sets or [tu.TRANSLATION_FIELDS] * len(cands)
            return "msgbatch_new", [({f: ZH[f] for f in s}, "fake") for s in sets]

        tu.request_translation_batch = fake_batch
        tu.make_client = lambda: self._client(statuses, listable=listable)
        rc, out = self.run_main(["--locale", LOCALE, "--limit", "10", "--batch",
                                 "--provider-failure-mode", "warn"] + list(extra_args))
        return rc, out, submitted["n"]

    def test_running_batch_blocks_a_new_submission(self):
        rc, out, submitted = self._run_batch(["in_progress"])
        self.assertEqual(submitted, 0, "must not submit while a batch is still running")
        self.assertIn("blocked_by_running_batch  : 1", out)
        self.assertEqual(rc, 0)

    def test_canceling_batch_also_blocks(self):
        _rc, _out, submitted = self._run_batch(["canceling"])
        self.assertEqual(submitted, 0)

    def test_idle_workspace_allows_submission(self):
        _rc, out, submitted = self._run_batch(["ended"])
        self.assertEqual(submitted, 1)
        self.assertIn("blocked_by_running_batch  : 0", out)

    def test_unreadable_batch_list_fails_closed(self):
        """No list means no recovery and no interlock: submitting would risk
        paying twice, so refuse rather than continue."""
        _rc, _out, submitted = self._run_batch(["in_progress"], listable=False)
        self.assertEqual(submitted, 0)

    def test_interlock_can_be_disabled_explicitly_for_a_shared_workspace(self):
        _rc, _out, submitted = self._run_batch(
            ["in_progress"], extra_args=("--no-batch-interlock",)
        )
        self.assertEqual(submitted, 1, "--no-batch-interlock is the documented escape hatch")

    def test_blocked_run_does_not_publish_a_stale_translation(self):
        self.write_items([item(0, title_en="Only title")])

        def must_not_submit(*args, **kwargs):
            raise AssertionError("must not submit while a batch is running")

        tu.make_client = lambda: self._client(["in_progress"])
        tu.request_translation_batch = must_not_submit
        rc, _ = self.run_main(["--locale", LOCALE, "--limit", "10", "--batch",
                               "--provider-failure-mode", "warn"])
        self.assertEqual(rc, 0)
        self.assertNotIn("translations", self.read_items()[0])


class TestReviewedFieldOverrides(FieldCacheBase):
    """A person's correction must reach every card showing that exact English."""

    def _reviewed_file(self, text):
        it = item(0)
        key = tu.compute_field_hash(it, "summary", LOCALE, tu.PROMPT_VERSION)
        path = self.published.parent / "reviewed.json"
        path.write_text(json.dumps({LOCALE: {key: {
            "field": "summary", "text": text, "reviewed_at": "2026-08-20",
        }}}, ensure_ascii=False), encoding="utf-8")
        return path, key

    def test_reviewed_text_overrides_a_seeded_canonical(self):
        path, key = self._reviewed_file("已由人工审阅的译文。")
        entries = {key: {"field": "summary", "text": "机器生成的译文。",
                         "prompt_version": tu.PROMPT_VERSION,
                         "translated_at": "t", "model": "claude-sonnet-5"}}
        applied = tu.apply_reviewed_fields(entries, tu.load_reviewed_fields(LOCALE, path))
        self.assertEqual(applied, 1)
        self.assertEqual(entries[key]["text"], "已由人工审阅的译文。")
        self.assertTrue(entries[key]["reviewed"])
        self.assertEqual(entries[key]["model"], "human-reviewed")

    def test_reviewed_text_is_pushed_onto_already_cached_items_for_free(self):
        it = item(0)
        path, _key = self._reviewed_file("已由人工审阅的译文。")
        entries = {}
        tu.apply_reviewed_fields(entries, tu.load_reviewed_fields(LOCALE, path))
        cached = {"title": ZH["title"], "summary": "旧的机器译文。",
                  "business_impact": ZH["business_impact"],
                  "recommended_action": ZH["recommended_action"]}
        self.assertTrue(tu.refresh_reviewed_translation(it, cached, entries, LOCALE))
        self.assertEqual(cached["summary"], "已由人工审阅的译文。")
        # Idempotent: a second pass reports no change.
        self.assertFalse(tu.refresh_reviewed_translation(it, cached, entries, LOCALE))

    def test_reviewed_pin_stops_applying_when_the_english_changes(self):
        path, _key = self._reviewed_file("已由人工审阅的译文。")
        entries = {}
        tu.apply_reviewed_fields(entries, tu.load_reviewed_fields(LOCALE, path))
        changed = item(0, summary_en="An entirely different English sentence.")
        cached = {"title": ZH["title"], "summary": "旧的机器译文。",
                  "business_impact": ZH["business_impact"],
                  "recommended_action": ZH["recommended_action"]}
        self.assertFalse(
            tu.refresh_reviewed_translation(changed, cached, entries, LOCALE),
            "an approval must not carry over to different English",
        )
        self.assertEqual(cached["summary"], "旧的机器译文。")

    def test_invalid_reviewed_entries_are_ignored_not_published(self):
        it = item(0)
        key = tu.compute_field_hash(it, "summary", LOCALE, tu.PROMPT_VERSION)
        path = self.published.parent / "bad.json"
        for bad in ("", "   ", "<b>markup</b>", "x" * (tu.FIELD_LIMITS["summary"] + 1)):
            path.write_text(json.dumps({LOCALE: {key: {"field": "summary", "text": bad}}},
                                       ensure_ascii=False), encoding="utf-8")
            entries = {}
            self.assertEqual(
                tu.apply_reviewed_fields(entries, tu.load_reviewed_fields(LOCALE, path)),
                0, repr(bad[:12]),
            )
            self.assertEqual(entries, {})

    def test_missing_reviewed_file_is_not_an_error(self):
        self.assertEqual(
            tu.load_reviewed_fields(LOCALE, self.published.parent / "nope.json"), {}
        )

    def test_shipped_reviewed_file_is_valid_and_covers_the_boilerplate(self):
        """The three strings shown on every not-yet-summarized card."""
        reviewed = tu.load_reviewed_fields(LOCALE)
        self.assertEqual(len(reviewed), 3, "expected the three rule-based body fields")
        fields = {spec["field"] for spec in reviewed.values()}
        self.assertEqual(fields, {"summary", "business_impact", "recommended_action"})
        for spec in reviewed.values():
            self.assertTrue(tu.valid_field_translation(spec["field"], spec["text"]))
            self.assertTrue(spec.get("reviewed_at"))


class TestPublishedOutputIsUnchanged(FieldCacheBase):
    def test_english_canonical_and_metadata_are_never_touched(self):
        original = item(1)
        self.write_items([dict(original)])
        self.install_recorder()
        self.run_main(["--locale", LOCALE, "--limit", "5"])
        out = self.read_items()[0]
        for key, value in original.items():
            self.assertEqual(out[key], value, key)
        self.assertEqual(set(out) - set(original), {"translations"})


if __name__ == "__main__":
    unittest.main()
