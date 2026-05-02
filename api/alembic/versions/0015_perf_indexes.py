"""perf indexes — orphan-match + scheduler hot paths

Pre-v0.4.3, four queries were sequential scans on tables that grow
with usage:

1. orphan_matcher.count_potential_matches / find_matches —
   JOIN entries fresh, entries orphan ON path/name/kind WHERE
   orphan.source_id IS NULL. With no index on orphan-side, this
   degraded to O(N×M) and froze the source-detail panel on open
   (the v0.4.0 banner regression).

2. scheduler._check_and_trigger_scans —
   SELECT FROM sources WHERE scan_schedule IS NOT NULL …
   Sequential scan every 60s.

3. scheduler._check_stale_scans (sources side) —
   SELECT FROM sources WHERE status='scanning' AND last_scan_at < cutoff.
   Sequential scan every 60s.

4. scheduler._check_stale_scans (scans side) —
   SELECT FROM scans WHERE status IN ('pending','running')
   AND started_at < cutoff. Existing ix_scans_lease_pending covers
   (pool, status, lease_expires_at), not (status, started_at).

Each gets a targeted index. Verified post-deploy via EXPLAIN
(ANALYZE, BUFFERS) on representative rows.

Revision ID: 0015_perf_indexes
Revises: 0014_relax_entry_source_fk
Create Date: 2026-05-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_perf_indexes"
down_revision: Union[str, None] = "0014_relax_entry_source_fk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Orphan-recovery JOIN. Partial — only NULL-source rows are
    #    queried via this index. (path, name, kind) matches the join
    #    predicate exactly, so the planner picks it for both
    #    count_potential_matches and find_matches.
    op.create_index(
        "ix_entries_orphan_match",
        "entries",
        ["path", "name", "kind"],
        postgresql_where=sa.text("source_id IS NULL"),
    )

    # 2. Scheduler's _check_and_trigger_scans loops over sources
    #    with a cron schedule. Partial keeps the index tiny — most
    #    sources don't have a schedule.
    op.create_index(
        "ix_sources_scan_schedule",
        "sources",
        ["scan_schedule"],
        postgresql_where=sa.text("scan_schedule IS NOT NULL"),
    )

    # 3. Scheduler's stale-scan watchdog filters sources by
    #    (status='scanning', last_scan_at < cutoff). Composite
    #    supports both columns.
    op.create_index(
        "ix_sources_status_last_scan",
        "sources",
        ["status", "last_scan_at"],
    )

    # 4. Scan-watchdog: status IN ('pending','running') AND
    #    started_at < cutoff. Existing ix_scans_lease_pending covers
    #    (pool, status, lease_expires_at) — different predicate. Add a
    #    sibling on (status, started_at), partial to the same row set.
    op.create_index(
        "ix_scans_status_started",
        "scans",
        ["status", "started_at"],
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("ix_scans_status_started", table_name="scans")
    op.drop_index("ix_sources_status_last_scan", table_name="sources")
    op.drop_index("ix_sources_scan_schedule", table_name="sources")
    op.drop_index("ix_entries_orphan_match", table_name="entries")
