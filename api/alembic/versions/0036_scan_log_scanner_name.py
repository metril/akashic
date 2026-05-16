"""scan_log_entries.scanner_name — durable per-line scanner attribution

v0.30.2 — the Live Log resolved a log line's scanner name at *read*
time with a LEFT JOIN from scan_log_entries.scanner_id to scanners.
That JOIN is fragile: the scanner name showed while a scan streamed
live (resolved at broadcast time) but vanished when the panel was
closed and reopened (the snapshot/backfill re-derived it). A log line
is immutable history — its attribution must be snapshotted when the
line is written, not re-derived later.

This adds a denormalized scanner_name column, written at log-ingest
time. The backfill recovers the name for existing rows whose scanner
still exists; rows whose scanner is already gone stay NULL.

Revision ID: 0036_scan_log_scanner_name
Revises: 0035_scan_cancel_reason
Create Date: 2026-05-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0036_scan_log_scanner_name"
down_revision: Union[str, None] = "0035_scan_cancel_reason"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scan_log_entries",
        sa.Column("scanner_name", sa.String(), nullable=True),
    )
    # Backfill recoverable rows: a row whose scanner still exists gets
    # the current name; rows whose scanner was deleted stay NULL.
    op.execute(
        """
        UPDATE scan_log_entries AS e
        SET scanner_name = s.name
        FROM scanners AS s
        WHERE e.scanner_id = s.id
          AND e.scanner_name IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("scan_log_entries", "scanner_name")
