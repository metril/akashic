"""partial index for stale-file move-detection

The end-of-batch stale loop in routers/ingest.py runs, for every stale
file with a content_hash, a SELECT against entries that filters by:

    WHERE content_hash = ?
      AND kind = 'file'
      AND is_deleted = false
      AND last_seen_at >= scan.started_at
      AND id != stale.id
    LIMIT 1

The existing `ix_entries_content_hash(content_hash)` index exists, but
it covers ALL rows — including the (typically) much larger set of
deleted/historic entries. The planner has to scan and discard them.

This partial composite (source_id leading, content_hash second, scoped
to live files only) is small (< 1 % of entries on a healthy install)
and serves the move-detection lookup directly. source_id leads because
move-detection is also useful when scoped to one source — e.g. the
common case of an entry moving within a single SSH source — and
because the optimizer prefers a tighter index when source_id is also
known. The hot path on full-tree moves still benefits even if the
caller doesn't pin source_id, because the planner can do an index-only
scan filtered by content_hash.

Revision ID: 0021_entries_active_chash
Revises: 0020_directory_top_children
Create Date: 2026-05-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0021_entries_active_chash"
down_revision: Union[str, None] = "0020_directory_top_children"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_entries_active_content_hash",
        "entries",
        ["source_id", "content_hash"],
        postgresql_where=sa.text(
            "is_deleted = false AND kind = 'file' AND content_hash IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_entries_active_content_hash", table_name="entries")
