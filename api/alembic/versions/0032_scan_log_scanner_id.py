"""scan_log_entries.scanner_id — attribute live-log rows to the scanner

v0.28.2 — the Live Log panel in the web app couldn't show *which*
scanner produced each row. With multiple scanners feeding the same
scan (work-units) or just multiple scans interleaved on the
dashboard, every line looked identical.

Adds a nullable `scanner_id` column. The scan-progress endpoints
extract the leasing scanner's id from a new JWT claim minted by
`_mint_ingest_jwt` and persist it per row. Rows produced by
older agents (pre-v0.28.2) lack the claim and land with NULL.

Partial index covers the read path used by the Live Log filter
("show only this scanner's lines") so we don't pay full-table cost
on the legacy NULL rows.

Revision ID: 0032_scan_log_scanner_id
Revises: 0031_reachability_ondemand
Create Date: 2026-05-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "0032_scan_log_scanner_id"
down_revision: Union[str, None] = "0031_reachability_ondemand"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scan_log_entries",
        sa.Column(
            "scanner_id",
            UUID(as_uuid=True),
            sa.ForeignKey("scanners.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_scan_log_entries_scanner_id",
        "scan_log_entries",
        ["scanner_id"],
        postgresql_where=sa.text("scanner_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scan_log_entries_scanner_id", table_name="scan_log_entries",
    )
    op.drop_column("scan_log_entries", "scanner_id")
