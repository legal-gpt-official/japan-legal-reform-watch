"""Regression tests for scripts/build_public_data.py (Stage 2).

These tests pin down CURRENT behavior so future source additions, classification
tweaks, and ranking adjustments do not silently break the published dashboard:

- stage classification (Public Comment Open / Closed / Results / Draft Guideline /
  Government Announcement, plus the law-lifecycle stages),
- area classification across the business-facing categories,
- rule-based English title generation (prefixes, draft markers, shortening),
- Public Comment Closed ordering demotion (kept, demoted, relief for strong signals),
- Claude (Stage 3) summary preservation across Stage 2 rebuilds.

Runnable with either `python -m unittest discover -s tests` or `python -m pytest`.
No network or repository file writes; temporary files exercise the Stage 2 write path.
"""

import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_public_data as bpd  # noqa: E402

JFTC_UTF8 = "公正取引委員会 (JFTC) 報道発表"
CAA_UTF8 = "消費者庁 (CAA) 新着情報"
DIGITAL_UTF8 = "Digital Agency (デジタル庁) 新着・更新"
MOF_UTF8 = "財務省 (MOF) 新着情報"
MOE_UTF8 = "環境省 (MOE) 報道発表"
MLIT_UTF8 = "国土交通省 (MLIT) 報道発表"
MAFF_UTF8 = "農林水産省 (MAFF) 報道発表"

# Hiragana / katakana / CJK ideographs / fullwidth punctuation.
CJK_RE = re.compile(r"[぀-ヿ㐀-鿿！-｠]")

EGOV = "e-Gov Public Comment (意見募集案件一覧)"
FSA = "Financial Services Agency (金融庁) 新着情報"
METI = "経済産業省 (METI) ニュースリリース"
MHLW = "Ministry of Health, Labour and Welfare (厚生労働省) 新着情報"
CAA = "消費者庁 (CAA) 新着情報"
PPC = "個人情報保護委員会 (PPC) 新着情報"
JFTC = "公正取引委員会 (JFTC) 報道発表"
MLIT = "国土交通省 (MLIT) 報道発表"
MAFF = "農林水産省 (MAFF) 報道発表"


class TestStageClassification(unittest.TestCase):
    def test_public_comment_open_by_source_type(self):
        self.assertEqual(
            bpd.classify_stage("国民年金法施行規則の一部を改正する省令案に関する御意見の募集について", "public_comment_rss"),
            "Public Comment Open",
        )

    def test_jpx_public_comment_html_is_public_comment_context(self):
        self.assertEqual(
            bpd.classify_stage("上場制度の見直しについて", "public_comment_html"),
            "Public Comment Open",
        )

    def test_public_comment_open_by_title_keyword_on_non_pc_source(self):
        self.assertEqual(
            bpd.classify_stage("「○○指針」の一部改正（案）に対する意見募集について", "regulator_html"),
            "Public Comment Open",
        )

    def test_public_comment_closed(self):
        self.assertEqual(
            bpd.classify_stage("（受付終了）薬局製剤指針の一部改正（案）に関する意見募集について", "public_comment_rss"),
            "Public Comment Closed",
        )

    def test_public_comment_results_published(self):
        self.assertEqual(
            bpd.classify_stage("「○○ガイドライン（案）」に関する意見募集の結果について", "public_comment_rss"),
            "Public Comment Results Published",
        )

    def test_results_marker_wins_over_closed_marker(self):
        # Results markers are checked before closed markers.
        self.assertEqual(
            bpd.classify_stage("（受付終了）○○に関する意見募集の結果について", "public_comment_rss"),
            "Public Comment Results Published",
        )

    def test_draft_guideline(self):
        self.assertEqual(
            bpd.classify_stage("ステルスマーケティングに関するガイドライン（案）を公表しました", "agency_rss"),
            "Draft Guideline",
        )

    def test_government_announcement_fallback(self):
        self.assertEqual(
            bpd.classify_stage("新しい支援策のページを掲載しました", "ministry_rss"),
            "Government Announcement",
        )

    def test_generic_result_wording_outside_pc_context_is_not_pc_results(self):
        # Ministry "selection/survey results" must not be mislabeled as
        # public-comment results; the generic marker needs a PC context.
        for title in (
            "令和８年度モデル事業対象事業の選定結果について",
            "実態調査の結果について",
            "研究開発に係る提案公募の結果",
        ):
            with self.subTest(title=title):
                self.assertEqual(bpd.classify_stage(title, "ministry_rss"), "Government Announcement")

    def test_law_lifecycle_stages(self):
        self.assertEqual(bpd.classify_stage("改正個人情報保護法が施行されました", "regulator_rss"), "In Force")
        self.assertEqual(bpd.classify_stage("○○法の施行期日を定める政令について", "ministry_rss"), "Scheduled to Take Effect")
        self.assertEqual(bpd.classify_stage("○○法を公布しました", "ministry_rss"), "Promulgated")
        self.assertEqual(bpd.classify_stage("○○法が成立しました", "ministry_rss"), "Enacted")
        self.assertEqual(bpd.classify_stage("○○法律案が国会に提出されました", "ministry_rss"), "Bill Submitted")

    def test_controlled_stage_hint_overrides_title_only_for_official_adapters(self):
        raw = {
            "id": "raw-diet-1",
            "title_ja": "会社法の一部を改正する法律案",
            "source_name": "House of Representatives (衆議院) 議案情報",
            "source_url": "https://www.shugiin.go.jp/example",
            "source_type": "legislature_html",
            "published_at": "",
            "fetched_at": "2026-08-06T00:00:00Z",
            "stage_hint": "Enacted",
        }
        item = bpd.build_public_item(raw, "2026-08-06", 10.0)
        self.assertEqual(item["stage"], "Enacted")

        raw["source_type"] = "ministry_rss"
        self.assertEqual(bpd.trusted_stage_hint(raw), "")

    def test_jsda_results_and_court_stage_hints_are_trusted(self):
        jsda = {
            "source_type": "public_comment_results_html",
            "stage_hint": "Public Comment Results Published",
        }
        court = {"source_type": "court_html", "stage_hint": "Court Decision"}
        self.assertEqual(bpd.trusted_stage_hint(jsda), "Public Comment Results Published")
        self.assertEqual(bpd.trusted_stage_hint(court), "Court Decision")
        sesc = {"source_type": "enforcement_html", "stage_hint": "Enforcement Action"}
        self.assertEqual(bpd.trusted_stage_hint(sesc), "Enforcement Action")

    def test_supreme_court_income_tax_case_is_classified_as_tax(self):
        self.assertEqual(
            bpd.classify_area(
                "所得税更正処分取消等請求事件",
                "Courts in Japan (裁判所) Recent Supreme Court Decisions",
            ),
            "Tax / Stamp Duty",
        )


class TestPublicCommentDeadlineResolution(unittest.TestCase):
    def test_future_date_deadline_stays_open(self):
        self.assertEqual(
            bpd.resolve_public_comment_stage(
                "Public Comment Open",
                "2026-07-19",
                now=datetime(2026, 7, 18, 12, 0, tzinfo=bpd.JST),
            ),
            "Public Comment Open",
        )

    def test_date_only_deadline_stays_open_through_235959_jst(self):
        self.assertEqual(
            bpd.resolve_public_comment_stage(
                "Public Comment Open",
                "2026-07-18",
                now=datetime(2026, 7, 18, 23, 59, 59, tzinfo=bpd.JST),
            ),
            "Public Comment Open",
        )

    def test_date_only_deadline_closes_at_next_midnight_jst(self):
        self.assertEqual(
            bpd.resolve_public_comment_stage(
                "Public Comment Open",
                "2026-07-18",
                now=datetime(2026, 7, 19, 0, 0, tzinfo=bpd.JST),
            ),
            "Public Comment Closed",
        )

    def test_explicit_midnight_deadline_closes_at_that_instant(self):
        self.assertEqual(
            bpd.resolve_public_comment_stage(
                "Public Comment Open",
                "2026-07-18T00:00:00+09:00",
                now=datetime(2026, 7, 17, 23, 59, 59, tzinfo=bpd.JST),
            ),
            "Public Comment Open",
        )
        self.assertEqual(
            bpd.resolve_public_comment_stage(
                "Public Comment Open",
                "2026-07-18T00:00:00+09:00",
                now=datetime(2026, 7, 18, 0, 0, 0, tzinfo=bpd.JST),
            ),
            "Public Comment Closed",
        )

    def test_explicit_1700_deadline_closes_at_that_instant(self):
        self.assertEqual(
            bpd.resolve_public_comment_stage(
                "Public Comment Open",
                "2026-07-18T17:00:00+09:00",
                now=datetime(2026, 7, 18, 16, 59, 59, tzinfo=bpd.JST),
            ),
            "Public Comment Open",
        )
        self.assertEqual(
            bpd.resolve_public_comment_stage(
                "Public Comment Open",
                "2026-07-18T17:00:00+09:00",
                now=datetime(2026, 7, 18, 17, 0, 0, tzinfo=bpd.JST),
            ),
            "Public Comment Closed",
        )
        self.assertEqual(
            bpd.resolve_public_comment_stage(
                "Public Comment Open",
                "2026-07-18T17:00:00+09:00",
                now=datetime(2026, 7, 18, 17, 0, 1, tzinfo=bpd.JST),
            ),
            "Public Comment Closed",
        )

    def test_explicit_2359_deadline_closes_at_that_instant(self):
        deadline = "2026-07-18T23:59:00+09:00"
        self.assertEqual(
            bpd.resolve_public_comment_stage(
                "Public Comment Open",
                deadline,
                now=datetime(2026, 7, 18, 23, 58, 59, tzinfo=bpd.JST),
            ),
            "Public Comment Open",
        )
        self.assertEqual(
            bpd.resolve_public_comment_stage(
                "Public Comment Open",
                deadline,
                now=datetime(2026, 7, 18, 23, 59, 0, tzinfo=bpd.JST),
            ),
            "Public Comment Closed",
        )

    def test_missing_deadline_stays_open(self):
        self.assertEqual(
            bpd.resolve_public_comment_stage(
                "Public Comment Open",
                None,
                now=datetime(2026, 7, 20, tzinfo=bpd.JST),
            ),
            "Public Comment Open",
        )

    def test_invalid_deadline_stays_open(self):
        self.assertEqual(
            bpd.resolve_public_comment_stage(
                "Public Comment Open",
                "not-a-deadline",
                now=datetime(2026, 7, 20, tzinfo=bpd.JST),
            ),
            "Public Comment Open",
        )

    def test_non_open_stage_is_unchanged(self):
        self.assertEqual(
            bpd.resolve_public_comment_stage(
                "Government Announcement",
                "2026-01-01",
                now=datetime(2026, 7, 20, tzinfo=bpd.JST),
            ),
            "Government Announcement",
        )

    def test_utc_deadline_is_compared_as_the_same_instant(self):
        self.assertEqual(
            bpd.resolve_public_comment_stage(
                "Public Comment Open",
                "2026-07-18T15:00:00Z",
                now=datetime(2026, 7, 19, 0, 0, 0, tzinfo=bpd.JST),
            ),
            "Public Comment Closed",
        )

    def test_egov_date_only_and_explicit_midnight_remain_distinct(self):
        date_only = {
            "source_type": "public_comment_rss",
            "raw_summary": (
                "案の公示日：2026/06/18 "
                "受付締切日時：2026/07/18 "
                "カテゴリー：環境保全"
            ),
        }
        explicit_midnight = {
            "source_type": "public_comment_rss",
            "raw_summary": (
                "案の公示日：2026/06/18 "
                "受付締切日時：2026/07/18 00:00 "
                "カテゴリー：環境保全"
            ),
        }

        self.assertEqual(bpd.structured_comment_deadline(date_only), ("2026-07-18", "valid"))
        self.assertEqual(
            bpd.structured_comment_deadline(explicit_midnight),
            ("2026-07-18T00:00:00+09:00", "valid"),
        )

    def test_legacy_egov_fixed_field_is_normalized_without_prose_inference(self):
        raw = {
            "source_type": "public_comment_rss",
            "raw_summary": (
                "案の公示日：2026/06/18 "
                "受付締切日時：2026/07/18 00:00 "
                "カテゴリー：環境保全 問合せ先：担当課"
            ),
        }
        self.assertEqual(
            bpd.structured_comment_deadline(raw),
            ("2026-07-18T00:00:00+09:00", "valid"),
        )

    def test_invalid_explicit_field_does_not_fall_back_to_summary(self):
        raw = {
            "source_type": "public_comment_rss",
            "comment_deadline": "invalid",
            "raw_summary": (
                "案の公示日：2026/06/18 "
                "受付締切日時：2026/07/18 00:00 "
                "カテゴリー：環境保全"
            ),
        }
        self.assertEqual(bpd.structured_comment_deadline(raw), (None, "invalid"))

    def test_public_item_uses_resolved_stage_and_keeps_existing_fields(self):
        raw = {
            "id": "raw-expired",
            "title_ja": "省令案に関する意見募集について",
            "source_name": EGOV,
            "source_type": "public_comment_rss",
            "source_url": "https://public-comment.e-gov.go.jp/servlet/Public?id=expired",
            "published_at": "2026-06-18T00:00:00Z",
            "fetched_at": "2026-07-19T00:00:00Z",
            "comment_deadline": "2026-07-18",
        }

        item = bpd.build_public_item(
            raw,
            "2026-07-19",
            12.0,
            "2026-07-19",
            now=datetime(2026, 7, 19, 0, 0, tzinfo=bpd.JST),
        )

        self.assertEqual(item["stage"], "Public Comment Closed")
        self.assertEqual(item["comment_deadline"], "2026-07-18")
        self.assertEqual(item["comment_deadline_source"], "source_metadata")
        self.assertIs(item["comment_deadline_inherited"], False)
        self.assertNotIn("comment_deadline_source_id", item)
        for field in bpd.REQUIRED_FIELDS:
            self.assertIn(field, item)
        self.assertEqual(item["id"], raw["id"])
        self.assertEqual(item["source_url"], raw["source_url"])

    def test_public_item_keeps_future_deadline_open(self):
        raw = {
            "id": "raw-future",
            "title_ja": "省令案に関する意見募集について",
            "source_name": EGOV,
            "source_type": "public_comment_rss",
            "source_url": "https://public-comment.e-gov.go.jp/servlet/Public?id=future",
            "published_at": "2026-07-01",
            "fetched_at": "2026-07-01T00:00:00Z",
            "comment_deadline": "2026-07-31T23:59:00+09:00",
        }

        item = bpd.build_public_item(
            raw,
            "2026-07-20",
            12.0,
            "2026-07-20",
            now=datetime(2026, 7, 20, tzinfo=bpd.JST),
        )

        self.assertEqual(item["stage"], "Public Comment Open")


class TestPublicCommentDeadlinePropagation(unittest.TestCase):
    NOW = datetime(2026, 7, 20, 12, 0, tzinfo=bpd.JST)
    TITLE = "○○法施行規則の一部を改正する省令案に関する意見募集について"

    def _egov(
        self,
        *,
        item_id="raw-egov",
        title=None,
        published_at="2026-06-18",
        deadline="2026-07-18T00:00:00+09:00",
        contact="環境省自然環境局",
    ):
        return {
            "id": item_id,
            "title_ja": title or self.TITLE,
            "source_name": EGOV,
            "source_type": "public_comment_rss",
            "source_url": (
                "https://public-comment.e-gov.go.jp/servlet/Public?"
                f"CLASSNAME=PCMMSTDETAIL&id={item_id}&Mode=0"
            ),
            "published_at": published_at,
            "fetched_at": "2026-06-18T01:00:00Z",
            "raw_summary": (
                "案の公示日：2026/06/18 "
                f"問合せ先（所管省庁・部局名等）：{contact}"
            ),
            "comment_deadline": deadline,
        }

    def _ministry(
        self,
        *,
        item_id="raw-ministry",
        title=None,
        published_at="2026-06-18",
        source_name=MOE_UTF8,
        source_url="https://www.env.go.jp/press/press_test.html",
        comment_deadline=None,
    ):
        raw = {
            "id": item_id,
            "title_ja": title or self.TITLE,
            "source_name": source_name,
            "source_type": "ministry_html",
            "source_url": source_url,
            "published_at": published_at,
            "fetched_at": "2026-06-18T02:00:00Z",
            "raw_summary": "official ministry category",
        }
        if comment_deadline is not None:
            raw["comment_deadline"] = comment_deadline
        return raw

    def _run(self, raw_items, *, item_mutator=None, now=None):
        build_now = now or self.NOW
        items = [
            bpd.build_public_item(
                raw,
                "2026-07-20",
                12.0,
                "2026-07-20",
                now=build_now,
            )
            for raw in raw_items
        ]
        if item_mutator:
            item_mutator(items)
        raw_by_id = {raw["id"]: raw for raw in raw_items}
        deadline_state_by_id = {
            raw["id"]: bpd.structured_comment_deadline(raw)
            for raw in raw_items
        }
        propagated, stats = bpd.propagate_public_comment_deadlines(
            items,
            raw_by_id=raw_by_id,
            deadline_state_by_id=deadline_state_by_id,
            now=build_now,
        )
        return {item["id"]: item for item in propagated}, stats, items

    def test_title_normalization_is_limited_and_preserves_legal_meaning(self):
        left = "「○○法施行規則（案）」 に関する意見募集（パブリック・コメント）"
        right = "｢○○法施行規則(案)｣に関する意見募集(パブリックコメント)"
        self.assertEqual(
            bpd.normalize_public_comment_title(left),
            bpd.normalize_public_comment_title(right),
        )
        self.assertNotEqual(
            bpd.normalize_public_comment_title("○○法の一部を改正する案"),
            bpd.normalize_public_comment_title("○○法を廃止する案"),
        )
        self.assertNotEqual(
            bpd.normalize_public_comment_title("令和8年度○○制度の意見募集"),
            bpd.normalize_public_comment_title("令和9年度○○制度の意見募集"),
        )

    def test_unique_expired_pair_inherits_deadline_closes_and_records_provenance(self):
        items, stats, originals = self._run([self._egov(), self._ministry()])
        source = items["raw-egov"]
        target = items["raw-ministry"]

        self.assertEqual(source["stage"], "Public Comment Closed")
        self.assertEqual(source["comment_deadline_source"], "source_metadata")
        self.assertIs(source["comment_deadline_inherited"], False)
        self.assertNotIn("comment_deadline_source_id", source)

        self.assertEqual(target["stage"], "Public Comment Closed")
        self.assertEqual(target["comment_deadline"], "2026-07-18T00:00:00+09:00")
        self.assertEqual(target["comment_deadline_source"], "related_egov_item")
        self.assertEqual(target["comment_deadline_source_id"], "raw-egov")
        self.assertIs(target["comment_deadline_inherited"], True)
        self.assertEqual(stats.direct_deadline_items, 1)
        self.assertEqual(stats.inherited_deadline_items, 1)
        self.assertEqual(stats.inherited_and_closed_items, 1)
        self.assertEqual(stats.inherited_but_still_open_items, 0)
        self.assertNotIn("comment_deadline_source", originals[1])

    def test_unique_future_pair_inherits_deadline_but_stays_open(self):
        items, stats, _ = self._run([
            self._egov(deadline="2026-08-18T17:00:00+09:00"),
            self._ministry(),
        ])
        target = items["raw-ministry"]
        self.assertEqual(target["stage"], "Public Comment Open")
        self.assertIs(target["comment_deadline_inherited"], True)
        self.assertEqual(stats.inherited_and_closed_items, 0)
        self.assertEqual(stats.inherited_but_still_open_items, 1)

    def test_one_day_publication_difference_is_allowed(self):
        items, stats, _ = self._run([
            self._egov(published_at="2026-06-18"),
            self._ministry(published_at="2026-06-17"),
        ])
        self.assertIs(items["raw-ministry"]["comment_deadline_inherited"], True)
        self.assertEqual(stats.inherited_deadline_items, 1)

    def test_title_only_match_with_wrong_agency_does_not_propagate(self):
        items, stats, _ = self._run([
            self._egov(contact="環境省自然環境局"),
            self._ministry(
                source_name="総務省 (MIC) 新着情報",
                source_url="https://www.soumu.go.jp/menu_news/s-news/test.html",
            ),
        ])
        self.assertNotIn("comment_deadline", items["raw-ministry"])
        self.assertEqual(stats.inherited_deadline_items, 0)

    def test_official_name_with_untrusted_domain_does_not_propagate(self):
        items, stats, _ = self._run([
            self._egov(),
            self._ministry(source_url="https://env.go.jp.example.com/press/test"),
        ])
        self.assertNotIn("comment_deadline", items["raw-ministry"])
        self.assertEqual(stats.inherited_deadline_items, 0)

    def test_different_law_name_does_not_propagate(self):
        items, stats, _ = self._run([
            self._egov(title="○○法施行規則の一部を改正する省令案に関する意見募集"),
            self._ministry(title="△△法施行規則の一部を改正する省令案に関する意見募集"),
        ])
        self.assertNotIn("comment_deadline", items["raw-ministry"])
        self.assertEqual(stats.inherited_deadline_items, 0)

    def test_same_title_pattern_with_different_year_does_not_propagate(self):
        items, stats, _ = self._run([
            self._egov(title="令和8年度○○制度改正案に関する意見募集"),
            self._ministry(title="令和9年度○○制度改正案に関する意見募集"),
        ])
        self.assertNotIn("comment_deadline", items["raw-ministry"])
        self.assertEqual(stats.inherited_deadline_items, 0)

    def test_publication_dates_more_than_one_day_apart_do_not_propagate(self):
        items, stats, _ = self._run([
            self._egov(published_at="2026-06-18"),
            self._ministry(published_at="2026-06-20"),
        ])
        self.assertNotIn("comment_deadline", items["raw-ministry"])
        self.assertEqual(stats.inherited_deadline_items, 0)

    def test_multiple_same_deadline_sources_are_ambiguous(self):
        items, stats, _ = self._run([
            self._egov(item_id="raw-egov-1"),
            self._egov(item_id="raw-egov-2"),
            self._ministry(),
        ])
        self.assertNotIn("comment_deadline", items["raw-ministry"])
        self.assertEqual(stats.ambiguous_related_matches, 1)
        self.assertEqual(stats.conflicting_deadline_matches, 0)

    def test_multiple_source_deadlines_conflict(self):
        items, stats, _ = self._run([
            self._egov(item_id="raw-egov-1", deadline="2026-07-18T00:00:00+09:00"),
            self._egov(item_id="raw-egov-2", deadline="2026-07-19T00:00:00+09:00"),
            self._ministry(),
        ])
        self.assertNotIn("comment_deadline", items["raw-ministry"])
        self.assertEqual(stats.conflicting_deadline_matches, 1)
        self.assertEqual(stats.ambiguous_related_matches, 0)

    def test_one_source_matching_multiple_targets_is_ambiguous(self):
        items, stats, _ = self._run([
            self._egov(),
            self._ministry(
                item_id="raw-ministry-1",
                source_url="https://www.env.go.jp/press/press_1.html",
            ),
            self._ministry(
                item_id="raw-ministry-2",
                source_url="https://www.env.go.jp/press/press_2.html",
            ),
        ])
        self.assertNotIn("comment_deadline", items["raw-ministry-1"])
        self.assertNotIn("comment_deadline", items["raw-ministry-2"])
        self.assertEqual(stats.ambiguous_related_matches, 2)

    def test_target_with_own_deadline_is_not_overwritten(self):
        own_deadline = "2026-07-30T23:59:00+09:00"
        items, stats, _ = self._run([
            self._egov(),
            self._ministry(comment_deadline=own_deadline),
        ])
        target = items["raw-ministry"]
        self.assertEqual(target["comment_deadline"], own_deadline)
        self.assertEqual(target["comment_deadline_source"], "source_metadata")
        self.assertIs(target["comment_deadline_inherited"], False)
        self.assertEqual(stats.inherited_deadline_items, 0)

    def test_non_open_target_is_not_changed(self):
        def close_target(items):
            items[1]["stage"] = "Public Comment Results Published"

        items, stats, _ = self._run(
            [self._egov(), self._ministry()],
            item_mutator=close_target,
        )
        target = items["raw-ministry"]
        self.assertEqual(target["stage"], "Public Comment Results Published")
        self.assertNotIn("comment_deadline", target)
        self.assertEqual(stats.inherited_deadline_items, 0)

    def test_invalid_related_deadline_does_not_propagate(self):
        items, stats, _ = self._run([
            self._egov(deadline="not-a-deadline"),
            self._ministry(),
        ])
        self.assertNotIn("comment_deadline", items["raw-ministry"])
        self.assertEqual(stats.invalid_related_deadlines, 1)
        self.assertEqual(stats.inherited_deadline_items, 0)

    def test_inherited_deadline_uses_existing_date_only_and_utc_boundaries(self):
        cases = (
            (
                "2026-07-18",
                datetime(2026, 7, 18, 23, 59, 59, tzinfo=bpd.JST),
                "Public Comment Open",
            ),
            (
                "2026-07-18",
                datetime(2026, 7, 19, 0, 0, 0, tzinfo=bpd.JST),
                "Public Comment Closed",
            ),
            (
                "2026-07-18T15:00:00Z",
                datetime(2026, 7, 18, 23, 59, 59, tzinfo=bpd.JST),
                "Public Comment Open",
            ),
            (
                "2026-07-18T15:00:00Z",
                datetime(2026, 7, 19, 0, 0, 0, tzinfo=bpd.JST),
                "Public Comment Closed",
            ),
        )
        for deadline, now, expected_stage in cases:
            with self.subTest(deadline=deadline, now=now):
                items, _, _ = self._run(
                    [self._egov(deadline=deadline), self._ministry()],
                    now=now,
                )
                self.assertEqual(items["raw-ministry"]["stage"], expected_stage)

    def test_ai_summary_and_translation_preservation_do_not_remove_provenance(self):
        items, _, _ = self._run([self._egov(), self._ministry()])
        target = items["raw-ministry"]
        provenance = {
            key: target[key]
            for key in (
                "comment_deadline",
                "comment_deadline_source",
                "comment_deadline_source_id",
                "comment_deadline_inherited",
            )
        }
        existing = {
            "id": target["id"],
            "source_url": target["source_url"],
            "summary_source": "claude",
            "summary_en": "AI summary.",
            "business_impact_en": "AI impact.",
            "recommended_action_en": "AI action.",
            "confidence": "medium",
            "ai_notes": "",
            "summarized_at": "2026-07-01T00:00:00Z",
            "summary_model": "claude-opus-4-8",
            "translations": {
                "zh-Hans": {
                    "title": "标题",
                    "summary": "摘要",
                    "business_impact": "影响",
                    "recommended_action": "行动",
                },
            },
        }
        self.assertTrue(bpd.preserve_ai_summary_fields(target, {target["id"]: existing}))
        self.assertTrue(bpd.preserve_translations(target, {target["id"]: existing}))
        for key, value in provenance.items():
            self.assertEqual(target[key], value)

    def test_build_summary_reports_propagation_counts(self):
        raw_items = [self._egov(), self._ministry()]
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            raw_path = tmp / "raw_items.json"
            output_path = tmp / "legal_updates.json"
            backup_path = tmp / "legal_updates.backup.json"
            raw_path.write_text(
                json.dumps(raw_items, ensure_ascii=False),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with (
                mock.patch.object(bpd, "RAW_PATH", raw_path),
                mock.patch.object(bpd, "OUTPUT_PATH", output_path),
                mock.patch.object(bpd, "BACKUP_PATH", backup_path),
                redirect_stdout(stdout),
            ):
                result = bpd.main(["--dry-run"])

        self.assertEqual(result, 0)
        summary = stdout.getvalue()
        for label, expected in (
            ("direct_deadline_items", 1),
            ("inherited_deadline_items", 1),
            ("inherited_and_closed_items", 1),
            ("inherited_but_still_open_items", 0),
            ("ambiguous_related_matches", 0),
            ("conflicting_deadline_matches", 0),
            ("unmatched_open_public_comments", 0),
            ("invalid_related_deadlines", 0),
        ):
            with self.subTest(label=label):
                match = re.search(rf"^{label}\s*:\s*(\d+)$", summary, re.MULTILINE)
                self.assertIsNotNone(match, summary)
                self.assertEqual(int(match.group(1)), expected)

    def test_propagation_preserves_item_order_ids_urls_fields_and_scores(self):
        raws = [
            self._egov(),
            self._ministry(),
            self._ministry(
                item_id="raw-unrelated",
                title="別の○○法を改正する案に関する意見募集",
                source_url="https://www.env.go.jp/press/press_other.html",
            ),
        ]
        items, _, _ = self._run(raws)
        self.assertEqual(list(items), [raw["id"] for raw in raws])
        for raw in raws:
            item = items[raw["id"]]
            self.assertEqual(item["source_url"], raw["source_url"])
            self.assertEqual(item["relevance_score"], 12.0)
            for field in bpd.REQUIRED_FIELDS:
                self.assertIn(field, item)


class TestAreaClassification(unittest.TestCase):
    CASES = [
        # (expected_area, title_ja, source_name)
        ("Data / Privacy / AI",
         "個人情報の保護に関する法律についてのガイドライン（通則編）の一部を改正する件（案）に関する意見募集について", PPC),
        ("Antitrust / Fair Trade",
         "独占禁止法に基づく排除措置命令及び課徴金納付命令について", JFTC),
        ("Healthcare / Pharmaceuticals",
         "薬局製剤指針の一部改正（案）について（概要）", EGOV),
        ("Transport / Infrastructure",
         "鉄道車両に固定して用いられる容器の再検査の方法等に関する意見募集", EGOV),
        ("Energy / Environment",
         "重要電源開発地点の指定に関する規程の一部を改正する告示案", METI),
        ("Food / Agriculture",
         "食品、添加物等の規格基準の一部改正について", MHLW),
        ("Real Estate / Land Use",
         "空家等に関する施策を総合的かつ計画的に実施するための基本的な指針の変更案に関する意見募集", EGOV),
        ("Public Safety / Disaster Management",
         "「人とペットの災害対策ガイドライン」の改訂案に係る意見の募集", EGOV),
        ("Consumer / Advertising",
         "景品表示法に基づく措置命令について", CAA),
        ("Finance / AML",
         "「中小・地域金融機関向けの総合的な監督指針」等の一部改正（案）について", FSA),
        ("Labor / Employment",
         "労働者派遣事業の許可の取消しについて", MHLW),
    ]

    def test_business_facing_areas(self):
        for expected, title, source in self.CASES:
            with self.subTest(area=expected):
                self.assertEqual(bpd.classify_area(title, source), expected)

    def test_veterinary_pharma_goes_to_food_agriculture(self):
        # Current rule order: 動物用医薬品 matches Food / Agriculture before Healthcare.
        self.assertEqual(
            bpd.classify_area("動物用医薬品等取締規則の一部改正案についての意見・情報の募集について", EGOV),
            "Food / Agriculture",
        )

    def test_other_is_reserved_for_genuinely_unclear(self):
        self.assertEqual(bpd.classify_area("ありふれた一般的なお知らせ", "Unknown Agency"), "Other")

    def test_nta_items_default_to_finance_aml(self):
        self.assertEqual(
            bpd.classify_area("法令解釈通達の一部改正について", "国税庁 (NTA) 新着・通達"),
            "Finance / AML",
        )

    # --- Ministry expansion (MOJ / MOE / MOF / MIC) ---

    MOJ = "法務省 (MOJ) 新着情報"
    MOE = "環境省 (MOE) 報道発表"
    MOF = "財務省 (MOF) 新着情報"
    MIC = "総務省 (MIC) 新着情報"
    MLIT = "国土交通省 (MLIT) 報道発表"
    MAFF = "農林水産省 (MAFF) 報道発表"

    def test_moj_area_rules(self):
        cases = [
            ("Corporate / Governance", "会社法の一部を改正する法律案について"),
            ("Corporate / Governance", "商業登記規則の一部改正について"),
            ("Real Estate / Land Use", "不動産登記規則の一部改正について"),
            ("Labor / Employment", "在留資格「特定技能」に関する省令改正について"),
            ("Finance / AML", "犯罪収益移転防止法の改正について"),
            ("Data / Privacy / AI", "個人情報の取扱いに関する留意事項について"),
            ("Corporate / Governance", "法制審議会民法（契約関係）部会の開催について"),
        ]
        for expected, title in cases:
            with self.subTest(title=title):
                self.assertEqual(bpd.classify_area(title, self.MOJ), expected)

    def test_moe_area_rules(self):
        cases = [
            ("Energy / Environment", "廃棄物処理法施行令の一部を改正する政令について"),
            ("Energy / Environment", "化学物質の審査及び製造等の規制に関する法律の改正について"),
            ("Energy / Environment", "温室効果ガス排出量算定・報告・公表制度について"),
            ("Public Safety / Disaster Management", "災害廃棄物対策に関する指針の改定について"),
        ]
        for expected, title in cases:
            with self.subTest(title=title):
                self.assertEqual(bpd.classify_area(title, self.MOE), expected)

    def test_mof_area_rules(self):
        cases = [
            ("Economic Security / FDI", "外国為替及び外国貿易法に基づく対内直接投資等の届出について"),
            ("Finance / AML", "関税定率法等の一部を改正する法律案について"),
            ("Finance / AML", "令和９年度税制改正要望について"),
        ]
        for expected, title in cases:
            with self.subTest(title=title):
                self.assertEqual(bpd.classify_area(title, self.MOF), expected)

    def test_mic_area_rules(self):
        cases = [
            ("Data / Privacy / AI", "電気通信事業法施行規則の一部改正に関する意見募集"),
            ("Data / Privacy / AI", "電波法関係審査基準の一部を改正する訓令案について"),
        ]
        for expected, title in cases:
            with self.subTest(title=title):
                self.assertEqual(bpd.classify_area(title, self.MIC), expected)

    def test_mlit_area_rules(self):
        cases = [
            ("Real Estate / Land Use", "建築基準法施行規則の一部改正について"),
            ("Real Estate / Land Use", "土地の取得・利用等の在り方に関する有識者会議について"),
            ("Transport / Infrastructure", "道路運送車両の保安基準等の改正について"),
            ("Transport / Infrastructure", "鉄道・航空・港湾物流に関する制度見直しについて"),
            ("Public Safety / Disaster Management", "河川整備基本方針と砂防関係事業の見直しについて"),
            ("Energy / Environment", "ブルーカーボンと脱炭素に関する取組について"),
            ("Consumer / Advertising", "旅行業標準約款の一部改正について"),
        ]
        for expected, title in cases:
            with self.subTest(title=title):
                self.assertEqual(bpd.classify_area(title, self.MLIT), expected)

    def test_maff_area_rules(self):
        cases = [
            ("Consumer / Advertising", "食品表示基準の一部改正と不適正表示への対応について"),
            ("Food / Agriculture", "農業・農地・農産物に関する認定制度について"),
            ("Food / Agriculture", "水産資源管理及び林野政策に関する制度見直しについて"),
            ("Food / Agriculture", "動物検疫・植物検疫及び輸出入規制について"),
            ("Food / Agriculture", "高病原性鳥インフルエンザ及び家畜伝染病への対応について"),
        ]
        for expected, title in cases:
            with self.subTest(title=title):
                self.assertEqual(bpd.classify_area(title, self.MAFF), expected)

    def test_ministry_source_fallbacks(self):
        self.assertEqual(bpd.classify_area("特になし", self.MOJ), "Corporate / Governance")
        self.assertEqual(bpd.classify_area("特になし", self.MOE), "Energy / Environment")
        self.assertEqual(bpd.classify_area("特になし", self.MOF), "Finance / AML")
        self.assertEqual(bpd.classify_area("特になし", self.MLIT), "Transport / Infrastructure")
        self.assertEqual(bpd.classify_area("特になし", self.MAFF), "Food / Agriculture")
        self.assertEqual(
            bpd.classify_area("安全対策情報を掲載しました", "Pharmaceuticals and Medical Devices Agency (PMDA) Safety Updates"),
            "Healthcare / Pharmaceuticals",
        )
        self.assertEqual(
            bpd.classify_area("制度の見直しについて", "Japan Exchange Group (JPX) Public Comments"),
            "Finance / AML",
        )
        self.assertEqual(
            bpd.classify_area(
                "制度の見直しについて",
                "Japan Securities Dealers Association (JSDA) Public Comments",
            ),
            "Finance / AML",
        )
        self.assertEqual(
            bpd.classify_area(
                "検査結果に基づく勧告について",
                "Securities and Exchange Surveillance Commission (SESC) Enforcement Updates",
            ),
            "Finance / AML",
        )
        # MIC deliberately has no fallback — broad scope stays "Other".
        self.assertEqual(bpd.classify_area("特になし", self.MIC), "Other")


class TestTitleEnGeneration(unittest.TestCase):
    def _title(self, title_ja, source_name, source_type):
        stage = bpd.classify_stage(title_ja, source_type)
        return stage, bpd.generate_title_en(title_ja, source_name, stage)

    def test_egov_public_comment_prefix_and_english_subject(self):
        stage, t = self._title(
            "「薬局製剤指針の一部改正（案）について（概要）」に関する意見募集について", EGOV, "public_comment_rss")
        self.assertEqual(stage, "Public Comment Open")
        self.assertEqual(t, "Public Comment: Draft Amendment to Pharmacy Preparation Guidelines")
        self.assertIsNone(CJK_RE.search(t))

    def test_nta_fallback_titles_are_conservative_and_english_only(self):
        source = "国税庁 (NTA) 新着・通達"
        cases = (
            ("所得税法基本通達の一部改正について", "NTA Update: Tax interpretation or administrative guidance revised"),
            ("国税庁告示の一部改正", "NTA Update: Tax agency notice issued or revised"),
            ("令和8年度税制改正について", "NTA Update: Tax System Measures"),
            ("申告手続に関するお知らせ", "NTA Update: Tax administration guidance updated"),
        )
        for title_ja, expected in cases:
            with self.subTest(title_ja=title_ja):
                stage, title_en = self._title(title_ja, source, "agency_html")
                self.assertEqual(stage, "Government Announcement")
                self.assertEqual(title_en, expected)
                self.assertIsNone(CJK_RE.search(title_en))

    def test_jftc_public_comment_prefix(self):
        _, t = self._title(
            "「流通・取引慣行に関する独占禁止法上の指針」の一部改正（案）に対する意見募集について", JFTC, "regulator_html")
        self.assertTrue(t.startswith("JFTC Public Comment: "))
        self.assertIn("Antimonopoly Act Guidelines", t)
        self.assertIsNone(CJK_RE.search(t))

    def test_jpx_and_pmda_fallback_titles(self):
        jpx = bpd.generate_title_en(
            "上場制度の見直しについて",
            "Japan Exchange Group (JPX) Public Comments",
            "Public Comment Open",
            "Finance / AML",
        )
        pmda = bpd.generate_title_en(
            "クラスI回収 該当回収品目について",
            "Pharmaceuticals and Medical Devices Agency (PMDA) Safety Updates",
            "Government Announcement",
            "Healthcare / Pharmaceuticals",
        )
        self.assertEqual(jpx, "JPX Public Comment: Regulatory proposal open for comment")
        self.assertEqual(pmda, "PMDA Safety Update: Class I recall information published")

    def test_jsda_and_courts_fallback_titles(self):
        jsda = bpd.generate_title_en(
            "「店頭有価証券規則」の一部改正について",
            "Japan Securities Dealers Association (JSDA) Public Comment Results",
            "Public Comment Results Published",
            "Finance / AML",
        )
        court = bpd.generate_title_en(
            "令和6(オ)720 損害賠償請求本訴、同反訴事件",
            "Courts in Japan (裁判所) Recent Supreme Court Decisions",
            "Court Decision",
            "Other",
        )
        self.assertEqual(jsda, "JSDA Public Comment Results: Regulatory comment results published")
        self.assertEqual(court, "Supreme Court Decision: Damages case")

        generic_court = bpd.generate_title_en(
            "令和6(許)30 受託者解任申立て却下決定に対する抗告棄却決定に対する許可抗告事件",
            "Courts in Japan (裁判所) Recent Supreme Court Decisions",
            "Court Decision",
            "Other",
        )
        self.assertEqual(generic_court, "Supreme Court Decision: Trust case")

    def test_sesc_enforcement_fallback_titles(self):
        source = "Securities and Exchange Surveillance Commission (SESC) Enforcement Updates"
        penalty = bpd.generate_title_en(
            "内部者取引に対する課徴金納付命令の勧告について",
            source,
            "Enforcement Action",
            "Finance / AML",
        )
        referral = bpd.generate_title_en(
            "相場操縦事件の告発について",
            source,
            "Enforcement Action",
            "Finance / AML",
        )
        self.assertEqual(penalty, "SESC Enforcement Action: Administrative monetary penalty recommendation")
        self.assertEqual(referral, "SESC Enforcement Action: Criminal referral announced")

    def test_ppc_update_prefix(self):
        _, t = self._title("特定個人情報の漏えい等事案への対応について", PPC, "regulator_html")
        self.assertTrue(t.startswith("PPC Update: "))
        self.assertIsNone(CJK_RE.search(t))

    def test_closed_public_comment_prefix(self):
        stage, t = self._title(
            "（受付終了）国民年金法施行規則の一部を改正する省令案に関する御意見の募集について", EGOV, "public_comment_rss")
        self.assertEqual(stage, "Public Comment Closed")
        self.assertTrue(t.startswith("Closed Public Comment: "))
        self.assertIsNone(CJK_RE.search(t))

    def test_generic_fallback_prefix_is_rare(self):
        """'Japanese Regulatory Update' must not appear for any configured source."""
        import fetch_updates as fu

        neutral = "ありふれたお知らせ"
        for source in fu.SOURCES:
            stage = bpd.classify_stage(neutral, source["source_type"])
            prefix = bpd.title_prefix(source["name"], stage, neutral)
            with self.subTest(source=source["name"]):
                self.assertNotEqual(prefix, "Japanese Regulatory Update")

    def test_generic_fallback_prefix_for_unknown_source(self):
        # The fallback still exists for sources we do not recognize.
        self.assertEqual(
            bpd.title_prefix("Unknown Agency", "Government Announcement", "ありふれたお知らせ"),
            "Japanese Regulatory Update",
        )

    def test_long_title_is_shortened_to_max_chars(self):
        _, t = self._title("Long regulatory notice " * 20, "Unknown Agency", "ministry_rss")
        self.assertLessEqual(len(t), bpd.TITLE_MAX_CHARS)
        self.assertTrue(t.endswith("..."))

    def test_shorten_title_leaves_short_titles_alone(self):
        self.assertEqual(bpd.shorten_title("short title"), "short title")

    def test_contains_japanese_detects_scripts(self):
        self.assertTrue(bpd.contains_japanese("Hiragana あ"))
        self.assertTrue(bpd.contains_japanese("Katakana カタカナ"))
        self.assertTrue(bpd.contains_japanese("Kanji 勧告"))
        self.assertTrue(bpd.contains_japanese("Halfwidth ｶﾀｶﾅ"))
        self.assertFalse(bpd.contains_japanese("English title only"))

    def test_jftc_recommendation_fallback(self):
        title = bpd.generate_title_en(
            "株式会社ヘリテージに対する勧告",
            JFTC_UTF8,
            "Government Announcement",
            "Antitrust / Fair Trade",
        )
        self.assertEqual(title, "JFTC Update: Recommendation issued to a company")
        self.assertFalse(bpd.contains_japanese(title))

    def test_caa_serious_accident_fallback(self):
        title = bpd.generate_title_en(
            "消費者安全法の重大事故等に係る公表について",
            CAA_UTF8,
            "Government Announcement",
            "Consumer / Advertising",
        )
        self.assertEqual(title, "CAA Update: Consumer accident information published")
        self.assertFalse(bpd.contains_japanese(title))

    def test_digital_agency_generative_ai_fallback(self):
        title = bpd.generate_title_en(
            "生成AIの調達・利活用に係るガイドラインの更新について",
            DIGITAL_UTF8,
            "Government Announcement",
            "Data / Privacy / AI",
        )
        self.assertEqual(
            title,
            "Digital Agency Update: Generative AI procurement and use guidelines updated",
        )
        self.assertFalse(bpd.contains_japanese(title))

    def test_mof_tobacco_retail_price_fallback(self):
        title = bpd.generate_title_en(
            "製造たばこの小売定価の認可",
            MOF_UTF8,
            "Government Announcement",
            "Finance / AML",
        )
        self.assertEqual(title, "MOF Update: Approval of retail prices for tobacco products")
        self.assertFalse(bpd.contains_japanese(title))

    def test_moe_environmental_impact_assessment_fallback(self):
        title = bpd.clean_english_title(
            "MOE Update: 環境影響評価法施行規則の改正",
            "環境影響評価法施行規則の改正について",
            "Energy / Environment",
            "Government Announcement",
            MOE_UTF8,
        )
        self.assertEqual(title, "MOE Update: Environmental impact assessment rules updated")
        self.assertFalse(bpd.contains_japanese(title))

    def test_mlit_fallback_titles(self):
        cases = [
            (
                "建築基準法施行規則の一部改正について",
                "Real Estate / Land Use",
                "MLIT Update: Building standards regulation information updated",
            ),
            (
                "道路運送車両の保安基準等の改正について",
                "Transport / Infrastructure",
                "MLIT Update: Road transport vehicle regulation information updated",
            ),
            (
                "河川及び砂防関係事業の防災対応について",
                "Public Safety / Disaster Management",
                "MLIT Update: Disaster management and infrastructure safety information updated",
            ),
        ]
        for title_ja, area, expected in cases:
            with self.subTest(title_ja=title_ja):
                title = bpd.generate_title_en(title_ja, MLIT_UTF8, "Government Announcement", area)
                self.assertEqual(title, expected)
                self.assertFalse(bpd.contains_japanese(title))

    def test_maff_fallback_titles(self):
        cases = [
            (
                "食品安全規制に関する情報について",
                "Consumer / Advertising",
                "MAFF Update: Food safety and labeling regulation information updated",
            ),
            (
                # Quarantine now takes precedence over the import/export bucket.
                "動物検疫及び輸入規制に関する情報について",
                "Food / Agriculture",
                "MAFF Update: Animal and plant quarantine information updated",
            ),
            (
                "牛肉の輸出証明書発行手続の見直しについて",
                "Food / Agriculture",
                "MAFF Update: Agricultural import and export regulation information updated",
            ),
            (
                "高病原性鳥インフルエンザへの対応について",
                "Food / Agriculture",
                "MAFF Update: Animal health and disease control information published",
            ),
        ]
        for title_ja, area, expected in cases:
            with self.subTest(title_ja=title_ja):
                title = bpd.generate_title_en(title_ja, MAFF_UTF8, "Government Announcement", area)
                self.assertEqual(title, expected)
                self.assertFalse(bpd.contains_japanese(title))

    def test_generic_public_comment_fallback(self):
        title = bpd.generate_title_en(
            "制度改正案に関する意見募集について",
            "Unknown Agency",
            "Public Comment Open",
            "Other",
        )
        self.assertEqual(title, "Public Comment: Regulatory proposal open for comment")
        self.assertFalse(bpd.contains_japanese(title))


class TestClosedPublicCommentOrdering(unittest.TestCase):
    def test_closed_is_not_excluded(self):
        title = "（受付終了）○○に関する意見募集について"
        self.assertFalse(bpd.is_hard_excluded(title))
        self.assertEqual(bpd.classify_stage(title, "public_comment_rss"), "Public Comment Closed")

    def test_closed_is_demoted_below_open(self):
        open_adj = bpd.stage_ordering_adjustment("Public Comment Open", "○○")
        closed_adj = bpd.stage_ordering_adjustment("Public Comment Closed", "○○")
        self.assertEqual(open_adj, 4.0)
        self.assertEqual(closed_adj, -14.0)
        self.assertGreater(open_adj, closed_adj)

    def test_strong_signal_softens_but_keeps_demotion(self):
        plain = bpd.stage_ordering_adjustment("Public Comment Closed", "ありふれた件名")
        important = bpd.stage_ordering_adjustment("Public Comment Closed", "○○法の改正に関する件")
        self.assertEqual(important, -12.0)
        self.assertGreater(important, plain)   # relief applied...
        self.assertLess(important, 0)          # ...but still demoted.

    def test_strong_closed_item_can_outrank_weak_announcement(self):
        """The demotion must not pin important closed items to the absolute bottom."""
        closed_title = "個人情報保護法施行規則の改正に関する意見募集（受付終了）"
        closed_score = bpd.relevance_score(closed_title, "public_comment_rss")
        closed_ordering = closed_score + bpd.stage_ordering_adjustment("Public Comment Closed", closed_title)

        weak_title = "新しい支援策のページを掲載しました"
        weak_score = bpd.relevance_score(weak_title, "ministry_rss")
        weak_ordering = weak_score + bpd.stage_ordering_adjustment("Government Announcement", weak_title)

        self.assertGreater(closed_ordering, weak_ordering)


class TestRelevanceScoring(unittest.TestCase):
    def test_hard_exclusion_and_strong_keyword_rescue(self):
        self.assertTrue(bpd.is_hard_excluded("職員採用のお知らせ"))
        self.assertFalse(bpd.is_hard_excluded("職員採用規則の改正について"))  # rescued by 改正

    def test_deliberative_noise_scores_negative(self):
        self.assertLess(bpd.keyword_score("第65回審議会 議事録"), 0)

    def test_admin_noise_is_hard_excluded(self):
        for title in (
            "職員採用のお知らせ",
            "調達情報を掲載しました",
            "入札公告について",
            "記念イベントの開催について",
            "シンポジウムを開催します",
        ):
            with self.subTest(title=title):
                self.assertTrue(bpd.is_hard_excluded(title))

    def test_ministry_noise_scores_below_floor(self):
        # MOF market data / speeches, MOJ exams, MOE subsidy adoptions, plain
        # meeting announcements: all must fall below the relevance floor.
        for title in (
            "国債金利情報(令和8年6月11日)",
            "大臣のスピーチについて",
            "令和８年度司法書士試験の出願状況について",
            "令和８年度モデル構築事業の採択について",
            "対策技術の実証事業の二次公募について",
            "検討会（第16回）の開催について",
        ):
            with self.subTest(title=title):
                self.assertLess(bpd.relevance_score(title, "ministry_rss"), bpd.RELEVANCE_FLOOR)

    def test_egov_source_bonus(self):
        title = "○○省令案に関する意見募集"
        self.assertEqual(
            bpd.relevance_score(title, "public_comment_rss"),
            bpd.relevance_score(title, "ministry_rss") + bpd.SOURCE_BONUS["public_comment_rss"],
        )
        self.assertEqual(
            bpd.relevance_score(title, "public_comment_html"),
            bpd.relevance_score(title, "ministry_rss") + bpd.SOURCE_BONUS["public_comment_html"],
        )
        self.assertEqual(
            bpd.relevance_score(title, "public_comment_results_html"),
            bpd.relevance_score(title, "ministry_rss") + bpd.SOURCE_BONUS["public_comment_results_html"],
        )

    def test_recent_supreme_court_channel_meets_relevance_floor(self):
        title = "Neutral recent Supreme Court case"
        self.assertEqual(bpd.relevance_score(title, "court_html"), bpd.RELEVANCE_FLOOR)

    def test_sesc_channel_is_medium_impact_and_meets_floor(self):
        title = "Neutral filtered SESC enforcement item"
        self.assertEqual(bpd.relevance_score(title, "enforcement_html"), bpd.RELEVANCE_FLOOR)
        self.assertEqual(bpd.classify_impact(title, "enforcement_html"), "Medium")

    def test_pmda_safety_signals_are_medium_and_above_floor(self):
        for title in ("クラスI回収について", "使用上の注意の改訂指示通知", "安全性速報を掲載しました"):
            with self.subTest(title=title):
                self.assertGreaterEqual(bpd.relevance_score(title, "agency_html"), bpd.RELEVANCE_FLOOR)
                self.assertEqual(bpd.classify_impact(title, "agency_html"), "Medium")

    def test_pmda_safety_channel_has_minimum_source_bonus(self):
        title = "PMDA item without general regulatory keywords"
        self.assertEqual(
            bpd.relevance_score(title, "pmda_safety_html"),
            bpd.RELEVANCE_FLOOR,
        )

    def test_default_output_is_unlimited(self):
        self.assertEqual(bpd.DEFAULT_OUTPUT_LIMIT, 0)

    def test_rule_based_impact_never_high(self):
        aggressive_titles = [
            "○○法の改正が施行されました（義務化）",
            "個人情報保護法のガイドライン改正と課徴金について",
            "金融商品取引法の罰則強化を公布",
        ]
        for title in aggressive_titles:
            for source_type in ("public_comment_rss", "regulator_rss", "ministry_rss"):
                with self.subTest(title=title, source_type=source_type):
                    self.assertIn(bpd.classify_impact(title, source_type), ("Low", "Medium"))


class TestPublishedItemLimit(unittest.TestCase):
    """Exercise the uncapped default and optional diagnostic limit."""

    @staticmethod
    def _raw_items(count):
        return [
            {
                "id": f"raw-{index:04d}",
                "title_ja": f"candidate {index}",
                "source_name": "Test Official Source",
                "source_type": "public_comment_rss",
                "source_url": f"https://example.go.jp/update/{index}",
                "published_at": "2026-01-01T00:00:00Z",
                "fetched_at": "2026-01-02T00:00:00Z",
            }
            for index in range(count)
        ]

    def _run_build(self, count, argv=None):
        raw_items = self._raw_items(count)
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            raw_path = tmp / "raw_items.json"
            output_path = tmp / "legal_updates.json"
            backup_path = tmp / "legal_updates.backup.json"
            raw_path.write_text(json.dumps(raw_items), encoding="utf-8")
            raw_before = raw_path.read_bytes()

            def synthetic_score(title, _source_type):
                # Earlier candidates are deliberately more relevant, making the
                # expected post-ranking cutoff and output order unambiguous.
                return float(4000 - int(title.rsplit(" ", 1)[1]))

            stdout = io.StringIO()
            with (
                mock.patch.object(bpd, "RAW_PATH", raw_path),
                mock.patch.object(bpd, "OUTPUT_PATH", output_path),
                mock.patch.object(bpd, "BACKUP_PATH", backup_path),
                mock.patch.object(bpd, "relevance_score", side_effect=synthetic_score),
                redirect_stdout(stdout),
            ):
                result = bpd.main(argv or [])

            self.assertEqual(result, 0)
            self.assertEqual(raw_path.read_bytes(), raw_before, "Stage 2 must not trim raw history")
            output = json.loads(output_path.read_text(encoding="utf-8"))
            return raw_items, output, stdout.getvalue()

    @staticmethod
    def _summary_count(stdout, label):
        match = re.search(rf"^{label}\s*:\s*(\d+)$", stdout, re.MULTILINE)
        if not match:
            raise AssertionError(f"Missing {label} in build summary:\n{stdout}")
        return int(match.group(1))

    def test_more_than_3000_candidates_outputs_every_item_by_default(self):
        raw_items, output, stdout = self._run_build(3001)
        self.assertEqual(len(raw_items), 3001)
        self.assertEqual(len(output), 3001)
        self.assertEqual([item["id"] for item in output], [f"raw-{i:04d}" for i in range(3001)])
        self.assertEqual(
            [item["relevance_score"] for item in output],
            sorted((item["relevance_score"] for item in output), reverse=True),
        )
        for item in output:
            self.assertTrue(set(bpd.REQUIRED_FIELDS).issubset(item))
            self.assertIn("relevance_score", item)
            self.assertTrue(item["id"])
            self.assertTrue(item["source_url"])
        self.assertEqual(self._summary_count(stdout, "candidate_items"), 3001)
        self.assertEqual(self._summary_count(stdout, "output_items"), len(output))

    def test_explicit_limit_remains_available_for_diagnostics(self):
        raw_items, output, stdout = self._run_build(12, ["--limit", "10"])
        self.assertEqual(len(raw_items), 12, "Stage 2 must never trim raw history")
        self.assertEqual(len(output), 10)
        self.assertEqual([item["id"] for item in output], [f"raw-{i:04d}" for i in range(10)])
        self.assertEqual(self._summary_count(stdout, "candidate_items"), 12)
        self.assertEqual(self._summary_count(stdout, "output_items"), 10)


class TestAiSummaryPreservation(unittest.TestCase):
    URL = "https://example.go.jp/page1.html"

    def _new_item(self):
        raw = {
            "id": "raw-abc123",
            "title_ja": "個人情報保護法ガイドラインの一部改正（案）に関する意見募集について",
            "source_name": PPC,
            "source_type": "regulator_html",
            "source_url": self.URL,
            "published_at": "2026-06-09T15:00:00Z",
            "fetched_at": "2026-06-10T00:00:00Z",
        }
        return bpd.build_public_item(raw, "2026-06-11", 12.0)

    def _existing_claude(self, **overrides):
        existing = {
            "id": "raw-abc123",
            "source_url": self.URL,
            "title_ja": "個人情報保護法ガイドラインの一部改正（案）に関する意見募集について",
            "summary_source": "claude",
            "summary_en": "AI summary text.",
            "business_impact_en": "AI business impact.",
            "recommended_action_en": "AI recommended action.",
            "summary_ja": "日本語原文に基づくAI要約です。",
            "business_impact_ja": "事業への影響が生じる可能性があります。",
            "recommended_action_ja": "日本語の公式情報源を確認することが考えられます。",
            "summary_ja_source": "claude",
            "ja_summarized_at": "2026-06-10T09:00:00Z",
            "ja_summary_model": "claude-opus-4-8",
            "confidence": "medium",
            "ai_notes": "AI notes.",
            "summarized_at": "2026-06-10T08:00:00Z",
            "summary_model": "claude-opus-4-8",
            # Stale build-owned metadata that must NOT be carried forward:
            "title_en": "OLD TITLE",
            "area": "Other",
            "stage": "Government Announcement",
            "impact_level": "Low",
            "relevance_score": 1.0,
        }
        existing.update(overrides)
        return existing

    def test_preserved_when_id_and_url_match(self):
        item = self._new_item()
        preserved = bpd.preserve_ai_summary_fields(item, {"raw-abc123": self._existing_claude()})
        self.assertTrue(preserved)
        self.assertEqual(item["summary_en"], "AI summary text.")
        self.assertEqual(item["summary_source"], "claude")
        self.assertEqual(item["summarized_at"], "2026-06-10T08:00:00Z")
        self.assertEqual(item["summary_model"], "claude-opus-4-8")
        self.assertEqual(item["confidence"], "medium")
        self.assertEqual(item["summary_ja"], "日本語原文に基づくAI要約です。")
        self.assertEqual(item["summary_ja_source"], "claude")
        self.assertEqual(item["ja_summarized_at"], "2026-06-10T09:00:00Z")

    def test_japanese_summary_is_preserved_independently_of_rule_based_english(self):
        item = self._new_item()
        existing = self._existing_claude(summary_source="rule_based")

        preserved_english = bpd.preserve_ai_summary_fields(item, {"raw-abc123": existing})

        self.assertFalse(preserved_english)
        self.assertEqual(item["summary_en"], bpd.SUMMARY_EN)
        self.assertEqual(item["summary_ja"], "日本語原文に基づくAI要約です。")
        self.assertEqual(item["summary_ja_source"], "claude")

    def test_japanese_summary_is_not_preserved_when_original_title_changes(self):
        item = self._new_item()
        existing = self._existing_claude(title_ja="変更前の日本語原題")

        self.assertTrue(bpd.preserve_ai_summary_fields(item, {"raw-abc123": existing}))

        self.assertEqual(item["summary_en"], "AI summary text.")
        for field in bpd.JAPANESE_SUMMARY_FIELDS:
            self.assertNotIn(field, item)

    def test_not_preserved_when_source_url_differs(self):
        item = self._new_item()
        existing = self._existing_claude(source_url="https://example.go.jp/DIFFERENT.html")
        preserved = bpd.preserve_ai_summary_fields(item, {"raw-abc123": existing})
        self.assertFalse(preserved)
        self.assertEqual(item["summary_en"], bpd.SUMMARY_EN)      # template kept
        self.assertNotIn("summary_source", item)

    def test_not_preserved_for_rule_based_or_missing_source(self):
        for summary_source in ("rule_based", None):
            with self.subTest(summary_source=summary_source):
                item = self._new_item()
                existing = self._existing_claude()
                if summary_source is None:
                    existing.pop("summary_source")
                else:
                    existing["summary_source"] = summary_source
                preserved = bpd.preserve_ai_summary_fields(item, {"raw-abc123": existing})
                self.assertFalse(preserved)
                self.assertEqual(item["summary_en"], bpd.SUMMARY_EN)

    def test_build_owned_metadata_prefers_fresh_build(self):
        """area / stage / title_en / impact / relevance_score come from the new build."""
        item = self._new_item()
        fresh = {k: item[k] for k in ("title_en", "area", "stage", "impact_level", "relevance_score")}
        bpd.preserve_ai_summary_fields(item, {"raw-abc123": self._existing_claude()})
        for key, value in fresh.items():
            with self.subTest(field=key):
                self.assertEqual(item[key], value)
        self.assertNotEqual(item["title_en"], "OLD TITLE")
        self.assertNotEqual(item["area"], "Other")

    def test_no_match_leaves_item_untouched(self):
        item = self._new_item()
        before = dict(item)
        self.assertFalse(bpd.preserve_ai_summary_fields(item, {}))
        self.assertEqual(item, before)


class TestTranslationPreservation(unittest.TestCase):
    URL = "https://example.go.jp/page1.html"

    def _new_item(self):
        raw = {
            "id": "raw-tr-1",
            "title_ja": "個人情報保護法ガイドラインの一部改正（案）に関する意見募集について",
            "source_name": PPC,
            "source_type": "regulator_html",
            "source_url": self.URL,
            "published_at": "2026-06-09T15:00:00Z",
            "fetched_at": "2026-06-10T00:00:00Z",
        }
        return bpd.build_public_item(raw, "2026-06-11", 12.0)

    def _existing(self, translations=None, **overrides):
        existing = {
            "id": "raw-tr-1",
            "source_url": self.URL,
            "translations": translations if translations is not None else {
                "zh-Hans": {
                    "title": "标题",
                    "summary": "摘要",
                    "business_impact": "业务影响",
                    "recommended_action": "建议措施",
                }
            },
        }
        existing.update(overrides)
        return existing

    def test_preserved_when_id_and_url_match(self):
        item = self._new_item()
        self.assertTrue(bpd.preserve_translations(item, {"raw-tr-1": self._existing()}))
        self.assertEqual(item["translations"]["zh-Hans"]["title"], "标题")

    def test_block_reduced_to_four_fields(self):
        noisy = {"zh-Hans": {
            "title": "标题", "summary": "摘要",
            "business_impact": "业务影响", "recommended_action": "建议措施",
            "source_hash": "deadbeef", "model": "x",  # metadata must be dropped
        }}
        item = self._new_item()
        bpd.preserve_translations(item, {"raw-tr-1": self._existing(translations=noisy)})
        self.assertEqual(set(item["translations"]["zh-Hans"]), set(bpd.TRANSLATION_FIELDS))

    def test_not_preserved_when_source_url_differs(self):
        item = self._new_item()
        existing = self._existing(source_url="https://example.go.jp/OTHER.html")
        self.assertFalse(bpd.preserve_translations(item, {"raw-tr-1": existing}))
        self.assertNotIn("translations", item)

    def test_not_preserved_when_block_is_malformed(self):
        for bad in ({"zh-Hans": {"title": "只有标题"}}, {"zh-Hans": "not-a-dict"}, {}):
            with self.subTest(bad=bad):
                item = self._new_item()
                self.assertFalse(
                    bpd.preserve_translations(item, {"raw-tr-1": self._existing(translations=bad)})
                )
                self.assertNotIn("translations", item)

    def test_does_not_touch_english_canonical_or_ai_fields(self):
        item = self._new_item()
        english_before = {k: item[k] for k in (
            "title_en", "summary_en", "business_impact_en", "recommended_action_en"
        )}
        existing = self._existing(
            summary_source="claude", summary_en="AI summary that must NOT be copied.",
        )
        bpd.preserve_translations(item, {"raw-tr-1": existing})
        for key, value in english_before.items():
            with self.subTest(field=key):
                self.assertEqual(item[key], value)
        self.assertNotIn("summary_source", item)


class TestFirstSeenAtPublication(unittest.TestCase):
    def _raw(self, **overrides):
        raw = {
            "id": "raw-first-seen",
            "title_ja": "蛟倶ｺｺ諠・ｱ菫晁ｭｷ豕輔ぎ繧､繝峨Λ繧､繝ｳ縺ｮ荳驛ｨ謾ｹ豁｣縺ｫ縺､縺・※",
            "source_name": PPC,
            "source_type": "regulator_html",
            "source_url": "https://example.go.jp/first-seen.html",
            "published_at": "2026-06-16T00:00:00Z",
            "fetched_at": "2026-06-17T00:00:00Z",
        }
        raw.update(overrides)
        return raw

    def test_valid_first_seen_at_is_copied_to_public_item(self):
        item = bpd.build_public_item(
            self._raw(first_seen_at="2026-06-17"),
            "2026-06-18",
            12.0,
            today="2026-06-18",
        )
        self.assertEqual(item["first_seen_at"], "2026-06-17")

    def test_current_jst_date_uses_japan_day_at_utc_boundary(self):
        utc_boundary = datetime(2026, 6, 17, 15, 30, tzinfo=timezone.utc)

        self.assertEqual(bpd.current_jst_date(utc_boundary), "2026-06-18")

    def test_first_seen_at_validation_uses_jst_today_at_utc_boundary(self):
        today_jst = bpd.current_jst_date(datetime(2026, 6, 17, 15, 30, tzinfo=timezone.utc))

        self.assertEqual(bpd.valid_first_seen_at("2026-06-18", today_jst), "2026-06-18")
        self.assertEqual(bpd.valid_first_seen_at("2026-06-17", today_jst), "2026-06-17")
        self.assertEqual(bpd.valid_first_seen_at("2026-06-19", today_jst), "")

    def test_build_public_item_uses_injected_jst_today_for_first_seen_at(self):
        today_jst = bpd.current_jst_date(datetime(2026, 6, 17, 15, 30, tzinfo=timezone.utc))

        valid_today = bpd.build_public_item(
            self._raw(first_seen_at="2026-06-18"),
            "2026-06-17",
            12.0,
            today=today_jst,
        )
        valid_yesterday = bpd.build_public_item(
            self._raw(first_seen_at="2026-06-17"),
            "2026-06-17",
            12.0,
            today=today_jst,
        )
        future = bpd.build_public_item(
            self._raw(first_seen_at="2026-06-19"),
            "2026-06-17",
            12.0,
            today=today_jst,
        )

        self.assertEqual(valid_today["first_seen_at"], "2026-06-18")
        self.assertEqual(valid_yesterday["first_seen_at"], "2026-06-17")
        self.assertNotIn("first_seen_at", future)

    def test_missing_invalid_or_future_first_seen_at_is_omitted(self):
        cases = [
            {},
            {"first_seen_at": None},
            {"first_seen_at": ""},
            {"first_seen_at": "2026-06-17T00:00:00Z"},
            {"first_seen_at": "not-a-date"},
            {"first_seen_at": "2026-06-19"},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                item = bpd.build_public_item(self._raw(**overrides), "2026-06-18", 12.0, today="2026-06-18")
                self.assertNotIn("first_seen_at", item)

    def test_first_seen_at_survives_ai_summary_preservation(self):
        item = bpd.build_public_item(
            self._raw(first_seen_at="2026-06-17"),
            "2026-06-18",
            12.0,
            today="2026-06-18",
        )
        before = item["first_seen_at"]
        existing = {
            "id": "raw-first-seen",
            "source_url": "https://example.go.jp/first-seen.html",
            "summary_source": "claude",
            "summary_en": "AI summary text.",
            "business_impact_en": "AI business impact.",
            "recommended_action_en": "AI recommended action.",
            "confidence": "medium",
            "ai_notes": "AI notes.",
            "summarized_at": "2026-06-17T08:00:00Z",
            "summary_model": "claude-opus-4-8",
        }

        self.assertTrue(bpd.preserve_ai_summary_fields(item, {"raw-first-seen": existing}))
        self.assertEqual(item["first_seen_at"], before)
        self.assertEqual(item["summary_source"], "claude")


class TestFallbackTitleVariety(unittest.TestCase):
    """M-1: frequent duplicate groups get more specific English fallbacks."""

    MOE = "環境省 (MOE) 報道発表"

    def _title(self, title_ja, source_name):
        stage = bpd.classify_stage(title_ja, "ministry_html")
        area = bpd.classify_area(title_ja, source_name)
        return bpd.generate_title_en(title_ja, source_name, stage, area)

    def test_jftc_law_specific_recommendation_fallbacks(self):
        self.assertEqual(
            self._title("下請代金支払遅延等防止法第７条に基づく勧告について", JFTC),
            "JFTC Update: Recommendation issued under the Subcontract Act",
        )
        self.assertEqual(
            self._title("特定受託事業者に係る取引の適正化等に関する法律に基づく勧告について", JFTC),
            "JFTC Update: Recommendation issued under the Freelance Act",
        )
        self.assertEqual(
            self._title("株式会社ヘリテージに対する勧告について", JFTC),
            "JFTC Update: Recommendation issued to a company",
        )
        self.assertEqual(
            self._title("○○株式会社から申請があった確約計画の認定について", JFTC),
            "JFTC Update: Commitment plan procedure under the Antimonopoly Act",
        )

    def test_moe_topic_fallbacks(self):
        # 廃棄物 is already covered by TITLE_TOPIC_RULES (English topic path).
        self.assertEqual(
            self._title("産業廃棄物処理施設の設置状況等について", self.MOE),
            "MOE Update: Waste Management Rules",
        )
        self.assertEqual(
            self._title("（仮称）○○ウィンドファーム事業に係る計画段階環境配慮書に対する環境大臣意見の提出について", self.MOE),
            "MOE Update: Environmental minister opinion issued on a project environmental review",
        )

    def test_mlit_recall_and_guideline_fallbacks(self):
        self.assertEqual(
            self._title("リコールの届出について（ホンダ ○○ 他）", MLIT),
            "MLIT Update: Vehicle recall notification filed",
        )
        self.assertEqual(
            self._title("○○のためのガイドラインを策定 ～対応方針をとりまとめ～", MLIT),
            "MLIT Update: Guideline formulation or revision announced",
        )

    def test_maff_quarantine_fallback(self):
        self.assertEqual(
            self._title("動物検疫所における家きん肉等の取扱いについて", MAFF),
            "MAFF Update: Animal and plant quarantine information updated",
        )

    def test_caa_premiums_and_food_labeling_fallbacks(self):
        # Full law name hits the existing English topic rule; the abbreviated
        # 景表法 form exercises the new keyword fallback.
        self.assertEqual(
            self._title("景品表示法に基づく措置命令について（○○株式会社）", CAA),
            "CAA Update: Act against Unjustifiable Premiums and Misleading Representations",
        )
        self.assertEqual(
            self._title("景表法に基づく措置命令について（○○株式会社）", CAA),
            "CAA Update: Measure under the Act against Unjustifiable Premiums and Misleading Representations",
        )

    def test_mhlw_occupational_accident_fallback(self):
        self.assertEqual(
            self._title("令和７年の労働災害発生状況を公表", MHLW),
            "MHLW Update: Occupational accident prevention information updated",
        )


class TestDisambiguateDuplicateTitles(unittest.TestCase):
    """M-1: only colliding titles get a published-date suffix."""

    @staticmethod
    def _item(title, date="2026-06-10"):
        return {"title_en": title, "published_at": date}

    def test_duplicates_get_dated_and_uniques_stay_clean(self):
        items = [
            self._item("JFTC Update: Recommendation issued to a company", "2026-06-11"),
            self._item("JFTC Update: Recommendation issued to a company", "2026-06-04"),
            self._item("Unique title stays as-is", "2026-06-12"),
        ]
        bpd.disambiguate_duplicate_titles(items)
        self.assertEqual(items[0]["title_en"], "JFTC Update: Recommendation issued to a company (2026-06-11)")
        self.assertEqual(items[1]["title_en"], "JFTC Update: Recommendation issued to a company (2026-06-04)")
        self.assertEqual(items[2]["title_en"], "Unique title stays as-is")

    def test_duplicate_without_date_is_left_unchanged(self):
        items = [self._item("Same title", ""), self._item("Same title", "")]
        bpd.disambiguate_duplicate_titles(items)
        self.assertEqual(items[0]["title_en"], "Same title")
        self.assertEqual(items[1]["title_en"], "Same title")

    def test_dated_title_respects_length_cap_and_stays_english(self):
        long_title = "A" * (bpd.TITLE_MAX_CHARS - 1)
        items = [self._item(long_title, "2026-06-10"), self._item(long_title, "2026-06-11")]
        bpd.disambiguate_duplicate_titles(items)
        for item in items:
            with self.subTest(title=item["title_en"]):
                self.assertLessEqual(len(item["title_en"]), bpd.TITLE_MAX_CHARS)
                self.assertRegex(item["title_en"], r" \(2026-06-1[01]\)$")
                self.assertFalse(bpd.contains_japanese(item["title_en"]))

    def test_suffix_helper_reserves_space_before_shortening_body(self):
        suffix = " (2026-06-10)"
        base = "Public Comment: " + ("Long regulatory amendment " * 8)
        title = bpd.append_suffix_within_title_cap(base, suffix)

        self.assertLessEqual(len(title), bpd.TITLE_MAX_CHARS)
        self.assertTrue(title.endswith(suffix))
        self.assertFalse(bpd.contains_japanese(title))

    def test_trimmed_duplicate_titles_remain_identifiable(self):
        long_title = "Public Comment: " + ("Draft amendment to technical standards " * 5)
        items = [self._item(long_title, "2026-06-10"), self._item(long_title, "2026-06-11")]

        bpd.disambiguate_duplicate_titles(items)

        titles = [item["title_en"] for item in items]
        self.assertEqual(len(set(titles)), 2)
        self.assertTrue(titles[0].endswith(" (2026-06-10)"))
        self.assertTrue(titles[1].endswith(" (2026-06-11)"))
        for title in titles:
            with self.subTest(title=title):
                self.assertLessEqual(len(title), bpd.TITLE_MAX_CHARS)
                self.assertFalse(bpd.contains_japanese(title))


if __name__ == "__main__":
    unittest.main()
