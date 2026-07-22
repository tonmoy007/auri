"""Initial schema: confessions and anonymous_users tables.

Revision ID: d4f8b2c1a9e0
Revises:
Create Date: 2026-07-17 00:00:00.000000
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "d4f8b2c1a9e0"
down_revision = None
branch_labels = None
depends_on = None

confession_status = postgresql.ENUM(
    "pending",
    "forwarded",
    "deleted",
    "flagged",
    name="confession_status",
)


def upgrade() -> None:
    """Create the ``confessions`` and ``anonymous_users`` tables."""
    bind = op.get_bind()
    confession_status.create(bind, checkfirst=True)

    op.create_table(
        "confessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            default=uuid.uuid4,
            nullable=False,
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
        sa.Column("device_token_hash", sa.String(length=256), nullable=False),
        sa.Column(
            "voice_mask", sa.String(length=64), nullable=False, server_default="warm"
        ),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column(
            "pii_stripped", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "status", confession_status, nullable=False, server_default="pending"
        ),
        sa.Column("recipient_dept", sa.String(length=128), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_confessions_id", "confessions", ["id"])
    op.create_index(
        "ix_confessions_device_token_hash", "confessions", ["device_token_hash"]
    )
    op.create_index("ix_confessions_status", "confessions", ["status"])
    op.create_index("ix_confessions_created_at", "confessions", ["created_at"])

    op.create_table(
        "anonymous_users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            default=uuid.uuid4,
            nullable=False,
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
            "device_token_hash", sa.String(length=256), nullable=False, unique=True
        ),
        sa.Column(
            "last_confession_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("confession_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_anonymous_users_id", "anonymous_users", ["id"])
    op.create_index(
        "ix_anonymous_users_device_token_hash", "anonymous_users", ["device_token_hash"]
    )


def downgrade() -> None:
    """Drop the ``anonymous_users`` and ``confessions`` tables."""
    op.drop_index("ix_anonymous_users_device_token_hash", table_name="anonymous_users")
    op.drop_index("ix_anonymous_users_id", table_name="anonymous_users")
    op.drop_table("anonymous_users")

    op.drop_index("ix_confessions_created_at", table_name="confessions")
    op.drop_index("ix_confessions_status", table_name="confessions")
    op.drop_index("ix_confessions_device_token_hash", table_name="confessions")
    op.drop_index("ix_confessions_id", table_name="confessions")
    op.drop_table("confessions")

    bind = op.get_bind()
    confession_status.drop(bind, checkfirst=True)
