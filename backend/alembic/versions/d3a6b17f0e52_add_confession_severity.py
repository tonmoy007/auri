"""Split moderation into a severity and add crisis acknowledgement.

Revision ID: d3a6b17f0e52
Revises: b8f2c05d4a91
Create Date: 2026-09-04 03:05:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "d3a6b17f0e52"
down_revision = "b8f2c05d4a91"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``severity`` plus the crisis-acknowledgement columns.

    Existing rows get ``none``: they predate the severity split, and
    claiming a severity nobody classified would be worse than admitting
    the classification is absent. Flagged rows keep their flagged status
    and are still reviewed — only the crisis *ranking* is unavailable for
    history, which is accurate.
    """
    op.add_column(
        "confessions",
        sa.Column(
            "severity",
            sa.String(length=16),
            server_default=sa.text("'none'"),
            nullable=False,
        ),
    )
    op.add_column(
        "confessions",
        sa.Column(
            "acknowledged_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "confessions",
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_confessions_severity", "confessions", ["severity"])


def downgrade() -> None:
    """Drop the severity and acknowledgement columns."""
    op.drop_index("ix_confessions_severity", table_name="confessions")
    op.drop_column("confessions", "acknowledged_at")
    op.drop_column("confessions", "acknowledged_by")
    op.drop_column("confessions", "severity")
