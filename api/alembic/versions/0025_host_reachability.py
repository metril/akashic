"""host reachability columns + reachability_checks table

Two upgrades in one revision:

  1. Add the same three reachability columns to `hosts` that
     0022_source_reachability added to `sources`:
       is_reachable, last_reachable_at, last_reachability_check_at.
     A direct host probe writes all three; a roll-up from attached
     sources writes only `is_reachable` so timestamps preserve probe
     provenance.

  2. New `reachability_checks` table — work items leased by either
     scanner agents or the api self-worker. Mirrors the work-unit
     pattern from 0024_scan_work_units. The partial unique index on
     (source_id) WHERE status='pending' guarantees the scheduler's
     INSERT loop is race-safe with ON CONFLICT DO NOTHING.

Revision ID: 0025_host_reachability
Revises: 0024_scan_work_units
Create Date: 2026-05-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "0025_host_reachability"
down_revision: Union[str, None] = "0024_scan_work_units"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Host reachability columns — mirror 0022 source reachability.
    op.add_column(
        "hosts",
        sa.Column("is_reachable", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "hosts",
        sa.Column(
            "last_reachable_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "hosts",
        sa.Column(
            "last_reachability_check_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # 2. reachability_checks work-item table.
    op.create_table(
        "reachability_checks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id", UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(),
            nullable=False, server_default="pending",
        ),
        sa.Column(
            "assigned_scanner_id", UUID(as_uuid=True),
            sa.ForeignKey("scanners.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pool", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_ok", sa.Boolean(), nullable=True),
        sa.Column("result_step", sa.String(), nullable=True),
        sa.Column("result_error", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_reachability_checks_source_id",
        "reachability_checks", ["source_id"],
    )
    # Partial index for the lease query — only active rows.
    op.create_index(
        "ix_reachability_checks_lease",
        "reachability_checks",
        ["status", "lease_expires_at"],
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )
    # Partial unique index — at most one pending check per source so
    # the scheduler's enqueue loop is race-safe with ON CONFLICT DO
    # NOTHING. Cheaper than a SELECT-then-INSERT lock.
    op.create_index(
        "uq_reachability_checks_pending",
        "reachability_checks",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_reachability_checks_pending", table_name="reachability_checks")
    op.drop_index("ix_reachability_checks_lease", table_name="reachability_checks")
    op.drop_index("ix_reachability_checks_source_id", table_name="reachability_checks")
    op.drop_table("reachability_checks")
    op.drop_column("hosts", "last_reachability_check_at")
    op.drop_column("hosts", "last_reachable_at")
    op.drop_column("hosts", "is_reachable")
