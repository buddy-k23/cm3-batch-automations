"""Contract tests for scripts/valdo-setup.sh (S10-3, #398; S11-2, #400; S11-3, #401).

These tests assert the single setup script honours its documented contract
without performing the (slow) full venv build or a live docker-compose run:

  * ``--env int`` is IMPLEMENTED (S11-3): it is no longer "deferred". It
    scaffolds ``.env.int`` from ``.env.int.example`` (never clobbering),
    validates the required INT vars are set to non-placeholder values
    (reporting any missing precisely and exiting non-zero), and only when all
    are set AND the Oracle DSN is reachable runs migrations + a smoke. Verified
    here both as static guards on the script text and a live run that asserts
    the placeholder-template path reports missing vars and exits non-zero;
  * ``--env full-stack`` is IMPLEMENTED (S11-2): it no longer prints "deferred";
    its branch drives docker-compose with a Docker preflight, an ``up -d
    --build``, a wait-for-healthy poll, and a host smoke check (static guards
    on the script text, since a live compose run is verified out-of-band);
  * ``--help`` / ``-h`` print usage and exit 0;
  * an unknown flag and a bad ``--env`` value exit non-zero;
  * the script never clobbers an existing ``.env`` / ``.env.int`` (static guards);
  * the script wires the zero-infra SQLite defaults (static guard);
  * the script resolves the repo root from its own location and uses
    ``set -euo pipefail`` (static guards on architecture principle #5 and
    safe-shell requirements);
  * the committed ``.env.int.example`` carries placeholders only — no real
    secret / high-entropy value (Sprint 11 HIGH-impact risk).

The full venv + migration + ``valdo info`` local path, the live docker-compose
full-stack bring-up, and the reachable-Oracle migrate/smoke INT path are
verified manually in the implementation runs (documented in CHANGELOG / the
story write-ups); they are intentionally NOT executed here to keep the unit
suite fast and hermetic.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "valdo-setup.sh"
INT_ENV_EXAMPLE = REPO_ROOT / ".env.int.example"

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


def test_env_int_scaffolds_and_reports_placeholders(tmp_path):
    """``--env int`` from the placeholder template reports missing vars + exits non-zero.

    The script resolves PROJECT_ROOT from its own location and operates on the
    repo-root ``.env.int``, so we back up / restore any real ``.env.int`` the
    developer may have, and remove it first so the run scaffolds a fresh one
    from the committed placeholder template.
    """
    int_env = REPO_ROOT / ".env.int"
    backup = tmp_path / "env.int.backup"
    had_existing = int_env.exists()
    if had_existing:
        shutil.copy2(int_env, backup)
    try:
        int_env.unlink(missing_ok=True)
        result = _run(["--env", "int"], cwd=REPO_ROOT)
        # Placeholder template => required vars unset => non-zero exit.
        assert result.returncode != 0, result.stdout + result.stderr
        out = result.stdout + result.stderr
        # The scaffold step created .env.int from the example.
        assert ".env.int" in out
        assert int_env.exists(), "the run must scaffold a fresh .env.int"
        # The placeholder Oracle creds + signing keys are reported as missing.
        for var in (
            "ORACLE_USER",
            "ORACLE_PASSWORD",
            "ORACLE_DSN",
            "VALDO_MCP_TOKEN_SIGNING_KEY",
            "VALDO_SESSION_SIGNING_KEY",
        ):
            assert var in out, f"{var} must be reported as still-placeholder"
        assert "complete .env.int" in out.lower()
        # It is no longer the old "deferred" seam.
        assert "deferred" not in result.stdout.lower()
    finally:
        int_env.unlink(missing_ok=True)
        if had_existing:
            shutil.move(str(backup), str(int_env))


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


def test_env_int_is_implemented_not_deferred():
    """``--env int`` is wired to the INT scaffold+validate flow, not deferred.

    Static guards on the script text: the int branch dispatches to ``run_int``
    (the scaffold/validate/reachability orchestrator), scaffolds ``.env.int``
    from ``.env.int.example`` without clobbering, validates required vars, and
    does a bounded reachability check before any migrate. No env value is
    "deferred" any more.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    assert "run_int" in text
    assert ".env.int.example" in text
    assert "int_validate_required" in text
    assert "int_dsn_reachable" in text
    assert "alembic upgrade head" in text
    # No-clobber on the INT env file.
    assert 'if [ -f "$INT_ENV_FILE" ]; then' in text
    # The old standalone "deferred" int echo is gone.
    assert "setup is deferred to a future sprint" not in text


def test_no_env_value_is_deferred_in_help():
    """The help text no longer marks any --env value as deferred."""
    result = _run(["--help"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    assert "deferred" not in result.stdout.lower()


def test_env_equals_form_is_accepted(tmp_path):
    """``--env=int`` (equals form) reaches the INT flow (scaffolds .env.int)."""
    int_env = REPO_ROOT / ".env.int"
    backup = tmp_path / "env.int.backup"
    had_existing = int_env.exists()
    if had_existing:
        shutil.copy2(int_env, backup)
    try:
        int_env.unlink(missing_ok=True)
        result = _run(["--env=int"], cwd=REPO_ROOT)
        # Placeholder template => non-zero, and .env.int was scaffolded.
        assert result.returncode != 0, result.stdout + result.stderr
        assert int_env.exists()
    finally:
        int_env.unlink(missing_ok=True)
        if had_existing:
            shutil.move(str(backup), str(int_env))


def test_env_int_example_committed():
    """The committed INT env template exists.

    S16-4 (#424): the former ``config/int.json`` assertions were removed along
    with the file itself — ``config/<env>.json`` was a dead, no-runtime-effect
    config layer (operator trap).  The LIVE INT configuration mechanism is the
    ``.env.int`` env-var file scaffolded from this committed example template.
    """
    assert INT_ENV_EXAMPLE.is_file(), ".env.int.example must be committed"


def test_env_int_example_has_no_real_secrets():
    """Sprint 11 HIGH-impact risk: the committed example is placeholders only.

    Every line that assigns a secret-ish variable must carry a placeholder
    marker (``<...>`` / ``SET_ME``) rather than a real high-entropy value.
    """
    text = INT_ENV_EXAMPLE.read_text(encoding="utf-8")
    secret_keys = (
        "ORACLE_PASSWORD",
        "VALDO_SESSION_SIGNING_KEY",
        "VALDO_MCP_TOKEN_SIGNING_KEY",
        "VAULT_SECRET_ID",
    )
    placeholder = re.compile(r"<[^>]+>|SET_ME", re.IGNORECASE)
    for line in text.splitlines():
        stripped = line.lstrip("# ").rstrip()
        for key in secret_keys:
            if stripped.startswith(f"{key}="):
                value = stripped.split("=", 1)[1]
                assert placeholder.search(value), (
                    f"{key} in .env.int.example must be a placeholder, "
                    f"got {value!r}"
                )
                # Defensive: no long hex/base64-ish blob (a real key shape).
                assert not re.search(r"[0-9a-fA-F]{32,}", value), (
                    f"{key} appears to contain a real high-entropy value"
                )


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
