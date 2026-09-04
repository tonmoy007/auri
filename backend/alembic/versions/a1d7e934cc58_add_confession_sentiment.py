"""Add confessions.sentiment for aggregate tone reporting.

Revision ID: a1d7e934cc58
Revises: f5c3a8e21b40
Create Date: 2026-09-04 01:55:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1d7e934cc58"
down_revision = "f5c3a8e21b40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable ``sentiment`` column."""
    op.add_column(
        "confessions", sa.Column("sentiment", sa.String(length=16), nullable=True)
    )
    op.create_index("ix_confessions_sentiment", "confessions", ["sentiment"])


def downgrade() -> None:
    """Drop the ``sentiment`` column."""
    op.drop_index("ix_confessions_sentiment", table_name="confessions")
    op.drop_column("confessions", "sentiment")
