"""Offline tests for transparent before/after routine-summary quality reporting."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import summary_value_audit as audit


def example(i, *, summary="", published="2026-11-09"):
    return {
        "id": f"raw-{i}", "title_ja": f"法律の改正{i}", "source_url": f"https://www.meti.go.jp/{i}",
        "published_at": published, "summary_ja": summary, "summary_en": "English overview",
    }


def metadata(i):
    return {"id": f"raw-{i}", "source_url": f"https://www.meti.go.jp/{i}",
            "raw_content_hash": f"hash-{i}"}


def evidence(i, *, fresh=True):
    return {"source_url": f"https://www.meti.go.jp/{i}",
            "raw_hash": f"hash-{i}" if fresh else "old-hash",
            "documents": [{"url": f"https://www.meti.go.jp/{i}", "format": "html",
                           "excerpt": "改正案の対象者と施行期日及び経過措置について説明する資料です。" * 10}]}


class TestSummaryValueAudit(unittest.TestCase):
    def test_source_document_requires_matching_url_and_content_hash(self):
        item = example(1)
        raw = metadata(1)
        self.assertTrue(audit.valid_documents(item, raw, {item["id"]: evidence(1)}))
        self.assertEqual(audit.valid_documents(item, raw, {item["id"]: evidence(1, fresh=False)}), [])
        bad_url = evidence(1)
        bad_url["source_url"] = "https://www.meti.go.jp/another"
        self.assertEqual(audit.valid_documents(item, raw, {item["id"]: bad_url}), [])

    def test_twenty_items_are_reproducibly_chosen_and_source_backed_first(self):
        items = [example(i, published=f"2026-11-{i:02d}") for i in range(1, 31)]
        raw = {i["id"]: metadata(int(i["id"][4:])) for i in items}
        evidence_by_id = {f"raw-{i}": evidence(i) for i in (1, 2, 4, 7)}
        chosen = audit.choose(items, raw, evidence_by_id, 20)
        self.assertEqual(len(chosen), 20)
        self.assertEqual({i["id"] for i in chosen[:4]}, {"raw-1", "raw-2", "raw-4", "raw-7"})
        self.assertEqual(chosen, audit.choose(items, raw, evidence_by_id, 20))

    def test_snapshot_and_comparison_preserve_before_without_claiming_legal_accuracy(self):
        before = example(1, summary="要約は公式資料で確認してください。")
        raw = {before["id"]: metadata(1)}
        shot = audit.build_snapshot([before], raw, {before["id"]: evidence(1)}, 20)
        after = {**before, "summary_ja": "2027年4月1日から新様式に移行する予定です。"}
        report = audit.compare(shot, [after])
        self.assertEqual(report["sampled"], 1)
        self.assertEqual(report["with_official_excerpts"], 1)
        self.assertEqual(report["changed"], 1)
        self.assertEqual(report["records"][0]["changed_fields"], ["summary_ja"])
        self.assertEqual(report["records"][0]["before"]["summary_ja"], before["summary_ja"])
        self.assertEqual(report["records"][0]["after"]["summary_ja"], after["summary_ja"])
        self.assertIn("do NOT establish", report["limitation"])
        md = audit.render_markdown(report)
        self.assertIn("before:", md)
        self.assertIn("after:", md)
        self.assertIn("https://www.meti.go.jp/1", md)

    def test_no_text_change_reported_as_no_improvement(self):
        item = example(1, summary="変更は不明です。")
        snap = audit.build_snapshot([item], {item["id"]: metadata(1)}, {}, 20)
        report = audit.compare(snap, [item])
        self.assertEqual(report["changed"], 0)
        self.assertEqual(report["unchanged"], 1)
        self.assertEqual(report["with_official_excerpts"], 0)

if __name__ == "__main__":
    unittest.main()
