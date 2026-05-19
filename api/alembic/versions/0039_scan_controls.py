"""scan-distribution controls — host + source tuning columns

v0.35.0 — operator controls over how a multi-scanner scan spreads work:

  - hosts.max_parallel_scanners / hosts.scan_chunk_size — host-level
    defaults inherited by every attached source.
  - sources.scan_chunk_size — per-source work-unit entry budget.
  - sources.max_parallel_scanners becomes nullable: NULL now means
    "inherit from the host" (the built-in default 1 when the host is
    unset too). Existing non-NULL values are preserved as-is, so a
    source that was explicitly pinned stays pinned.

Revision ID: 0039_scan_controls
Revises: 0038_unit_attempt_count
Create Date: 2026-05-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0039_scan_controls"
down_revision: Union[str, None] = "0038_unit_attempt_count"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "hosts",
        sa.Column("max_parallel_scanners", sa.Integer(), nullable=True),
    )
    op.add_column(
        "hosts",
        sa.Column("scan_chunk_size", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column("scan_chunk_size", sa.Integer(), nullable=True),
    )
    # sources.max_parallel_scanners: NOT NULL default 1 → nullable, no
    # server default. NULL = inherit from the host. Existing values are
    # untouched by ALTER COLUMN, so a previously-pinned source stays pinned.
    op.alter_column(
        "sources",
        "max_parallel_scanners",
        existing_type=sa.Integer(),
        nullable=True,
        server_default=None,
    )


def downgrade() -> None:
    # Restore the NOT NULL contract: backfill inherited NULLs to the
    # legacy default before re-imposing the constraint.
    op.execute(
        "UPDATE sources SET max_parallel_scanners = 1 "
        "WHERE max_parallel_scanners IS NULL"
    )
    op.alter_column(
        "sources",
        "max_parallel_scanners",
        existing_type=sa.Integer(),
        nullable=False,
        server_default="1",
    )
    op.drop_column("sources", "scan_chunk_size")
    op.drop_column("hosts", "scan_chunk_size")
    op.drop_column("hosts", "max_parallel_scanners")
