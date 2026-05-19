"""scanners.max_concurrent_units — handshake-reported unit concurrency

v0.36.0 — the scanner self-reports its MaxConcurrentUnits at handshake
time so admins can see in Settings → Scanners which scanners are
running at 1 unit vs 4 without SSH-ing onto each box. Configuration
still lives on the scanner host (AKASHIC_MAX_CONCURRENT_UNITS); this
column is just the read-side surface.

Revision ID: 0040_scanner_concurrency
Revises: 0039_scan_controls
Create Date: 2026-05-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0040_scanner_concurrency"
down_revision: Union[str, None] = "0039_scan_controls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scanners",
        sa.Column("max_concurrent_units", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scanners", "max_concurrent_units")
