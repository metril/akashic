"""scanner_discovery_requests table

Pending self-registration requests posted by scanners running in
discovery mode. Admin approves/denies via the SettingsScanners UI.

Revision ID: 0012_scanner_discovery_requests
Revises: 0011_scanner_claim_tokens
Create Date: 2026-05-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "0012_scanner_discovery_requests"
down_revision: Union[str, None] = "0011_scanner_claim_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scanner_discovery_requests",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("public_key_pem", sa.Text(), nullable=False),
        sa.Column("key_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("pairing_code", sa.String(length=9), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("agent_version", sa.String(length=32), nullable=True),
        sa.Column("requested_pool", sa.String(length=64), nullable=True),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False,
            server_default="pending",
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "decided_by_user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("deny_reason", sa.String(length=255), nullable=True),
        sa.Column(
            "approved_scanner_id", UUID(as_uuid=True),
            sa.ForeignKey("scanners.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_discovery_status_requested_at",
        "scanner_discovery_requests",
        ["status", "requested_at"],
    )
    op.create_index(
        "ix_discovery_pubkey_pending",
        "scanner_discovery_requests",
        ["key_fingerprint"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_discovery_pubkey_pending",
        table_name="scanner_discovery_requests",
    )
    op.drop_index(
        "ix_discovery_status_requested_at",
        table_name="scanner_discovery_requests",
    )
    op.drop_table("scanner_discovery_requests")
