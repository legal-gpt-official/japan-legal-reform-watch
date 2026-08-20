"""Regression tests for repeatable pipeline logger initialization."""

import logging
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_updates as fu  # noqa: E402
import summarize_updates as su  # noqa: E402
import translate_updates as tu  # noqa: E402


class TestLoggingSetup(unittest.TestCase):
    def test_reinitialization_closes_replaced_handlers(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cases = (
                (fu, {"LOG_DIR": root / "fetch", "LOG_PATH": root / "fetch" / "fetch.log"}),
                (su, {"LOG_PATH": root / "summarize" / "summarize.log"}),
                (tu, {"LOG_PATH": root / "translate" / "translate.log"}),
            )

            for module, replacements in cases:
                with self.subTest(module=module.__name__):
                    originals = {name: getattr(module, name) for name in replacements}
                    for handler in list(module.logger.handlers):
                        module.logger.removeHandler(handler)
                        handler.close()
                    replaced_handler = logging.NullHandler()
                    module.logger.addHandler(replaced_handler)
                    try:
                        for name, value in replacements.items():
                            setattr(module, name, value)

                        module.setup_logging()

                        self.assertNotIn(replaced_handler, module.logger.handlers)
                        self.assertTrue(replaced_handler._closed)
                    finally:
                        for handler in list(module.logger.handlers):
                            module.logger.removeHandler(handler)
                            handler.close()
                        for name, value in originals.items():
                            setattr(module, name, value)

class TestAtomicReplaceRetries(unittest.TestCase):
    """A transient Windows file lock must not abort a pipeline write.

    os.replace is atomic, but on Windows an on-access scanner can hold the
    freshly written temp file for a few milliseconds. Before the retry this made
    the offline suite fail on roughly four runs in five, which both masked real
    failures and could abort a local pipeline run mid-write.
    """

    def _tmp_pair(self, base):
        target = base / "data.json"
        tmp = base / "data.json.tmp"
        tmp.write_text("payload", encoding="utf-8")
        return tmp, target

    def test_transient_permission_error_is_retried(self):
        import tempfile
        from pathlib import Path as _Path
        with tempfile.TemporaryDirectory() as raw:
            base = _Path(raw)
            tmp, target = self._tmp_pair(base)
            calls = {"n": 0}
            real_replace = type(tmp).replace

            def flaky(self, dest):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise PermissionError(5, "Access is denied")
                return real_replace(self, dest)

            with mock.patch.object(type(tmp), "replace", flaky):
                su.atomic_replace(tmp, target, delay=0)
            self.assertEqual(calls["n"], 3)
            self.assertEqual(target.read_text(encoding="utf-8"), "payload")

    def test_a_persistent_permission_error_still_surfaces(self):
        import tempfile
        from pathlib import Path as _Path
        with tempfile.TemporaryDirectory() as raw:
            base = _Path(raw)
            tmp, target = self._tmp_pair(base)

            def always_denied(self, dest):
                raise PermissionError(5, "Access is denied")

            with mock.patch.object(type(tmp), "replace", always_denied):
                with self.assertRaises(PermissionError):
                    su.atomic_replace(tmp, target, attempts=3, delay=0)

    def test_every_pipeline_writer_uses_the_retrying_replace(self):
        root = Path(__file__).resolve().parents[1] / "scripts"
        for name in ("build_public_data.py", "fetch_updates.py", "source_health.py",
                     "summarize_updates.py", "translate_updates.py",
                     "evaluate_summary_model.py"):
            source = (root / name).read_text(encoding="utf-8")
            self.assertIn("atomic_replace(tmp, path)", source, name)
            # The only bare tmp.replace left must be the one inside the helper.
            self.assertEqual(
                source.count("tmp.replace(path)"), 1,
                f"{name} still calls tmp.replace() directly somewhere",
            )
            self.assertIn("def atomic_replace(", source, name)


if __name__ == "__main__":
    unittest.main()
