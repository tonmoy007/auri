"""Add a detail column to audit events.

Revision ID: d5b9e6f2a047
Revises: c4a8d5e91f36
Create Date: 2026-09-30 18:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d5b9e6f2a047"
down_revision = "c4a8d5e91f36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add a nullable ``detail``; existing rows keep no detail."""
    op.add_column(
        "audit_events",
        sa.Column(
            "detail",
            sa.Text(),
            nullable=True,
            comment="What changed, for actions that alter settings (never confession content)",
        ),
    )


def downgrade() -> None:
    """Drop ``detail``."""
    op.drop_column("audit_events", "detail")
