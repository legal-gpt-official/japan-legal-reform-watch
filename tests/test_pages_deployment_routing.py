"""Offline tests for the safe legacy/custom Pages transition."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import request_pages_deployment as routing


class PagesRoutingTests(unittest.TestCase):
    def test_legacy_and_workflow_routes_are_mutually_exclusive(self):
        for mode, expected in (
            ("legacy", ["gh", "api", "--method", "POST", "repos/unit/repo/pages/builds"]),
            ("workflow", ["gh", "workflow", "run", "deploy-static-pages.yml", "--repo", "unit/repo", "--ref", "main"]),
        ):
            with self.subTest(mode=mode), mock.patch.object(subprocess, "check_output", return_value=mode+"\n"), mock.patch.object(subprocess, "run") as run:
                self.assertEqual(routing.request_deployment("unit/repo"), mode)
                run.assert_called_once_with(expected, check=True)

    def test_unknown_mode_or_failed_lookup_never_dispatches(self):
        with mock.patch.object(subprocess, "check_output", return_value="null"), mock.patch.object(subprocess, "run") as run:
            with self.assertRaises(RuntimeError):
                routing.request_deployment("unit/repo")
            run.assert_not_called()
        with mock.patch.object(subprocess, "check_output", side_effect=subprocess.CalledProcessError(1, "gh")), mock.patch.object(subprocess, "run") as run:
            with self.assertRaises(subprocess.CalledProcessError):
                routing.request_deployment("unit/repo")
            run.assert_not_called()

    def test_only_latest_successful_matching_deployment_reused(self):
        sha, old = "a"*40, "b"*40
        row = {"id": 1, "path": "dynamic/pages/pages-build-deployment", "head_branch": "main",
               "status": "completed", "conclusion": "success", "head_sha": old, "created_at": "2026-11-27T01:00:00Z"}
        with mock.patch.object(routing, "_api", side_effect=[
            {"workflow_runs": [row]}, {"status": "ahead", "files": [{"filename": "data/ledger.json"}]}
        ]):
            self.assertEqual(routing.reusable_deployment("unit/repo", "legacy", sha), 1)
        with mock.patch.object(routing, "_api", return_value={"workflow_runs": [
            row, dict(row, id=2, status="in_progress", created_at="2026-11-27T02:00:00Z")
        ]}):
            self.assertIsNone(routing.reusable_deployment("unit/repo", "legacy", sha))
        with mock.patch.object(routing, "_api", side_effect=[
            {"workflow_runs": [row]}, {"status": "ahead", "files": [{"filename": "docs/index.html"}]}
        ]):
            self.assertIsNone(routing.reusable_deployment("unit/repo", "legacy", sha))
        with mock.patch.object(routing, "_api", return_value={"workflow_runs": [row]}):
            self.assertIsNone(routing.reusable_deployment("unit/repo", "workflow", sha))

    def test_reuse_requires_actual_deploy_and_no_truncated_compare(self):
        sha = "a"*40
        row = {"id": 3, "path": ".github/workflows/deploy-static-pages.yml", "head_branch": "main",
               "status": "completed", "conclusion": "success", "head_sha": sha}
        for step, expected in (("skipped", None), ("success", 3)):
            with mock.patch.object(routing, "_api", side_effect=[
                {"workflow_runs": [row]}, {"jobs": [{"name": "deploy", "conclusion": "success",
                    "steps": [{"name": "Deploy to GitHub Pages", "conclusion": step}]}]}
            ]):
                self.assertEqual(routing.reusable_deployment("unit/repo", "workflow", sha), expected)
        with mock.patch.object(routing, "_api", return_value={"status": "ahead", "files": [{}]*300}):
            self.assertFalse(routing.same_docs("unit/repo", sha, "b"*40))

    def test_request_does_not_dispatch_if_latest_deployment_is_reusable(self):
        with mock.patch.object(subprocess, "check_output", side_effect=["legacy", "a"*40]), \
                mock.patch.object(routing, "reusable_deployment", return_value=42), \
                mock.patch.object(subprocess, "run") as run:
            self.assertEqual(routing.request_deployment("unit/repo", reuse_current=True), "legacy")
            run.assert_not_called()

    def test_daily_paid_pipeline_is_schedule_or_manual_only(self):
        w = (ROOT / ".github/workflows/daily-update.yml").read_text()
        self.assertNotIn("  push:", w)
        self.assertIn("  schedule:", w)
        self.assertIn("  workflow_dispatch:", w)
        self.assertIn("scripts/request_pages_deployment.py --reuse-current", w)

    def test_every_writer_routes_to_configured_mode(self):
        for name in ("daily-update.yml", "publish-explainer-pages.yml", "english-summary-backfill.yml",
                     "japanese-summary-backfill.yml", "translation-backfill.yml"):
            w = (ROOT / ".github/workflows" / name).read_text()
            self.assertIn("scripts/request_pages_deployment.py", w, name)
            self.assertIn("actions: write", w, name)
            self.assertIn("pages: write", w, name)
            self.assertNotIn("pages/builds", w, name)

    def test_custom_workflow_only_deploys_main_docs_in_active_mode(self):
        w = (ROOT / ".github/workflows/deploy-static-pages.yml").read_text()
        self.assertIn('branches: [main]', w)
        self.assertIn('"docs/**"', w)
        self.assertNotIn('"data/**"', w)
        self.assertIn("github.ref == 'refs/heads/main'", w)
        self.assertIn("cancel-in-progress: false", w)
        self.assertIn("workflow) echo", w)
        self.assertIn("legacy) echo", w)
        self.assertIn("HEAD:docs", w)
        self.assertIn("FETCH_HEAD:docs", w)
        self.assertIn("path: docs", w)
        self.assertIn("include-hidden-files: true", w)
        self.assertIn("actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9", w)
        self.assertIn("! -path 'docs/.nojekyll'", w)
        self.assertIn("id-token: write", w)
        self.assertIn("pages: write", w)
        self.assertIn("name: Deploy to GitHub Pages\n        if: steps.mode.outputs.active == 'true'", w)


if __name__ == "__main__":
    unittest.main()
