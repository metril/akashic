"""seekable browse cursor index + pg_trgm for server-side filter

v0.4.11 — /api/browse switches from "load entire folder" to cursor
pagination plus an optional server-side substring filter via `?q=`.
Two index changes support that:

  1. ix_entries_browse_cursor: seekable composite that matches the new
     ORDER BY (kind, name, id). Leading on (source_id, parent_path)
     so the existing folder-narrowing predicate piggybacks.
     Partial-on-active to keep it lean (a meaningful fraction of the
     entries table is soft-deleted on long-lived installs).

  2. ix_entries_name_trgm: trigram GIN index on entry.name to make
     ILIKE '%q%' indexable. Without it the server-side filter falls
     back to a seq scan over the per-folder candidate set — fine for
     small folders, ugly at 100k+. CREATE EXTENSION pg_trgm is
     IF NOT EXISTS (extension ships with stock postgres but isn't
     always loaded; superuser-only on hosted setups, where it's
     usually pre-enabled by the operator).

Revision ID: 0018_browse_pagination_indexes
Revises: 0017_scans_source_status_indexes
Create Date: 2026-05-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018_browse_pagination_indexes"
down_revision: Union[str, None] = "0017_scans_source_status_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Seekable browse cursor. Order matches browse.py's ORDER BY:
    #   (kind != 'directory') ASC, name ASC, id ASC
    # Indexing on `kind` (rather than the boolean expression) is fine
    # because postgres B-tree handles 2-value lookups efficiently —
    # the planner picks rows where kind = 'directory' first, then
    # the other rows, matching the boolean order.
    op.create_index(
        "ix_entries_browse_cursor",
        "entries",
        ["source_id", "parent_path", "kind", "name", "id"],
        postgresql_where=sa.text("is_deleted = false"),
    )

    # Trigram GIN for ILIKE. Wrapped in a try so installs without
    # pg_trgm permission don't fail the migration — the browse handler
    # still works without the index, just slower on huge folders.
    try:
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_entries_name_trgm "
            "ON entries USING gin (name gin_trgm_ops) "
            "WHERE is_deleted = false"
        )
    except Exception:  # noqa: BLE001
        # Log via alembic's normal output; downgrade still works.
        op.execute(
            "DO $$ BEGIN RAISE NOTICE 'pg_trgm unavailable; "
            "skipped ix_entries_name_trgm'; END $$"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_entries_name_trgm")
    op.drop_index("ix_entries_browse_cursor", table_name="entries")
