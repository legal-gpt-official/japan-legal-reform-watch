"""Temporary offline inspection of public source records; remove before merge."""
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import explainers as ex

IDS = [
 "raw-c0242242e59b6dea",
 "raw-528dc420ddae884d",
 "raw-416ee13dde3b18a6",
 "raw-5daf6c3c7e93d898",
 "raw-66ad06863627386b",
 "raw-6f6ba33df6f6f458",
 "raw-e99b922033c552b4",
 "raw-0f915389da50170d",
 "raw-fb3733c298e4f4aa",
 "raw-5d8de6de8c85584b",
 "raw-8318b071bee6bcf6",
 "raw-a774a90138a1dcf2",
 "raw-22524fb54e7ce1d5"
]
class InspectPublicSources(unittest.TestCase):
 def test_context(self):
  index=ex.OfficialIndex.build(ex.load_json(ex.RAW_PATH))
  for item_id in IDS:
   if item_id not in index.raw_by_id:
    continue
   d=ex.context(index,item_id,{})
   print("CONTEXT_20261031 "+json.dumps({"seed":item_id,"ids":d["item_ids"],"fingerprint":d["current_fingerprint"],"records":d["records"]},ensure_ascii=False),flush=True)
  self.assertTrue(True)
