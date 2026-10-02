"""Add counselor_reply (structured counselor reply) to confessions.

Revision ID: b6e1f4a82c35
Revises: c3d5e7f9a1b2
Create Date: 2026-10-02 18:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b6e1f4a82c35"
down_revision = "c3d5e7f9a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable ``counselor_reply`` JSON column."""
    op.add_column(
        "confessions",
        sa.Column(
            "counselor_reply",
            sa.JSON(),
            nullable=True,
            comment="The same counselor reply as structured parts (acknowledgement, reflection, suggestions, closing, tone); counselor_response holds the rendered text. Null for a crisis reply, which is a fixed template",
        ),
    )


def downgrade() -> None:
    """Drop the ``counselor_reply`` column."""
    op.drop_column("confessions", "counselor_reply")
