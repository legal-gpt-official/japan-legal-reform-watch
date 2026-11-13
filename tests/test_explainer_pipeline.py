"""Dossier preflight, computed pipeline stages, resume after interruption, slot planning.

Uses the repository's real official records and a real published dossier copied
under a NEW id (so it needs a case file), exercising the same checks CI runs.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import explainer_locales as el  # noqa: E402
import explainer_pipeline as ep  # noqa: E402
import explainer_preflight as pf  # noqa: E402
import explainer_state as es  # noqa: E402
import explainers as ex  # noqa: E402

SOURCE_DOSSIER = "agricultural-modernisation-loan-order-300-2026"
NEW_ID = "fixture-agri-loan-order-2026"
SHA = "b" * 64
TODAY = date(2026, 11, 13)
INDEX = ex.OfficialIndex.build(ex.load_json(ex.RAW_PATH))


def fixture():
    """(english dict, english text, locale bundle, case file) for NEW_ID, all mutually consistent."""
    en = ex.load_json(ex.EXPLAINERS_DIR / f"{SOURCE_DOSSIER}.json")
    en["dossier_id"] = NEW_ID
    text = ex.dump(en)
    bundle = ex.load_json(el.TRANSLATIONS_DIR / f"{SOURCE_DOSSIER}.json")
    bundle["dossier_id"] = NEW_ID
    for lang in el.LOCALES:
        bundle["translations"][lang]["english_file_sha"] = el.blob_sha(text.encode("utf-8"))
    facts = []
    for n, category in enumerate(es.FACT_CATEGORIES, start=1):
        fact = {"id": f"f{n}", "category": category, "status": "confirmed",
                "statement": f"{category} confirmed.", "source_ids": ["s1"]}
        if category == "instrument_form":
            fact["value"] = "in_force_instrument"
        if category == "obligation_type":
            fact["value"] = ["none"]
        facts.append(fact)
    state = {
        "schema_version": 1, "dossier_id": NEW_ID, "kind": "new", "queue_id": "fixture",
        "seed_item_id": en["related_item_ids"][0], "hold": None,
        "sources": [{"id": "s1", "url": en["official_sources"][0]["url"], "kind": "egov_public_comment",
                     "status": "fetched", "fetched_at": "2026-11-12T00:00:00Z", "sha256": SHA,
                     "error_class": None, "attempts": 1}],
        "facts": facts, "unresolved": [],
    }
    return en, text, bundle, state


def check(en, bundle, state, text):
    return pf.check_dossier(NEW_ID, explainers={NEW_ID: en}, index=INDEX, today=TODAY,
                            bundles={NEW_ID: bundle}, states={NEW_ID: state} if state else {},
                            english_sha=el.blob_sha(text.encode("utf-8")))


class PreflightTests(unittest.TestCase):
    def test_consistent_new_dossier_passes(self):
        en, text, bundle, state = fixture()
        self.assertEqual(check(en, bundle, state, text)["errors"], [])

    def test_translation_number_is_reported_with_field_and_sentence(self):
        # Reproduces the 2026-11-10..12 CI failures ("number 12", "number 1800"):
        # the report must name the field to fix, not only the number.
        en, text, bundle, state = fixture()
        bundle["translations"]["zh-Hans"]["history"][0] += "另有1800家机构参与。"
        errors = check(en, bundle, state, text)["errors"]
        located = [e for e in errors if "zh-Hans: history[0] states number 1800" in e]
        self.assertTrue(located, errors)
        self.assertIn("1800", located[0])

    def test_quick_take_overrun_reports_the_cut_needed(self):
        en, text, bundle, state = fixture()
        en["quick_take"]["what_changed"] = "x" * 371
        text = ex.dump(en)
        errors = check(en, bundle, state, text)["errors"]
        self.assertIn("quick_take.what_changed: 371 characters, limit 360 (cut 11)", errors)
        self.assertEqual(sum("quick_take.what_changed" in e for e in errors), 1, errors)

    def test_missing_case_file_and_unconfirmed_fact_block(self):
        en, text, bundle, state = fixture()
        self.assertTrue(any("needs data/explainer_state" in e for e in check(en, bundle, None, text)["errors"]))
        state["facts"][2]["status"] = "unconfirmed"
        self.assertTrue(any("critical legal facts not confirmed: effective_date" in e
                            for e in check(en, bundle, state, text)["errors"]))

    def test_stale_stamp_is_a_warning_with_the_command_to_fix(self):
        en, text, bundle, state = fixture()
        bundle["translations"]["ja"]["english_file_sha"] = "0" * 40
        result = check(en, bundle, state, text)
        self.assertEqual(result["errors"], [])
        rendered = pf.render([result])
        self.assertIn("ja: stale canonical English file SHA", rendered)
        self.assertIn("sync-stamps", rendered)

    def test_sync_stamps_refuses_broken_translation_and_writes_exact_sha(self):
        en = ex.load_json(ex.EXPLAINERS_DIR / f"{SOURCE_DOSSIER}.json")
        bundle = ex.load_json(el.TRANSLATIONS_DIR / f"{SOURCE_DOSSIER}.json")
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for lang in el.LOCALES:
                bundle["translations"][lang]["english_file_sha"] = "0" * 40
            broken = copy.deepcopy(bundle)
            broken["translations"]["ja"]["background"] = broken["translations"]["ja"]["background"][:-1]
            (d / f"{SOURCE_DOSSIER}.json").write_text(ex.dump(broken), encoding="utf-8")
            with self.assertRaises(SystemExit):
                pf.sync_stamps(SOURCE_DOSSIER, ["ja"], explainers={SOURCE_DOSSIER: en}, index=INDEX,
                               today=TODAY, locale_dir=d)
            (d / f"{SOURCE_DOSSIER}.json").write_text(ex.dump(bundle), encoding="utf-8")
            self.assertEqual(pf.sync_stamps(SOURCE_DOSSIER, ["ja"], explainers={SOURCE_DOSSIER: en}, index=INDEX,
                                            today=TODAY, locale_dir=d), ["ja"])
            written = json.loads((d / f"{SOURCE_DOSSIER}.json").read_text(encoding="utf-8"))
            self.assertEqual(written["translations"]["ja"]["english_file_sha"], el.canonical_file_sha(SOURCE_DOSSIER))
            self.assertEqual(written["translations"]["zh-Hans"]["english_file_sha"], "0" * 40,
                             "a language not named is never re-certified")


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Scenario:
    """Snapshots of one new dossier moving through the pipeline."""

    def __init__(self):
        self.en, self.text, self.bundle, self.state = fixture()
        self.t0 = datetime(2026, 11, 13, 2, 0, tzinfo=timezone.utc)  # 11:00 JST slot
        self.pr = {"number": 900, "state": "open", "draft": True, "created_at": iso(self.t0), "merged_at": None,
                   "head_ref": f"explainer/{self.state['seed_item_id']}", "head_sha": "1" * 40, "same_repo": True,
                   "labels": [], "files": [{"filename": f"data/explainer_state/{NEW_ID}.json", "status": "added"}],
                   "contents": {f"data/explainer_state/{NEW_ID}.json": ex.dump(self.research_state())}}
        self.ci, self.publish, self.live = [], [], []

    def research_state(self):
        s = copy.deepcopy(self.state)
        s["facts"][2]["status"] = "unconfirmed"
        return s

    def snapshot(self):
        return {"pulls": [copy.deepcopy(self.pr)], "ci_runs": list(self.ci),
                "publish_runs": list(self.publish), "live_runs": list(self.live)}

    def add_files(self, en=True, locale=True, state=True):
        files = []
        contents = {}
        if state:
            files.append({"filename": f"data/explainer_state/{NEW_ID}.json", "status": "added"})
            contents[f"data/explainer_state/{NEW_ID}.json"] = ex.dump(self.state)
        if en:
            files.append({"filename": f"data/explainers/{NEW_ID}.json", "status": "added"})
            contents[f"data/explainers/{NEW_ID}.json"] = self.text
        if locale:
            files.append({"filename": f"data/explainer_locales/{NEW_ID}.json", "status": "added"})
            contents[f"data/explainer_locales/{NEW_ID}.json"] = ex.dump(self.bundle)
        self.pr["files"], self.pr["contents"] = files, contents

    def ci_run(self, run_id, at, conclusion, steps=()):
        self.ci.append({"id": run_id, "head_sha": self.pr["head_sha"], "status": "completed", "conclusion": conclusion,
                        "created_at": iso(at), "run_started_at": iso(at), "updated_at": iso(at + timedelta(seconds=80)),
                        "pull_requests": [900], "tests_ok": conclusion == "success", "failed_steps": list(steps)})


def run(scn, previous, now, **kw):
    return ep.reconcile(scn.snapshot(), previous, index=INDEX, main_explainers={}, main_bundles={},
                        main_states={}, now=now, run_id="1", **kw)


class ReconcileTests(unittest.TestCase):
    def test_full_lifecycle_with_interruption_resume_and_idempotency(self):
        scn = Scenario()
        led, _ = run(scn, None, scn.t0)
        e = led["dossiers"][NEW_ID]
        self.assertEqual((e["stage"], e["kind"]), ("RESEARCH_REQUIRED", "new"))
        self.assertIn("effective_date", e["next_action"])

        # Slot interrupted after the English was written: the next run resumes at EN_READY.
        scn.add_files(locale=False)
        led, _ = run(scn, led, scn.t0 + timedelta(hours=1))
        self.assertEqual(led["dossiers"][NEW_ID]["stage"], "EN_READY")
        self.assertIn("translations", led["dossiers"][NEW_ID]["next_action"])

        # Same inputs again (a retried run): byte-identical ledger, nothing new recorded.
        again, actions = run(scn, led, scn.t0 + timedelta(hours=1, minutes=30))
        self.assertEqual(ep.dump(again), ep.dump(led))
        self.assertEqual(actions, [])

        scn.add_files()
        scn.pr["head_sha"] = "2" * 40
        scn.ci_run(11, scn.t0 + timedelta(hours=2), "failure", ["Dossier preflight (changed explainers)"])
        led, _ = run(scn, led, scn.t0 + timedelta(hours=2, minutes=5))
        e = led["dossiers"][NEW_ID]
        self.assertEqual(e["stage"], "STAMPED_AND_SYNCED")
        self.assertIn("Fix the failed Pull request checks run 11", e["next_action"])
        self.assertEqual(e["ci"]["failed_runs"], 1)

        scn.pr["head_sha"] = "3" * 40
        scn.ci_run(12, scn.t0 + timedelta(hours=3), "success")
        led, _ = run(scn, led, scn.t0 + timedelta(hours=3, minutes=5))
        e = led["dossiers"][NEW_ID]
        self.assertEqual(e["stage"], "CI_PASSED")
        self.assertIn("substantive legal", e["next_action"])  # still draft
        self.assertEqual(e["ci"]["failed_runs"], 1, "an old failure is counted once, not per run")

        scn.pr.update({"draft": False, "labels": ["legal-content-verified"]})
        led, _ = run(scn, led, scn.t0 + timedelta(hours=3, minutes=10))
        self.assertIn("Automerge", led["dossiers"][NEW_ID]["next_action"])

        merged_at = scn.t0 + timedelta(hours=3, minutes=12)
        scn.pr.update({"state": "closed", "merged_at": iso(merged_at), "contents": {}})
        # No Publish yet, 30 minutes later: one bounded re-dispatch.
        led, actions = run(scn, led, merged_at + timedelta(minutes=30))
        self.assertEqual(led["dossiers"][NEW_ID]["stage"], "MERGED")
        self.assertEqual([a["workflow"] for a in actions], ["publish-explainer-pages.yml"])
        for n in range(5):
            led, actions = run(scn, led, merged_at + timedelta(minutes=40 + n))
        self.assertEqual(led["dossiers"][NEW_ID]["dispatches"]["publish"], ep.MAX_DISPATCHES_PER_DOSSIER)
        self.assertEqual(actions, [], "re-dispatch is capped per dossier")

        scn.publish.append({"id": 21, "status": "completed", "conclusion": "success",
                            "created_at": iso(merged_at + timedelta(minutes=2)),
                            "updated_at": iso(merged_at + timedelta(minutes=9))})
        scn.live.append({"id": 31, "status": "completed", "conclusion": "success",
                         "created_at": iso(merged_at + timedelta(minutes=10)),
                         "updated_at": iso(merged_at + timedelta(minutes=11))})
        led, _ = run(scn, led, merged_at + timedelta(minutes=50),
                     live_report={"run_id": 31, "dossiers_verified": [NEW_ID]})
        e = led["dossiers"][NEW_ID]
        self.assertEqual((e["stage"], e["publish"]["run_id"], e["live"]["run_id"]), ("LIVE_VERIFIED", 21, 31))
        day = led["daily"]["2026-11-13"]["new"]
        self.assertEqual(day, {"drafted": 1, "ci_passed": 1, "merged": 1, "pages_deployed": 1, "live_verified": 1})

    def test_live_report_listing_other_dossiers_does_not_count(self):
        scn = Scenario()
        scn.add_files()
        merged_at = scn.t0
        scn.pr.update({"state": "closed", "merged_at": iso(merged_at), "contents": {}})
        scn.publish.append({"id": 21, "status": "completed", "conclusion": "success",
                            "created_at": iso(merged_at + timedelta(minutes=1)), "updated_at": iso(merged_at + timedelta(minutes=5))})
        scn.live.append({"id": 31, "status": "completed", "conclusion": "success",
                         "created_at": iso(merged_at + timedelta(minutes=6)), "updated_at": iso(merged_at + timedelta(minutes=7))})
        led, _ = run(scn, None, merged_at + timedelta(minutes=8), live_report={"run_id": 31, "dossiers_verified": ["other"]})
        self.assertEqual(led["dossiers"][NEW_ID]["stage"], "PAGES_DEPLOYED")

    def test_update_prs_and_ops_prs_are_not_new_dossiers(self):
        scn = Scenario()
        scn.pr.update({"files": [{"filename": f"data/explainers/{NEW_ID}.json", "status": "modified"}],
                       "state": "closed", "merged_at": iso(scn.t0), "contents": {}})
        ops = {"number": 901, "state": "closed", "merged_at": iso(scn.t0), "created_at": iso(scn.t0),
               "files": [{"filename": "scripts/explainers.py", "status": "modified"}]}
        snap = scn.snapshot()
        snap["pulls"].append(ops)
        led, _ = ep.reconcile(snap, None, index=INDEX, main_explainers={NEW_ID: scn.en}, main_bundles={},
                              main_states={}, now=scn.t0 + timedelta(minutes=5))
        day = led["daily"]["2026-11-13"]
        self.assertEqual(day["new"]["merged"], 0)
        self.assertEqual(day["updates_merged"], 1)
        self.assertEqual(set(led["dossiers"]), {NEW_ID})

    def test_blocked_candidate_is_remembered_after_pr_closes_and_released_by_new_records(self):
        scn = Scenario()
        held = scn.research_state()
        held["hold"] = {"state": "BLOCKED", "reason": "Final ordinance text not published.",
                        "unblock_condition": "official_records_change"}
        scn.pr["contents"] = {f"data/explainer_state/{NEW_ID}.json": ex.dump(held)}
        led, _ = run(scn, None, scn.t0)
        hold = led["dossiers"][NEW_ID]["hold"]
        self.assertTrue(hold["active"])
        self.assertTrue(led["dossiers"][NEW_ID]["next_action"].startswith("ON HOLD (BLOCKED)"))
        # PR closed unmerged: the ledger still remembers the hold.
        scn.pr.update({"state": "closed", "merged_at": None})
        led, _ = run(scn, led, scn.t0 + timedelta(days=1))
        self.assertTrue(led["dossiers"][NEW_ID]["hold"]["active"])
        plan = ep.slot_plan(led, {"entries": [{"kind": "new", "queue_id": "q", "suggested_seed_item_id": scn.state["seed_item_id"]}]},
                            index=INDEX, research_dir=es.RESEARCH_DIR, today=TODAY)
        self.assertFalse([p for p in plan if p["tier"] == "new"], "a held candidate is not offered again")
        # Official records change -> fingerprint differs -> released.
        led["dossiers"][NEW_ID]["hold"]["fingerprint_at_hold"] = "sha256:older"
        led2, _ = run(scn, led, scn.t0 + timedelta(days=2))
        self.assertFalse(led2["dossiers"][NEW_ID]["hold"]["active"])


class SlotPlanTests(unittest.TestCase):
    def test_most_advanced_unfinished_first_then_unclaimed_new(self):
        ledger = {"dossiers": {
            "a": {"dossier_id": "a", "stage": "EN_READY", "next_action": "translate", "related_item_ids": ["raw-1"]},
            "b": {"dossier_id": "b", "stage": "CI_PASSED", "next_action": "label", "related_item_ids": ["raw-2"]},
            "c": {"dossier_id": "c", "stage": "LIVE_VERIFIED", "next_action": "done", "related_item_ids": ["raw-3"]},
        }}
        queue = ex.load_json(ex.QUEUE_PATH)
        plan = ep.slot_plan(ledger, queue, index=INDEX, research_dir=es.RESEARCH_DIR, today=TODAY, top=0)
        self.assertEqual([p["dossier_id"] for p in plan[:2]], ["b", "a"])
        news = [p for p in plan if p["tier"] == "new"]
        self.assertTrue(news)
        seeds = [p["seed_item_id"] for p in news]
        self.assertEqual(len(seeds), len(set(seeds)))
        self.assertTrue(all(p["branch"] == f"explainer/{p['seed_item_id']}" for p in news))

    def test_report_names_shortfall_and_plan(self):
        scn = Scenario()
        led, _ = run(scn, None, scn.t0)
        text = ep.report_markdown(led, [{"tier": "resume", "dossier_id": NEW_ID, "action": "x"}], [], [], scn.t0)
        self.assertIn("target 5", text)
        self.assertIn("shortfall 5", text)
        self.assertIn("Next slot plan", text)


class WorkflowTests(unittest.TestCase):
    def scripts(self, name):
        lines = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8").splitlines()
        blocks, i = [], 0
        while i < len(lines):
            if lines[i].strip() == "script: |":
                indent = len(lines[i]) - len(lines[i].lstrip()) + 2
                body = []
                i += 1
                while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) >= indent):
                    body.append(lines[i][indent:])
                    i += 1
                blocks.append("\n".join(body))
            else:
                i += 1
        return blocks

    def test_pipeline_workflow_javascript_parses(self):
        blocks = self.scripts("explainer-pipeline.yml")
        self.assertEqual(len(blocks), 3)
        for source in blocks:
            wrapped = "(async () => {\n" + source + "\n})();\n"
            result = subprocess.run(["node", "--check", "-"], input=wrapped, text=True, capture_output=True)
            if result.returncode != 0:  # older node: --check reads files only
                with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
                    f.write(wrapped)
                result = subprocess.run(["node", "--check", f.name], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_pipeline_workflow_safety_properties(self):
        text = (ROOT / ".github/workflows/explainer-pipeline.yml").read_text(encoding="utf-8")
        self.assertIn("group: explainer-pipeline-ledger", text)
        self.assertNotIn("japan-legal-reform-data-writer", text)
        self.assertIn("allowed = new Set(['publish-explainer-pages.yml', 'verify-live-explainers.yml'])", text)
        for forbidden in ("pulls.merge", "addLabels", "legal-content-verified"):
            self.assertNotIn(forbidden, text)
        self.assertIn('"50 1,2,3,4,5,11 * * *"', text)

    def test_automerge_accepts_case_files_only_beside_dossier_files(self):
        text = (ROOT / ".github/workflows/explainer-safe-automerge.yml").read_text(encoding="utf-8")
        self.assertIn("data/explainer_state/", text)
        self.assertIn("legal-content-verified", text)
        self.assertTrue(ep.automerge_eligible_files([{"filename": "data/explainers/a.json"},
                                                      {"filename": "data/explainer_state/a.json"}]))
        self.assertFalse(ep.automerge_eligible_files([{"filename": "data/explainer_state/a.json"}]))
        self.assertFalse(ep.automerge_eligible_files([{"filename": "data/explainers/a.json"},
                                                       {"filename": "scripts/x.py"}]))

    def test_pr_checks_run_preflight_before_the_suite(self):
        text = (ROOT / ".github/workflows/pr-checks.yml").read_text(encoding="utf-8")
        self.assertLess(text.index("explainer_preflight.py check"), text.index("unittest discover"))
        self.assertIn("explainer_state.py validate", text)
        self.assertIn("fetch-depth: 2", text)


if __name__ == "__main__":
    unittest.main()


class CollectorHarnessTests(unittest.TestCase):
    """Run the real collector JavaScript against a mocked GitHub API; reconcile its output."""

    def test_collector_snapshot_feeds_reconcile(self):
        source = WorkflowTests().scripts("explainer-pipeline.yml")[0]
        harness = r"""
const SOURCE = %s;
const now = new Date().toISOString();
const rest = {
  pulls: {list: 'pulls.list', listFiles: 'pulls.listFiles'},
  repos: {getContent: async ({path, ref}) => ({data: {encoding: 'base64', size: 10,
          content: Buffer.from(JSON.stringify({path, ref})).toString('base64')}})},
  actions: {listWorkflowRuns: 'runs', listJobsForWorkflowRun: 'jobs'},
};
const github = {rest, paginate: async (fn, params, mapFn) => {
  if (fn === 'pulls.list') {
    const data = [
      {number: 5, state: 'open', draft: true, title: 't', created_at: now, updated_at: now, merged_at: null,
       closed_at: null, base: {ref: 'main'}, head: {ref: 'explainer/x', sha: 'h5', repo: {full_name: 'o/r'}}, labels: []},
      {number: 4, state: 'closed', draft: false, title: 'ops', created_at: now, updated_at: now, merged_at: now,
       closed_at: now, base: {ref: 'main'}, head: {ref: 'ops', sha: 'h4', repo: {full_name: 'o/r'}}, labels: []},
      {number: 1, state: 'closed', updated_at: '2000-01-01T00:00:00Z', base: {ref: 'main'}, head: {}},
    ];
    let stop = false;
    const out = mapFn({data}, () => { stop = true; });
    return out;
  }
  if (fn === 'pulls.listFiles') return params.pull_number === 5
      ? [{filename: 'data/explainer_state/x.json', status: 'added'}] : [{filename: 'scripts/a.py', status: 'modified'}];
  if (fn === 'runs') return params.workflow_id === 'pr-checks.yml'
      ? [{id: 9, status: 'completed', conclusion: 'failure', head_sha: 'h5', created_at: now, updated_at: now,
          run_started_at: now, pull_requests: [{number: 5}]}] : [];
  if (fn === 'jobs') return [{name: 'tests', conclusion: 'failure', steps: [{name: 'Dossier preflight (changed explainers)', conclusion: 'failure'}]}];
  throw new Error('unexpected ' + fn);
}};
const context = {repo: {owner: 'o', repo: 'r'}};
const core = {info: () => {}, warning: m => console.error(m)};
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
new AsyncFunction('github', 'context', 'core', 'require', SOURCE)(github, context, core, require)
  .catch(e => { console.error(e); process.exit(1); });
""" % json.dumps(source)
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(["node", "-e", harness], cwd=tmp, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            snap = json.loads((Path(tmp) / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual([p["number"] for p in snap["pulls"]], [5], "ops PRs and stale PRs are not collected")
        self.assertIn("data/explainer_state/x.json", snap["pulls"][0]["contents"])
        self.assertEqual(snap["ci_runs"][0]["failed_steps"], ["Dossier preflight (changed explainers)"])
        self.assertEqual(snap["ci_runs"][0]["pull_requests"], [5])
        led, _ = ep.reconcile(snap, None, index=INDEX, main_explainers={}, main_bundles={}, main_states={},
                              now=datetime.now(timezone.utc))
        self.assertEqual(led["dossiers"]["x"]["stage"], "RESEARCH_REQUIRED")
