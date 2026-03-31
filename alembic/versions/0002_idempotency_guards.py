"""Idempotency guards — handle pre-existing CM3_RUN_HISTORY and CM3_RUN_TESTS.

This migration adds columns that were introduced after the original manual DDL
at ``sql/cm3int/setup_cm3_run_history.sql``.  Production instances that ran
the original script are missing these columns; fresh installs from migration
0001 already have them.

The following columns are present in migration 0001 but NOT in the original
manual DDL, meaning old installs may lack them:

- ``CM3_RUN_HISTORY.quality_score``   — overall quality percentage for the run
- ``CM3_RUN_HISTORY.run_duration_seconds`` — wall-clock seconds for the run

Each addition is wrapped in an existence check so the migration is safe to
apply against both fresh and legacy installations.

Downgrade is intentionally a no-op: we cannot safely remove these columns
because they may contain live data or may have been present before this
migration ran.

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "0002"
down_revision = "0001"
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


def _column_exists(table_name: str, column_name: str) -> bool:
    """Return True if the named column exists on the given table.

    Args:
        table_name: The table to inspect.
        column_name: The column name to look up (case-insensitive match).

    Returns:
        True if the column is present, False otherwise.
    """
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    cols = [c["name"].upper() for c in inspector.get_columns(table_name)]
    return column_name.upper() in cols


def upgrade() -> None:
    """Add new columns to CM3_RUN_HISTORY for installs missing them.

    Idempotent: each op.add_column is only executed when the table exists
    but the column is absent, so applying this migration multiple times is
    harmless.
    """
    # quality_score — overall quality percentage for the run (e.g. 98.50).
    # Present in migration 0001 but absent from the original manual DDL.
    if _table_exists("CM3_RUN_HISTORY") and not _column_exists(
        "CM3_RUN_HISTORY", "quality_score"
    ):
        op.add_column(
            "CM3_RUN_HISTORY",
            sa.Column("quality_score", sa.Numeric(5, 2), nullable=True),
        )

    # run_duration_seconds — total wall-clock seconds for the run.
    # Present in migration 0001 but absent from the original manual DDL.
    if _table_exists("CM3_RUN_HISTORY") and not _column_exists(
        "CM3_RUN_HISTORY", "run_duration_seconds"
    ):
        op.add_column(
            "CM3_RUN_HISTORY",
            sa.Column("run_duration_seconds", sa.Numeric(10, 3), nullable=True),
        )


def downgrade() -> None:
    """No-op — do not remove columns that may contain live data.

    Columns added by this migration could have been present before the
    migration ran (on an instance that was manually patched).  Dropping
    them in a downgrade would be both surprising and potentially destructive.
    """
    pass
