"""Contract tests for scripts/valdo-setup.sh (S10-3, #398; S11-2, #400).

These tests assert the single setup script honours its documented contract
without performing the (slow) full venv build or a live docker-compose run:

  * the deferred ``--env int`` seam exits 0 with a clear "deferred" message and
    does NOT touch the filesystem;
  * ``--env full-stack`` is IMPLEMENTED (S11-2): it no longer prints "deferred";
    its branch drives docker-compose with a Docker preflight, an ``up -d
    --build``, a wait-for-healthy poll, and a host smoke check (static guards
    on the script text, since a live compose run is verified out-of-band);
  * ``--help`` / ``-h`` print usage and exit 0;
  * an unknown flag and a bad ``--env`` value exit non-zero;
  * the script never clobbers an existing ``.env`` (static guard);
  * the script wires the zero-infra SQLite defaults (static guard);
  * the script resolves the repo root from its own location and uses
    ``set -euo pipefail`` (static guards on architecture principle #5 and
    safe-shell requirements).

The full venv + migration + ``valdo info`` local path and the live
docker-compose full-stack bring-up are verified manually in the implementation
runs (documented in CHANGELOG / the story write-ups); they are intentionally
NOT executed here to keep the unit suite fast and hermetic.
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


def test_env_full_stack_is_implemented_not_deferred():
    """``--env full-stack`` is wired to docker-compose (S11-2), not deferred.

    We assert on the script *text* rather than executing it: a live run would
    require the Docker daemon and would build/start containers, which is not
    appropriate for a fast, hermetic unit test. The full bring-up is verified
    live in the S11-2 implementation run.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    # The full-stack branch dispatches to the bring-up orchestrator, not a
    # "deferred" message.
    assert "run_fullstack" in text
    # It must NOT lump full-stack into the deferred seam any more: the old
    # shared deferred case label (``int|full-stack)`` at the start of a line)
    # is gone; "int" now stands alone as the only deferred env.
    import re

    assert not re.search(r"(?m)^\s*int\|full-stack\)", text)
    # Preflight: verifies docker + the daemon + a compose CLI.
    assert "fullstack_preflight" in text
    assert "docker info" in text  # daemon-running check
    # Drives compose up with build.
    assert "up -d --build" in text
    # Waits for the valdo service to be healthy and smoke-checks the endpoint.
    assert "fullstack_wait_healthy" in text
    assert "fullstack_smoke" in text
    assert "/api/v1/system/health" in text


def test_full_stack_supports_both_compose_v2_and_legacy():
    """Compose detection must support `docker compose` (v2) and `docker-compose`."""
    text = SCRIPT.read_text(encoding="utf-8")
    # v2 plugin probe, preferred first.
    assert "docker compose version" in text
    # legacy v1 binary fallback.
    assert "docker-compose" in text


def test_full_stack_documents_teardown():
    """The full-stack path offers/documents a teardown (--down, compose down)."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "--down" in text
    assert "fullstack_down" in text
    # Volume-dropping teardown variant.
    assert "down -v" in text


def test_env_int_is_implemented_message_lists_only_int_as_deferred():
    """The int deferred message must no longer call full-stack deferred."""
    result = _run(["--env", "int"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    out = result.stdout.lower()
    assert "deferred" in out
    # full-stack is now implemented; the int message advertises it, but must
    # not describe full-stack itself as deferred.
    assert "full-stack" not in out or "implemented" in out


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
