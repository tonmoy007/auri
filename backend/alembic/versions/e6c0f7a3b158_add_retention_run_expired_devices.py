"""Count the device records each retention run removes.

Revision ID: e6c0f7a3b158
Revises: d5b9e6f2a047
Create Date: 2026-09-30 19:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e6c0f7a3b158"
down_revision = "d5b9e6f2a047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``expired_devices``; runs recorded before this removed none, so 0."""
    op.add_column(
        "retention_runs",
        sa.Column(
            "expired_devices",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Device rate-limit records deleted after their window passed",
        ),
    )


def downgrade() -> None:
    """Drop ``expired_devices``."""
    op.drop_column("retention_runs", "expired_devices")
