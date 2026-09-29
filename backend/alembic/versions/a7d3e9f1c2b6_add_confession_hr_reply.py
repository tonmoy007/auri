"""Add the organisation's reply to a confession.

Revision ID: a7d3e9f1c2b6
Revises: c9e4b71a2f68
Create Date: 2026-09-29 09:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a7d3e9f1c2b6"
down_revision = "c9e4b71a2f68"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``hr_reply`` plus its first-saved and last-edited timestamps.

    There is deliberately no author column: who wrote a reply is recorded
    only in the append-only ``audit_events`` trail (``hr_reply.write``).
    Existing rows get NULL in all three, which means "no reply yet".
    """
    op.add_column(
        "confessions",
        sa.Column(
            "hr_reply",
            sa.Text(),
            nullable=True,
            comment="Organisation's reply to the confessor, written by HR. The author is recorded only in audit_events (hr_reply.write)",
        ),
    )
    op.add_column(
        "confessions",
        sa.Column(
            "hr_replied_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the first HR reply was saved; null means no reply",
        ),
    )
    op.add_column(
        "confessions",
        sa.Column(
            "hr_reply_edited_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the HR reply text last changed after the first save; null if never edited",
        ),
    )
    op.create_index("ix_confessions_hr_replied_at", "confessions", ["hr_replied_at"])


def downgrade() -> None:
    """Drop the reply index and columns."""
    op.drop_index("ix_confessions_hr_replied_at", table_name="confessions")
    op.drop_column("confessions", "hr_reply_edited_at")
    op.drop_column("confessions", "hr_replied_at")
    op.drop_column("confessions", "hr_reply")
