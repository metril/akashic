"""server_settings table — runtime-mutable knobs.

Generic key-value store so future toggles don't need their own
column or migration. First key shipped: `discovery_enabled`.

Revision ID: 0013_server_settings
Revises: 0012_scanner_discovery_requests
Create Date: 2026-05-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "0013_server_settings"
down_revision: Union[str, None] = "0012_scanner_discovery_requests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "server_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", JSONB, nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_by_user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("server_settings")
