"""composite scans indexes for source-keyed lookups

v0.4.7 — POST /scans/trigger does two source-keyed scans queries on
each call:

  1. Dedup (added v0.4.4):
       WHERE source_id = ? AND status IN ('pending','running')

  2. previous_files_for_source (ETA seed):
       WHERE source_id = ? AND status = 'completed'
       ORDER BY completed_at DESC LIMIT 1

Existing indexes:
  - ix_scans_lease_pending(pool, status, lease_expires_at) WHERE
    status IN ('pending','running') — keyed on `pool` first; cannot
    serve a source_id-leading predicate.
  - ix_scans_status_started(status, started_at) WHERE status IN
    ('pending','running') — keyed on `status`; same problem.

Without a source_id-leading index the planner falls back to a seq
scan on the partial set or the heap. With a moderate scans table
(thousands of rows accumulated over a few weeks of scanning) each
trigger call takes hundreds of ms — synchronous, in the request
handler — which is what the user perceives as "Scan now lags
everything" while the panel is waiting on the await.

Both indexes are partial: dedup never queries closed-state rows,
ETA never queries open-state rows. Keeps the index small + the
write cost negligible.

Revision ID: 0017_scans_source_status_indexes
Revises: 0016_world_readable_index
Create Date: 2026-05-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017_scans_source_status_indexes"
down_revision: Union[str, None] = "0016_world_readable_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Dedup query (scans.py:trigger_scan): WHERE source_id = ?
    # AND status IN ('pending','running'). Partial because closed
    # scans aren't queried this way.
    op.create_index(
        "ix_scans_source_status_open",
        "scans",
        ["source_id"],
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    # previous_files_for_source (scan_factory.py): WHERE source_id = ?
    # AND status = 'completed' ORDER BY completed_at DESC LIMIT 1.
    # Composite (source_id, completed_at DESC) — the LIMIT 1 means
    # the planner just needs a seekable order on the leading columns
    # to terminate after one row. Partial on closed-state.
    op.create_index(
        "ix_scans_source_completed_at",
        "scans",
        ["source_id", sa.text("completed_at DESC")],
        postgresql_where=sa.text("status = 'completed'"),
    )


def downgrade() -> None:
    op.drop_index("ix_scans_source_completed_at", table_name="scans")
    op.drop_index("ix_scans_source_status_open", table_name="scans")
