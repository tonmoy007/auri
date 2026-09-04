"""Add users.token_version so logout can revoke issued session tokens.

Revision ID: e2b9d4c60a17
Revises: c7e1a5f0d3b2
Create Date: 2026-09-03 22:40:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e2b9d4c60a17"
down_revision = "c7e1a5f0d3b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the ``token_version`` column, defaulting existing rows to 0."""
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Drop the ``token_version`` column."""
    op.drop_column("users", "token_version")
