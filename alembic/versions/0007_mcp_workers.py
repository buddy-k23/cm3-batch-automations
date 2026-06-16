"""Create MCP_WORKERS worker-liveness table (S10-2, #397).

The S10-2 async cutover (ADR 0021) flips ``VALDO_MCP_ASYNC_VALIDATE`` ON by
default. To avoid stranding validations when no worker is running,
``validate_file`` consults "is ANY worker draining the queue?" before taking
the fast enqueue path — falling back to a synchronous inline run when not.

That liveness signal lives in a **dedicated** ``MCP_WORKERS`` table rather than
a sentinel row in ``APP_MCP_RUN_REGISTRY``: the run registry is a SOX-audited
table of *runs*, and overloading it with non-run rows would pollute the audit
trail. A separate table also enables future worker observability (how many
workers, on which hosts).

Table shape
-----------
- ``worker_id`` (PK, ``String(255)``) — stable per-process id (e.g.
  ``"<host>:<pid>"``). The worker upserts this row on start and on every poll
  iteration.
- ``host`` (``String(255)``, nullable) — hostname for observability.
- ``started_at`` (``DateTime``) — when the worker process started.
- ``last_heartbeat_at`` (``DateTime``) — refreshed on register; the freshness
  test ``last_heartbeat_at >= now - within_seconds`` answers "is this worker
  live?".

Cross-dialect design decisions
------------------------------
- Dialect-agnostic SQLAlchemy generics (``String``, ``DateTime``) only, same
  approach as migrations 0003/0004/0005/0006 — Oracle/PostgreSQL/SQLite each
  render natively.
- Idempotent ``upgrade``: skipped when the table already exists, mirroring
  0004's table-exists guard, so it is safe to re-run against a rehearsal
  environment where the table was created manually.
- ``downgrade`` is a clean guarded ``DROP TABLE``.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_TABLE = "MCP_WORKERS"


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
    """Create MCP_WORKERS.

    Idempotent: skipped when the table already exists, so safe to apply
    against an environment where the table was created manually for a
    rehearsal.
    """
    if _table_exists(_TABLE):
        return

    op.create_table(
        _TABLE,
        sa.Column("worker_id", sa.String(255), primary_key=True),
        sa.Column("host", sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    """Drop MCP_WORKERS (guarded)."""
    if not _table_exists(_TABLE):
        return
    op.drop_table(_TABLE)
