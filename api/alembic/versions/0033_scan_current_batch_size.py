"""scans.current_batch_size — surface adaptive batch size in heartbeats

v0.29.2 — the scanner agent now uses AIMD adaptive batch sizing
(scanner/internal/scanner/batchsize.go) and reports the current value
on every heartbeat. The Live Log row tooltip needs somewhere to read
that from, so persist it on the scan row alongside the other
heartbeat-driven progress fields. Nullable so legacy rows + pre-v0.29.2
agents stay valid.

Revision ID: 0033_scan_current_batch_size
Revises: 0032_scan_log_scanner_id
Create Date: 2026-05-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0033_scan_curr_batch_size"
down_revision: Union[str, None] = "0032_scan_log_scanner_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column("current_batch_size", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scans", "current_batch_size")
