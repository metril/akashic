"""scanners + pool-tagged lease queue (Phase 1 of multi-scanner)

Adds the `scanners` table — one row per registered remote agent, keyed
by Ed25519 public key. Adds `sources.preferred_pool` for routing, and
extends `scans` with `pool` / `assigned_scanner_id` / `lease_expires_at`
so a scanner agent can atomically claim a pending scan.

Lease semantics live in routers/scanners.py; this migration only sets
up the columns + indexes.

Revision ID: 0008_scanners_and_pools
Revises: 0007_entry_tags
Create Date: 2026-05-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_scanners_and_pools"
down_revision: Union[str, None] = "0007_entry_tags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scanners",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "pool", sa.String(), nullable=False, server_default="default",
        ),
        sa.Column("public_key_pem", sa.Text(), nullable=False),
        sa.Column("key_fingerprint", sa.String(), nullable=False),
        sa.Column("hostname", sa.String(), nullable=True),
        sa.Column("version", sa.String(), nullable=True),
        sa.Column("protocol_version", sa.Integer(), nullable=True),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("key_fingerprint"),
    )
    op.create_index("ix_scanners_pool", "scanners", ["pool"])

    op.add_column(
        "sources",
        sa.Column("preferred_pool", sa.String(), nullable=True),
    )

    op.add_column("scans", sa.Column("pool", sa.String(), nullable=True))
    op.add_column(
        "scans",
        sa.Column(
            "assigned_scanner_id",
            sa.UUID(),
            sa.ForeignKey("scanners.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "scans",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_scans_lease_pending",
        "scans",
        ["pool", "status", "lease_expires_at"],
        # Partial: this index only matters for rows that could still be
        # leased. Speeds up the SELECT … FOR UPDATE SKIP LOCKED inside
        # /api/scans/lease without bloating the index for terminal rows.
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("ix_scans_lease_pending", table_name="scans")
    op.drop_column("scans", "lease_expires_at")
    op.drop_column("scans", "assigned_scanner_id")
    op.drop_column("scans", "pool")
    op.drop_column("sources", "preferred_pool")
    op.drop_index("ix_scanners_pool", table_name="scanners")
    op.drop_table("scanners")
