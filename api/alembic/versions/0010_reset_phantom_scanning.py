"""reset phantom 'scanning' status from v0.2.0 trigger flips

In v0.2.0 the trigger endpoint flipped `source.status='scanning'` on
enqueue (vestigial behaviour from v0.1.0 when the api spawned the
subprocess immediately). With no scanner ever claiming the work
(common in a fresh install), sources stayed permanently
'scanning' — confusing the watchdog, the UI's polling gate, and
the operator. v0.2.2 stops the eager flip; this migration heals
existing data so a v0.2.0 → v0.2.2 upgrade doesn't require manual
SQL.

Targets only sources that were never actually scanned
(last_scan_at IS NULL) AND aren't currently held by an in-flight
agent lease. Won't touch a legitimately-scanning source.

Idempotent: re-running on a healthy DB is a no-op.

Revision ID: 0010_reset_phantom_scanning
Revises: 0009_source_cascade_fixes
Create Date: 2026-05-02
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


revision: str = "0010_reset_phantom_scanning"
down_revision: Union[str, None] = "0009_source_cascade_fixes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(text("""
        UPDATE sources
           SET status = 'offline'
         WHERE status = 'scanning'
           AND last_scan_at IS NULL
           AND id NOT IN (
               SELECT source_id FROM scans
                WHERE status = 'running'
                  AND assigned_scanner_id IS NOT NULL
           )
    """))


def downgrade() -> None:
    # No-op: there's no useful "re-introduce phantom scanning" state.
    pass
