"""generated effective_size_bytes + composite index for storage tree CTE

v0.4.11 — Phase 6. The storage tree recursive CTE
([storage_explorer._fetch_tree_rows]) joins on `parent_path` and
sorts each parent's children by `COALESCE(subtree_size_bytes,
size_bytes, 0) DESC`. With the LATERAL top-K-per-dir rewrite
(Phase 6b), the planner needs an index that's seekable on this
sort order.

Two changes:

  1. Generated column `effective_size_bytes` =
     COALESCE(subtree_size_bytes, size_bytes, 0). Postgres 12+
     STORED generated column, maintained by the database — zero
     app-side write cost. Costs ~8 bytes per row but lets us put
     the value in an index in DESC order.

  2. Composite partial index `ix_entries_tree_walk` on
     (source_id, parent_path, effective_size_bytes DESC, id)
     WHERE is_deleted = false. Matches the LATERAL subquery's
     ORDER BY exactly so the planner can do an index-only scan
     for the per-dir top-K — bounded fan-out, no sort step.

Revision ID: 0019_storage_tree_indexes
Revises: 0018_browse_pagination_indexes
Create Date: 2026-05-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0019_storage_tree_indexes"
down_revision: Union[str, None] = "0018_browse_pagination_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Generated column. STORED so it's actually persisted (and
    # indexable). The COALESCE order matches the existing CTE so
    # behavior is bit-identical to the pre-Phase-6 code.
    op.execute("""
        ALTER TABLE entries
        ADD COLUMN effective_size_bytes BIGINT
        GENERATED ALWAYS AS (
            COALESCE(subtree_size_bytes, size_bytes, 0)
        ) STORED
    """)

    # Composite seekable index for the LATERAL top-K-per-dir CTE.
    # Partial-on-active so soft-deleted rows don't bloat the index.
    op.create_index(
        "ix_entries_tree_walk",
        "entries",
        ["source_id", "parent_path",
         sa.text("effective_size_bytes DESC"), "id"],
        postgresql_where=sa.text("is_deleted = false"),
    )


def downgrade() -> None:
    op.drop_index("ix_entries_tree_walk", table_name="entries")
    op.execute("ALTER TABLE entries DROP COLUMN effective_size_bytes")
