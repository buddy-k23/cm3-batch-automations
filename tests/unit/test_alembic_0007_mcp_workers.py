"""Tests for Alembic migration 0007 — MCP_WORKERS liveness table (S10-2, #397).

Migration ``0007_mcp_workers`` creates a dedicated worker-liveness table so
``validate_file`` can ask "is ANY worker draining the queue?" before choosing
the fast enqueue path vs the synchronous inline fallback. A dedicated table
(NOT a sentinel row in the SOX-audited run registry) keeps the run-registry
clean and enables future worker observability (count / which-host).

``MCP_WORKERS`` columns:

* ``worker_id`` (PK, String) — stable per-process id (pid+hostname).
* ``host`` (String, nullable) — hostname for observability.
* ``started_at`` (DateTime) — when the worker process started.
* ``last_heartbeat_at`` (DateTime) — refreshed on start + each loop iteration.

Testing strategy
----------------
The local ``alembic/`` directory at project root shadows the (un-installed)
``alembic`` package on ``sys.path``, and Alembic itself is not importable in
the unit venv (same constraint as :mod:`tests.unit.test_alembic_0006_heartbeat`
and :mod:`tests.unit.test_migration_0001`). So we use the same two strategies:

1. **AST inspection** — parse the migration source and assert structure
   (revision chain, function defs, the created table + columns, idempotency
   guard).
2. **Executable schema proof** — replicate the migration's CREATE TABLE DDL
   against a real SQLite database and assert the resulting columns exist and a
   row round-trips. Kept in lock-step with the migration source by the AST
   assertions above.

The full live ``alembic upgrade`` is exercised in the integration suite
against Oracle/PostgreSQL where the real Alembic runtime is available.
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
        "../../alembic/versions/0007_mcp_workers.py",
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


def _column_names(tree: ast.Module) -> set:
    """Collect column-name literals from every ``sa.Column(...)`` call."""
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "Column"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            names.add(node.args[0].value)
    return names


# ---------------------------------------------------------------------------
# File + revision metadata
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    """The 0007 migration file must be present."""
    assert os.path.isfile(MIGRATION_PATH), f"Migration not found at {MIGRATION_PATH}"


def test_revision_chain():
    """0007 follows 0006."""
    source = _read_source()
    assert 'revision = "0007"' in source
    assert 'down_revision = "0006"' in source
    assert "branch_labels = None" in source
    assert "depends_on = None" in source


def test_upgrade_and_downgrade_defined():
    """Both upgrade() and downgrade() must be defined."""
    names = _function_names(_parse_source())
    assert "upgrade" in names
    assert "downgrade" in names


# ---------------------------------------------------------------------------
# Table structure + cross-dialect + idempotency (AST / source)
# ---------------------------------------------------------------------------


def test_creates_mcp_workers_table():
    """The migration creates the MCP_WORKERS table."""
    source = _read_source()
    assert "MCP_WORKERS" in source
    assert "create_table" in source


def test_table_has_expected_columns():
    """MCP_WORKERS declares worker_id, host, started_at, last_heartbeat_at."""
    cols = _column_names(_parse_source())
    for expected in ("worker_id", "host", "started_at", "last_heartbeat_at"):
        assert expected in cols, f"missing {expected!r}; got {cols!r}"


def test_uses_dialect_agnostic_types():
    """Generic SA types only — no Oracle-specific identifiers in the code."""
    source = _read_source()
    doc_end = source.find('"""', 3) + 3
    code = source[doc_end:]
    assert "sa.String" in code
    assert "sa.DateTime" in code
    for oracle_type in ("VARCHAR2", "NUMBER(", "TIMESTAMP WITH TIME ZONE"):
        assert oracle_type not in code, f"Oracle-specific type {oracle_type!r} in code"


def test_is_idempotent_guarded():
    """Upgrade must skip creation when the table already exists (idempotent)."""
    source = _read_source()
    assert "_table_exists" in source or "get_table_names" in source


# ---------------------------------------------------------------------------
# Executable schema proof: replicate the CREATE TABLE DDL against SQLite
# ---------------------------------------------------------------------------


def _apply_0007_ddl(engine) -> None:
    """Apply the table creation the 0007 migration declares.

    Equivalent to the migration's ``op.create_table`` on SQLite — kept in
    lock-step with the migration source by the AST assertions above.
    """
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE MCP_WORKERS ("
            "worker_id TEXT PRIMARY KEY, "
            "host TEXT, "
            "started_at TIMESTAMP NOT NULL, "
            "last_heartbeat_at TIMESTAMP NOT NULL)"
        ))


def test_resulting_schema_has_expected_columns_and_roundtrips():
    """After applying the 0007 DDL the table exists with the four columns."""
    tmpdir = tempfile.mkdtemp(prefix="valdo_mig_0007_")
    db_path = Path(tmpdir) / "test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        _apply_0007_ddl(engine)

        cols = {c["name"] for c in inspect(engine).get_columns("MCP_WORKERS")}
        assert {"worker_id", "host", "started_at", "last_heartbeat_at"} <= cols

        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO MCP_WORKERS "
                "(worker_id, host, started_at, last_heartbeat_at) VALUES "
                "('w1', 'box-a', '2026-06-16 10:00:00', '2026-06-16 10:00:00')"
            ))
            row = conn.execute(text(
                "SELECT worker_id, host FROM MCP_WORKERS WHERE worker_id = 'w1'"
            )).fetchone()
        assert row[0] == "w1"
        assert row[1] == "box-a"
    finally:
        engine.dispose()
        try:
            db_path.unlink()
        except OSError:
            pass
