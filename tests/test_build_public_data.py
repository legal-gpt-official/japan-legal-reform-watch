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
No network, no file writes; the scripts under test are imported directly.
"""

import re
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_public_data as bpd  # noqa: E402

# Hiragana / katakana / CJK ideographs / fullwidth punctuation.
CJK_RE = re.compile(r"[぀-ヿ㐀-鿿！-｠]")

EGOV = "e-Gov Public Comment (意見募集案件一覧)"
FSA = "Financial Services Agency (金融庁) 新着情報"
METI = "経済産業省 (METI) ニュースリリース"
MHLW = "Ministry of Health, Labour and Welfare (厚生労働省) 新着情報"
CAA = "消費者庁 (CAA) 新着情報"
PPC = "個人情報保護委員会 (PPC) 新着情報"
JFTC = "公正取引委員会 (JFTC) 報道発表"


class TestStageClassification(unittest.TestCase):
    def test_public_comment_open_by_source_type(self):
        self.assertEqual(
            bpd.classify_stage("国民年金法施行規則の一部を改正する省令案に関する御意見の募集について", "public_comment_rss"),
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

    def test_law_lifecycle_stages(self):
        self.assertEqual(bpd.classify_stage("改正個人情報保護法が施行されました", "regulator_rss"), "In Force")
        self.assertEqual(bpd.classify_stage("○○法の施行期日を定める政令について", "ministry_rss"), "Scheduled to Take Effect")
        self.assertEqual(bpd.classify_stage("○○法を公布しました", "ministry_rss"), "Promulgated")
        self.assertEqual(bpd.classify_stage("○○法が成立しました", "ministry_rss"), "Enacted")
        self.assertEqual(bpd.classify_stage("○○法律案が国会に提出されました", "ministry_rss"), "Bill Submitted")


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

    def test_jftc_public_comment_prefix(self):
        _, t = self._title(
            "「流通・取引慣行に関する独占禁止法上の指針」の一部改正（案）に対する意見募集について", JFTC, "regulator_html")
        self.assertTrue(t.startswith("JFTC Public Comment: "))
        self.assertIn("Antimonopoly Act Guidelines", t)
        self.assertIsNone(CJK_RE.search(t))

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
        _, t = self._title("あ" * 150, "Unknown Agency", "ministry_rss")
        self.assertLessEqual(len(t), bpd.TITLE_MAX_CHARS)
        self.assertTrue(t.endswith("..."))

    def test_shorten_title_leaves_short_titles_alone(self):
        self.assertEqual(bpd.shorten_title("short title"), "short title")


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

    def test_egov_source_bonus(self):
        title = "○○省令案に関する意見募集"
        self.assertEqual(
            bpd.relevance_score(title, "public_comment_rss"),
            bpd.relevance_score(title, "ministry_rss") + bpd.SOURCE_BONUS["public_comment_rss"],
        )

    def test_output_cap_is_50(self):
        self.assertEqual(bpd.MAX_OUTPUT_ITEMS, 50)

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
            "summary_source": "claude",
            "summary_en": "AI summary text.",
            "business_impact_en": "AI business impact.",
            "recommended_action_en": "AI recommended action.",
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


if __name__ == "__main__":
    unittest.main()
