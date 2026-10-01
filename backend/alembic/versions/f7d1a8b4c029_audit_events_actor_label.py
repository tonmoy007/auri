"""Let an audit row name a non-person actor, such as the Telegram bot.

Revision ID: f7d1a8b4c029
Revises: e6c0f7a3b158
Create Date: 2026-09-30 22:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f7d1a8b4c029"
down_revision = "e6c0f7a3b158"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``actor_label``, allow a null account, and require one of the two."""
    op.add_column(
        "audit_events",
        sa.Column(
            "actor_label",
            sa.String(length=64),
            nullable=True,
            comment="Who acted when no named account did, e.g. 'telegram-bot' for the shared moderation key",
        ),
    )
    op.alter_column(
        "audit_events",
        "actor_user_id",
        nullable=True,
        comment="Staff account that performed the action; RESTRICT so history outlives account cleanup. Null only when actor_label names a non-person actor",
    )
    op.create_check_constraint(
        "ck_audit_events_has_actor",
        "audit_events",
        "actor_user_id IS NOT NULL OR actor_label IS NOT NULL",
    )


def downgrade() -> None:
    """Drop the label. Rows with no account (the bot's decisions) cannot be kept and are deleted."""
    op.drop_constraint("ck_audit_events_has_actor", "audit_events", type_="check")
    op.execute("DELETE FROM audit_events WHERE actor_user_id IS NULL")
    op.alter_column(
        "audit_events",
        "actor_user_id",
        nullable=False,
        comment="Staff account that performed the action; RESTRICT so history outlives account cleanup",
    )
    op.drop_column("audit_events", "actor_label")
