"""Offline tests for uncapped yearly public archive generation."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_public_archives as bpa  # noqa: E402


def item(item_id, published_at):
    return {"id": item_id, "published_at": published_at, "title_en": item_id}


class TestArchivePayload(unittest.TestCase):
    def test_groups_by_valid_year_and_preserves_ranked_order_within_each_shard(self):
        items = [
            item("a", "2026-08-01"),
            item("b", "2025-12-31T10:00:00+09:00"),
            item("c", "2026-01-01"),
            item("d", ""),
            item("e", "not-a-date"),
        ]

        manifest, shards = bpa.build_archive_payload(items)

        self.assertEqual(list(shards), ["2026", "2025", "undated"])
        self.assertEqual([row["id"] for row in shards["2026"]], ["a", "c"])
        self.assertEqual([row["id"] for row in shards["undated"]], ["d", "e"])
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["total_items"], 5)
        self.assertEqual(manifest["latest_period"], "2026")
        self.assertEqual(
            manifest["periods"],
            [
                {"value": "2026", "label": "2026", "file": "./data/archive/2026.json", "count": 2},
                {"value": "2025", "label": "2025", "file": "./data/archive/2025.json", "count": 1},
                {"value": "undated", "label": "Undated", "file": "./data/archive/undated.json", "count": 2},
            ],
        )

    def test_no_per_year_cap(self):
        items = [item(f"id-{index}", "2026-01-01") for index in range(3001)]
        manifest, shards = bpa.build_archive_payload(items)
        self.assertEqual(manifest["total_items"], 3001)
        self.assertEqual(len(shards["2026"]), 3001)

    def test_future_year_rollover_and_leap_day_validation(self):
        items = [
            item("next", "2027-01-01"),
            item("future-leap", "2028-02-29"),
            item("invalid-leap", "2027-02-29"),
            item("current", "2026-12-31"),
        ]

        manifest, shards = bpa.build_archive_payload(items)

        self.assertEqual(list(shards), ["2028", "2027", "2026", "undated"])
        self.assertEqual(manifest["latest_period"], "2028")
        self.assertEqual([row["id"] for row in shards["2028"]], ["future-leap"])
        self.assertEqual([row["id"] for row in shards["undated"]], ["invalid-leap"])

    def test_duplicate_or_missing_ids_fail(self):
        for items in (
            [item("same", "2026-01-01"), item("same", "2025-01-01")],
            [{"published_at": "2026-01-01"}],
        ):
            with self.subTest(items=items), self.assertRaises(ValueError):
                bpa.build_archive_payload(items)


class TestArchiveWriting(unittest.TestCase):
    def test_writes_manifest_shards_and_removes_only_stale_generated_files(self):
        items = [item("a", "2026-01-01"), item("b", "2025-01-01")]
        manifest, shards = bpa.build_archive_payload(items)
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            archive_dir = root / "archive"
            archive_dir.mkdir()
            (archive_dir / "2024.json").write_text("[]", encoding="utf-8")
            (archive_dir / "notes.json").write_text("{}", encoding="utf-8")
            manifest_path = root / "manifest.json"

            removed = bpa.write_archives(
                manifest,
                shards,
                manifest_path=manifest_path,
                archive_dir=archive_dir,
            )

            self.assertEqual([path.name for path in removed], ["2024.json"])
            self.assertTrue((archive_dir / "notes.json").exists())
            self.assertEqual(json.loads((archive_dir / "2026.json").read_text(encoding="utf-8")), [items[0]])
            self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8")), manifest)

    def test_main_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_path = root / "input.json"
            manifest_path = root / "manifest.json"
            archive_dir = root / "archive"
            input_path.write_text(json.dumps([item("a", "2026-01-01")]), encoding="utf-8")

            result = bpa.main(
                [
                    "--input", str(input_path),
                    "--manifest", str(manifest_path),
                    "--archive-dir", str(archive_dir),
                    "--dry-run",
                ]
            )

            self.assertEqual(result, 0)
            self.assertFalse(manifest_path.exists())
            self.assertFalse(archive_dir.exists())


class TestCheckedInArchiveArtifacts(unittest.TestCase):
    def test_manifest_and_shards_exactly_partition_canonical_dataset(self):
        canonical_path = REPO_ROOT / "docs" / "data" / "legal_updates.json"
        manifest_path = REPO_ROOT / "docs" / "data" / "legal_updates_manifest.json"
        archive_dir = REPO_ROOT / "docs" / "data" / "archive"
        self.assertTrue(manifest_path.exists(), "run scripts/build_public_archives.py")

        canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_manifest, expected_shards = bpa.build_archive_payload(canonical)
        self.assertEqual(manifest, expected_manifest)

        combined_ids = []
        for entry in manifest["periods"]:
            shard_path = archive_dir / f"{entry['value']}.json"
            self.assertTrue(shard_path.exists(), shard_path)
            shard = json.loads(shard_path.read_text(encoding="utf-8"))
            self.assertEqual(shard, expected_shards[entry["value"]])
            self.assertEqual(len(shard), entry["count"])
            combined_ids.extend(row["id"] for row in shard)

        self.assertEqual(len(combined_ids), manifest["total_items"])
        self.assertEqual(set(combined_ids), {row["id"] for row in canonical})


if __name__ == "__main__":
    unittest.main()
