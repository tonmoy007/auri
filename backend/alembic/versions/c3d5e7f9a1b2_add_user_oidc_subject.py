"""Add users.oidc_subject, the single sign-on identity linked at first SSO login.

Revision ID: c3d5e7f9a1b2
Revises: b2c3d4e5f6a7
Create Date: 2026-10-01 21:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c3d5e7f9a1b2"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable, unique ``oidc_subject`` column (plan 15.10)."""
    op.add_column(
        "users",
        sa.Column(
            "oidc_subject",
            sa.String(length=512),
            nullable=True,
            unique=True,
            comment="Single sign-on identity (issuer and subject) linked at first SSO login",
        ),
    )


def downgrade() -> None:
    """Drop the ``oidc_subject`` column (its unique constraint goes with it)."""
    op.drop_column("users", "oidc_subject")
