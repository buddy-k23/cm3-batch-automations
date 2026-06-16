"""Create APP_MCP_RUN_REGISTRY table for MCP run-state persistence (S6-1, #386).

EF-S4 tracks MCP validation runs in a process-local Python dict in
``src/mcp/action_tools.py``. The dict is per-worker, in-memory only, and
lost on FastAPI/gunicorn restart, worker rotation, or graceful reload.
S6-1 replaces that dict with a database-backed registry so a BA who
started a long ``validate_file`` can still poll ``get_run_status`` after
the operator restarts the server.

Table shape
-----------
- ``run_id`` (PK, ``String(64)``) — UUID4 hex string. Wider than the 36
  in ``APP_RUN_HISTORY`` because MCP runs use uuid4().hex (no dashes,
  32 chars today) and we want headroom for any future tweak.
- ``source`` (``String(100)``) — Canonical source name (e.g. ``SHAW``).
  Duplicated outside the payload so the idempotency-lookup query
  (``find_inflight``) can filter without parsing JSON.
- ``file_path`` (``String(1000)``) — File path as supplied by the
  caller. Same rationale.
- ``status`` (``String(20)``) — One of ``queued`` / ``running`` /
  ``completed`` / ``failed``. Indexed so the in-flight lookup can use
  the ``status NOT IN ('completed','failed')`` predicate efficiently.
- ``started_at`` (``DateTime``) — Run start, ISO-8601 UTC in the app
  layer, native datetime in the column.
- ``finished_at`` (``DateTime``, nullable) — Set when status reaches a
  terminal value.
- ``violation_count`` (``Integer``, nullable) — Set when status reaches
  a terminal value. Nullable rather than zero-defaulted so the
  in-flight state is unambiguous.
- ``payload`` (``Text``) — Full JSON of the run record (including the
  flattened violations list). Read whole on every ``get`` because the
  MCP layer's record-of-truth is the in-memory dataclass.
- ``created_ts`` (``DateTime``) — DB insert timestamp; useful for
  diagnostics + future expiry sweep job.

Cross-dialect design decisions
------------------------------
- All types are dialect-agnostic SQLAlchemy generics: ``String``,
  ``Integer``, ``DateTime``, ``Text``. Oracle renders ``Text`` as
  ``CLOB``; PostgreSQL as ``TEXT``; SQLite as ``TEXT``. Same approach
  as ``APP_BASELINES`` (migration 0003).
- One composite index on ``(source, file_path, status)`` for the
  ``find_inflight`` lookup.
- One index on ``status`` to cover any future "list runs in flight"
  diagnostic query.
- The migration is idempotent — ``upgrade`` skips creation when the
  table already exists (mirrors 0003). Downgrade is a clean
  ``DROP TABLE``.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


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


def upgrade() -> None:
    """Create APP_MCP_RUN_REGISTRY table and its supporting indexes.

    Idempotent: skipped when the table already exists, so safe to apply
    against environments where the table was created manually for a
    rehearsal.
    """
    if _table_exists("APP_MCP_RUN_REGISTRY"):
        return

    op.create_table(
        "APP_MCP_RUN_REGISTRY",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("file_path", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("violation_count", sa.Integer, nullable=True),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column(
            "created_ts",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "IDX_MCP_RUN_INFLIGHT",
        "APP_MCP_RUN_REGISTRY",
        ["source", "file_path", "status"],
    )
    op.create_index(
        "IDX_MCP_RUN_STATUS",
        "APP_MCP_RUN_REGISTRY",
        ["status"],
    )


def downgrade() -> None:
    """Drop APP_MCP_RUN_REGISTRY and its indexes."""
    if not _table_exists("APP_MCP_RUN_REGISTRY"):
        return
    op.drop_index("IDX_MCP_RUN_STATUS", table_name="APP_MCP_RUN_REGISTRY")
    op.drop_index("IDX_MCP_RUN_INFLIGHT", table_name="APP_MCP_RUN_REGISTRY")
    op.drop_table("APP_MCP_RUN_REGISTRY")
