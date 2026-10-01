"""Move the recipient-department directory into the database.

Revision ID: c9e4b71a2f68
Revises: d3a6b17f0e52
Create Date: 2026-09-04 03:35:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c9e4b71a2f68"
down_revision = "d3a6b17f0e52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the ``departments`` table.

    Rows are not inserted here: the application seeds the table from the
    ``DEPARTMENTS`` env value on first start, so a deployment's real list
    lands even though this migration cannot see it.
    """
    op.create_table(
        "departments",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
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
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("telegram_chat_id", sa.String(length=64), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
    )
    op.create_index("ix_departments_id", "departments", ["id"])
    op.create_index("ix_departments_name", "departments", ["name"], unique=True)


def downgrade() -> None:
    """Drop the ``departments`` table."""
    op.drop_index("ix_departments_name", table_name="departments")
    op.drop_index("ix_departments_id", table_name="departments")
    op.drop_table("departments")
