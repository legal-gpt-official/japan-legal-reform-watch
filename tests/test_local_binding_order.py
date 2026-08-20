"""Static guard against 'used before it is bound' locals in the pipeline scripts.

Phase 3A shipped a real outage of exactly this shape: a batch-metrics counter was
incremented (`recovered_items += ...`) in a block that ran *before* the line that
initialised it. Python only raises at runtime, and only on the path that reaches
the increment, so the offline suite and every no-API smoke run stayed green while
the first real batch run died with UnboundLocalError.

The same misordering silently reset `provider_fatal` / `provider_error_type`
afterwards, which would have let a blocked submission interlock go ahead and
submit — a duplicate-spend bug hiding behind a crash.

These checks are deliberately mechanical and cheap: for every function scope, a
name that is *read* by an augmented assignment or mutated through a method call
must have a plain binding earlier in the source. That is a line-order
approximation of dominance, not a proof, but it catches this whole family of
mistakes at import time instead of in production.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Method calls that mutate the receiver in place; the receiver must already exist.
MUTATING_METHODS = {"append", "extend", "update", "add", "setdefault", "insert"}


class ScopeAnalyzer(ast.NodeVisitor):
    """Collect, per function scope, where each local name is bound and used."""

    def __init__(self) -> None:
        self.problems: list[tuple[str, int, str]] = []

    # -- scope entry points -------------------------------------------------
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._analyze_scope(node)

    def visit_AsyncFunctionDef(self, node) -> None:  # pragma: no cover - none today
        self._analyze_scope(node)

    def _analyze_scope(self, func) -> None:
        bindings: dict[str, int] = {}
        aug_uses: dict[str, int] = {}
        mutations: dict[str, int] = {}
        declared_elsewhere: set[str] = set()

        for arg in list(func.args.args) + list(func.args.kwonlyargs) + list(func.args.posonlyargs):
            bindings.setdefault(arg.arg, func.lineno)
        for arg in (func.args.vararg, func.args.kwarg):
            if arg is not None:
                bindings.setdefault(arg.arg, func.lineno)

        for node in self._walk_scope(func):
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                declared_elsewhere.update(node.names)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    self._record_targets(target, node.lineno, bindings)
            elif isinstance(node, ast.AnnAssign):
                if node.value is not None:
                    self._record_targets(node.target, node.lineno, bindings)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                self._record_targets(node.target, node.lineno, bindings)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        self._record_targets(item.optional_vars, node.lineno, bindings)
            elif isinstance(node, ast.ExceptHandler):
                if node.name:
                    bindings.setdefault(node.name, node.lineno)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    bindings.setdefault(name, node.lineno)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bindings.setdefault(node.name, node.lineno)
            elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                aug_uses.setdefault(node.target.id, node.lineno)
                bindings.setdefault(node.target.id, node.lineno)
            elif isinstance(node, ast.Call):
                func_node = node.func
                if (
                    isinstance(func_node, ast.Attribute)
                    and func_node.attr in MUTATING_METHODS
                    and isinstance(func_node.value, ast.Name)
                ):
                    mutations.setdefault(func_node.value.id, node.lineno)

        where = f"{func.name}()"
        for name, use_line in sorted(aug_uses.items()):
            if name in declared_elsewhere:
                continue
            bound = bindings.get(name)
            if bound is None or bound >= use_line:
                self.problems.append((where, use_line, f"`{name} +=` runs before {name} is bound"))
        for name, use_line in sorted(mutations.items()):
            if name in declared_elsewhere or name not in bindings:
                continue  # not a local of this scope (module global, import, ...)
            if bindings[name] > use_line:
                self.problems.append((where, use_line, f"`{name}.<mutate>()` runs before {name} is bound"))

        # Nested functions are separate scopes; analyse them on their own terms.
        for node in ast.walk(func):
            if node is not func and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._analyze_scope(node)

    @staticmethod
    def _walk_scope(func):
        """Walk `func`'s body without descending into nested function scopes."""
        stack = list(func.body)
        while stack:
            node = stack.pop(0)
            yield node
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    continue
                stack.append(child)

    @staticmethod
    def _record_targets(target, lineno: int, bindings: dict[str, int]) -> None:
        for node in ast.walk(target):
            if isinstance(node, ast.Name):
                bindings.setdefault(node.id, lineno)


def analyze(path: Path) -> list[tuple[str, int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    analyzer = ScopeAnalyzer()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            analyzer._analyze_scope(node)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    analyzer._analyze_scope(sub)
    return analyzer.problems


PIPELINE_SCRIPTS = (
    "anthropic_batch.py",
    "build_public_archives.py",
    "build_public_data.py",
    "evaluate_summary_model.py",
    "fetch_updates.py",
    "generate_alert_digest.py",
    "public_comment_deadlines.py",
    "source_health.py",
    "summarize_updates.py",
    "translate_updates.py",
)


class TestNoUseBeforeBinding(unittest.TestCase):
    def test_no_local_is_incremented_or_mutated_before_it_is_bound(self):
        failures = []
        for name in PIPELINE_SCRIPTS:
            for scope, line, message in analyze(SCRIPTS_DIR / name):
                failures.append(f"{name}:{line} in {scope}: {message}")
        self.assertEqual(failures, [], "use-before-binding:\n  " + "\n  ".join(failures))

    def test_the_checker_actually_catches_the_shipped_bug(self):
        """Guard the guard: the analyzer must flag the exact Phase 3A pattern."""
        import tempfile

        source = (
            "def main():\n"
            "    if flag:\n"
            "        recovered_items += 1\n"
            "    recovered_items = 0\n"
            "    return recovered_items\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bug.py"
            path.write_text(source, encoding="utf-8")
            problems = analyze(path)
        self.assertTrue(problems)
        self.assertIn("recovered_items", problems[0][2])

    def test_the_checker_does_not_flag_correct_ordering(self):
        import tempfile

        source = (
            "def main():\n"
            "    recovered_items = 0\n"
            "    rows = []\n"
            "    if flag:\n"
            "        recovered_items += 1\n"
            "        rows.append(1)\n"
            "    return recovered_items, rows\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ok.py"
            path.write_text(source, encoding="utf-8")
            self.assertEqual(analyze(path), [])

    def test_nonlocal_and_global_names_are_not_misreported(self):
        import tempfile

        source = (
            "total = 0\n"
            "def outer():\n"
            "    acc = 0\n"
            "    def inner():\n"
            "        nonlocal acc\n"
            "        acc += 1\n"
            "    def bump():\n"
            "        global total\n"
            "        total += 1\n"
            "    return inner, bump\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scopes.py"
            path.write_text(source, encoding="utf-8")
            self.assertEqual(analyze(path), [])


class TestPhase3AMetricsAreInitialisedFirst(unittest.TestCase):
    """Every Phase 3A metric must be bound before the batch block that updates it."""

    METRICS = (
        "recovered_items", "batch_blocked", "batch_blocked_reason", "preflight_cost_usd",
        "preflight_trimmed", "batch_outcomes", "provider_fatal", "provider_error_type",
    )

    def _main_source(self, script: str) -> str:
        text = (SCRIPTS_DIR / script).read_text(encoding="utf-8")
        tree = ast.parse(text)
        main = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
        lines = text.splitlines()
        return "\n".join(lines[main.lineno - 1: main.end_lineno])

    def test_summarize_initialises_metrics_before_the_batch_block(self):
        body = self._main_source("summarize_updates.py")
        recovery = body.index("batch_blocked = False")
        for metric in self.METRICS:
            if metric == "batch_blocked":
                continue
            first = body.find(f"{metric} = ")
            self.assertNotEqual(first, -1, f"{metric} is never initialised")
            self.assertLess(
                first, recovery,
                f"{metric} is initialised after the batch block that updates it",
            )

    def test_summarize_recovery_still_runs_before_the_item_loop(self):
        """Relocating the block must not push it past the loop it feeds."""
        body = self._main_source("summarize_updates.py")
        self.assertLess(
            body.index("batch_blocked = False"),
            body.index("for it in items:"),
            "recovered results must be visible to the item loop as cache hits",
        )


if __name__ == "__main__":
    unittest.main()
