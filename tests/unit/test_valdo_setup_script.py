"""Contract tests for scripts/valdo-setup.sh (S10-3, #398).

These tests assert the single local-setup script honours its documented
contract without performing the (slow) full venv build:

  * the deferred ``--env int`` / ``--env full-stack`` seams exit 0 with a
    clear "deferred" message and do NOT touch the filesystem;
  * ``--help`` / ``-h`` print usage and exit 0;
  * an unknown flag and a bad ``--env`` value exit non-zero;
  * the script never clobbers an existing ``.env`` (static guard);
  * the script wires the zero-infra SQLite defaults (static guard);
  * the script resolves the repo root from its own location and uses
    ``set -euo pipefail`` (static guards on architecture principle #5 and
    safe-shell requirements).

The full venv + migration + ``valdo info`` path is verified manually in the
S10-3 implementation run (documented in CHANGELOG / the story write-up); it is
intentionally NOT executed here to keep the unit suite fast and hermetic.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "valdo-setup.sh"

_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(_BASH is None, reason="bash not available")


def _run(args, cwd):
    """Run the setup script with *args* in *cwd*; return CompletedProcess."""
    return subprocess.run(  # noqa: S603 — fixed argv, no shell.
        [_BASH, str(SCRIPT), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_script_exists_and_is_bash():
    assert SCRIPT.is_file(), "scripts/valdo-setup.sh must exist"
    first_line = SCRIPT.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("#!"), "script must have a shebang"
    assert "bash" in first_line


def test_uses_strict_mode_and_repo_root_resolution():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text, "must use strict bash mode"
    # Principle #5: resolve repo root from the script's own location.
    assert "BASH_SOURCE[0]" in text
    assert "PROJECT_ROOT=" in text
    # No hardcoded absolute /Users or /home path literals.
    assert "/Users/" not in text
    assert "/home/" not in text


def test_help_exits_zero_and_prints_usage():
    for flag in ("-h", "--help"):
        result = _run([flag], cwd=REPO_ROOT)
        assert result.returncode == 0, result.stderr
        assert "Usage:" in result.stdout


def test_env_int_is_deferred_and_touches_nothing(tmp_path):
    # Run in an empty scratch dir; a deferred path must not create files.
    result = _run(["--env", "int"], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "deferred" in result.stdout.lower()
    # No .env / .venv / dirs created by the deferred path.
    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / ".venv").exists()


def test_env_full_stack_is_deferred():
    result = _run(["--env", "full-stack"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    assert "deferred" in result.stdout.lower()


def test_env_equals_form_is_accepted():
    result = _run(["--env=int"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    assert "deferred" in result.stdout.lower()


def test_unknown_flag_exits_nonzero():
    result = _run(["--bogus"], cwd=REPO_ROOT)
    assert result.returncode != 0
    assert "unknown argument" in result.stderr.lower()


def test_bad_env_value_exits_nonzero():
    result = _run(["--env", "staging"], cwd=REPO_ROOT)
    assert result.returncode != 0
    assert "invalid --env" in result.stderr.lower()


def test_env_requires_a_value():
    result = _run(["--env"], cwd=REPO_ROOT)
    assert result.returncode != 0


def test_static_guards_no_env_clobber_and_sqlite_default():
    text = SCRIPT.read_text(encoding="utf-8")
    # No-clobber: only create .env when missing.
    assert 'if [ -f ".env" ]; then' in text
    assert "no clobber" in text.lower() or "untouched" in text.lower()
    # Zero-infra SQLite defaults are written for a fresh .env.
    assert "DB_ADAPTER=sqlite" in text
    assert "DB_PATH=valdo.db" in text
    # Migrations are run.
    assert "alembic upgrade head" in text
    # Local is the default env.
    assert 'ENV_TARGET="local"' in text


def test_required_dirs_are_created():
    text = SCRIPT.read_text(encoding="utf-8")
    for d in ("uploads", "logs", "reports", "mappings", "rules", "data"):
        assert d in text, f"setup must create the {d}/ directory"
