"""Mark confessions retention has emptied down to a reply-only shell.

Revision ID: b6e2f8a41d97
Revises: a7d3e9f1c2b6
Create Date: 2026-09-30 14:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b6e2f8a41d97"
down_revision = "a7d3e9f1c2b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``purged_at``; existing rows get NULL, meaning "still whole"."""
    op.add_column(
        "confessions",
        sa.Column(
            "purged_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When retention emptied this row down to a reply-only shell; null means it still holds its confession",
        ),
    )
    op.create_index("ix_confessions_purged_at", "confessions", ["purged_at"])


def downgrade() -> None:
    """Drop the shell marker, first deleting the shells it marks.

    A shell is a blank row kept only for its reply. Older code has no notion of
    one and would list it in HR views, count it in Insights and offer it to the
    delivery bot, so the rows are removed rather than left to look like live
    confessions. Their replies are lost with them; that is the price of going
    back to a schema that cannot tell the difference.
    """
    op.execute("DELETE FROM confessions WHERE purged_at IS NOT NULL")
    op.drop_index("ix_confessions_purged_at", table_name="confessions")
    op.drop_column("confessions", "purged_at")
