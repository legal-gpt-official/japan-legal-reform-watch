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
                                        "raw-p": {"count": 2}}}, d, {"raw-a"}, "2026-10-17")
        self.assertEqual(s["attempts"]["raw-a"]["count"], 2)
        self.assertEqual(s["attempts"]["raw-p"]["count"], 2)
        self.assertNotIn("raw-gone", s["attempts"])

    def test_report_mode_changes_nothing(self):
        source = (REPO_ROOT / "scripts" / "quality_gate.py").read_text(encoding="utf-8")
        enforce_block = source[source.index('if args.mode == "enforce":'):source.index('print("")')]
        for write in ("save_json(qa.PUBLISHED_PATH", "save_json(STATE_PATH", "save_json(requeue.AUTO_REQUEUE_PATH"):
            self.assertIn(write, enforce_block, "every write is inside the enforce branch")
        self.assertEqual(source.count("save_json("), 3)


class TestEnforceOnTheRealCorpus(unittest.TestCase):
    """Runs the real enforce path (main) over a temporary copy of the published
    corpus, the way the daily step does, and compares item by item."""

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        paths = {}
        for name, src in (("published", qa.PUBLISHED_PATH), ("raw", qa.RAW_PATH), ("cache", qa.CACHE_PATH)):
            dst = self.tmp / f"{name}.json"
            shutil.copyfile(src, dst)
            paths[name] = dst
        (self.tmp / "state.json").write_text('{"version": 1, "attempts": {}}', encoding="utf-8")
        for target, attr, value in ((qa, "PUBLISHED_PATH", paths["published"]), (qa, "RAW_PATH", paths["raw"]),
                                    (qa, "CACHE_PATH", paths["cache"]), (qg, "STATE_PATH", self.tmp / "state.json"),
                                    (qg.requeue, "AUTO_REQUEUE_PATH", self.tmp / "auto.json")):
            patcher = mock.patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.before = json.loads(paths["published"].read_text(encoding="utf-8"))

    def _enforce(self, extra: dict) -> tuple[list[dict], str]:
        real_audit = qa.audit

        def audit(items, raw, cache):
            result = real_audit(items, raw, cache)
            for check, ids in extra.items():
                result["found"].setdefault(check, []).extend(ids)
            return result
        out = io.StringIO()
        with mock.patch.object(qa, "audit", audit), contextlib.redirect_stdout(out):
            self.assertEqual(qg.main(["--mode", "enforce"]), 0)
        return json.loads(qa.PUBLISHED_PATH.read_text(encoding="utf-8")), out.getvalue()

    def _translated_ai_ids(self, n):
        return [i["id"] for i in self.before
                if i.get("summary_source") == "claude" and (i.get("translations") or {}).get("zh-Hans")][:n]

    def _changed(self, after):
        old = {i["id"]: i for i in self.before}
        return sorted(i["id"] for i in after if i != old[i["id"]])

    def test_the_current_corpus_is_published_unchanged_and_reruns_are_idempotent(self):
        published_bytes = qa.PUBLISHED_PATH.read_bytes()
        after, out = self._enforce({})
        self.assertEqual(self._changed(after), [], "requeue-only findings never change what is published")
        self.assertEqual(len(after), len(self.before))
        self.assertEqual(qa.PUBLISHED_PATH.read_bytes(), published_bytes, "no rewrite, so no daily diff")
        state_bytes = (self.tmp / "state.json").read_bytes()
        auto_bytes = (self.tmp / "auto.json").read_bytes() if (self.tmp / "auto.json").exists() else b""
        self._enforce({})
        self.assertEqual((self.tmp / "state.json").read_bytes(), state_bytes, "a rerun spends no attempt")
        self.assertEqual((self.tmp / "auto.json").read_bytes() if (self.tmp / "auto.json").exists() else b"",
                         auto_bytes, "an unprocessed request keeps its time")

    def test_attempts_count_only_requests_a_stage_actually_processed(self):
        item_id = self._translated_ai_ids(1)[0]
        extra = {"zh_kana_or_ja_forms": [item_id]}
        self._enforce(extra)
        state_file = self.tmp / "state.json"
        count = lambda: json.loads(state_file.read_text(encoding="utf-8"))["attempts"][item_id]["count"]  # noqa: E731
        self.assertEqual(count(), 1)
        for _ in range(3):  # no budget for three days: still requested, no attempt spent
            self._enforce(extra)
        self.assertEqual(count(), 1)
        auto = json.loads((self.tmp / "auto.json").read_text(encoding="utf-8"))
        requested_at = auto["zh-Hans"][item_id]

        def processed():  # the translate stage redid it after the request, and it still fails
            cache = json.loads(qa.CACHE_PATH.read_text(encoding="utf-8"))
            from datetime import datetime, timedelta
            later = datetime.strptime(requested_at, "%Y-%m-%dT%H:%M:%SZ") + timedelta(seconds=1)
            cache["entries"]["zh-Hans"][item_id]["translated_at"] = later.strftime("%Y-%m-%dT%H:%M:%SZ")
            qa.CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            auto = json.loads((self.tmp / "auto.json").read_text(encoding="utf-8"))
            auto["zh-Hans"][item_id] = requested_at
            (self.tmp / "auto.json").write_text(json.dumps(auto), encoding="utf-8")
        processed()
        with mock.patch.object(qg, "datetime") as dt:
            dt.now.return_value.strftime.return_value = "2099-01-01T00:00:00Z"
            self._enforce(extra)
        self.assertEqual(count(), 2, "processed and still failing: a second attempt")
        auto = json.loads((self.tmp / "auto.json").read_text(encoding="utf-8"))
        requested_at = auto["zh-Hans"][item_id]
        processed()
        _, out = self._enforce(extra)
        self.assertIn("parked after 2 attempts 1", out, "after two processed attempts it waits for a person")
        auto = json.loads((self.tmp / "auto.json").read_text(encoding="utf-8"))
        self.assertNotIn(item_id, auto.get("zh-Hans", {}))
        after, _ = self._enforce({})
        self.assertEqual(self._changed(after), [])

    def test_a_broken_check_flagging_hundreds_withholds_nothing(self):
        broken = self._translated_ai_ids(400)
        after, out = self._enforce({"zh_era": broken[:300], "en_law_number_year": broken[300:]})
        self.assertIn("circuit breaker", out)
        self.assertIn("::warning::Publication policy would withhold 400", out)
        self.assertEqual(self._changed(after), [], "no item may lose its English or Chinese")
        auto = json.loads((self.tmp / "auto.json").read_text(encoding="utf-8"))
        self.assertTrue(auto.get("zh-Hans") or auto.get("english"), "regeneration is still requested")

    def test_at_the_limit_only_the_flagged_renderings_are_withheld(self):
        ids = self._translated_ai_ids(qg.MAX_WITHHOLD_PER_RUN)
        after, out = self._enforce({"zh_truncated": ids[:-1], "en_law_number_year": ids[-1:]})
        self.assertNotIn("circuit breaker", out)
        self.assertEqual(self._changed(after), sorted(ids))
        by_id = {i["id"]: i for i in after}
        for item_id in ids[:-1]:
            self.assertNotIn("translations", by_id[item_id])
            self.assertEqual(by_id[item_id]["summary_source"], "claude", "a Chinese defect keeps the English")
        self.assertEqual(by_id[ids[-1]]["summary_source"], "rule_based")
        for item_id in ids:
            for key in ("title_ja", "source_name", "source_url", "stage", "published_at"):
                self.assertEqual(by_id[item_id].get(key), {i["id"]: i for i in self.before}[item_id].get(key))

    def test_one_over_the_limit_trips_the_breaker(self):
        ids = self._translated_ai_ids(qg.MAX_WITHHOLD_PER_RUN + 1)
        after, out = self._enforce({"zh_truncated": ids})
        self.assertIn("circuit breaker", out)
        self.assertEqual(self._changed(after), [])


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
        self.assertIn("QUALITY_GATE_MODE: enforce", step)
        self.assertEqual(qg.MAX_WITHHOLD_PER_RUN, 15, "the breaker the owner approved enforcement with")
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

    def test_workflow_is_weekly_and_read_only(self):
        wf = (REPO_ROOT / ".github" / "workflows" / "semantic-audit.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", wf)
        self.assertIn('cron: "0 1 * * 1"', wf, "weekly, clear of the 21:00 UTC daily run")
        self.assertIn("contents: read", wf)
        self.assertNotIn("contents: write", wf)
        for forbidden in ("git push", "git commit", "requeue.json", "legal_updates.json"):
            self.assertNotIn(forbidden, wf, "an AI verdict alone never changes published data")
        self.assertIn("OPENAI_SERVICE_TIER: flex", wf)
        self.assertIn('--canaries "$CANARIES"', wf)

    def test_canaries_are_planted_copies_outside_the_sample(self):
        items = self._items()
        items[5]["stage"] = "Public Comment Open"
        items[6]["summary_en"] = "Cabinet Order No. 341 of 2025 amends an order; effective 2026-04-01."
        items[7]["summary_en"] = "Promulgated 2026-04-01."
        before = json.dumps(items, sort_keys=True)
        chosen = {"raw-000", "raw-001"}
        seeded = sa.seed_canaries(items, chosen, 5, "2026-10-16")
        self.assertEqual(json.dumps(items, sort_keys=True), before, "the published items are never mutated")
        kinds = [k for k, _ in seeded]
        self.assertEqual(kinds, ["draft_vs_enacted", "obligation_misstated", "identifier_error", "date_error",
                                 "zh_meaning_shift"])
        ids = [c["id"] for _, c in seeded]
        self.assertEqual(len(set(ids)), 5)
        self.assertFalse(chosen & set(ids))
        by_kind = dict(seeded)
        self.assertIn("enacted", by_kind["draft_vs_enacted"]["summary_en"])
        self.assertIn("of 2020", by_kind["identifier_error"]["summary_en"])
        self.assertIn("2027-04-01", by_kind["date_error"]["summary_en"])
        self.assertEqual(seeded, sa.seed_canaries(items, chosen, 5, "2026-10-16"), "deterministic")


if __name__ == "__main__":
    unittest.main()
