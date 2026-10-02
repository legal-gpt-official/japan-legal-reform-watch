"""AGENTS.md (read by Codex) and CLAUDE.md (read by Claude Code) carry one set of rules.

They drifted once: each agent updated only its own file, so each became stale
where the other had moved on. CLAUDE.md still forbade the Japanese UI the
dashboard ships, while AGENTS.md still named a retired summary model and a
Japanese translation path that does not exist. Apart from the title line the two
files must stay identical.
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def read_lines(name: str) -> list[str]:
    return (REPO_ROOT / name).read_text(encoding="utf-8").splitlines()


class TestAgentInstructionsInSync(unittest.TestCase):
    def test_agents_md_and_claude_md_share_one_body(self):
        agents = read_lines("AGENTS.md")
        claude = read_lines("CLAUDE.md")
        self.assertTrue(agents[0].startswith("# ") and claude[0].startswith("# "))
        if agents[1:] == claude[1:]:
            return
        mismatch = next(
            (i for i, (a, c) in enumerate(zip(agents[1:], claude[1:]), start=2) if a != c),
            min(len(agents), len(claude)) + 1,
        )
        self.fail(
            f"AGENTS.md and CLAUDE.md differ from line {mismatch}. Apart from the title line they "
            "must be identical: apply the same edit to both files."
        )

    def test_both_files_state_the_rule(self):
        for name in ("AGENTS.md", "CLAUDE.md"):
            with self.subTest(name=name):
                self.assertIn("tests/test_agent_instructions_in_sync.py", "\n".join(read_lines(name)[:4]))


if __name__ == "__main__":
    unittest.main()
