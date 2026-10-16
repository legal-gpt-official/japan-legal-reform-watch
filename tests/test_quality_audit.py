"""Offline tests for scripts/quality_audit.py and the stage-prefix alignment.

Each defect class the 2026-10-16 multilingual audit found has a synthetic item
that must be flagged, and the false positives met while building the audit
must stay unflagged.
"""

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import quality_audit as qa  # noqa: E402
import translate_updates as tu  # noqa: E402

EGOV_RAW = ("更新一覧日: 20260812; 対象法令: 特定複合観光施設区域整備法施行令; 法令番号: 平成三十一年政令第七十二号; "
            "改正法令: 関係政令の整備に関する政令; 改正法令番号: 令和七年政令第三百四十一号; 公布日: 2025-10-01; 施行日: 2027-04-01")
GOOD_EN = {
    "title_en": "Cabinet Order No. 341 of 2025 amending the Integrated Resort Enforcement Order",
    "summary_en": "Cabinet Order No. 341 of 2025 amends the Enforcement Order (Cabinet Order No. 72 of 2019); "
                  "promulgated 2025-10-01, effective 2027-04-01.",
    "business_impact_en": "Integrated resort operators may be affected.",
    "recommended_action_en": "Review the official Japanese text.",
    "confidence": "medium", "ai_notes": "Based on the title and listing metadata.",
}
GOOD_ZH = {"title": "修订《特定综合度假区区域整备法施行令》的政令",
           "summary": "2025年第341号政令修订施行令（2019年第72号政令）；2025-10-01公布，2027-04-01生效。",
           "business_impact": "综合度假区运营者可能受到影响。",
           "recommended_action": "建议查阅日文官方文本。"}


def item(iid="raw-egov", **over):
    it = {"id": iid, "title_ja": "関係政令の整備に関する政令", "source_name": "e-Gov Law Search (法令更新一覧)",
          "stage": "Scheduled to Take Effect", "published_at": "2025-10-01", "summary_source": "claude",
          **GOOD_EN, "translations": {"zh-Hans": dict(GOOD_ZH)}}
    for key, value in over.items():
        if key == "zh":
            it["translations"]["zh-Hans"].update(value)
        else:
            it[key] = value
    return it


def run(*items, raw=None):
    raw = raw or {"raw-egov": {"raw_summary": EGOV_RAW}}
    cache = {"entries": {"zh-Hans": {i["id"]: dict(i["translations"]["zh-Hans"])
                                    for i in items if i.get("translations")}}}
    return qa.audit(list(items), raw, cache)["found"]


class TestQualityAudit(unittest.TestCase):
    def test_a_clean_item_raises_nothing(self):
        self.assertEqual(run(item()), {})

    def test_each_english_defect_is_flagged(self):
        cases = {
            "en_japanese": item(summary_en=GOOD_EN["summary_en"] + " (棄却)"),
            "en_title_truncated": item(title_en="Cabinet Order amending the Enforcement Order of the Act on..."),
            "en_main_law_number": item(summary_en="A Cabinet Order amends the Enforcement Order (Cabinet Order No. 72 of 2019); "
                                                  "promulgated 2025-10-01, effective 2027-04-01.",
                                       title_en="Cabinet Order amending the Integrated Resort Enforcement Order"),
            "en_law_number_year": item(summary_en=GOOD_EN["summary_en"].replace("No. 72 of 2019", "No. 72 of 2033")),
            "en_field_names": item(summary_en=GOOD_EN["summary_en"] + " The raw_summary was empty."),
            "en_wrong_bureau": item(summary_en=GOOD_EN["summary_en"] + " Contact: Health and Welfare Bureau."),
        }
        for check, it in cases.items():
            with self.subTest(check=check):
                self.assertIn(check, run(it))

    def test_official_mhlw_names_are_not_wrong_bureaus(self):
        for name in ("Health and Welfare Bureau for the Elderly", "Health and Welfare Bureau for Persons with Disabilities"):
            self.assertNotIn("en_wrong_bureau", run(item(summary_en=GOOD_EN["summary_en"] + f" Contact: {name}.")))

    def test_the_pre_2023_mhlw_name_is_wrong_only_after_the_reorganization(self):
        old = "Contact: Pharmaceutical Safety and Environmental Health Bureau."
        self.assertIn("en_wrong_bureau", run(item(summary_en=GOOD_EN["summary_en"] + " " + old, published_at="2026-08-26")))
        self.assertNotIn("en_wrong_bureau", run(item(summary_en=GOOD_EN["summary_en"] + " " + old, published_at="2022-05-01")))

    def test_court_case_code_romanization(self):
        court = {"raw-court": {"raw_summary": "令和8年6月22日 最高裁判所第三小法廷 決定 棄却 東京高等裁判所 令和7(ラ)1003"}}
        base = dict(id="raw-court", title_ja="令和8(ク)407 宗教法人解散命令申立事件", source_name="Courts in Japan (裁判所)",
                    stage="Court Decision", published_at="2026-06-22",
                    title_en="Supreme Court decision on religious corporation dissolution petition",
                    business_impact_en="x.", recommended_action_en="Review the decision.")
        bad = item(**base, summary_en="Decision of 2026-06-22 on appeal from Reiwa 7 (La) No. 1003.")
        good = item(**base, summary_en="Decision of 2026-06-22 on appeal from Reiwa 7 (Ra) No. 1003.")
        self.assertIn("en_case_code", run(bad, raw=court))
        self.assertNotIn("en_case_code", run(good, raw=court))

    def test_each_chinese_defect_is_flagged(self):
        cases = {
            "zh_kana_or_ja_forms": item(zh={"summary": GOOD_ZH["summary"] + "第三条の三。"}),
            "zh_era": item(zh={"summary": "昭和一年厚生省令第46号。"}),
            "zh_truncated": item(zh={"summary": "2025年第341号政令修订施行令（"}),
            "zh_title_gate": item(zh={"title": "修订施行令的政令出版？"}),
        }
        for check, it in cases.items():
            with self.subTest(check=check):
                self.assertIn(check, run(it))
        self.assertIn("zh_missing", run(item(translations={})))

    def test_a_film_title_with_a_question_mark_and_jp_era_in_source_are_fine(self):
        ok = item(zh={"title": "数字厅：电影《驾照返还!?》与我的号码卡开展联动（公告）",
                      "summary": "平成三十一年政令第七十二号涉及的规则；2025-10-01公布。"})
        found = run(ok)
        self.assertNotIn("zh_title_gate", found)
        self.assertNotIn("zh_era", found, "an era year the source states is allowed")

    def test_stage_prefix_contradictions_only(self):
        closed_open = item(stage="Public Comment Closed", zh={"title": "公开征求意见：某规则修订草案"})
        variant = item(stage="Public Comment Closed", zh={"title": "公开征求意见（已结束）：某规则修订草案"})
        open_closed = item(stage="Public Comment Open", zh={"title": "（已结束）公开征求意见：某规则修订草案"})
        self.assertIn("zh_stage_prefix", run(closed_open))
        self.assertIn("zh_stage_prefix", run(open_closed))
        self.assertNotIn("zh_stage_prefix", run(variant))

    def test_published_chinese_that_drifted_from_its_cache_entry_is_flagged(self):
        it = item()
        cache = {"entries": {"zh-Hans": {"raw-egov": dict(GOOD_ZH, summary="另一个版本。")}}}
        found = qa.audit([it], {"raw-egov": {"raw_summary": EGOV_RAW}}, cache)["found"]
        self.assertIn("zh_cache_mismatch", found)

    def test_an_egov_call_labelled_as_results_is_flagged(self):
        call = item(iid="raw-pc", source_name="e-Gov Public Comment (意見募集案件一覧)",
                    stage="Public Comment Results Published",
                    title_ja="シアナジンに係る食品健康影響評価に関する審議結果（案）についての意見・情報の募集について")
        real = item(iid="raw-pc2", source_name="e-Gov Public Comment (意見募集案件一覧)",
                    stage="Public Comment Results Published", title_ja="○○の改正案に関する御意見の募集の結果について")
        found = run(call, real, raw={})
        self.assertEqual(found.get("pc_stage_results"), ["raw-pc"])

    def test_japanese_law_number_in_kanji_counts_as_kept(self):
        ja = item(summary_ja_source="claude", summary_ja="令和七年政令第三百四十一号による改正です。",
                  business_impact_ja="影響がある可能性があります。", recommended_action_ja="原文を確認してください。")
        self.assertNotIn("ja_main_law_number", run(ja))

    def test_report_lists_a_requeue_section_per_remedy(self):
        text, high = qa.report({"items": 2, "found": {"en_japanese": ["raw-a"], "zh_era": ["raw-b"]}})
        self.assertEqual(high, 2)
        self.assertIn('"english": [', text)
        self.assertIn('"zh-Hans": [', text)


class TestAlignStagePrefix(unittest.TestCase):
    def test_closed_titles_get_the_closed_prefix(self):
        f = tu.align_stage_prefix({"title": "公开征求意见：某规则修订草案"}, "Public Comment Closed")
        self.assertEqual(f["title"], "（已结束）公开征求意见：某规则修订草案")
        f = tu.align_stage_prefix({"title": "公开征求意见（已结束）：某规则修订草案"}, "Public Comment Closed")
        self.assertEqual(f["title"], "（已结束）公开征求意见：某规则修订草案")

    def test_open_titles_lose_a_closed_prefix_and_others_are_untouched(self):
        f = tu.align_stage_prefix({"title": "（已结束）公开征求意见：某规则修订草案"}, "Public Comment Open")
        self.assertEqual(f["title"], "公开征求意见：某规则修订草案")
        same = {"title": "政令修订：某施行令"}
        self.assertIs(tu.align_stage_prefix(same, "In Force"), same)


class TestDailyWorkflowRunsTheAudit(unittest.TestCase):
    def test_the_audit_runs_through_the_policy_step_without_blocking(self):
        wf = (REPO_ROOT / ".github" / "workflows" / "daily-update.yml").read_text(encoding="utf-8")
        start = wf.index("name: Quality audit and publication policy")
        step = wf[start:wf.index("\n      - name:", start + 1)]
        self.assertIn("python scripts/quality_gate.py", step)
        self.assertNotIn("--fail-on-high", step, "findings warn; they never block the data commit")
        self.assertLess(start, wf.index("name: Commit and push updated data"))
        self.assertIn("scripts/quality_audit.py", wf[:wf.index("name: Fetch raw updates")])


if __name__ == "__main__":
    unittest.main()
