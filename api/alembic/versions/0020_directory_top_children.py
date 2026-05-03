"""top_children JSONB column on directory entries

v0.4.11 — Phase 8. Pre-compute per-directory top-K children at scan
time so the storage tree read path can iteratively expand from
indexed JSONB columns instead of running a recursive CTE.

Read latency at large subtree sizes: hundreds-of-ms (CTE) → single-
digit ms (indexed JSONB lookup). Maintained by the rollup task
(post-scan) and optionally by the streaming worker mid-scan
(Phase 8e, behind AKASHIC_STREAMING_TOPCHILDREN feature flag).

Schema shape on directory rows (NULL on file rows):

    {
      "k": 256,
      "computed_at": "<iso>",
      "children": [
        {"id": "uuid", "name": "...", "path": "/...",
         "kind": "file"|"directory",
         "size": 12345, "color_key": "mp4",
         "owner": "alice", "modified": "2024-..."},
        ...
      ],
      "other_size": 999000  // sum of effective_size for skipped children
    }

Read path checks top_children IS NOT NULL and falls back to the
legacy recursive CTE otherwise (covers new sources mid-rollup,
incremental scans where streaming hasn't caught up, etc.).

Revision ID: 0020_directory_top_children
Revises: 0019_storage_tree_indexes
Create Date: 2026-05-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0020_directory_top_children"
down_revision: Union[str, None] = "0019_storage_tree_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "entries",
        sa.Column("top_children", postgresql.JSONB(), nullable=True),
    )
    # Compact partial index — directory rows that have top_children
    # populated. Read path uses (source_id, path) lookups for the root
    # node and (source_id, parent_path = previous_node_path) for
    # subsequent children, so the lookup pattern is well-served by
    # the existing browse cursor index from migration 0018. This
    # partial index is just to keep the planner aware of which rows
    # have top_children when the read path checks IS NOT NULL.
    op.create_index(
        "ix_entries_top_children_present",
        "entries",
        ["source_id", "path"],
        postgresql_where=sa.text("top_children IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_entries_top_children_present", table_name="entries")
    op.drop_column("entries", "top_children")
