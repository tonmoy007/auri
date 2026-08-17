"""Add app_settings table for live-configurable dashboard settings.

Revision ID: b4615604f7b8
Revises: f3a7c1d9b2e4
Create Date: 2026-08-18 00:45:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "b4615604f7b8"
down_revision = "f3a7c1d9b2e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the app_settings key/value table."""
    op.create_table(
        "app_settings",
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
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
    )
    op.create_index("ix_app_settings_id", "app_settings", ["id"])
    op.create_index("ix_app_settings_key", "app_settings", ["key"], unique=True)


def downgrade() -> None:
    """Drop the app_settings table."""
    op.drop_index("ix_app_settings_key", table_name="app_settings")
    op.drop_index("ix_app_settings_id", table_name="app_settings")
    op.drop_table("app_settings")
