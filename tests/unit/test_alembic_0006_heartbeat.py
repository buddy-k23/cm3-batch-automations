"""Tests for Alembic migration 0006 — heartbeat + attempt_count (S10-1, #397).

Migration ``0006_mcp_run_heartbeat_attempt`` adds two columns to
``APP_MCP_RUN_REGISTRY``:

* ``last_heartbeat_at`` (DateTime, nullable) — refreshed by the worker
  while a run is in flight; the stuck-run reaper uses it to distinguish a
  live long validation from a dead worker.
* ``attempt_count`` (Integer, nullable, server_default ``'0'``) —
  incremented each time the reaper reclaims a stuck row back to queued.

Testing strategy
----------------
The local ``alembic/`` directory at project root shadows the (un-installed)
``alembic`` package on ``sys.path``, so ``from alembic import op`` cannot be
resolved inside a migration file when exec'd from a unit test — and Alembic
itself is not installed as an importable runtime in the unit venv. This is
the same constraint documented in :mod:`tests.unit.test_migration_0001`, so
we use the same two safe strategies:

1. **AST inspection** — parse the migration source with :mod:`ast` and
   assert structure (revision chain, function defs, the two added columns,
   the idempotency guard, the cross-dialect ``batch_alter_table`` usage).
2. **Source-level string checks** — quick textual sanity checks.

To still prove the *resulting* schema is what the migration intends —
"apply against SQLite, columns exist, existing rows get defaults" — we
replicate the migration's add-column DDL against a real SQLite database and
assert the post-state. This mirrors the column shape declared in the
migration source (kept in lock-step by the AST assertions above), giving an
executable schema proof without importing the un-resolvable Alembic runtime.

The full live ``alembic upgrade`` is exercised in the integration suite
against Oracle/PostgreSQL, where the real Alembic runtime is available.
"""

from __future__ import annotations

import ast
import os
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

MIGRATION_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "../../alembic/versions/0006_mcp_run_heartbeat_attempt.py",
    )
)


def _read_source() -> str:
    """Return the raw source text of the migration file."""
    with open(MIGRATION_PATH) as fh:
        return fh.read()


def _parse_source() -> ast.Module:
    """Parse the migration source into an AST module node."""
    return ast.parse(_read_source())


def _function_names(tree: ast.Module) -> set:
    """Return the set of top-level function names defined in the module."""
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def _added_column_names(tree: ast.Module) -> set:
    """Collect column-name literals from every ``add_column``/``Column`` call.

    Walks the AST for ``op.add_column``, ``batch_op.add_column`` and
    ``sa.Column(...)`` calls and returns the set of first-arg string
    literals — the column names the migration introduces.
    """
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in ("add_column", "Column"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Call):  # add_column(sa.Column('name', ...))
                inner = arg
                if (
                    isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "Column"
                    and inner.args
                    and isinstance(inner.args[0], ast.Constant)
                ):
                    names.add(inner.args[0].value)
            elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.add(arg.value)
    return names


# ---------------------------------------------------------------------------
# File + revision metadata
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    """The 0006 migration file must be present."""
    assert os.path.isfile(MIGRATION_PATH), f"Migration not found at {MIGRATION_PATH}"


def test_revision_chain():
    """0006 follows 0005."""
    source = _read_source()
    assert 'revision = "0006"' in source
    assert 'down_revision = "0005"' in source
    assert "branch_labels = None" in source
    assert "depends_on = None" in source


def test_upgrade_and_downgrade_defined():
    """Both upgrade() and downgrade() must be defined."""
    names = _function_names(_parse_source())
    assert "upgrade" in names
    assert "downgrade" in names


# ---------------------------------------------------------------------------
# Column structure + cross-dialect + idempotency (AST / source)
# ---------------------------------------------------------------------------


def test_adds_both_new_columns():
    """The migration introduces last_heartbeat_at and attempt_count."""
    added = _added_column_names(_parse_source())
    assert "last_heartbeat_at" in added, f"got {added!r}"
    assert "attempt_count" in added, f"got {added!r}"


def test_uses_dialect_agnostic_types_and_default():
    """Generic SA types + a server_default of '0' for attempt_count."""
    source = _read_source()
    doc_end = source.find('"""', 3) + 3
    code = source[doc_end:]
    assert "sa.DateTime" in code
    assert "sa.Integer" in code
    assert "server_default" in code
    for oracle_type in ("VARCHAR2", "NUMBER(", "TIMESTAMP WITH TIME ZONE"):
        assert oracle_type not in code, f"Oracle-specific type {oracle_type!r} in code"


def test_uses_batch_alter_table_for_sqlite():
    """Cross-dialect ALTER must go through batch_alter_table (SQLite safe)."""
    assert "batch_alter_table" in _read_source()


def test_is_idempotent_guarded():
    """Upgrade must skip a column that already exists (idempotent)."""
    source = _read_source()
    # A helper that checks existing columns guards the add.
    assert "_column_exists" in source or "get_columns" in source


# ---------------------------------------------------------------------------
# Executable schema proof: replicate the add-column DDL against SQLite
# ---------------------------------------------------------------------------


def _create_0005_shaped_table(engine) -> None:
    """Create APP_MCP_RUN_REGISTRY in its post-0005 shape (no new columns)."""
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE APP_MCP_RUN_REGISTRY ("
            "run_id TEXT PRIMARY KEY, source TEXT NOT NULL, "
            "file_path TEXT NOT NULL, status TEXT NOT NULL, "
            "started_at TIMESTAMP NOT NULL, finished_at TIMESTAMP, "
            "violation_count INTEGER, payload TEXT NOT NULL, "
            "created_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(text(
            "INSERT INTO APP_MCP_RUN_REGISTRY "
            "(run_id, source, file_path, status, started_at, payload) VALUES "
            "('pre-existing', 'S', '/tmp/a', 'running', "
            " '2026-06-15 10:00:00', '{}')"
        ))


def _apply_0006_ddl(engine) -> None:
    """Apply the column additions the 0006 migration declares.

    Equivalent to the migration's ``batch_op.add_column`` calls on SQLite —
    kept in lock-step with the migration source by the AST assertions above.
    """
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE APP_MCP_RUN_REGISTRY ADD COLUMN last_heartbeat_at TIMESTAMP"
        ))
        conn.execute(text(
            "ALTER TABLE APP_MCP_RUN_REGISTRY ADD COLUMN attempt_count INTEGER DEFAULT 0"
        ))


def test_resulting_schema_has_new_columns_and_defaults():
    """After applying the 0006 DDL, both columns exist and rows get defaults."""
    tmpdir = tempfile.mkdtemp(prefix="valdo_mig_0006_")
    db_path = Path(tmpdir) / "test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        _create_0005_shaped_table(engine)
        _apply_0006_ddl(engine)

        cols = {c["name"] for c in inspect(engine).get_columns("APP_MCP_RUN_REGISTRY")}
        assert "last_heartbeat_at" in cols
        assert "attempt_count" in cols

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT attempt_count, last_heartbeat_at FROM APP_MCP_RUN_REGISTRY "
                "WHERE run_id = 'pre-existing'"
            )).fetchone()
        assert row[0] == 0
        assert row[1] is None
    finally:
        engine.dispose()
        try:
            db_path.unlink()
        except OSError:
            pass
