"""Record each retention job run.

Revision ID: c4a8d5e91f36
Revises: b6e2f8a41d97
Create Date: 2026-09-30 17:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c4a8d5e91f36"
down_revision = "b6e2f8a41d97"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create ``retention_runs``; existing deployments start with no history."""
    op.create_table(
        "retention_runs",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
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
        sa.Column(
            "ran_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="When the run happened, from the job's own injected clock",
        ),
        sa.Column(
            "retention_hours",
            sa.Integer(),
            nullable=False,
            comment="The confession window this run enforced, so the log can prove the published one",
        ),
        sa.Column(
            "reply_retention_days",
            sa.Integer(),
            nullable=False,
            comment="The reply window this run enforced",
        ),
        sa.Column(
            "deleted",
            sa.Integer(),
            nullable=False,
            comment="Confessions hard-deleted outright",
        ),
        sa.Column(
            "emptied_to_shell",
            sa.Integer(),
            nullable=False,
            comment="Replied confessions emptied to a reply-only shell",
        ),
        sa.Column(
            "expired_replies",
            sa.Integer(),
            nullable=False,
            comment="Replied confessions deleted for an expired reply",
        ),
    )
    op.create_index("ix_retention_runs_id", "retention_runs", ["id"])
    op.create_index("ix_retention_runs_ran_at", "retention_runs", ["ran_at"])


def downgrade() -> None:
    """Drop the run log."""
    op.drop_index("ix_retention_runs_ran_at", table_name="retention_runs")
    op.drop_index("ix_retention_runs_id", table_name="retention_runs")
    op.drop_table("retention_runs")
