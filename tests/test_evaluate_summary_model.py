"""Offline tests for scripts/evaluate_summary_model.py.

No API calls: `build-set` and `compare` are pure, and `run` must refuse to spend
without --confirm-spend. These pin the properties that make the harness worth
trusting — a deterministic, genuinely stratified set, and metrics that actually
flag the failure modes a cheaper model would introduce.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import evaluate_summary_model as ev  # noqa: E402


def entry(source_text="意見募集について 令和8年6月16日"):
    return {"id": "raw-1", "source": {"title_ja": source_text, "raw_summary": "",
                                      "published_at": "", "source_name": ""}}


def good_result(**overrides):
    result = {
        "title_en": "Public Comment: Draft Pharmacy Guidelines",
        "summary_en": "A Japanese authority published a draft out for public comment.",
        "business_impact_en": "Pharmacies may be affected if the draft is adopted.",
        "recommended_action_en": "Review the official Japanese source.",
        "confidence": "medium",
        "ai_notes": "Based only on the Japanese title.",
    }
    result.update(overrides)
    return result


class TestEvaluationSet(unittest.TestCase):
    def test_build_set_is_deterministic(self):
        a = ev.build_set(12)
        b = ev.build_set(12)
        self.assertEqual([i["id"] for i in a["items"]], [i["id"] for i in b["items"]])

    def test_build_set_covers_many_stages_not_just_the_alphabetical_head(self):
        frozen = ev.build_set(24)
        stages = {i["stage"] for i in frozen["items"]}
        self.assertGreaterEqual(len(stages), 8, f"stratification too narrow: {sorted(stages)}")

    def test_build_set_has_no_duplicates_and_respects_size(self):
        frozen = ev.build_set(20)
        ids = [i["id"] for i in frozen["items"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLessEqual(len(ids), 20)

    def test_frozen_item_carries_the_exact_production_prompt_input(self):
        frozen = ev.build_set(3)
        for item in frozen["items"]:
            # source_payload() is what summarize_updates.py sends; freezing it is
            # what makes two model runs byte-identical in their input.
            self.assertIn("title_ja", item["source"])
            self.assertIn("preliminary_rule_based_labels", item["source"])


class TestHardCaseProfile(unittest.TestCase):
    """A stratified sample is mostly easy items; a model swap can pass it and
    still fail on the cases this dashboard exists to triage."""

    def test_hard_profile_is_deterministic(self):
        a = ev.build_set(20, "hard")
        b = ev.build_set(20, "hard")
        self.assertEqual([i["id"] for i in a["items"]], [i["id"] for i in b["items"]])

    def test_hard_profile_selects_only_items_that_trip_signals(self):
        frozen = ev.build_set(20, "hard")
        self.assertTrue(frozen["items"])
        for item in frozen["items"]:
            self.assertTrue(item["hard_signals"], item["id"])

    def test_hard_profile_is_measurably_harder_than_stratified(self):
        hard = ev.build_set(24, "hard")
        plain = ev.build_set(24, "stratified")
        mean_hard = sum(len(i["hard_signals"]) for i in hard["items"]) / len(hard["items"])
        self.assertGreaterEqual(mean_hard, 3.0)
        self.assertNotEqual(
            {i["id"] for i in hard["items"]}, {i["id"] for i in plain["items"]}
        )

    def test_signals_cover_the_documented_failure_modes(self):
        names = {name for name, _ in ev.HARD_CASE_SIGNALS}
        for expected in ("thin_source", "amendment_vs_enactment",
                         "has_deadline_or_effective_date", "long_statute_name",
                         "instrument_distinction", "public_comment",
                         "legal_status_easy_to_overstate", "foreign_or_treaty"):
            self.assertIn(expected, names)

    def test_scoring_detects_an_amendment_with_an_effective_date(self):
        item = {"title_ja": "医療法等の一部を改正する法律の施行に伴う厚生労働省関係省令の整備等に関する省令",
                "stage": "Scheduled to Take Effect"}
        score, signals = ev.hard_case_score(item, {"raw_summary": ""})
        self.assertGreaterEqual(score, 4)
        for expected in ("amendment_vs_enactment", "has_deadline_or_effective_date",
                         "instrument_distinction", "legal_status_easy_to_overstate",
                         "thin_source"):
            self.assertIn(expected, signals)

    def test_an_easy_item_scores_low(self):
        item = {"title_ja": "広報誌の発行について", "stage": "Other"}
        score, _ = ev.hard_case_score(item, {"raw_summary": "x" * 200})
        self.assertLessEqual(score, 1)


class TestAutomaticMetrics(unittest.TestCase):
    def test_unsupported_numbers_flags_only_digits_absent_from_the_source(self):
        source = "第五条の改正について 2026-06-16"
        self.assertEqual(ev.unsupported_numbers("Effective 2026-06-16.", source), [])
        self.assertEqual(ev.unsupported_numbers("Article 47 applies.", source), ["47"])
        # Small ordinals are structural, not factual claims.
        self.assertEqual(ev.unsupported_numbers("Two or 3 points.", source), [])

    def test_english_score_flags_japanese_title(self):
        scored = ev.score_english(good_result(title_en="特定外来生物 designation"), entry())
        self.assertTrue(scored["title_has_japanese"])
        self.assertFalse(ev.score_english(good_result(), entry())["title_has_japanese"])

    def test_english_score_flags_definitive_guardrail_wording(self):
        bad = good_result(summary_en="The amendment has been enacted and is in force.")
        self.assertTrue(ev.score_english(bad, entry())["caution_violations"])
        self.assertFalse(ev.score_english(good_result(), entry())["caution_violations"])

    def test_english_score_flags_schema_violation(self):
        self.assertFalse(ev.score_english(good_result(confidence="certain"), entry())["schema_compliant"])
        self.assertFalse(ev.score_english(good_result(summary_en="  "), entry())["schema_compliant"])

    def test_japanese_score_flags_an_answer_with_no_japanese_script(self):
        english_answer = {
            "summary_ja": "This answer is in English by mistake.",
            "business_impact_ja": "Still English.",
            "recommended_action_ja": "Also English.",
        }
        scored = ev.score_japanese(english_answer, entry())
        self.assertFalse(scored["contains_japanese"])
        good = {
            "summary_ja": "日本語の公表情報に関する要約です。",
            "business_impact_ja": "事業に影響が生じる可能性があります。",
            "recommended_action_ja": "日本語の公式情報源の確認が考えられます。",
        }
        self.assertTrue(ev.score_japanese(good, entry())["contains_japanese"])


class TestComparisonVerdict(unittest.TestCase):
    def _run(self, model, scores_list, cost=0.01):
        return {
            "model": model, "language": "english", "estimated_cost_usd": cost * len(scores_list),
            "records": [
                {"id": f"raw-{i}", "scores": s, "output": good_result(),
                 "usage": {"input_tokens": 800, "output_tokens": 300},
                 "estimated_cost_usd": cost}
                for i, s in enumerate(scores_list)
            ],
        }

    def test_aggregate_reports_rates_not_counts(self):
        clean = ev.score_english(good_result(), entry())
        dirty = ev.score_english(good_result(title_en="特定外来生物 x"), entry())
        agg = ev.aggregate(self._run("m", [clean, clean, clean, dirty]))
        self.assertEqual(agg["items"], 4)
        self.assertAlmostEqual(agg["title_japanese_pct"], 25.0)
        # valid_result() rejects a Japanese title_en (it would otherwise abort a
        # whole production run), so that item is a schema failure too.
        self.assertAlmostEqual(agg["schema_compliance_pct"], 75.0)
        self.assertAlmostEqual(agg["validation_failure_pct"], 25.0)

    def test_regression_in_any_guardrail_is_reported_as_a_blocker(self):
        import types
        clean = ev.score_english(good_result(), entry())
        dirty = ev.score_english(
            good_result(summary_en="The law has been enacted and is in force."), entry()
        )
        ev_dir = ev.EVAL_DIR
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                ev.EVAL_DIR = Path(tmp)
                ev.save_json(ev.EVAL_DIR / "results_english_base.json", self._run("base", [clean, clean]))
                ev.save_json(ev.EVAL_DIR / "results_english_cand.json", self._run("cand", [dirty, dirty], cost=0.004))
                args = types.SimpleNamespace(baseline="base", candidate="cand", language="english")
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    ev.compare(args)
                out = buf.getvalue()
            self.assertIn("BLOCKER", out)
            self.assertIn("do NOT switch the production model", out)
            # Cost improving must not suppress a quality blocker.
            self.assertIn("% of baseline", out)
        finally:
            ev.EVAL_DIR = ev_dir

    def test_no_regression_still_defers_to_human_review(self):
        import tempfile, types
        clean = ev.score_english(good_result(), entry())
        ev_dir = ev.EVAL_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ev.EVAL_DIR = Path(tmp)
                ev.save_json(ev.EVAL_DIR / "results_english_base.json", self._run("base", [clean, clean]))
                ev.save_json(ev.EVAL_DIR / "results_english_cand.json", self._run("cand", [clean, clean]))
                args = types.SimpleNamespace(baseline="base", candidate="cand", language="english")
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    ev.compare(args)
                out = buf.getvalue()
                self.assertTrue(list(Path(tmp).glob("side_by_side_*.md")))
            self.assertIn("no automatic regression detected", out)
            self.assertIn("cannot judge factual accuracy", out)
        finally:
            ev.EVAL_DIR = ev_dir


class TestSpendSafety(unittest.TestCase):
    def test_run_does_not_call_the_api_without_confirm_spend(self):
        import types
        called = {"n": 0}
        original = ev.summarizer.make_client
        ev.summarizer.make_client = lambda: called.__setitem__("n", called["n"] + 1)
        try:
            args = types.SimpleNamespace(model="claude-sonnet-5", language="english",
                                         limit=5, max_cost_usd=1.0, confirm_spend=False)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = ev.run_model(args)
            self.assertEqual(rc, 0)
            self.assertEqual(called["n"], 0, "must not construct a client without --confirm-spend")
            self.assertIn("Nothing was called", buf.getvalue())
        finally:
            ev.summarizer.make_client = original

    def test_unpriced_model_is_refused_before_any_call(self):
        import types
        args = types.SimpleNamespace(model="some-unreleased-model", language="english",
                                     limit=5, max_cost_usd=1.0, confirm_spend=True)
        with self.assertRaises(SystemExit) as raised:
            with contextlib.redirect_stdout(io.StringIO()):
                ev.run_model(args)
        self.assertIn("no configured pricing", str(raised.exception))

    def test_projected_cost_uses_the_full_output_ceiling(self):
        projected = ev.projected_cost_usd("claude-opus-4-8", 10, "english")
        # 10 * (800 in * $5 + 1500 out * $25) / 1e6
        self.assertAlmostEqual(projected, 10 * (800 * 5.0 + 1500 * 25.0) / 1e6)

    def test_script_never_writes_outside_the_eval_directory(self):
        source = (REPO_ROOT / "scripts" / "evaluate_summary_model.py").read_text(encoding="utf-8")
        self.assertIn("EVAL_DIR", source)
        for forbidden in ("OUTPUT_PATH", "CACHE_PATH =", "legal_updates.json\"", "summary_cache"):
            self.assertNotIn(f"save_json({forbidden}", source)


if __name__ == "__main__":
    unittest.main()
