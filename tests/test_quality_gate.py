"""Offline tests for scripts/quality_gate.py (publication policy) and
scripts/semantic_audit.py (sampled read-only semantic review)."""

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import quality_audit as qa  # noqa: E402
import quality_gate as qg  # noqa: E402
import semantic_audit as sa  # noqa: E402
import summarize_updates as su  # noqa: E402

ZH = {"title": "修订施行令的政令", "summary": "2025年第341号政令修订施行令。",
      "business_impact": "运营者可能受到影响。", "recommended_action": "建议查阅日文官方文本。"}


def item(iid, **over):
    it = {"id": iid, "title_ja": "関係政令の整備に関する政令", "source_name": "e-Gov Law Search (法令更新一覧)",
          "stage": "Scheduled to Take Effect", "area": "Other", "published_at": "2025-10-01",
          "summary_source": "claude", "title_en": "Cabinet Order No. 341 of 2025",
          "summary_en": "Cabinet Order No. 341 of 2025 amends an order.", "business_impact_en": "May affect.",
          "recommended_action_en": "Review the source.", "confidence": "medium", "ai_notes": "n",
          "summarized_at": "2026-10-01T00:00:00Z", "summary_model": "m",
          "translations": {"zh-Hans": dict(ZH)}}
    it.update(over)
    return it


class TestPolicy(unittest.TestCase):
    def test_every_high_check_has_a_policy_and_withholding_is_limited(self):
        self.assertEqual(set(qg.POLICY), set(qa.HIGH))
        # Only certain, high/medium-impact errors withhold; low-impact ones never do.
        for check in qg.WITHHOLD_EN + qg.WITHHOLD_ZH:
            certainty, impact, _ = qg.POLICY[check]
            self.assertEqual(certainty, "high", check)
            self.assertIn(impact, ("high", "medium"), check)
        for check in ("en_japanese", "en_title_truncated", "en_wrong_bureau", "zh_kana_or_ja_forms"):
            self.assertNotIn(check, qg.WITHHOLD_EN + qg.WITHHOLD_ZH)

    def test_plan_routes_findings_and_caps_requeues(self):
        found = {"en_law_number_year": ["raw-a"], "zh_era": ["raw-a", "raw-b"], "en_japanese": ["raw-c"],
                 "zh_stage_prefix": ["raw-d"], "pc_stage_results": ["raw-e"], "en_title_truncated": ["raw-f", "raw-a"]}
        d = qg.plan(found, {"attempts": {"raw-c": {"count": 2}}})
        self.assertEqual(d["withhold_en"], ["raw-a"])
        self.assertEqual(d["withhold_zh"], ["raw-b"], "an item whose English is withheld loses Chinese anyway")
        self.assertEqual(d["auto_correct"], ["raw-d"])
        self.assertEqual(d["manual"], ["raw-e"])
        self.assertEqual(d["parked"], ["raw-c"], "after the cap the item is parked, not requeued again")
        self.assertEqual(d["requeue"]["english"], ["raw-a"])
        self.assertEqual(d["requeue"]["english_title"], ["raw-f"], "a full English redo already rewrites the title")
        self.assertEqual(d["requeue"]["zh-Hans"], ["raw-b"])

    def test_apply_withholds_only_the_wrong_rendering(self):
        a = item("raw-a")
        b = item("raw-b")
        c = item("raw-c", stage="Public Comment Closed",
                 translations={"zh-Hans": dict(ZH, title="公开征求意见：某规则修订草案")})
        counts = qg.apply([a, b, c], {"withhold_en": ["raw-a"], "withhold_zh": ["raw-b"], "auto_correct": ["raw-c"],
                                      "manual": [], "requeue": {}, "parked": []})
        self.assertEqual(counts, {"withheld_en": 1, "withheld_zh": 1, "auto_corrected": 1})
        self.assertEqual(a["summary_source"], "rule_based")
        self.assertEqual(a["summary_en"], su.public_data.SUMMARY_EN)
        self.assertNotIn("translations", a)
        for kept in ("id", "title_ja", "source_name", "stage", "published_at"):
            self.assertIn(kept, a, "attribution and metadata always stay")
        self.assertNotIn("translations", b)
        self.assertEqual(b["summary_source"], "claude", "a Chinese defect never touches the English")
        self.assertEqual(c["translations"]["zh-Hans"]["title"], "（已结束）公开征求意见：某规则修订草案")

    def test_state_counts_requeues_and_forgets_fixed_items(self):
        d = {"requeue": {"english": ["raw-a"]}, "parked": ["raw-p"]}
        s = qg.next_state({"attempts": {"raw-a": {"count": 1}, "raw-gone": {"count": 1},
                                        "raw-p": {"count": 2}}}, d, "2026-10-17")
        self.assertEqual(s["attempts"]["raw-a"]["count"], 2)
        self.assertEqual(s["attempts"]["raw-p"]["count"], 2)
        self.assertNotIn("raw-gone", s["attempts"])

    def test_report_mode_changes_nothing(self):
        source = (REPO_ROOT / "scripts" / "quality_gate.py").read_text(encoding="utf-8")
        enforce_block = source[source.index('if args.mode == "enforce":'):source.index('print("")')]
        for write in ("save_json(qa.PUBLISHED_PATH", "save_json(STATE_PATH", "save_json(requeue.AUTO_REQUEUE_PATH"):
            self.assertIn(write, enforce_block, "every write is inside the enforce branch")
        self.assertEqual(source.count("save_json("), 3)


class TestDailyWorkflowPolicyStep(unittest.TestCase):
    WF = (REPO_ROOT / ".github" / "workflows" / "daily-update.yml").read_text(encoding="utf-8")

    def test_runs_in_report_mode_before_anything_is_derived_from_the_data(self):
        wf = self.WF
        gate = wf.index("name: Quality audit and publication policy")
        self.assertLess(wf.index("name: Translate Simplified Chinese updates"), gate)
        for later in ("name: Build yearly public archives", "name: Build public feeds",
                      "name: Build public pages", "name: Commit and push updated data"):
            self.assertLess(gate, wf.index(later), later)
        step = wf[gate:wf.index("\n      - name:", gate + 1)]
        self.assertIn("QUALITY_GATE_MODE: report", step, "enforcement needs the owner's approval")
        self.assertNotIn("API_KEY", step)

    def test_state_files_are_committed(self):
        for path in ("data/quality_state.json", "data/requeue_auto.json"):
            self.assertEqual(self.WF.count(path), 2, path)
            self.assertTrue((REPO_ROOT / path).exists(), path)


class TestSemanticAudit(unittest.TestCase):
    def _items(self):
        items = []
        for n in range(30):
            items.append(item(f"raw-{n:03d}", source_name=["e-Gov", "FSA", "MLIT"][n % 3],
                              summarized_at="2026-10-15T00:00:00Z" if n < 4 else "2026-01-01T00:00:00Z",
                              title_ja="経過措置に関する政令案" if n in (10, 11) else "お知らせ"))
        return items

    def test_sampling_is_deterministic_and_risk_weighted(self):
        items = self._items()
        a = sa.sample(items, {}, 10, "2026-10-16")
        b = sa.sample(items, {}, 10, "2026-10-16")
        self.assertEqual([i["id"] for i in a], [i["id"] for i in b])
        ids = [i["id"] for i in a]
        self.assertEqual(len(set(ids)), 10)
        for recent in ("raw-000", "raw-001", "raw-002", "raw-003"):
            self.assertIn(recent, ids, "recently changed items come first")
        self.assertTrue({"raw-010", "raw-011"} & set(ids), "risk markers are preferred")

    def test_schema_requires_quoted_evidence(self):
        issue = sa.SCHEMA["properties"]["issues"]["items"]
        self.assertIn("source_evidence", issue["required"])
        self.assertIn("output_evidence", issue["required"])
        for t in ("obligation_misstated", "scope_misidentified", "draft_vs_enacted", "transitional_omitted"):
            self.assertIn(t, sa.ISSUE_TYPES)

    def test_no_api_without_confirmation(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(
                sa.openai_provider, "make_client", side_effect=AssertionError("no API without confirmation")):
            self.assertEqual(sa.main(["--size", "2"]), 0)
        self.assertIn("Nothing was called", out.getvalue())

    def test_review_records_issues_without_writing_data(self):
        payload = {"verdict": "issues", "issues": [{"type": "draft_vs_enacted", "language": "en", "severity": "high",
                   "source_evidence": "法律案", "output_evidence": "has been enacted", "explanation": "x"}]}
        msg = mock.Mock()
        with mock.patch.object(sa.openai_provider, "create_structured", return_value=msg), \
                mock.patch.object(sa.su, "parse_summary_message", return_value=(payload, "m", {"output_tokens": 1})):
            result, usage = sa.review(object(), "gpt-6.1-sol", item("raw-x"), {})
        self.assertEqual(result["issues"][0]["type"], "draft_vs_enacted")
        source = (REPO_ROOT / "scripts" / "semantic_audit.py").read_text(encoding="utf-8")
        self.assertNotIn("save_json(", source)
        self.assertEqual(source.count("write_text("), 2)

    def test_workflow_is_manual_read_only(self):
        wf = (REPO_ROOT / ".github" / "workflows" / "semantic-audit.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", wf)
        self.assertNotIn("schedule:", wf, "a schedule needs the owner's approval")
        self.assertIn("contents: read", wf)
        self.assertNotIn("git push", wf)
        self.assertIn("OPENAI_SERVICE_TIER: flex", wf)


if __name__ == "__main__":
    unittest.main()
