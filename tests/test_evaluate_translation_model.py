"""Offline tests for scripts/evaluate_translation_model.py.

No API calls and no repository writes: `build-set` is exercised on in-memory
items, `compare` writes into a temporary directory, and `run` must refuse to
spend without --confirm-spend. These pin what makes the harness worth trusting:
it measures production's own gate, it flags the failure modes a cheaper
translation model would introduce, and it does not flag faithful renderings.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import evaluate_translation_model as ev  # noqa: E402
import translate_updates as tu  # noqa: E402


def item(item_id="raw-1", **overrides):
    base = {
        "id": item_id,
        "stage": "Public Comment Open",
        "area": "Finance / AML",
        "source_name": "金融庁",
        "summary_source": "claude",
        "title_ja": "銀行法施行規則の一部を改正する内閣府令（案）",
        "title_en": "FSA public comment: draft Cabinet Office Order amending Banking Act rules",
        "summary_en": "The FSA opened public comment on a draft Cabinet Office Order. Comments are accepted until 2026-08-22.",
        "business_impact_en": "Banks may be affected if the draft is adopted.",
        "recommended_action_en": "Consider reviewing the official Japanese source.",
    }
    base.update(overrides)
    return base


def fields(**overrides):
    base = {
        "title": "公开征求意见：《银行法施行规则》部分修订内阁府令草案",
        "summary": "金融厅就内阁府令草案公开征求意见，意见截止日期为2026-08-22。",
        "business_impact": "如果草案获得采纳，银行可能会受到影响。",
        "recommended_action": "建议考虑查阅日文官方来源。",
    }
    base.update(overrides)
    return base


def score(f, entry=None):
    entry = entry or ev.frozen_entry(item())
    errors = tu.title_quality_errors(f["title"])
    return ev.score_translation(f, entry, errors, errors)


class TestEvaluationSet(unittest.TestCase):
    def test_only_ai_summarized_items_are_sampled(self):
        items = [item("raw-a"), item("raw-b", summary_source="rule_based"), item("raw-c", title_ja="")]
        frozen = ev.build_set(10, "stratified", items=items, cache={})
        self.assertEqual([e["id"] for e in frozen["items"]], ["raw-a"])

    def test_build_set_is_deterministic_and_freezes_the_prompt_inputs(self):
        items = [item(f"raw-{n}", stage=stage) for n, stage in enumerate(
            ("In Force", "Public Comment Open", "Bill Submitted", "In Force"))]
        a = ev.build_set(3, "stratified", items=items, cache={})
        b = ev.build_set(3, "stratified", items=list(reversed(items)), cache={})
        self.assertEqual([e["id"] for e in a["items"]], [e["id"] for e in b["items"]])
        self.assertEqual(a["prompt_version"], tu.PROMPT_VERSION)
        for key in ("title_ja", "stage", "source_name", "title_en", "summary_en"):
            self.assertIn(key, a["items"][0])

    def test_hard_set_puts_production_rejections_first(self):
        noisy = item("raw-a", title_ja="特定外来生物・育成就労に関する政令の一部を改正する政令" * 2)
        rejected = item("raw-z", title_ja="短い題名", stage="In Force")
        cache = {"rejected": {"zh-Hans": {"raw-z": {"attempts": 3}}}}
        frozen = ev.build_set(1, "hard", items=[noisy, rejected], cache=cache)
        self.assertEqual(frozen["items"][0]["id"], "raw-z")
        self.assertIn("previously_rejected", frozen["items"][0]["hard_signals"])

    def test_committed_sets_exist_and_match_the_prompt_version(self):
        for profile in ("hard", "stratified"):
            with self.subTest(profile=profile):
                frozen = json.loads(ev.set_path(profile).read_text(encoding="utf-8"))
                self.assertTrue(frozen["items"])
                self.assertEqual(frozen["locale"], "zh-Hans")
                ids = [e["id"] for e in frozen["items"]]
                self.assertEqual(len(ids), len(set(ids)))


class TestMetrics(unittest.TestCase):
    def test_a_faithful_translation_raises_no_flag(self):
        s = score(fields())
        self.assertEqual(ev._flags(s), "")
        self.assertTrue(s["stage_prefix_ok"])

    def test_production_title_gate_is_used(self):
        s = score(fields(title="公开征求意见：銀行法の施行規則"))
        self.assertTrue(s["title_rejected_final"])
        self.assertIn("title contains Japanese kana", s["title_reasons"])

    def test_number_not_in_the_english_is_flagged(self):
        s = score(fields(summary="金融厅公开征求意见，意见截止日期为2026-09-30。"))
        self.assertIn(30, s["numbers_added"])
        self.assertIn(22, s["numbers_dropped"])

    def test_faithful_number_spellings_are_not_flagged(self):
        entry = ev.frozen_entry(item(
            summary_en="Comments ran from January 20, 2026. It amends Act No. 128 of 1958 "
                       "and 令和八年政令第七十一号 (Reiwa 8).",
        ))
        f = fields(summary="意见期自2026-01-20起。修订昭和33年法律第128号及令和8年政令第71号（2026年）。")
        s = ev.score_translation(f, entry, [], [])
        self.assertEqual(s["numbers_added"], [])

    def test_wrong_instrument_in_title_is_flagged(self):
        entry = ev.frozen_entry(item(
            title_ja="食品添加物等の規格基準の一部を改正する告示（案）",
            title_en="Draft notice amending food additive standards",
            summary_en="A draft notice is out for comment.",
        ))
        f = fields(title="公开征求意见：食品添加物规格标准修订政令草案", summary="告示草案公开征求意见。")
        s = ev.score_translation(f, entry, [], [])
        self.assertEqual(s["instrument_unsupported"], ["政令"])

    def test_dropped_operative_instrument_is_flagged_but_a_quoted_one_is_not(self):
        entry = ev.frozen_entry(item(
            title_ja="標準化対象事務を定める政令に規定する事務の一部を改正する省令案",
            title_en="Draft ordinance on standardization",
        ))
        kept = ev.score_translation(fields(title="公开征求意见：标准化对象事务省令修订草案", summary="省令草案。"), entry, [], [])
        self.assertEqual(kept["instrument_missing"], [])
        dropped = ev.score_translation(fields(title="公开征求意见：标准化对象事务修订草案", summary="草案。"), entry, [], [])
        self.assertEqual(dropped["instrument_missing"], ["省令"])

    def test_obligation_wording_absent_from_the_english_is_flagged(self):
        s = score(fields(recommended_action="银行必须查阅日文官方来源。"))
        self.assertTrue(s["obligation_added"])
        entry = ev.frozen_entry(item(recommended_action_en="Banks must file a report."))
        s = ev.score_translation(fields(recommended_action="银行必须提交报告。"), entry, [], [])
        self.assertFalse(s["obligation_added"])

    def test_non_simplified_characters_are_flagged_but_kankoku_is_allowed(self):
        s = score(fields(summary="根据e-Gov法令検索，金融厅公开征求意见，截止日期为2026-08-22。"))
        self.assertEqual(s["non_simplified_chars"], ["検"])
        # The prompt asks for 勧告 explicitly; it must never count against a model.
        self.assertNotIn("勧", ev.NON_SIMPLIFIED_CHARS)
        # 断 is the Simplified form (Traditional is 斷).
        self.assertNotIn("断", ev.NON_SIMPLIFIED_CHARS)

    def test_body_kana_is_reported_but_does_not_block(self):
        s = score(fields(summary="第一号イ的要件。截止日期为2026-08-22。"))
        self.assertTrue(s["kana_anywhere"])
        self.assertNotIn("kana_pct", [key for key, _ in ev.BLOCKING_METRICS])


class TestCompare(unittest.TestCase):
    def _run(self, model, scored):
        return {"model": model, "estimated_cost_usd": 0.01, "records": [
            {"id": f"raw-{n}", "stage": "In Force", "area": "", "calls": 1,
             "estimated_cost_usd": 0.001, "output": fields(), "scores": s}
            for n, s in enumerate(scored)]}

    def test_a_worse_candidate_is_blocked_and_side_by_side_is_written(self):
        good = score(fields())
        bad = score(fields(title="公开征求意见：銀行法の規則"))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ev, "EVAL_DIR", Path(tmp)):
            ev.save_json(ev.set_path("hard"), {"items": [ev.frozen_entry(item("raw-0")), ev.frozen_entry(item("raw-1"))]})
            ev.save_json(ev.results_path("hard", "base"), self._run("base", [good, good]))
            ev.save_json(ev.results_path("hard", "cand"), self._run("cand", [good, bad]))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ev.main(["compare", "--baseline", "base", "--candidate", "cand", "--profile", "hard"])
            text = out.getvalue()
            self.assertIn("BLOCKER: more titles rejected", text)
            side = ev.side_by_side_path("hard", "base", "cand").read_text(encoding="utf-8")
            self.assertIn("title_ja", side)
            self.assertIn("title rejected", side)

    def test_result_files_match_the_existing_ignore_rule(self):
        self.assertTrue(ev.results_path("hard", "m").name.startswith("results_"))
        self.assertTrue(ev.side_by_side_path("hard", "a", "b").name.startswith("side_by_side_"))
        ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("data/eval/results_*.json", ignore)
        self.assertIn("data/eval/side_by_side_*.md", ignore)


class TestRunSafety(unittest.TestCase):
    def test_run_without_confirm_spend_calls_nothing(self):
        out = io.StringIO()
        with mock.patch.object(tu, "make_client", side_effect=AssertionError("API called")), \
                contextlib.redirect_stdout(out):
            self.assertEqual(ev.main(["run", "--model", "claude-haiku-5-5", "--profile", "hard"]), 0)
        self.assertIn("Nothing was called", out.getvalue())

    def test_unpriced_model_is_refused(self):
        with self.assertRaises(SystemExit):
            ev.main(["run", "--model", "some-unreleased-model", "--profile", "hard"])


class TestWorkflow(unittest.TestCase):
    WORKFLOW = REPO_ROOT / ".github" / "workflows" / "translation-model-eval.yml"

    def test_every_offered_model_is_priced(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        models = set(re.findall(r'-\s+"(claude-[a-z0-9-]+)"', text))
        self.assertIn("claude-haiku-5-5", models)
        for model in models:
            with self.subTest(model=model):
                self.assertIsNotNone(tu.model_pricing(model))

    def test_default_baseline_is_the_production_translation_model(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        baseline = re.search(r'baseline:.*?default:\s*"([^"]+)"', text, re.S).group(1)
        daily = (REPO_ROOT / ".github" / "workflows" / "daily-update.yml").read_text(encoding="utf-8")
        self.assertIn(f"ANTHROPIC_TRANSLATION_MODEL: {baseline}", daily)

    def test_workflow_is_read_only_and_keeps_the_key_in_one_step(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("contents: read", text)
        self.assertNotIn("git push", text)
        self.assertEqual(text.count("secrets.ANTHROPIC_API_KEY"), 1)


if __name__ == "__main__":
    unittest.main()
