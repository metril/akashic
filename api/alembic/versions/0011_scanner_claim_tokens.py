"""scanner_claim_tokens table + scanner scope columns

Adds the join-token table that powers POST /api/scanners/claim
(self-registering scanner flow), and extends `scanners` with two
optional scope whitelists (allowed_source_ids, allowed_scan_types)
that the lease query enforces.

Revision ID: 0011_scanner_claim_tokens
Revises: 0010_reset_phantom_scanning
Create Date: 2026-05-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID


revision: str = "0011_scanner_claim_tokens"
down_revision: Union[str, None] = "0010_reset_phantom_scanning"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scanners",
        sa.Column(
            "allowed_source_ids", ARRAY(UUID(as_uuid=True)), nullable=True,
        ),
    )
    op.add_column(
        "scanners",
        sa.Column(
            "allowed_scan_types", ARRAY(sa.String(length=16)), nullable=True,
        ),
    )

    op.create_table(
        "scanner_claim_tokens",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column(
            "pool", sa.String(length=64), nullable=False, server_default="default",
        ),
        sa.Column(
            "allowed_source_ids", ARRAY(UUID(as_uuid=True)), nullable=True,
        ),
        sa.Column(
            "allowed_scan_types", ARRAY(sa.String(length=16)), nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "used_by_scanner_id",
            UUID(as_uuid=True),
            sa.ForeignKey("scanners.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_scanner_claim_tokens_token_hash",
        "scanner_claim_tokens",
        ["token_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scanner_claim_tokens_token_hash",
        table_name="scanner_claim_tokens",
    )
    op.drop_table("scanner_claim_tokens")
    op.drop_column("scanners", "allowed_scan_types")
    op.drop_column("scanners", "allowed_source_ids")
