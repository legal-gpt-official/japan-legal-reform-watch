"""Deterministic e-Gov evidence packs (real, trimmed e-Gov Law API v2 responses; no network)."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import explainer_evidence as ev  # noqa: E402
import explainer_state as es  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "egov"
LAW_ID = "335M50000100012"
NEW = "335M50000100012_20261001_508M60000100121"
OLD = "335M50000100012_20261001_508M60000100113"


def fixture_bytes(name: str) -> bytes:
    return (FIX / name).read_bytes()


class FakeFetch:
    """Serves the fixtures by e-Gov URL; any other URL is an HTTP 404."""

    def __init__(self, extra: dict[str, bytes] | None = None):
        self.routes = {
            f"{ev.API}/law_revisions/{LAW_ID}": fixture_bytes("law_revisions.json"),
            f"{ev.API}/law_data/{NEW}?response_format=json&law_full_text_format=json": fixture_bytes("law_data_new.json"),
            f"{ev.API}/law_data/{OLD}?response_format=json&law_full_text_format=json": fixture_bytes("law_data_old.json"),
        }
        self.routes.update(extra or {})
        self.calls: list[str] = []

    def __call__(self, url: str, limit: int):
        self.calls.append(url)
        if url not in self.routes:
            import requests
            response = requests.Response()
            response.status_code = 404
            raise requests.HTTPError(response=response)
        return self.routes[url], url


def client(**kw) -> ev.EgovClient:
    return ev.EgovClient(fetch=FakeFetch(**kw))


class NumberTests(unittest.TestCase):
    def test_kanji_numbers_in_both_e_gov_styles(self):
        self.assertEqual([ev.kanji_int(s) for s in ("百二十一", "一二一", "三百十", "十", "元", "１２")],
                         [121, 121, 310, 10, 1, 12])
        self.assertEqual(ev.law_number_key("令和八年厚生労働省令第百二十一号"),
                         ev.law_number_key("令和八年七月三一日厚生労働省令第一二一号"))
        self.assertNotEqual(ev.law_number_key("令和八年厚生労働省令第百二十一号"),
                            ev.law_number_key("令和八年厚生労働省令第百十三号"))


class TextTests(unittest.TestCase):
    def setUp(self):
        self.new = ev.provisions(json.loads(fixture_bytes("law_data_new.json"))["law_full_text"])
        self.old = ev.provisions(json.loads(fixture_bytes("law_data_old.json"))["law_full_text"])

    def test_article_diff_separates_substance_from_citation_edits(self):
        diff = ev.diff_provisions(self.old, self.new)
        self.assertEqual(diff["counts"], {"added": 1, "changed": 1, "citation_only": 1, "removed": 0})
        self.assertEqual([a["label"] for a in diff["added"]], ["第73条の8"])
        substantive = [c for c in diff["changed"] if not c["citation_only"]]
        self.assertEqual([c["label"] for c in substantive], ["第73条の7"])
        self.assertIn("この限りでない", substantive[0]["new"])
        self.assertNotIn("この限りでない", substantive[0]["old"])
        self.assertTrue(next(c for c in diff["changed"] if c["label"] == "第77条の6")["citation_only"])

    def test_amending_instruments_own_supplementary_provision(self):
        suppl = ev.suppl_for(self.new, "令和八年厚生労働省令第百二十一号")
        self.assertIn("令和八年十月一日から施行する", suppl)
        self.assertNotEqual(suppl, ev.suppl_for(self.new, "令和八年厚生労働省令第百十三号"))

    def test_delegation_implemented_versus_further(self):
        self.assertEqual(ev.delegation_kind("法第八十八条の三第一項に規定する厚生労働省令で定める事由は、次に掲げる事由とする。"),
                         "implements")
        self.assertEqual(ev.delegation_kind("前項の届出の様式その他必要な事項は、厚生労働省令で定める。"), "further")
        self.assertIsNone(ev.delegation_kind("届書を市町村長に提出しなければならない。"))


class AmendmentTests(unittest.TestCase):
    def test_in_force_amendment_with_diff_dates_and_provenance(self):
        out = ev.amendment_evidence(client(), LAW_ID, NEW, date(2026, 11, 15))
        self.assertEqual((out["status"], out["text_status"]), ("ok", "ok"))
        self.assertEqual(out["previous_revision_id"], OLD)
        self.assertEqual((out["promulgation_date"], out["stage"], out["instrument_form"]),
                         ("2026-07-31", "in_force", "in_force_instrument"))
        self.assertEqual([s["effective_date"] for s in out["effective_stages"]], ["2026-10-01"])
        self.assertEqual(out["transitional_sentences"], [])
        self.assertRegex(out["law_data_source"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(out["law_data_source"]["url"].startswith("https://laws.e-gov.go.jp/api/2/law_data/"))

    def test_same_amendment_before_commencement_is_promulgated(self):
        out = ev.amendment_evidence(client(), LAW_ID, NEW, date(2026, 9, 1))
        self.assertEqual((out["stage"], out["instrument_form"]), ("promulgated", "promulgated_instrument"))

    def test_partial_and_delegated_commencement(self):
        revs = json.loads(fixture_bytes("law_revisions.json"))
        first = copy.deepcopy(revs["revisions"][0])
        delegated = dict(first, law_revision_id="335M50000100012_20280730_508M60000100121",
                         amendment_enforcement_date=None,
                         amendment_enforcement_comment="公布の日から起算して二年を超えない範囲内において政令で定める日",
                         current_revision_status="UnEnforced")
        revs["revisions"].insert(0, delegated)
        c = client(extra={f"{ev.API}/law_revisions/{LAW_ID}": json.dumps(revs).encode()})
        out = ev.amendment_evidence(c, LAW_ID, NEW, date(2026, 11, 15))
        self.assertEqual(out["stage"], "partially_in_force")
        stage = next(s for s in out["effective_stages"] if s["delegated"])
        self.assertEqual((stage["effective_date"], stage["outer_limit"]), (None, "2028-07-30"))
        facts = ev.machine_facts({"instruments": [out]}, date(2026, 11, 15))
        self.assertEqual(facts["effective_date"]["value"], ["2026-10-01"])
        self.assertEqual(facts["effective_date"]["delegated"][0]["outer_limit"], "2028-07-30")
        self.assertIn("not a fixed date", ev.machine_statement("effective_date", facts["effective_date"]))

    def test_unlisted_revision_and_unreachable_api_are_errors_not_guesses(self):
        self.assertEqual(ev.amendment_evidence(client(), LAW_ID, "335M50000100012_20990101_X", date(2026, 11, 15))["status"],
                         "error")
        out = ev.amendment_evidence(client(), "999X", "999X_20260101_Y", date(2026, 11, 15))
        self.assertEqual(out["status"], "error")

    def test_api_budget_is_enforced(self):
        c = ev.EgovClient(fetch=FakeFetch(), max_calls=1)
        with self.assertRaises(ev.FetchBudgetExceeded):
            ev.amendment_evidence(c, LAW_ID, NEW, date(2026, 11, 15))


class DraftTests(unittest.TestCase):
    def test_instrument_name_and_target_law(self):
        cases = {
            "南極地域の環境の保護に関する法律施行規則の一部を改正する省令案に対する意見の募集（パブリック・コメント）":
                "南極地域の環境の保護に関する法律施行規則の一部を改正する省令",
            "「投資信託及び投資法人に関する法律施行規則の一部を改正する内閣府令（案）」に対するパブリックコメント":
                "投資信託及び投資法人に関する法律施行規則の一部を改正する内閣府令",
            "国民年金法施行規則の一部を改正する省令案に関する御意見の募集について": "国民年金法施行規則の一部を改正する省令",
        }
        for title, name in cases.items():
            self.assertEqual(ev.draft_instrument_name(title), name)
        self.assertEqual(ev.target_laws("投資信託及び投資法人に関する法律施行規則の一部を改正する内閣府令")[0],
                         "投資信託及び投資法人に関する法律施行規則")
        self.assertEqual(ev.target_laws("社会福祉法等の一部を改正する法律の施行に伴う厚生労働省関係省令の整備に関する省令"), [])

    def raw_draft(self, published="2026/06/09", deadline="2026/07/08"):
        return {"id": "raw-00000000000000d1", "title_ja": "国民年金法施行規則の一部を改正する省令案に関する御意見の募集について",
                "raw_summary": f"案の公示日：{published} 受付締切日時：{deadline} 19:00",
                "source_url": "https://public-comment.e-gov.go.jp/servlet/Public?CLASSNAME=PCMMSTDETAIL&id=1&Mode=0"}

    def search_route(self):
        body = json.dumps({"laws": [{"law_info": {"law_id": LAW_ID},
                                     "revision_info": {"law_title": "国民年金法施行規則"}}]}).encode()
        return {f"{ev.API}/laws?law_title={ev.quote('国民年金法施行規則')}": body}

    def test_api_route_narrows_same_titled_amendments_by_deadline(self):
        # 121 (2026-07-31) and 113 (2026-07-15) share a title here; only 121 follows a 07-20 deadline.
        out = ev.resolve_draft(client(extra=self.same_titled()), self.raw_draft("2026/06/09", "2026/07/20"),
                               date(2026, 11, 15))
        self.assertEqual(out["resolution"]["status"], "resolved")
        self.assertEqual(out["resolution"]["candidates"][0]["amendment_law_num"], "令和八年厚生労働省令第百二十一号")

    def same_titled(self):
        """Fixture revisions with 113 retitled like 121: two same-titled amendments after the draft."""
        revs = json.loads(fixture_bytes("law_revisions.json"))
        revs["revisions"][1]["amendment_law_title"] = revs["revisions"][0]["amendment_law_title"]
        return {**self.search_route(), f"{ev.API}/law_revisions/{LAW_ID}": json.dumps(revs).encode()}

    def test_several_candidates_stay_ambiguous_without_documents(self):
        out = ev.resolve_draft(client(extra=self.same_titled()), self.raw_draft("2026/06/09", "2026/06/30"),
                               date(2026, 11, 15))
        self.assertEqual(out["resolution"]["status"], "ambiguous")
        self.assertEqual(out["resolution"]["candidate_count"], 2)

    def test_draft_documents_citing_the_changed_article_resolve_ambiguity(self):
        out = ev.resolve_draft(client(extra=self.same_titled()), self.raw_draft("2026/06/09", "2026/06/30"),
                               date(2026, 11, 15), documents_text="第七十三条の七の改正及び第七十三条の八の新設")
        self.assertEqual(out["resolution"]["status"], "resolved")
        self.assertTrue(out["resolution"]["method"].startswith("article_overlap"))

    def test_nothing_after_the_draft_is_not_found_never_abandoned(self):
        out = ev.resolve_draft(client(extra=self.search_route()), self.raw_draft("2026/09/01", "2026/10/01"),
                               date(2026, 11, 15))
        self.assertEqual(out["resolution"]["status"], "not_found_in_egov")
        self.assertIn("not evidence that the draft was abandoned", out["resolution"]["note"])

    def test_repository_history_resolves_without_a_title_search(self):
        class Index:
            raw_by_id = {"raw-00000000000000e1": {
                "source_url": f"https://elaws.e-gov.go.jp/document?lawid={NEW}",
                "raw_summary": "改正法令: 国民年金法施行規則の一部を改正する省令; 改正法令番号: 令和八年厚生労働省令第百二十一号; 公布日: 2026-07-31"}}
        out = ev.resolve_draft(client(), self.raw_draft(), date(2026, 11, 15), index=Index())
        self.assertEqual((out["resolution"]["status"], out["resolution"]["method"]),
                         ("resolved", "repository_egov_history_exact_title"))


class PackTests(unittest.TestCase):
    class Index:
        raw_by_id = {"raw-00000000000000e1": {
            "id": "raw-00000000000000e1", "title_ja": "国民年金法施行規則の一部を改正する省令",
            "source_url": f"https://elaws.e-gov.go.jp/document?lawid={NEW}"}}

        def closure(self, ids):
            return list(ids)

        def fingerprint(self, ids):
            return "sha256:test"

    def build(self, previous=None):
        return ev.pack_for("raw-00000000000000e1", self.Index(), client(), date(2026, 11, 15), previous=previous)

    def test_pack_is_ready_and_its_case_file_template_is_valid(self):
        pack = self.build()
        self.assertEqual(pack["status"], "ready")
        self.assertEqual(pack["facts"]["amended_provisions"]["value"], ["第73条の7", "第73条の8"])
        template = dict(pack["case_file_template"], dossier_id="pension-test-2026")
        self.assertEqual(es.validate_state("pension-test-2026", template), ([], []))
        by = {f["category"]: f for f in template["facts"]}
        self.assertEqual(by["effective_date"]["status"], "confirmed")
        self.assertEqual(by["transitional_measures"]["status"], "not_applicable")
        for category in ("scope", "obligation_type", "old_vs_new", "policy_background", "business_impact"):
            self.assertEqual(by[category]["status"], "unconfirmed", category)
        # Interpretation is never pre-filled: the gate still blocks publication.
        self.assertTrue(es.critical_gaps(template))

    def test_rebuild_is_byte_identical_and_digest_ignores_fetch_times(self):
        first = self.build()
        later = self.build(previous=first)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(later, sort_keys=True))
        moved = copy.deepcopy(first)
        moved["instruments"][0]["law_data_source"]["fetched_at"] = "2030-01-01T00:00:00Z"
        self.assertEqual(ev.evidence_digest(first), ev.evidence_digest(moved))
        changed = copy.deepcopy(first)
        changed["instruments"][0]["law_data_source"]["sha256"] = "0" * 64
        self.assertNotEqual(ev.evidence_digest(first), ev.evidence_digest(changed))


class CheckTests(unittest.TestCase):
    def pack(self):
        return PackTests().build()

    def explainer(self, **over):
        data = {"current_stage": "in_force", "verified_at": "2026-11-15",
                "instruments": [{"number": "令和八年厚生労働省令第百二十一号"}],
                "key_dates": [{"kind": "promulgated", "date": "2026-07-31"},
                              {"kind": "effective", "date": "2026-10-01"}]}
        data.update(over)
        return data

    def test_consistent_explainer_passes_and_each_contradiction_is_named(self):
        pack = self.pack()
        self.assertEqual(ev.explainer_conflicts(self.explainer(), pack), [])
        wrong_prom = self.explainer(key_dates=[{"kind": "promulgated", "date": "2026-07-30"}])
        self.assertIn("differs from e-Gov", ev.explainer_conflicts(wrong_prom, pack)[0])
        wrong_eff = self.explainer(key_dates=[{"kind": "effective", "date": "2026-11-01"}])
        self.assertIn("not an effective date e-Gov records", ev.explainer_conflicts(wrong_eff, pack)[0])
        wrong_stage = self.explainer(current_stage="promulgated")
        self.assertIn("contradicts e-Gov", ev.explainer_conflicts(wrong_stage, pack)[0])

    def test_stage_is_judged_at_verified_at_so_time_passing_never_withholds(self):
        pack = self.pack()
        # Verified before commencement and correctly described as promulgated: no error later.
        self.assertEqual(ev.explainer_conflicts(self.explainer(current_stage="promulgated", verified_at="2026-09-20"), pack), [])
        self.assertTrue(ev.explainer_conflicts(self.explainer(current_stage="in_force", verified_at="2026-09-20"), pack))

    def test_case_file_citing_the_pack_must_agree(self):
        pack = self.pack()
        template = dict(pack["case_file_template"], dossier_id="pension-test-2026")
        self.assertEqual(ev.case_file_conflicts(template, pack), [])
        edited = copy.deepcopy(template)
        for fact in edited["facts"]:
            if fact["category"] == "effective_date":
                fact["statement"] = "Effective 2026-11-01."
            if fact["category"] == "instrument_form":
                fact["value"] = "promulgated_instrument"
        problems = ev.case_file_conflicts(edited, pack)
        self.assertEqual(len(problems), 2, problems)


if __name__ == "__main__":
    unittest.main()


class EvidenceWorkflowTests(unittest.TestCase):
    def test_workflow_is_bounded_and_never_merges_or_edits_explainers(self):
        text = (ROOT / ".github/workflows/explainer-evidence.yml").read_text(encoding="utf-8")
        self.assertIn("group: explainer-evidence", text)
        self.assertNotIn("japan-legal-reform-data-writer", text)
        self.assertIn("--max-api-calls", text)
        self.assertIn("git add -A -- data/explainer_evidence", text)
        for forbidden in ("pulls.merge", "addLabels", "legal-content-verified", "data/explainers/", "ANTHROPIC", "OPENAI"):
            self.assertNotIn(forbidden, text)
