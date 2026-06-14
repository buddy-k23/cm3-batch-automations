"""Create APP_BASELINES table for suite baseline persistence.

Stores the latest rolled-up baseline record for each test suite so that
historical quality comparisons survive application restarts and can be queried
from Oracle/PostgreSQL/SQLite without reading the JSON file.

The table uses ``suite_name`` as the primary key (one row per suite), and
``payload`` as a CLOB/TEXT column containing the full baseline JSON blob.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "0003"
down_revision = "0002"
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
    """Create APP_BASELINES table if it does not already exist.

    Idempotent: the creation is skipped when the table is already present,
    so this migration is safe to apply against environments where the table
    was created manually.
    """
    if not _table_exists("APP_BASELINES"):
        op.create_table(
            "APP_BASELINES",
            sa.Column("suite_name", sa.String(255), nullable=False),
            sa.Column("recorded_at", sa.DateTime, nullable=False),
            sa.Column("payload", sa.Text, nullable=False),
            sa.PrimaryKeyConstraint("suite_name"),
        )


def downgrade() -> None:
    """Drop APP_BASELINES table if it exists."""
    if _table_exists("APP_BASELINES"):
        op.drop_table("APP_BASELINES")
