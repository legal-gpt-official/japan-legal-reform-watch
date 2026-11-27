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
        self.assertIn("id-token: write", w)
        self.assertIn("pages: write", w)
        self.assertIn("name: Deploy to GitHub Pages\n        if: steps.mode.outputs.active == 'true'", w)


if __name__ == "__main__":
    unittest.main()
