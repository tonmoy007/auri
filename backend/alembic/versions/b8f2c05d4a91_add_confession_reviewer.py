"""Record who reviewed a flagged confession and when.

Revision ID: b8f2c05d4a91
Revises: a1d7e934cc58
Create Date: 2026-09-04 02:45:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "b8f2c05d4a91"
down_revision = "a1d7e934cc58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``reviewed_by``/``reviewed_at`` to confessions."""
    op.add_column(
        "confessions",
        sa.Column(
            "reviewed_by",
            postgresql.UUID(as_uuid=True),
            # SET NULL rather than RESTRICT: a departed reviewer should not
            # pin a confession row in place. The audit trail keeps the
            # durable record of who decided what.
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "confessions",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop the reviewer columns."""
    op.drop_column("confessions", "reviewed_at")
    op.drop_column("confessions", "reviewed_by")
