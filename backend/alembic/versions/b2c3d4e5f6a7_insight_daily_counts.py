"""Add count-only per-day Insights rollups.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-01 22:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create ``insight_daily_counts`` (plan 14.12)."""
    op.create_table(
        "insight_daily_counts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "day",
            sa.Date(),
            nullable=False,
            comment="UTC date the confessions were made",
        ),
        sa.Column(
            "dimension",
            sa.String(length=32),
            nullable=False,
            comment="total, category, sentiment, department, status, delivery or delivery_hours",
        ),
        sa.Column(
            "label",
            sa.String(length=128),
            nullable=False,
            comment="The value counted, e.g. a category name",
        ),
        sa.Column(
            "count",
            sa.Integer(),
            nullable=False,
            comment="Confessions folded in so far; never decreases",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "day", "dimension", "label", name="uq_insight_daily_counts"
        ),
    )
    op.create_index(
        "ix_insight_daily_counts_id", "insight_daily_counts", ["id"], unique=False
    )


def downgrade() -> None:
    """Drop ``insight_daily_counts``."""
    op.drop_index("ix_insight_daily_counts_id", table_name="insight_daily_counts")
    op.drop_table("insight_daily_counts")
