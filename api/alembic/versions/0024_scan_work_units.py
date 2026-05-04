"""scan_work_units table + sources.max_parallel_scanners

Adds a per-scan claimable work-unit table so multiple scanners can
cooperate on one scan. Each unit represents one directory subtree;
the walker decides at runtime whether to walk inline or split off as
a new pending unit.

`sources.max_parallel_scanners` (default 1) caps the number of
distinct scanners that can hold unit leases on a single scan
simultaneously. Default 1 preserves today's behaviour exactly.

Revision ID: 0024_scan_work_units
Revises: 0023_hosts
Create Date: 2026-05-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "0024_scan_work_units"
down_revision: Union[str, None] = "0023_hosts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scan_work_units",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "scan_id", UUID(as_uuid=True),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column(
            "parent_unit_id", UUID(as_uuid=True),
            sa.ForeignKey("scan_work_units.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column(
            "assigned_scanner_id", UUID(as_uuid=True),
            sa.ForeignKey("scanners.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("scan_id", "path", name="uq_scan_work_units_scan_path"),
    )
    op.create_index(
        "ix_scan_work_units_scan_id",
        "scan_work_units", ["scan_id"],
    )
    # Partial index for the lease query — only pending/running rows.
    # Speeds up SELECT … FOR UPDATE SKIP LOCKED on scans with many
    # completed units (the table grows; the index stays tight).
    op.create_index(
        "ix_scan_work_units_lease",
        "scan_work_units",
        ["scan_id", "status", "lease_expires_at"],
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    op.add_column(
        "sources",
        sa.Column(
            "max_parallel_scanners",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("sources", "max_parallel_scanners")
    op.drop_index("ix_scan_work_units_lease", table_name="scan_work_units")
    op.drop_index("ix_scan_work_units_scan_id", table_name="scan_work_units")
    op.drop_table("scan_work_units")
