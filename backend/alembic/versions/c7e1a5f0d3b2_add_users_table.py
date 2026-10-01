"""Add users table for named staff accounts (Phase 11 identity foundation).

Revision ID: c7e1a5f0d3b2
Revises: b4615604f7b8
Create Date: 2026-09-03 22:10:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c7e1a5f0d3b2"
down_revision = "b4615604f7b8"
branch_labels = None
depends_on = None

user_role = postgresql.ENUM("admin", "hr", "moderator", name="user_role")


def upgrade() -> None:
    """Create the ``users`` table and its ``user_role`` enum type."""
    bind = op.get_bind()
    user_role.create(bind, checkfirst=True)
    # Same reason as the confession_status enum in d4f8b2c1a9e0: without
    # this, create_table re-emits its own CREATE TYPE and the migration
    # fails with "type user_role already exists" on a fresh database.
    user_role.create_type = False

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
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
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)


def downgrade() -> None:
    """Drop the ``users`` table and its enum type."""
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
    user_role.drop(op.get_bind(), checkfirst=True)
