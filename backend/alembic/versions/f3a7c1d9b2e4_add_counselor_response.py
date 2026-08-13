"""Add counselor_response column to confessions.

Revision ID: f3a7c1d9b2e4
Revises: d4f8b2c1a9e0
Create Date: 2026-08-12 03:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f3a7c1d9b2e4"
down_revision = "d4f8b2c1a9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable ``counselor_response`` text column."""
    op.add_column(
        "confessions",
        sa.Column(
            "counselor_response",
            sa.Text(),
            nullable=True,
            comment="LLM-generated compassionate reflection returned to the confessor after submission",
        ),
    )


def downgrade() -> None:
    """Drop the ``counselor_response`` column."""
    op.drop_column("confessions", "counselor_response")
