"""Add CM3_BASELINES table for per-suite quality metric baselines.

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-31

The CM3_BASELINES table stores one row per suite containing the rolling-window
averaged metrics computed by :mod:`src.services.baseline_service`.  The primary
key is ``suite_name`` so every UPSERT replaces the existing row for a suite.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# ---------------------------------------------------------------------------
# Alembic revision metadata
# ---------------------------------------------------------------------------

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Migration steps
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Create the CM3_BASELINES table."""
    op.create_table(
        "CM3_BASELINES",
        sa.Column("suite_name", sa.String(200), primary_key=True),
        sa.Column("pass_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("avg_quality_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("avg_error_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "sample_size",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    """Drop the CM3_BASELINES table."""
    op.drop_table("CM3_BASELINES")
