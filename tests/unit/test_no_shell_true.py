"""Regression test: no shell=True in subprocess calls anywhere under src/.

This is a security guard test (P0). It fails immediately if any Python source
file under src/ introduces ``subprocess.run(..., shell=True)`` or equivalent.
A future contributor attempting to use shell=True will see an explicit failure
here before the change can be merged.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

# Root of the source tree to scan.
SRC_ROOT = Path(__file__).parent.parent.parent / "src"

# Regex-based quick scan — catches the obvious ``shell=True`` literal.
_SHELL_TRUE_RE = re.compile(r"\bshell\s*=\s*True\b")


def _python_files(root: Path):
    """Yield all .py files under *root* recursively.

    Args:
        root: Directory path to walk.

    Yields:
        Path objects for every .py file found.
    """
    for path in sorted(root.rglob("*.py")):
        yield path


def test_no_shell_true_regex():
    """No source file under src/ may contain the literal ``shell=True``.

    This regex-based check is the fast path: it will catch the most common
    form of the violation without needing to parse the AST.
    """
    violations: list[str] = []
    for py_file in _python_files(SRC_ROOT):
        text = py_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _SHELL_TRUE_RE.search(line):
                violations.append(f"{py_file.relative_to(SRC_ROOT.parent)}:{lineno}: {line.strip()}")

    assert not violations, (
        "shell=True found in src/ — use argument arrays instead:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_no_shell_true_ast():
    """AST-level check: subprocess.run / Popen / call / check_output must not pass shell=True.

    This deeper check parses each file and inspects every Call node whose
    function name ends in one of the subprocess entry-points, verifying that
    no keyword argument named ``shell`` has the value ``True``.
    """
    SUBPROCESS_FUNCS = {"run", "Popen", "call", "check_call", "check_output"}
    violations: list[str] = []

    for py_file in _python_files(SRC_ROOT):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            # If a file can't be parsed, skip it — syntax errors are caught elsewhere.
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            # Match bare names like run(...) and attribute access like subprocess.run(...)
            func = node.func
            func_name = None
            if isinstance(func, ast.Name):
                func_name = func.id
            elif isinstance(func, ast.Attribute):
                func_name = func.attr

            if func_name not in SUBPROCESS_FUNCS:
                continue

            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    violations.append(
                        f"{py_file.relative_to(SRC_ROOT.parent)}:{node.lineno}: "
                        f"{func_name}(..., shell=True)"
                    )

    assert not violations, (
        "shell=True detected in subprocess call under src/ — use argument arrays instead:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
