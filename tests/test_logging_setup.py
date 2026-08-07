"""Regression tests for repeatable pipeline logger initialization."""

import logging
import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
