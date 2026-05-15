"""scans.cancellation_reason — disambiguate the 409 cancellation signal

v0.29.8 — pre-fix the API returned HTTP 409 for ANY terminal status
(cancelled, completed, failed) on the heartbeat path, and the scanner
unconditionally logged "scan cancelled by user; exiting" on every 409
even when the trigger was the watchdog reap or a clean completion
race. Persisting a `cancellation_reason` column lets the API return
that reason in the 409 body and the scanner log it verbatim.

Allowed values written by the API:
- "user" — POST /api/scans/{id}/cancel
- "watchdog" — scheduler reap of a stale-heartbeat scan
- "completed" — terminal-success race
- "failed:<short-cause>" — explicit failure paths

Legacy rows stay NULL — the 409 serializer treats NULL as "user" so
already-cancelled scans before the migration runs keep the existing
log message rather than displaying "unknown".

Revision ID: 0035_scan_cancel_reason
Revises: 0034_credprof_encrypt
Create Date: 2026-05-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0035_scan_cancel_reason"
down_revision: Union[str, None] = "0034_cred_prof_encrypt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column("cancellation_reason", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scans", "cancellation_reason")
