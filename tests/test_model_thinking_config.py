"""Offline tests for the per-model thinking setting and safety-refusal handling.

Sonnet 5.5 rejects {"type": "disabled"} with a 400 and runs adaptive thinking
when `thinking` is omitted, so a model switch that only changed an environment
variable would either fail every request or bill thinking tokens on every one.
These tests pin the request shape each stage sends per model.
"""

import sys
import types
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import anthropic_batch as ab  # noqa: E402
import evaluate_summary_model as ev  # noqa: E402
import summarize_updates as su  # noqa: E402
import translate_updates as tu  # noqa: E402

ITEM = {
    "id": "jlrw-test-1",
    "title_en": "Draft amendment to the Enforcement Ordinance",
    "title_ja": "施行令の一部を改正する政令案",
    "summary_en": "s", "business_impact_en": "b", "recommended_action_en": "r",
    "stage": "Public Comment Open", "source_name": "e-Gov",
    "source_url": "https://example.go.jp/x", "area": "Other", "impact_level": "Low",
}
RAW = {"raw_summary": "意見募集", "source_type": "rss"}


def _message(stop_reason="end_turn", category=None, text='{"a": 1}'):
    details = types.SimpleNamespace(category=category) if stop_reason == "refusal" else None
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=details,
        model="claude-sonnet-5-5",
        usage=None,
    )


class TestLowestThinkingConfig(unittest.TestCase):
    def test_sonnet_5_5_uses_between_tools_not_disabled(self):
        self.assertEqual(ab.lowest_thinking_config("claude-sonnet-5-5"), {"type": "between_tools"})

    def test_models_that_accept_disabled(self):
        for model in ("claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"):
            with self.subTest(model=model):
                self.assertEqual(ab.lowest_thinking_config(model), {"type": "disabled"})

    def test_models_that_cannot_turn_thinking_off_omit_it(self):
        for model in ("claude-opus-5-5", "claude-fable-5", "claude-fable-5-1"):
            with self.subTest(model=model):
                self.assertIsNone(ab.lowest_thinking_config(model))
                params = ab.with_lowest_thinking({"model": model})
                self.assertNotIn("thinking", params)

    def test_between_tools_carries_no_other_field(self):
        # `display`, `budget_tokens` or `block_binding` alongside it is a 400.
        self.assertEqual(list(ab.lowest_thinking_config("claude-sonnet-5-5")), ["type"])


class TestStageRequestShape(unittest.TestCase):
    def test_every_stage_sends_between_tools_on_sonnet_5_5(self):
        builders = {
            "english summary": su.summary_request_params("claude-sonnet-5-5", ITEM, RAW),
            "japanese summary": su.japanese_summary_request_params("claude-sonnet-5-5", ITEM, RAW),
            "zh-Hans translation": tu.translation_request_params(
                "claude-sonnet-5-5", ITEM, "zh-Hans", cache_ttl="5m"
            ),
        }
        for stage, params in builders.items():
            with self.subTest(stage=stage):
                self.assertEqual(params["thinking"], {"type": "between_tools"})
                # Effort is left at the API default (`high`), the highest level
                # between_tools accepts; output_config is added by the callers.
                self.assertNotIn("output_config", params)

    def test_current_production_models_keep_thinking_off(self):
        self.assertEqual(
            su.summary_request_params("claude-opus-4-8", ITEM, RAW)["thinking"], {"type": "disabled"}
        )
        self.assertEqual(
            tu.translation_request_params("claude-sonnet-5", ITEM, "zh-Hans", cache_ttl="5m")["thinking"],
            {"type": "disabled"},
        )

    def test_sonnet_5_5_is_priced_in_both_stages(self):
        for pricing in (su.model_pricing, tu.model_pricing):
            with self.subTest(pricing=pricing.__module__):
                price = pricing("claude-sonnet-5-5")
                self.assertEqual((price["input"], price["output"], price["cache_read"]), (2.0, 10.0, 0.20))


class TestRefusalHandling(unittest.TestCase):
    def test_refusal_raises_instead_of_parsing(self):
        before = ab.refusal_count()
        for parse in (su.parse_summary_message, tu.parse_translation_message):
            with self.subTest(parse=parse.__module__):
                with self.assertRaises(ab.ModelRefusal) as raised:
                    parse(_message("refusal", category="general_harms", text=""), "claude-sonnet-5-5")
                self.assertEqual(raised.exception.category, "general_harms")
        self.assertEqual(ab.refusal_count(), before + 2)

    def test_missing_category_is_reported_as_unspecified(self):
        with self.assertRaises(ab.ModelRefusal) as raised:
            ab.raise_if_refused(_message("refusal", category=None))
        self.assertEqual(raised.exception.category, "unspecified")

    def test_normal_response_still_parses(self):
        result, _model, _usage = su.parse_summary_message(_message(), "claude-sonnet-5-5")
        self.assertEqual(result, {"a": 1})

    def test_refusal_is_classified_and_never_fatal(self):
        exc = ab.ModelRefusal("cyber")
        self.assertEqual(su.classify_provider_error(exc), "refusal")
        self.assertEqual(tu.classify_error(exc), "refusal")
        self.assertNotIn("refusal", su.FATAL_PROVIDER_ERRORS)
        self.assertNotIn("refusal", tu.FATAL_PROVIDER_ERRORS)


class TestEvalHarnessPaths(unittest.TestCase):
    def test_run_and_compare_share_the_profile_path(self):
        self.assertEqual(
            ev.results_path("english", "hard", "claude-sonnet-5-5").name,
            "results_english_hard_claude-sonnet-5-5.json",
        )
        self.assertEqual(
            ev.results_path("japanese", "stratified", "claude-opus-4-8").name,
            "results_japanese_claude-opus-4-8.json",
        )

    def test_more_refusals_block_the_switch(self):
        import contextlib
        import io
        import tempfile

        ok = {"id": "a", "scores": {"schema_compliant": True}, "estimated_cost_usd": 0.01,
              "usage": {"input_tokens": 1, "output_tokens": 1}}
        refused = {"id": "a", "error_type": "refusal", "scores": {"schema_compliant": False}}
        ev_dir = ev.EVAL_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ev.EVAL_DIR = Path(tmp)
                ev.save_json(ev.results_path("english", "hard", "base"),
                             {"records": [ok], "estimated_cost_usd": 0.01})
                ev.save_json(ev.results_path("english", "hard", "cand"),
                             {"records": [refused], "estimated_cost_usd": 0.0})
                args = types.SimpleNamespace(baseline="base", candidate="cand",
                                             language="english", profile="hard")
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    ev.compare(args)
                self.assertTrue(list(Path(tmp).glob("side_by_side_english_hard_*.md")))
            self.assertIn("more safety refusals", buf.getvalue())
        finally:
            ev.EVAL_DIR = ev_dir


if __name__ == "__main__":
    unittest.main()
