"""Case files: structured legal facts, source ledger, holds and the publication gate."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import explainer_state as es  # noqa: E402
import explainers as ex  # noqa: E402

SHA = "a" * 64


def confirmed_state(dossier_id: str = "new-reform-2026", seed: str = "raw-0123456789abcdef") -> dict:
    """A case file whose every critical fact is settled from a fetched official source."""
    facts = []
    for n, category in enumerate(es.FACT_CATEGORIES, start=1):
        fact = {"id": f"f{n}", "category": category, "status": "confirmed",
                "statement": f"{category} confirmed from the official text.", "source_ids": ["s1"]}
        if category == "instrument_form":
            fact["value"] = "promulgated_instrument"
        if category == "obligation_type":
            fact["value"] = ["obligation"]
        if category in ("partial_effective_date", "transitional_measures"):
            fact = {"id": f"f{n}", "category": category, "status": "not_applicable",
                    "statement": "The instrument has no such provision.", "source_ids": ["s1"]}
        if category == "business_impact":
            fact = {"id": f"f{n}", "category": category, "status": "inference",
                    "statement": "Editorial analysis of likely effects.", "source_ids": []}
        facts.append(fact)
    return {
        "schema_version": 1, "dossier_id": dossier_id, "kind": "new", "queue_id": "egov:x",
        "seed_item_id": seed, "hold": None, "next_action": "Translate.",
        "sources": [{"id": "s1", "url": "https://laws.e-gov.go.jp/law/123", "kind": "egov_law",
                     "status": "fetched", "fetched_at": "2026-11-13T01:00:00Z", "sha256": SHA,
                     "error_class": None, "attempts": 1}],
        "facts": facts, "unresolved": [],
    }


class ValidateStateTests(unittest.TestCase):
    def test_complete_case_file_is_valid_and_has_no_gaps(self):
        state = confirmed_state()
        self.assertEqual(es.validate_state("new-reform-2026", state), ([], []))
        self.assertEqual(es.critical_gaps(state), [])
        self.assertEqual(es.instrument_form(state), "promulgated_instrument")

    def test_confirmed_fact_needs_a_fetched_official_source(self):
        state = confirmed_state()
        state["facts"][0]["source_ids"] = []
        errors, _ = es.validate_state("new-reform-2026", state)
        self.assertTrue(any("needs at least one official source" in e for e in errors))
        state = confirmed_state()
        state["sources"][0].update({"status": "failed", "error_class": "http_4xx",
                                    "last_attempt_at": "2026-11-13T01:00:00Z"})
        del state["sources"][0]["sha256"]
        errors, _ = es.validate_state("new-reform-2026", state)
        self.assertTrue(any("status 'fetched'" in e for e in errors))

    def test_legal_facts_can_never_be_inference(self):
        state = confirmed_state()
        state["facts"][2]["status"] = "inference"  # effective_date
        errors, _ = es.validate_state("new-reform-2026", state)
        self.assertTrue(any("only policy_background / business_impact may be inference" in e for e in errors))

    def test_non_official_source_and_declared_stage_are_rejected(self):
        state = confirmed_state()
        state["sources"][0]["url"] = "https://lawfirm.example.com/note"
        state["stage"] = "CI_PASSED"
        errors, _ = es.validate_state("new-reform-2026", state)
        self.assertTrue(any("official Japanese public-sector host" in e for e in errors))
        self.assertTrue(any("stage is computed" in e for e in errors))

    def test_unconfirmed_critical_fact_keeps_category_open(self):
        state = confirmed_state()
        state["facts"].append({"id": "f99", "category": "effective_date", "status": "unconfirmed",
                               "statement": "Second-stage commencement not yet fixed.", "source_ids": []})
        self.assertEqual(es.critical_gaps(state), ["effective_date"])

    def test_hold_validation(self):
        state = confirmed_state()
        state["hold"] = {"state": "RETRY_WAIT", "reason": "Gazette text not yet online.",
                         "unblock_condition": "official_records_change"}
        errors, _ = es.validate_state("new-reform-2026", state)
        self.assertTrue(any("RETRY_WAIT needs hold.retry_after" in e for e in errors))


class GateTests(unittest.TestCase):
    def explainer(self, stage="promulgated", status="published"):
        return {"status": status, "current_stage": stage, "related_item_ids": ["raw-0123456789abcdef"],
                "official_sources": [{"url": "https://laws.e-gov.go.jp/law/123"}]}

    def test_new_dossier_without_case_file_cannot_publish(self):
        errors = es.publication_gate("new-reform-2026", self.explainer(), None)
        self.assertTrue(errors and "needs data/explainer_state" in errors[0])
        self.assertEqual(es.publication_gate("new-reform-2026", self.explainer(status="draft"), None), [])

    def test_grandfathered_dossiers_publish_as_before(self):
        self.assertEqual(es.publication_gate("appi-2026-amendment", self.explainer(), None), [])

    def test_gaps_hold_and_stage_contradiction_block_publication(self):
        state = confirmed_state()
        self.assertEqual(es.publication_gate("new-reform-2026", self.explainer(), state), [])
        self.assertTrue(es.publication_gate("new-reform-2026", self.explainer(stage="bill_submitted"), state))
        gap = copy.deepcopy(state)
        gap["facts"][1]["status"] = "unconfirmed"
        self.assertIn("promulgation_date", es.publication_gate("new-reform-2026", self.explainer(), gap)[0])
        held = copy.deepcopy(state)
        held["hold"] = {"state": "BLOCKED", "reason": "Operative text unreadable.",
                        "unblock_condition": "official_records_change"}
        self.assertTrue(any("on hold" in e for e in es.publication_gate("new-reform-2026", self.explainer(), held)))

    def test_citation_warning_when_explainer_does_not_cite_the_verified_source(self):
        state = confirmed_state()
        explainer = self.explainer()
        self.assertEqual(es.citation_warnings(explainer, state), [])
        explainer["official_sources"] = [{"url": "https://www.mhlw.go.jp/other"}]
        self.assertTrue(es.citation_warnings(explainer, state))


class HoldTests(unittest.TestCase):
    def test_blocked_until_official_records_change(self):
        hold = {"state": "BLOCKED", "reason": "x", "unblock_condition": "official_records_change"}
        self.assertTrue(es.hold_active(hold, today=date(2026, 11, 13), fingerprint_now="sha256:a", fingerprint_at_hold="sha256:a"))
        self.assertFalse(es.hold_active(hold, today=date(2026, 11, 13), fingerprint_now="sha256:b", fingerprint_at_hold="sha256:a"))

    def test_retry_wait_releases_on_date(self):
        hold = {"state": "RETRY_WAIT", "reason": "x", "unblock_condition": "date", "retry_after": "2026-11-15"}
        self.assertTrue(es.hold_active(hold, today=date(2026, 11, 14), fingerprint_now=None, fingerprint_at_hold=None))
        self.assertFalse(es.hold_active(hold, today=date(2026, 11, 15), fingerprint_now=None, fingerprint_at_hold=None))

    def test_retry_budget(self):
        src = {"status": "failed", "error_class": "http_5xx", "attempts": 2}
        self.assertTrue(es.retry_due(src))
        self.assertFalse(es.retry_due({**src, "attempts": 3}))
        self.assertFalse(es.retry_due({**src, "error_class": "http_4xx"}))


class InitTests(unittest.TestCase):
    def test_new_state_lists_packet_official_urls_and_opens_every_fact(self):
        packet = {"queue_id": "egov:x", "records": [
            {"source_url": "https://public-comment.e-gov.go.jp/pcm/detail?id=1"},
            {"source_url": "https://www.shugiin.go.jp/x.htm"},
            {"source_url": "http://insecure.example.com/"},
        ], "e_gov_law_api": ["https://laws.e-gov.go.jp/api/2/law_revisions/abc"]}
        state = es.new_state("new-reform-2026", seed="raw-0123456789abcdef", kind="new", queue_id="egov:x",
                             packet=packet, today=date(2026, 11, 13))
        self.assertEqual([s["kind"] for s in state["sources"]], ["egov_public_comment", "diet", "egov_law"])
        self.assertEqual(es.critical_gaps(state), list(es.CRITICAL_CATEGORIES))
        self.assertEqual(es.validate_state("new-reform-2026", state)[0], [])

    def test_cli_init_hold_release_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self.assertEqual(es.main(["--dir", str(d), "init", "new-reform-2026", "--seed", "raw-0123456789abcdef"]), 0)
            with self.assertRaises(SystemExit):  # never overwrite a case file in progress
                es.main(["--dir", str(d), "init", "new-reform-2026", "--seed", "raw-0123456789abcdef"])
            es.main(["--dir", str(d), "hold", "new-reform-2026", "--state", "BLOCKED",
                     "--reason", "Operative ordinance text not published yet."])
            state = json.loads((d / "new-reform-2026.json").read_text(encoding="utf-8"))
            self.assertEqual(state["hold"]["state"], "BLOCKED")
            es.main(["--dir", str(d), "release", "new-reform-2026"])
            state = json.loads((d / "new-reform-2026.json").read_text(encoding="utf-8"))
            self.assertIsNone(state["hold"])


class RepositoryStateTests(unittest.TestCase):
    def test_committed_case_files_and_gate(self):
        errors, _ = es.validate_all()
        self.assertEqual(errors, [])

    def test_every_published_explainer_without_a_case_file_is_grandfathered(self):
        states = es.load_states()
        for dossier_id, data in ex.load_explainers().items():
            if data.get("status") == "published" and dossier_id not in states:
                self.assertIn(dossier_id, es.GRANDFATHERED, dossier_id)


if __name__ == "__main__":
    unittest.main()
