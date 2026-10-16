"""Offline tests: reader-facing date-times keep their time and time zone, and
Japanese summaries are given (and keep) the instrument numbers the source states."""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import alert_common as ac  # noqa: E402
import quality_audit as qa  # noqa: E402
import summarize_updates as su  # noqa: E402
import translate_updates as tu  # noqa: E402


class TestReadableIsoDatetimes(unittest.TestCase):
    def test_time_and_zone_survive(self):
        cases = {
            "The stated comment deadline is 2026-09-29T23:59:00+09:00.":
                "The stated comment deadline is 2026-09-29 23:59 JST.",
            "截止时间为2026-09-25T00:00:00+09:00。": "截止时间为2026-09-25 00:00 JST。",
            "at 2026-09-25T10:30:15+09:00": "at 2026-09-25 10:30:15 JST",  # seconds kept when not :00
            "at 2026-09-25T09:00": "at 2026-09-25 09:00 JST",  # no offset means JST here
        }
        for raw, expected in cases.items():
            self.assertEqual(ac.readable_iso_datetimes(raw), expected)

    def test_another_offset_is_converted_to_the_same_instant(self):
        # 15:00 UTC is 00:00 JST the next day: the instant, not the digits, is kept.
        self.assertEqual(ac.readable_iso_datetimes("due 2026-09-25T15:00:00Z"), "due 2026-09-26 00:00 JST")

    def test_plain_dates_and_garbage_are_untouched(self):
        for text in ("Published 2026-09-25.", "2026-13-45T25:00:00+09:00", "Notice No. 457 of 1995"):
            self.assertEqual(ac.readable_iso_datetimes(text), text)

    def test_the_payload_deadline_is_readable(self):
        payload = su.source_payload({"title_ja": "x", "comment_deadline": "2026-09-29T23:59:00+09:00"}, {})
        self.assertEqual(payload["comment_deadline"], "2026-09-29 23:59 JST")
        payload = su.source_payload({"title_ja": "x", "comment_deadline": "2026-09-29"}, {})
        self.assertEqual(payload["comment_deadline"], "2026-09-29 (end of day, JST)")

    def test_english_and_chinese_outputs_are_rewritten(self):
        item = {"title_ja": "x"}
        su.apply_result(item, {"title_en": "Draft notice", "summary_en": "Deadline 2026-09-25T00:00:00+09:00.",
                               "business_impact_en": "May affect.", "recommended_action_en": "Review.",
                               "confidence": "medium"}, "2026-10-16T00:00:00Z", "m")
        self.assertEqual(item["summary_en"], "Deadline 2026-09-25 00:00 JST.")
        fields = tu.normalized_fields({"title": "草案", "summary": "截止时间为2026-09-25T00:00:00+09:00。",
                                       "business_impact": "可能受影响。", "recommended_action": "建议查阅。"})
        self.assertEqual(fields["summary"], "截止时间为2026-09-25 00:00 JST。")


class TestJapaneseInstrumentNumbers(unittest.TestCase):
    RAW = {"raw_summary": "対象法令: 地方公共団体情報システムの標準化に関する省令; 法令番号: 令和八年厚生労働省令第百二十六号; "
                          "改正法令: 関係省令の整備に関する省令; 改正法令番号: 令和八年厚生労働省令第百四十四号"}
    ITEM = {"id": "raw-x", "title_ja": "関係省令の整備に関する省令", "source_name": "e-Gov Law Search (法令更新一覧)",
            "stage": "In Force", "summary_ja_source": "claude", "business_impact_ja": "影響の可能性があります。",
            "recommended_action_ja": "原文を確認してください。"}

    def test_the_japanese_payload_and_prompt_carry_every_number(self):
        payload = su.japanese_source_payload(self.ITEM, self.RAW)
        written = {i["as_written"]: i["role"] for i in payload["legal_identifiers"]}
        self.assertEqual(written, {"令和八年厚生労働省令第百二十六号": "target_law",
                                   "令和八年厚生労働省令第百四十四号": "amending_instrument"})
        self.assertIn("role を問わず", su.SYSTEM_PROMPT_JA)
        self.assertIn("省略せずに", su.SYSTEM_PROMPT_JA)
        params = su.japanese_summary_request_params("claude-sonnet-5-5", self.ITEM, self.RAW)
        self.assertIn("令和八年厚生労働省令第百二十六号", params["messages"][0]["content"])

    def test_a_dropped_target_number_is_flagged_and_kanji_or_arabic_forms_count(self):
        dropped = dict(self.ITEM, summary_ja="令和8年厚生労働省令第144号により、標準化基準を定める省令が改正されます。")
        kept = dict(self.ITEM, summary_ja="令和8年厚生労働省令第144号により、令和八年厚生労働省令第百二十六号が改正されます。")
        kept_arabic = dict(self.ITEM, summary_ja="第144号により第126号の省令が改正されます。")
        raw = {"raw-x": self.RAW}
        self.assertIn("ja_law_number_dropped", qa.audit([dropped], raw, {})["found"])
        self.assertNotIn("ja_law_number_dropped", qa.audit([kept], raw, {})["found"])
        self.assertNotIn("ja_law_number_dropped", qa.audit([kept_arabic], raw, {})["found"])

    def test_iso_datetimes_shown_to_readers_are_flagged(self):
        item = dict(self.ITEM, summary_ja="受付締切は2026-09-25T00:00:00+09:00です。令和8年厚生労働省令第144号、第126号。")
        self.assertIn("ja_iso_datetime", qa.audit([item], {"raw-x": self.RAW}, {})["found"])


if __name__ == "__main__":
    unittest.main()
