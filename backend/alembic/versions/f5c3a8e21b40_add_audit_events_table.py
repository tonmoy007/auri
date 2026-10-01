"""Add the append-only audit_events table.

Revision ID: f5c3a8e21b40
Revises: e2b9d4c60a17
Create Date: 2026-09-04 01:20:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "f5c3a8e21b40"
down_revision = "e2b9d4c60a17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create ``audit_events`` and its lookup indexes."""
    op.create_table(
        "audit_events",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=64), nullable=False),
        # No FK to confessions: retention (6.1) hard-deletes confession rows,
        # and the record of who read one must outlive the row itself.
        sa.Column("target_confession_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content_tier", sa.String(length=16), nullable=True),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("source_ip", sa.String(length=45), nullable=True),
    )
    op.create_index("ix_audit_events_id", "audit_events", ["id"])
    op.create_index("ix_audit_events_actor_user_id", "audit_events", ["actor_user_id"])
    op.create_index(
        "ix_audit_events_target_confession_id", "audit_events", ["target_confession_id"]
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])


def downgrade() -> None:
    """Drop ``audit_events`` and its indexes."""
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_index("ix_audit_events_target_confession_id", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_user_id", table_name="audit_events")
    op.drop_index("ix_audit_events_id", table_name="audit_events")
    op.drop_table("audit_events")
