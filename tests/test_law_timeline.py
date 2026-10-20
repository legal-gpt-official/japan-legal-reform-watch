"""Offline tests for the conservative related-record timelines on item pages."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_public_pages as bpp  # noqa: E402
import law_timeline as lt  # noqa: E402

EGOV_LAW = "e-Gov Law Search (法令更新一覧)"
EGOV_PC = "e-Gov Public Comment (意見募集案件一覧)"
DIET = "House of Representatives (衆議院) 議案情報"


def law(item_id, target, number, amending, amending_number, promulgated, effective="", **extra):
    summary = (
        f"更新一覧日: 20261016; 対象法令: {target}; 法令番号: {number}; 改正法令: {amending}; "
        f"改正法令番号: {amending_number}; 公布日: {promulgated}"
    )
    if effective:
        summary += f"; 施行日: {effective}"
    record = {
        "id": item_id,
        "title_ja": amending,
        "source_name": EGOV_LAW,
        "source_url": f"https://elaws.e-gov.go.jp/document?lawid={item_id}",
        "raw_summary": summary,
        "first_seen_at": "2026-10-16",
    }
    record.update(extra)
    return record


def comment(item_id, title, published="2026/07/03", contact="経済産業省貿易経済安全保障局", deadline="2026/08/02 23:59"):
    return {
        "id": item_id,
        "title_ja": title,
        "source_name": EGOV_PC,
        "source_type": "public_comment_rss",
        "source_url": f"https://public-comment.e-gov.go.jp/servlet/Public?id={item_id}",
        "raw_summary": (
            f"案の公示日：{published} 受付締切日時：{deadline} カテゴリー：外国為替 "
            f"問合せ先（所管省庁・部局名等）：{contact}"
        ),
        "first_seen_at": "2026-07-04",
    }


def bill(item_id, title, status="成立", first_seen="2026-08-06", kind="閣法"):
    return {
        "id": item_id,
        "title_ja": title,
        "source_name": DIET,
        "source_url": f"https://www.shugiin.go.jp/internet/itdb_gian.nsf/html/gian/keika/{item_id}.htm",
        "raw_summary": f"{kind}; 審議状況: {status}; 提出回次: 221; 番号: 4",
        "first_seen_at": first_seen,
    }


ORDER = "輸出貿易管理令の一部を改正する政令"
ORDER_NO = "令和八年政令第二百九十六号"
MINISTRY_ORDINANCE = "貿易保険法施行規則の一部を改正する省令"
MINISTRY_ORDINANCE_NO = "令和八年経済産業省令第六十四号"
STATUTE = "建築士法の一部を改正する法律"
STATUTE_NO = "令和八年法律第七十四号"


def corpus():
    return [
        law("raw-order-a", "輸出貿易管理令", "昭和二十四年政令第三百七十八号", ORDER, ORDER_NO, "2026-09-16", "2026-10-01"),
        law("raw-order-b", "外国為替令", "昭和五十五年政令第二百六十号", ORDER, ORDER_NO, "2026-09-16", "2026-10-01"),
        law("raw-order-c", "輸出貿易管理令", "昭和二十四年政令第三百七十八号", ORDER, ORDER_NO, "2026-09-16", "2027-04-01"),
        comment("raw-pc-order", f"「{ORDER}（案）」等に対する意見募集について"),
        law("raw-statute", "建築士法", "昭和二十五年法律第二百二号", STATUTE, STATUTE_NO, "2026-07-31", "2026-10-31"),
        bill("raw-bill", f"{STATUTE}案"),
    ]


class TestParsing(unittest.TestCase):
    def test_draft_name_is_extracted_only_for_a_named_draft_instrument(self):
        self.assertEqual(
            lt.public_comment_draft_name(f"「{ORDER}（案）」等に対する意見募集について"), lt.normalize_title(ORDER)
        )
        self.assertEqual(
            lt.public_comment_draft_name(f"{MINISTRY_ORDINANCE}案に関する御意見の募集について"),
            lt.normalize_title(MINISTRY_ORDINANCE),
        )
        # A report or guideline is not a draft instrument.
        self.assertEqual(lt.public_comment_draft_name("「今後のインフラのマネジメントのあり方について 中間とりまとめ」に対する意見募集について"), "")
        self.assertEqual(lt.public_comment_draft_name(None), "")

    def test_instrument_year_and_issuers(self):
        self.assertEqual(lt.instrument_year("令和八年法律第六十七号"), 2026)
        self.assertEqual(lt.instrument_year("令和元年政令第一号"), 2019)
        self.assertEqual(lt.instrument_year("平成三十一年法律第二号"), 2019)
        self.assertIsNone(lt.instrument_year("第六十七号"))
        self.assertEqual(lt.instrument_issuers("令和八年内閣府・農林水産省令第十二号"), ["農林水産省", "内閣府"])
        self.assertEqual(lt.instrument_issuers(ORDER_NO), [])


class TestLinking(unittest.TestCase):
    def test_same_instrument_public_comment_and_bill_are_linked(self):
        by_item = lt.build_dossiers(corpus())
        order = by_item["raw-order-a"]
        self.assertIs(order, by_item["raw-order-b"])
        self.assertIs(order, by_item["raw-pc-order"])
        self.assertTrue(order.cross_source)
        self.assertEqual(order.next_step, "")
        labels = [e.label.split(" (")[0] for e in order.events]
        self.assertEqual(
            labels,
            [
                "Draft published for public comment",
                "Public comment deadline",
                "Promulgated",
                "Effective date stated by e-Gov Law Search",
                "Effective date stated by e-Gov Law Search",
            ],
        )
        self.assertEqual([e.when for e in order.events][0], date(2026, 7, 3))
        self.assertEqual(order.events[3].detail_ja, "外国為替令、輸出貿易管理令")
        self.assertEqual(order.events[3].item_ids, ("raw-order-a", "raw-order-b"))
        self.assertIn("JST", order.events[1].label)

        statute = by_item["raw-statute"]
        self.assertIs(statute, by_item["raw-bill"])
        self.assertEqual(statute.events[0].rank, 1)  # the undated bill sorts before promulgation
        self.assertIsNone(statute.events[0].when)
        self.assertIn("審議状況：成立", statute.events[0].detail_ja)

    def test_recurring_instrument_name_is_not_linked(self):
        records = corpus() + [
            law("raw-order-2025", "輸出貿易管理令", "昭和二十四年政令第三百七十八号", ORDER, "令和七年政令第十号", "2025-03-01")
        ]
        by_item = lt.build_dossiers(records)
        self.assertIsNot(by_item.get("raw-pc-order"), by_item["raw-order-a"])
        self.assertEqual(by_item["raw-pc-order"].next_step, lt.NEXT_STEP_DRAFT)

    def test_two_consultations_on_the_same_draft_link_neither(self):
        records = corpus() + [comment("raw-pc-order-2", f"{ORDER}案に関する意見募集について", published="2026/07/10")]
        by_item = lt.build_dossiers(records)
        self.assertNotIn("raw-pc-order", by_item["raw-order-a"].member_ids)
        self.assertNotIn("raw-pc-order-2", by_item["raw-order-a"].member_ids)

    def test_promulgation_must_follow_the_draft_within_the_window(self):
        for published in ("2026/09/20", "2025/09/01"):
            records = [r for r in corpus() if r["id"] != "raw-pc-order"]
            records.append(comment("raw-pc-order", f"{ORDER}案に対する意見募集", published=published))
            by_item = lt.build_dossiers(records)
            self.assertNotIn("raw-pc-order", by_item["raw-order-a"].member_ids, published)

    def test_issuing_ministry_must_appear_in_the_contact(self):
        ordinance = law(
            "raw-ord", "貿易保険法施行規則", "平成十七年経済産業省令第八号", MINISTRY_ORDINANCE,
            MINISTRY_ORDINANCE_NO, "2026-07-31", "2026-07-31",
        )
        draft = f"「{MINISTRY_ORDINANCE}（案）」等に関する意見募集について"
        linked = lt.build_dossiers([ordinance, comment("raw-pc", draft, published="2026/06/22")])
        self.assertIs(linked["raw-pc"], linked["raw-ord"])
        # An external agency is not mapped to its ministry in this version.
        skipped = lt.build_dossiers([ordinance, comment("raw-pc", draft, published="2026/06/22", contact="資源エネルギー庁")])
        self.assertIsNot(skipped["raw-pc"], skipped["raw-ord"])

    def test_bill_link_requires_a_unique_bill_and_a_statute_in_range(self):
        twin = corpus() + [bill("raw-bill-2", f"{STATUTE}案", kind="衆法")]
        self.assertNotIn("raw-bill", lt.build_dossiers(twin)["raw-statute"].member_ids)
        late = [r for r in corpus() if r["id"] != "raw-bill"] + [bill("raw-bill", f"{STATUTE}案", first_seen="2028-08-06")]
        self.assertNotIn("raw-bill", lt.build_dossiers(late)["raw-statute"].member_ids)
        unseen = [r for r in corpus() if r["id"] != "raw-bill"] + [dict(bill("raw-bill", f"{STATUTE}案"), first_seen_at="")]
        self.assertNotIn("raw-bill", lt.build_dossiers(unseen)["raw-statute"].member_ids)

    def test_unmatched_bills_get_a_status_specific_general_note(self):
        by_item = lt.build_dossiers(
            [
                bill("raw-p", "架空法の一部を改正する法律案", status="衆議院で審議中"),
                bill("raw-e", "別架空法案", status="成立"),
                bill("raw-l", "第三架空法案", status="未了"),
                bill("raw-w", "第四架空法案", status="撤回"),
            ]
        )
        self.assertEqual(by_item["raw-p"].next_step, lt.NEXT_STEP_BILL_PENDING)
        self.assertEqual(by_item["raw-e"].next_step, lt.NEXT_STEP_BILL_ENACTED)
        self.assertEqual(by_item["raw-l"].next_step, lt.NEXT_STEP_BILL_ENDED)
        self.assertEqual(by_item["raw-w"].next_step, lt.NEXT_STEP_BILL_ENDED)
        for text in (lt.NEXT_STEP_BILL_PENDING, lt.NEXT_STEP_DRAFT):
            self.assertIn("not a forecast", text)

    def test_single_event_instrument_and_other_sources_get_no_timeline(self):
        lone = law("raw-lone", "架空令", "令和元年政令第一号", "架空令の一部を改正する政令", "令和八年政令第一号", "2026-09-01")
        other = {"id": "raw-fsa", "title_ja": "お知らせ", "source_name": "Financial Services Agency (金融庁) 新着情報"}
        by_item = lt.build_dossiers([lone, other, "not a record", {"no": "id"}])
        self.assertEqual(by_item, {})

    def test_output_is_deterministic_regardless_of_input_order(self):
        first = lt.build_dossiers(corpus())
        second = lt.build_dossiers(list(reversed(corpus())))
        self.assertEqual(
            {k: (v.key, v.events, v.member_ids) for k, v in first.items()},
            {k: (v.key, v.events, v.member_ids) for k, v in second.items()},
        )

    def test_latest_detected_is_the_newest_member_first_seen(self):
        by_item = lt.build_dossiers(corpus())
        self.assertEqual(by_item["raw-order-a"].latest_detected, date(2026, 10, 16))

    def test_real_history_links_satisfy_the_rules(self):
        raw = json.loads((REPO_ROOT / "data" / "raw_items.json").read_text(encoding="utf-8"))
        by_id = {r["id"]: r for r in raw}
        by_item = lt.build_dossiers(raw)
        for dossier in {id(d): d for d in by_item.values()}.values():
            numbers = {
                lt.egov_law_fields(by_id[m]["raw_summary"]).get("改正法令番号")
                or lt.egov_law_fields(by_id[m]["raw_summary"]).get("法令番号")
                for m in dossier.member_ids
                if by_id[m]["source_name"].startswith(lt.EGOV_LAW_PREFIX)
            }
            self.assertLessEqual(len(numbers), 1, dossier.key)
            comments = [m for m in dossier.member_ids if by_id[m]["source_name"].startswith(lt.EGOV_PUBLIC_COMMENT_PREFIX)]
            bills = [m for m in dossier.member_ids if by_id[m]["source_name"].startswith(lt.DIET_PREFIX)]
            self.assertLessEqual(len(comments), 1, dossier.key)
            self.assertLessEqual(len(bills), 1, dossier.key)
            if numbers and comments:
                pc = by_id[comments[0]]
                names = {
                    lt.normalize_title(
                        lt.egov_law_fields(by_id[m]["raw_summary"]).get("改正法令")
                        or lt.egov_law_fields(by_id[m]["raw_summary"]).get("対象法令")
                    )
                    for m in dossier.member_ids
                    if by_id[m]["source_name"].startswith(lt.EGOV_LAW_PREFIX)
                }
                self.assertIn(lt.public_comment_draft_name(pc["title_ja"]), names)


class TestItemPageTimeline(unittest.TestCase):
    def setUp(self):
        self.by_item = lt.build_dossiers(corpus())

    def published(self, item_id):
        return {
            "id": item_id,
            "title_en": f"Title {item_id}",
            "title_ja": "日本語",
            "source_name": EGOV_LAW,
            "source_url": "https://elaws.e-gov.go.jp/x",
            "published_at": "2026-09-16",
            "summary_source": "claude",
            "summary_en": "AI summary.",
        }

    def test_timeline_marks_this_update_and_links_other_pages(self):
        html = bpp.render_item_page(
            self.published("raw-order-b"), self.by_item["raw-order-b"], {"raw-order-a", "raw-order-b", "raw-pc-order"}
        )
        self.assertIn("Timeline of related official records", html)
        self.assertIn(bpp.TIMELINE_NOTICE, html)
        self.assertIn("(this update)", html)
        self.assertIn('href="../../u/raw-pc-order/"', html)
        self.assertIn('lang="ja"', html)
        self.assertNotIn("What happens next", html)

    def test_no_dossier_means_no_timeline(self):
        html = bpp.render_item_page(self.published("raw-x"))
        self.assertNotIn("Timeline of related official records", html)

    def test_timeline_escapes_untrusted_fields_and_drops_unsafe_urls(self):
        evil = bill("raw-evil", '<script>alert(1)</script>"案', status="衆議院で審議中")
        evil["source_url"] = "javascript:alert(1)"
        dossier = lt.build_dossiers([evil])["raw-evil"]
        html = bpp.render_item_page(self.published("raw-evil"), dossier, {"raw-evil"})
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("javascript:", html)
        self.assertIn("What happens next", html)

    def test_lastmod_includes_the_newest_related_record(self):
        record = self.published("raw-order-a")
        self.assertEqual(bpp.item_lastmod(record), date(2026, 9, 16))
        self.assertEqual(bpp.item_lastmod(record, self.by_item["raw-order-a"]), date(2026, 10, 16))

    def test_build_reads_raw_history_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data, raw, index, sitemap = tmp / "data.json", tmp / "raw.json", tmp / "index.html", tmp / "sitemap.xml"
            data.write_text(json.dumps([self.published("raw-order-a"), self.published("raw-x")]), encoding="utf-8")
            raw.write_text(json.dumps(corpus()), encoding="utf-8")
            index.write_text(
                f'<div id="cards">{bpp.STATIC_START}{bpp.STATIC_END}</div>{bpp.AREA_NAV_START}{bpp.AREA_NAV_END}',
                encoding="utf-8",
            )
            first = bpp.build(data, index, sitemap, date(2026, 10, 20), raw_path=raw)
            second = bpp.build(data, index, sitemap, date(2026, 10, 20), raw_path=raw)
            self.assertEqual(first["item_pages_with_timeline"], 1)
            self.assertEqual(second["item_pages_changed"], 0)
            self.assertIn("Timeline", (tmp / "u" / "raw-order-a" / "index.html").read_text(encoding="utf-8"))
            missing = bpp.build(data, index, sitemap, date(2026, 10, 20), raw_path=tmp / "absent.json")
            self.assertEqual(missing["item_pages_with_timeline"], 0)


if __name__ == "__main__":
    unittest.main()
