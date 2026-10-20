"""Offline tests for hand-written reform explainers: validation, publication, queue, rendering."""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
TESTS_DIR = Path(__file__).resolve().parent
for path in (SCRIPTS_DIR, TESTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import build_public_pages as bpp  # noqa: E402
import explainers as ex  # noqa: E402
from test_law_timeline import ORDER_NO, STATUTE_NO, bill, comment, corpus, law  # noqa: E402

TODAY = date(2026, 10, 20)
AI_POLICY = {c: "ai_checked" for c in ex.CATEGORIES}
AI_POLICY_ENV = {ex.POLICY_ENV: ",".join(f"{c}=ai_checked" for c in ex.CATEGORIES)}


def explainer(dossier_id="export-control-order", related=("raw-order-a", "raw-pc-order"), **overrides):
    data = {
        "schema_version": 1,
        "dossier_id": dossier_id,
        "status": "published",
        "review_state": "ai_checked",
        "title_en": "Amendment of the Export Trade Control Order",
        "instrument_type": "cabinet_order",
        "current_stage": "partially_in_force",
        "related_item_ids": list(related),
        "instruments": [{"number": ORDER_NO, "name_ja": "輸出貿易管理令の一部を改正する政令",
                         "name_en": "Cabinet Order amending the Export Trade Control Order"}],
        "background": {"paragraphs": ["Why the order changes."], "source_ids": ["s1"]},
        "history": {"paragraphs": ["How it got here."], "source_ids": ["s1"]},
        "next_steps": {
            "confirmed": [{"date": "2027-04-01", "text": "Remaining provisions take effect.", "source_ids": ["s1"]}],
            "expected": ["Guidance is usually updated."],
            "uncertainties": ["Details of guidance are not yet known."],
        },
        "business_impact": {"paragraphs": ["Exporters may be affected."], "checklist": ["Review licences."],
                            "source_ids": ["s1"]},
        "key_dates": [
            {"kind": "promulgated", "date": "2026-09-16", "instrument_number": ORDER_NO, "label": "Promulgated",
             "source_ids": ["s1"]},
            {"kind": "partially_effective", "date": "2026-10-01", "instrument_number": ORDER_NO,
             "label": "First provisions take effect", "source_ids": ["s1"]},
            {"kind": "effective", "date": "2027-04-01", "instrument_number": ORDER_NO, "label": "Main provisions",
             "source_ids": ["s1"]},
        ],
        "official_sources": [
            {"id": "s1", "kind": "primary", "publisher": "e-Gov Law Search", "title": "Order text",
             "url": "https://elaws.e-gov.go.jp/document?lawid=x", "accessed_at": "2026-10-20"}
        ],
        "verified_at": "2026-10-20",
        "source_fingerprint": "",
        "review_notes": "",
    }
    data.update(overrides)
    return data


def stamped(index, data):
    data = copy.deepcopy(data)
    data["source_fingerprint"] = index.fingerprint(data["related_item_ids"])
    return data


class Base(unittest.TestCase):
    def setUp(self):
        self.raw = corpus()
        self.index = ex.OfficialIndex.build(self.raw)

    def report(self, data, explainers=None):
        explainers = explainers or {data["dossier_id"]: data}
        return ex.validate_all(explainers, self.index, TODAY)[data["dossier_id"]]


class TestValidation(Base):
    def test_valid_explainer_is_publishable(self):
        data = stamped(self.index, explainer())
        report = self.report(data)
        self.assertEqual(report.errors, [])
        self.assertEqual(report.warnings, [])
        self.assertTrue(ex.publishable(data, report, AI_POLICY))

    def test_insufficient_explainers_are_not_published(self):
        cases = {
            "draft": dict(status="draft"),
            "unreviewed": dict(review_state="unreviewed"),
            "no primary source": dict(official_sources=[
                {"id": "s1", "kind": "primary", "publisher": "News", "title": "Article",
                 "url": "https://news.example.com/a", "accessed_at": "2026-10-20"}]),
            "no uncertainties": dict(next_steps={"confirmed": [], "expected": [], "uncertainties": []}),
            "unsourced confirmed step": dict(next_steps={
                "confirmed": [{"text": "It takes effect.", "source_ids": []}], "uncertainties": ["x"]}),
            "unknown source id": dict(background={"paragraphs": ["x"], "source_ids": ["s9"]}),
            "unknown item": dict(related_item_ids=["raw-missing"]),
            "bad stage for type": dict(current_stage="diet_deliberation"),
            "future verification": dict(verified_at="2026-10-21"),
            "markup": dict(title_en="<script>alert(1)</script>"),
            "markdown link": dict(history={"paragraphs": ["see [this](https://x.y)"], "source_ids": ["s1"]}),
            "id mismatch": dict(dossier_id="another-id"),
            "no instruments": dict(instruments=[]),
            "bad instrument number": dict(instruments=[{"number": "Act No. 30", "name_ja": "x", "name_en": "x"}]),
            "wrong instrument name": dict(instruments=[{"number": ORDER_NO, "name_ja": "別の政令", "name_en": "x"}]),
        }
        for name, overrides in cases.items():
            with self.subTest(name):
                data = stamped(self.index, explainer(**overrides)) if name != "unknown item" else explainer(**overrides)
                report = ex.validate(
                    "export-control-order", data, self.index, TODAY
                )
                self.assertFalse(ex.publishable(data, report, AI_POLICY), report.errors)

    def test_promulgation_date_must_match_e_gov(self):
        data = explainer()
        data["key_dates"][0]["date"] = "2026-09-15"
        report = self.report(stamped(self.index, data))
        self.assertTrue(any("differs from e-Gov" in e for e in report.errors), report.errors)

    def test_comment_deadline_must_match_the_consultation_record(self):
        data = explainer()
        data["key_dates"].append({"kind": "comment_deadline", "date": "2026-08-01", "label": "Deadline",
                                  "source_ids": ["s1"]})
        report = self.report(stamped(self.index, data))
        self.assertTrue(any("comment deadline" in e for e in report.errors), report.errors)

    def test_stage_contradicting_the_records_is_an_error(self):
        cases = [
            (dict(current_stage="public_comment_closed"), "records a promulgation"),
            (dict(current_stage="in_force"), "still ahead"),
        ]
        for overrides, message in cases:
            with self.subTest(overrides):
                report = self.report(stamped(self.index, explainer(**overrides)))
                self.assertTrue(any(message in e for e in report.errors), report.errors)
        statute = explainer(
            "statute-x", ("raw-bill",), instrument_type="statute", current_stage="diet_deliberation",
            key_dates=[],
        )
        report = self.report(stamped(self.index, statute))
        self.assertTrue(any("enacted or promulgated" in e for e in report.errors), report.errors)

    def test_in_force_with_an_unfixed_effective_date_is_an_error(self):
        raw = corpus() + [
            {"id": "raw-order-pending", "title_ja": "x", "source_name": "e-Gov Law Search (法令更新一覧)",
             "source_url": "https://elaws.e-gov.go.jp/document?lawid=pending",
             "raw_summary": f"対象法令: 関税法; 法令番号: x; 改正法令: 輸出貿易管理令の一部を改正する政令; "
                            f"改正法令番号: {ORDER_NO}; 公布日: 2026-09-16"}
        ]
        index = ex.OfficialIndex.build(raw)
        data = explainer(current_stage="in_force", key_dates=[])
        data["next_steps"]["confirmed"] = []
        data = stamped(index, data)
        report = ex.validate(data["dossier_id"], data, index, date(2027, 5, 1))
        self.assertTrue(any("not yet fixed" in e for e in report.errors), report.errors)

    def test_dates_overtaken_since_verification_warn_and_notify(self):
        data = stamped(self.index, explainer(current_stage="promulgated"))
        report = self.report(data)
        self.assertIn("stage_overtaken:effective_date_passed", report.warnings)
        self.assertTrue(report.notices)
        self.assertTrue(ex.publishable(data, report, AI_POLICY))

    def test_one_reform_one_explainer(self):
        first = stamped(self.index, explainer("first-one"))
        second = stamped(self.index, explainer("second-one", related=("raw-order-b",)))
        reports = ex.validate_all({"first-one": first, "second-one": second}, self.index, TODAY)
        self.assertEqual(reports["first-one"].errors, [])
        self.assertTrue(any("already covered" in e for e in reports["second-one"].errors))

    def test_an_unrelated_reform_is_not_covered(self):
        data = stamped(self.index, explainer())
        by_item, _ = ex.published_by_item({data["dossier_id"]: data}, self.index, TODAY, AI_POLICY)
        self.assertIn("raw-order-c", by_item)  # same instrument, linked by R1
        self.assertNotIn("raw-statute", by_item)
        self.assertNotIn("raw-bill", by_item)


class TestFingerprint(Base):
    def test_new_related_record_requires_re_verification(self):
        data = stamped(self.index, explainer())
        raw = corpus() + [
            law("raw-order-d", "外国為替令", "昭和五十五年政令第二百六十号", "輸出貿易管理令の一部を改正する政令", ORDER_NO,
                "2026-09-16", "2027-10-01", first_seen_at="2026-10-21")
        ]
        index = ex.OfficialIndex.build(raw)
        report = ex.validate(data["dossier_id"], data, index, TODAY)
        self.assertIn("official_records_changed", report.warnings)
        self.assertTrue(ex.publishable(data, report, AI_POLICY))
        queue = ex.build_queue(index, {}, {data["dossier_id"]: data}, TODAY)
        update = [e for e in queue["entries"] if e["kind"] == "update"]
        self.assertEqual(update[0]["explainer_id"], data["dossier_id"])
        self.assertIn("raw-order-d", update[0]["new_related_item_ids"])

    def test_diet_status_change_on_the_same_url_changes_the_fingerprint(self):
        before = self.index.fingerprint(["raw-bill"])
        later = dict(bill("raw-bill-2", "建築士法の一部を改正する法律案", status="成立"))
        later["source_url"] = bill("raw-bill", "x")["source_url"]
        index = ex.OfficialIndex.build(corpus() + [later])
        self.assertNotEqual(before, index.fingerprint(["raw-bill"]))

    def test_fingerprint_is_order_independent(self):
        index = ex.OfficialIndex.build(list(reversed(corpus())))
        self.assertEqual(self.index.fingerprint(["raw-order-a"]), index.fingerprint(["raw-order-a"]))


class TestQueue(Base):
    def published(self, *ids):
        return {i: {"id": i, "area": "Economic Security / FDI", "stage": "Promulgated", "impact_level": "Medium"}
                for i in ids}

    def test_new_reform_is_queued_once_and_keeps_its_first_date(self):
        published = self.published("raw-order-a", "raw-statute")
        first = ex.build_queue(self.index, published, {}, TODAY)
        ids = [e["queue_id"] for e in first["entries"]]
        self.assertIn(f"instrument:{ORDER_NO}", ids)
        self.assertIn(f"instrument:{STATUTE_NO}", ids)
        second = ex.build_queue(self.index, published, {}, date(2026, 10, 25), previous=first)
        self.assertEqual(first, second)

    def test_covered_or_decided_reforms_are_not_queued(self):
        published = self.published("raw-order-a")
        done = stamped(self.index, explainer())
        skipped = stamped(self.index, explainer("skip-it", related=("raw-statute",), status="skipped",
                                                review_notes="Not material for foreign companies."))
        queue = ex.build_queue(self.index, published, {"export-control-order": done, "skip-it": skipped}, TODAY)
        self.assertEqual([e for e in queue["entries"] if e["kind"] == "new"], [])
        # The only entry is the finished explainer waiting for the human review the policy requires.
        self.assertEqual([(e["explainer_id"], e["reasons"]) for e in queue["entries"]],
                         [("export-control-order", ["awaiting_review:facts+procedure+outlook+assessment"])])
        with mock.patch.dict(os.environ, AI_POLICY_ENV):
            queue = ex.build_queue(self.index, published, {"export-control-order": done, "skip-it": skipped}, TODAY)
        self.assertEqual(queue["entries"], [])

    def test_unmonitored_areas_are_not_queued(self):
        published = {"raw-order-a": {"id": "raw-order-a", "area": "Other", "stage": "Promulgated"}}
        self.assertEqual(ex.build_queue(self.index, published, {}, TODAY)["entries"], [])

    def test_committed_queue_file_matches_its_schema(self):
        path = REPO_ROOT / "data" / "explainer_queue.json"
        queue = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(queue["schema_version"], ex.SCHEMA_VERSION)
        for entry in queue["entries"]:
            self.assertIn(entry["kind"], ("new", "update"))
            self.assertTrue(re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["first_queued_on"]))


class TestRendering(Base):
    def item(self, item_id, summary_source="rule_based"):
        return {"id": item_id, "title_en": "Order amendment", "title_ja": "輸出貿易管理令の一部を改正する政令",
                "source_name": "e-Gov Law Search (法令更新一覧)", "source_url": "https://elaws.e-gov.go.jp/document?lawid=a",
                "published_at": "2026-09-16", "summary_source": summary_source, "summary_en": "Template."}

    def entry(self, data):
        report = ex.validate(data["dossier_id"], data, self.index, TODAY)
        return ex.Published(data["dossier_id"], data, report)

    def test_explainer_sections_render_statically(self):
        html = bpp.render_item_page(self.item("raw-order-a"), self.index.timeline["raw-order-a"],
                                    {"raw-order-a"}, self.entry(stamped(self.index, explainer())))
        for anchor in ("explainer-background", "explainer-history", "explainer-next", "explainer-impact"):
            self.assertIn(f'id="{anchor}" open', html)
        for text in ("Confirmed next steps", "Expected procedural steps", "Outstanding uncertainties",
                     "Last verified: 2026-10-20", "Current stage of the reform: Partially in force", bpp.EXPLAINER_NOTICE,
                     'href="#explainer-source-1"', "Timeline of related official records",
                     "View the original Japanese source"):
            self.assertIn(text, html)
        self.assertLess(html.index("explainer-title"), html.index("item-timeline-title"))
        self.assertNotIn("noindex", html)  # an explainer makes a rule-based item page indexable

    def test_item_without_explainer_is_unchanged(self):
        html = bpp.render_item_page(self.item("raw-x"))
        self.assertNotIn("Reform explainer", html)
        self.assertIn("noindex", html)

    def test_untrusted_values_are_escaped_and_unsafe_urls_dropped(self):
        data = stamped(self.index, explainer())
        data["title_en"] = 'Title & "quotes" <b>'
        data["official_sources"][0]["url"] = "javascript:alert(1)"
        data["official_sources"][0]["title"] = "<img src=x onerror=alert(1)>"
        html = "\n".join(bpp.render_explainer(ex.Published(data["dossier_id"], data, ex.Report("x"))))
        self.assertNotIn("<img", html)
        self.assertNotIn("<b>", html)
        self.assertNotIn("javascript:", html)
        self.assertIn("&lt;img", html)

    def test_records_changed_notice_is_shown(self):
        data = explainer(source_fingerprint="sha256:old")
        html = "\n".join(bpp.render_explainer(self.entry(data)))
        self.assertIn("New or changed official records", html)

    @mock.patch.dict(os.environ, AI_POLICY_ENV)
    def test_build_publishes_only_valid_explainers_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            explainers_dir = tmp / "explainers"
            explainers_dir.mkdir()
            good = stamped(self.index, explainer())
            bad = stamped(self.index, explainer("bad-one", related=("raw-statute",), current_stage="nonsense"))
            for data in (good, bad):
                (explainers_dir / f"{data['dossier_id']}.json").write_text(json.dumps(data), encoding="utf-8")
            data_path, raw_path = tmp / "data.json", tmp / "raw.json"
            index_path, sitemap = tmp / "index.html", tmp / "sitemap.xml"
            data_path.write_text(json.dumps([self.item("raw-order-a"), self.item("raw-statute")]), encoding="utf-8")
            raw_path.write_text(json.dumps(self.raw), encoding="utf-8")
            index_path.write_text(
                f'<div id="cards">{bpp.STATIC_START}{bpp.STATIC_END}</div>{bpp.AREA_NAV_START}{bpp.AREA_NAV_END}',
                encoding="utf-8",
            )
            first = bpp.build(data_path, index_path, sitemap, TODAY, raw_path=raw_path, explainers_dir=explainers_dir)
            second = bpp.build(data_path, index_path, sitemap, TODAY, raw_path=raw_path, explainers_dir=explainers_dir)
            self.assertEqual(first["explainers"]["published"], 1)
            self.assertEqual(first["explainers"]["withheld_with_errors"], ["bad-one"])
            self.assertEqual(second["item_pages_changed"], 0)
            self.assertIn("Reform explainer", (tmp / "u" / "raw-order-a" / "index.html").read_text(encoding="utf-8"))
            self.assertNotIn("Reform explainer", (tmp / "u" / "raw-statute" / "index.html").read_text(encoding="utf-8"))
            sitemap_text = sitemap.read_text(encoding="utf-8")
            self.assertIn("u/raw-order-a/", sitemap_text)
            self.assertNotIn("u/raw-statute/", sitemap_text)


class TestReviewPolicy(Base):
    def test_default_policy_requires_human_review_for_every_category(self):
        self.assertEqual(ex.publish_policy({}), {c: "human_reviewed" for c in ex.CATEGORIES})
        data = stamped(self.index, explainer())
        report = self.report(data)
        self.assertTrue(report.ok)
        self.assertFalse(ex.publishable(data, report, ex.publish_policy({})))
        self.assertEqual(ex.awaiting_review(data, report, ex.publish_policy({})), list(ex.CATEGORIES))

    def test_policy_override_is_per_category_and_strict(self):
        policy = ex.publish_policy({ex.POLICY_ENV: "facts=ai_checked, procedure=ai_checked"})
        self.assertEqual(policy["facts"], "ai_checked")
        self.assertEqual(policy["assessment"], "human_reviewed")
        for bad in ("facts=unreviewed", "law=ai_checked", "facts", "facts=AI"):
            with self.subTest(bad), self.assertRaises(ValueError):
                ex.publish_policy({ex.POLICY_ENV: bad})

    def test_assessment_can_wait_while_facts_are_published(self):
        policy = ex.publish_policy({ex.POLICY_ENV: "facts=ai_checked,procedure=ai_checked,outlook=ai_checked"})
        data = stamped(self.index, explainer())
        report = self.report(data)
        self.assertTrue(ex.publishable(data, report, policy))
        self.assertEqual(ex.visible_categories(report, policy), {"facts", "procedure", "outlook"})
        entry = ex.Published(data["dossier_id"], data, report, frozenset(ex.visible_categories(report, policy)))
        html = "\n".join(bpp.render_explainer(entry))
        self.assertNotIn("Exporters may be affected.", html)
        self.assertNotIn("#explainer-impact", html)
        self.assertIn("Not shown until reviewed: business impact.", html)
        self.assertIn("Confirmed next steps", html)

    def test_human_review_needs_a_dated_record_and_expires_on_reverification(self):
        data = stamped(self.index, explainer(review_state="human_reviewed"))
        self.assertTrue(any("human_review" in e for e in self.report(data).errors))
        data["human_review"] = {"reviewer": "owner", "reviewed_at": "2026-10-20"}
        report = self.report(data)
        self.assertEqual(report.errors, [])
        self.assertTrue(ex.publishable(data, report, ex.publish_policy({})))
        data["human_review"]["reviewed_at"] = "2026-10-19"  # older than verified_at
        report = self.report(data)
        self.assertIn("human_review_outdated", report.warnings)
        self.assertFalse(ex.publishable(data, report, ex.publish_policy({})))

    def test_section_review_raises_single_categories(self):
        data = stamped(self.index, explainer(
            section_review={"facts": "human_reviewed"},
            human_review={"reviewer": "owner", "reviewed_at": "2026-10-20"},
        ))
        report = self.report(data)
        self.assertEqual(report.review["facts"], "human_reviewed")
        self.assertEqual(report.review["assessment"], "ai_checked")
        self.assertEqual(ex.visible_categories(report, ex.publish_policy({})), {"facts"})
        bad = stamped(self.index, explainer(section_review={"legal": "human_reviewed"}))
        self.assertTrue(any("section_review" in e for e in self.report(bad).errors))

    def test_queue_lists_explainers_awaiting_review(self):
        data = stamped(self.index, explainer())
        queue = ex.build_queue(self.index, {}, {data["dossier_id"]: data}, TODAY)
        entry = queue["entries"][0]
        self.assertEqual(entry["kind"], "update")
        self.assertIn("awaiting_review:facts+procedure+outlook+assessment", entry["reasons"])


class TestOfficialFacts(Base):
    def test_placeholder_effective_date_is_not_a_fixed_date(self):
        pending = {
            "id": "raw-order-pending", "title_ja": "x", "source_name": "e-Gov Law Search (法令更新一覧)",
            "source_url": "https://elaws.e-gov.go.jp/document?lawid=355CO0000000260_20280915_508CO0000000296",
            "raw_summary": f"対象法令: 関税法; 法令番号: x; 改正法令: 輸出貿易管理令の一部を改正する政令; "
                           f"改正法令番号: {ORDER_NO}; 公布日: 2026-09-16",
        }
        index = ex.OfficialIndex.build(corpus() + [pending])
        data = explainer()
        data["key_dates"].append({"kind": "effective", "date": "2028-09-15", "instrument_number": ORDER_NO,
                                  "label": "Takes effect", "source_ids": ["s1"]})
        data = stamped(index, data)
        report = ex.validate(data["dossier_id"], data, index, TODAY)
        self.assertTrue(any("placeholder" in e for e in report.errors), report.errors)

    def test_self_regulatory_sources_cannot_prove_state_legal_facts(self):
        jpx = {"id": "s1", "kind": "primary", "publisher": "JPX", "title": "Rule", "accessed_at": "2026-10-20",
               "url": "https://www.jpx.co.jp/rules/x.html"}
        report = self.report(stamped(self.index, explainer(official_sources=[jpx])))
        self.assertTrue(any("cite JPX or JSDA as self_regulatory" in e for e in report.errors))
        jpx_ok = dict(jpx, kind="self_regulatory")
        report = self.report(stamped(self.index, explainer(official_sources=[jpx_ok])))
        self.assertTrue(any("primary government source" in e for e in report.errors), report.errors)
        guideline = explainer(instrument_type="guideline", current_stage="published", key_dates=[],
                              official_sources=[jpx_ok])
        guideline["next_steps"]["confirmed"] = []
        self.assertEqual(self.report(stamped(self.index, guideline)).errors, [])

    def test_moving_stage_is_reverified_after_thirty_days(self):
        data = stamped(self.index, explainer(verified_at="2026-09-15"))
        for source in data["official_sources"]:
            source["accessed_at"] = "2026-09-15"
        self.assertIn("verification_old", self.report(data).warnings)


class TestFingerprintChanges(Base):
    def test_change_inside_an_existing_record_changes_the_fingerprint(self):
        before = self.index.fingerprint(["raw-order-a"])
        for field_name, value in (("raw_summary", "changed"), ("published_at", "2026-09-17"),
                                  ("stage_hint", "In Force"), ("comment_deadline", "2026-08-03")):
            with self.subTest(field_name):
                raw = copy.deepcopy(corpus())
                target = "raw-pc-order" if field_name == "comment_deadline" else "raw-order-a"
                next(r for r in raw if r["id"] == target)[field_name] = value
                self.assertNotEqual(before, ex.OfficialIndex.build(raw).fingerprint(["raw-order-a"]))

    def test_bill_carried_over_to_a_new_house_page_joins_the_closure(self):
        carried = bill("raw-bill-next", "建築士法の一部を改正する法律案", status="衆議院で審議中")
        carried["source_url"] = "https://www.shugiin.go.jp/internet/itdb_gian.nsf/html/gian/keika/next.htm"
        index = ex.OfficialIndex.build(corpus() + [carried])
        self.assertIn("raw-bill-next", index.closure(["raw-bill"]))
        other = dict(carried, id="raw-other", raw_summary="閣法; 審議状況: 成立; 提出回次: 221; 番号: 5")
        index = ex.OfficialIndex.build(corpus() + [other])
        self.assertNotIn("raw-other", index.closure(["raw-bill"]))

    def test_new_public_comment_record_on_the_same_draft_changes_the_fingerprint(self):
        before = self.index.fingerprint(["raw-pc-order"])
        result = dict(comment("raw-pc-result", "x"), source_url=comment("raw-pc-order", "x")["source_url"])
        self.assertNotEqual(before, ex.OfficialIndex.build(corpus() + [result]).fingerprint(["raw-pc-order"]))


class TestPageStatus(Base):
    def test_each_page_shows_its_own_instrument_status(self):
        status = self.index.page_status("raw-order-c")
        self.assertEqual(status["number"], ORDER_NO)
        self.assertEqual(status["target"], "輸出貿易管理令")
        self.assertEqual([d.isoformat() for d in status["effective_dates"]], ["2026-10-01", "2027-04-01"])
        self.assertIsNone(self.index.page_status("raw-bill"))
        data = stamped(self.index, explainer())
        entry = ex.Published(data["dossier_id"], data, self.report(data))
        html = "\n".join(bpp.render_explainer(entry, self.index.page_status("raw-order-b")))
        self.assertIn("This page&#x27;s record", html)
        self.assertIn("amending 外国為替令", html)
        self.assertIn("2026-10-01", html)


class TestRepositoryState(unittest.TestCase):
    def test_committed_explainers_validate(self):
        raw = json.loads((REPO_ROOT / "data" / "raw_items.json").read_text(encoding="utf-8"))
        index = ex.OfficialIndex.build(raw)
        explainers = ex.load_explainers()
        reports = ex.validate_all(explainers, index, TODAY)
        for dossier_id, report in reports.items():
            with self.subTest(dossier_id):
                self.assertEqual(report.errors, [])
                self.assertEqual(dossier_id, explainers[dossier_id]["dossier_id"])

    def test_pipeline_stages_never_touch_explainers(self):
        for name in ("summarize_updates.py", "translate_updates.py", "build_public_data.py", "fetch_updates.py",
                     "quality_gate.py", "requeue.py", "build_public_archives.py"):
            text = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("explainer", text, name)

    def test_daily_workflow_builds_and_commits_the_queue(self):
        workflow = (REPO_ROOT / ".github" / "workflows" / "daily-update.yml").read_text(encoding="utf-8")
        self.assertIn("python scripts/explainers.py queue --build", workflow)
        self.assertLess(workflow.index("explainers.py queue --build"), workflow.index("python scripts/build_public_pages.py"))
        for line in workflow.splitlines():
            if "git add data/" in line or "git diff --quiet -- data/" in line:
                self.assertIn("data/explainer_queue.json", line)
        self.assertEqual(workflow.count("EXPLAINER_PUBLISH_POLICY: ${{ vars.EXPLAINER_PUBLISH_POLICY }}"), 2)
        self.assertIn("scripts/explainers.py", workflow.split("name: Compile pipeline scripts")[1].split("- name:")[0])

    def test_pull_request_checks_run_the_full_suite_read_only(self):
        workflow = (REPO_ROOT / ".github" / "workflows" / "pr-checks.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertIn("python -m unittest discover -s tests", workflow)
        self.assertIn("python scripts/explainers.py validate", workflow)

    def test_operations_guide_names_the_commands(self):
        guide = (REPO_ROOT / "operations" / "EXPLAINER_OPERATIONS.md").read_text(encoding="utf-8")
        for command in ("queue --build", "context", "scaffold", "validate", "stamp", "preview"):
            self.assertIn(f"python scripts/explainers.py {command}", guide)
        for category in ex.CATEGORIES:
            self.assertIn(category, guide)
        self.assertIn(ex.POLICY_ENV, guide)
        prompt = (REPO_ROOT / "operations" / "CHATGPT_EXPLAINER_TASK_PROMPT_JA.md").read_text(encoding="utf-8")
        self.assertIn("python scripts/explainers.py validate", prompt)
        self.assertIn("human_reviewed", prompt)


if __name__ == "__main__":
    unittest.main()
