"""Count flagged and unacknowledged crisis items each retention run removes.

Since plan 14.10, pending and flagged confessions follow RETENTION_HOURS like the
rest. A flagged item can therefore leave before anyone reviewed it; the run log
records how many did, and how many were crisis items nobody had acknowledged.

Revision ID: a1b2c3d4e5f6
Revises: f7d1a8b4c029
Create Date: 2026-10-01 21:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "f7d1a8b4c029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add both counters; runs recorded before this removed no flagged items, so 0."""
    op.add_column(
        "retention_runs",
        sa.Column(
            "flagged_removed",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Flagged confessions removed at retention, reviewed or not (plan 14.10)",
        ),
    )
    op.add_column(
        "retention_runs",
        sa.Column(
            "unacknowledged_crisis_removed",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Crisis items removed before any staff member acknowledged them",
        ),
    )


def downgrade() -> None:
    """Drop both counters."""
    op.drop_column("retention_runs", "unacknowledged_crisis_removed")
    op.drop_column("retention_runs", "flagged_removed")
