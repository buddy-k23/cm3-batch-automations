"""Add heartbeat + attempt_count to APP_MCP_RUN_REGISTRY (S10-1, #397).

The S10-1 background-worker runtime (ADR 0021) needs two new columns on the
MCP run registry so the stuck-run reaper can tell a live long validation
apart from a worker that died mid-flight:

- ``last_heartbeat_at`` (``DateTime``, nullable) — refreshed by the worker
  (at claim time and periodically while the run executes, via a background
  heartbeat thread). The reaper reclaims a ``running`` row only when this is
  older than a threshold (or NULL and ``started_at`` is old).
- ``attempt_count`` (``Integer``, nullable, server_default ``'0'``) —
  incremented each time the reaper reclaims a stuck row back to ``queued``,
  so a poison job can eventually be capped (future work).

Cross-dialect design decisions
-------------------------------
- Dialect-agnostic SQLAlchemy generics (``DateTime``, ``Integer``), same
  approach as migrations 0003/0004/0005. Oracle/PostgreSQL render an
  ``ALTER TABLE ... ADD`` natively; SQLite needs the changes wrapped in
  ``batch_alter_table`` (Alembic's table-rebuild shim) because SQLite's
  ``ALTER TABLE ADD COLUMN`` cannot add a column with certain constraints
  in one statement. ``batch_alter_table`` is a no-op wrapper on the other
  dialects, so the single code path is correct everywhere.
- Idempotent ``upgrade``: each column is added only when absent, so the
  migration is safe to re-run against an environment where the columns were
  created manually for a rehearsal (mirrors 0004/0005's table-exists guard,
  applied at column granularity here).
- ``downgrade`` drops both columns (also guarded + batched).

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_TABLE = "APP_MCP_RUN_REGISTRY"


def _table_exists(table_name: str) -> bool:
    """Return True if the named table exists in the current schema.

    Args:
        table_name: The table name to look up (case-insensitive match).

    Returns:
        True if the table exists, False otherwise.
    """
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    return table_name.upper() in [t.upper() for t in inspector.get_table_names()]


def _column_exists(table_name: str, column_name: str) -> bool:
    """Return True if *column_name* exists on *table_name*.

    Args:
        table_name: Table to inspect (case-insensitive match).
        column_name: Column to look for (case-insensitive match).

    Returns:
        True if the column exists, False otherwise.
    """
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    try:
        cols = [c["name"].upper() for c in inspector.get_columns(table_name)]
    except Exception:  # noqa: BLE001 — missing table -> column cannot exist.
        return False
    return column_name.upper() in cols


def upgrade() -> None:
    """Add last_heartbeat_at + attempt_count to APP_MCP_RUN_REGISTRY.

    Idempotent at column granularity and cross-dialect via
    ``batch_alter_table`` (SQLite-safe; a no-op wrapper elsewhere).
    """
    if not _table_exists(_TABLE):
        return

    add_heartbeat = not _column_exists(_TABLE, "last_heartbeat_at")
    add_attempt = not _column_exists(_TABLE, "attempt_count")
    if not (add_heartbeat or add_attempt):
        return

    with op.batch_alter_table(_TABLE) as batch_op:
        if add_heartbeat:
            batch_op.add_column(
                sa.Column("last_heartbeat_at", sa.DateTime, nullable=True)
            )
        if add_attempt:
            batch_op.add_column(
                sa.Column(
                    "attempt_count",
                    sa.Integer,
                    nullable=True,
                    server_default=sa.text("0"),
                )
            )


def downgrade() -> None:
    """Drop last_heartbeat_at + attempt_count (guarded + batched)."""
    if not _table_exists(_TABLE):
        return

    drop_heartbeat = _column_exists(_TABLE, "last_heartbeat_at")
    drop_attempt = _column_exists(_TABLE, "attempt_count")
    if not (drop_heartbeat or drop_attempt):
        return

    with op.batch_alter_table(_TABLE) as batch_op:
        if drop_attempt:
            batch_op.drop_column("attempt_count")
        if drop_heartbeat:
            batch_op.drop_column("last_heartbeat_at")
