"""partial index for the world-readable file count

Pre-v0.4.4, the dashboard's `access_risks` tile ran:
  SELECT count(*) FROM entries
   WHERE kind='file' AND is_deleted=false
     AND viewable_by_read && ARRAY['*']
Under load (millions of entries + concurrent writes from a running
scan), even with the existing GIN index on viewable_by_read the
planner often picks a sequential scan because the GIN selectivity
estimate for `&& ['*']` is wide.

Partial index narrows the candidate set to JUST the world-readable
files (typically a small fraction of total entries in a well-
managed install). Lookup becomes "scan a small partial index" —
sub-100ms regardless of total entries-table size.

Revision ID: 0016_world_readable_index
Revises: 0015_perf_indexes
Create Date: 2026-05-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016_world_readable_index"
down_revision: Union[str, None] = "0015_perf_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Partial index — only world-readable rows live here. Index
    # on `id` because the dashboard does a COUNT, not a fetch;
    # the planner uses index-only scan if visibility-map allows.
    op.create_index(
        "ix_entries_world_readable",
        "entries",
        ["id"],
        postgresql_where=sa.text(
            "kind = 'file' AND is_deleted = false "
            "AND viewable_by_read && ARRAY['*']::text[]"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_entries_world_readable", table_name="entries")
