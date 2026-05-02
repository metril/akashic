"""Cascade deletes for source-dependent rows.

Pre-fix: deleting a source 500'd because `entries`, `entry_events`,
`scans`, `source_permissions` all had ON DELETE NO ACTION FKs to
`sources`. The api's delete handler called `await db.delete(source)`
and the FK constraint kicked it back. This migration tightens those
FKs so a source delete cleanly removes its data:

- entries.source_id           → CASCADE
- scans.source_id             → CASCADE
- source_permissions.source_id → CASCADE
- entry_events.{old,new}_source_id → SET NULL
  (the move-history row stays so audit trails survive; it just loses
  its source pointer when one side of the move is deleted)
- entry_versions.entry_id     → CASCADE
  (so the entries cascade above can actually run; otherwise the
  entry_versions FK blocks the entry deletion)

Revision ID: 0009_source_cascade_fixes
Revises: 0008_scanners_and_pools
Create Date: 2026-05-02
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0009_source_cascade_fixes"
down_revision: Union[str, None] = "0008_scanners_and_pools"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (constraint_name, table, column, referenced_table, referenced_column,
#  on_delete_action)
_NEW_FKS = [
    ("entries_source_id_fkey", "entries", "source_id", "sources", "id", "CASCADE"),
    ("scans_source_id_fkey", "scans", "source_id", "sources", "id", "CASCADE"),
    ("source_permissions_source_id_fkey",
     "source_permissions", "source_id", "sources", "id", "CASCADE"),
    ("entry_events_old_source_id_fkey",
     "entry_events", "old_source_id", "sources", "id", "SET NULL"),
    ("entry_events_new_source_id_fkey",
     "entry_events", "new_source_id", "sources", "id", "SET NULL"),
    ("entry_versions_entry_id_fkey",
     "entry_versions", "entry_id", "entries", "id", "CASCADE"),
]


def upgrade() -> None:
    for name, table, col, ref_table, ref_col, action in _NEW_FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(
            name, table, ref_table,
            [col], [ref_col],
            ondelete=action,
        )


def downgrade() -> None:
    for name, table, col, ref_table, ref_col, _ in _NEW_FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(
            name, table, ref_table,
            [col], [ref_col],
            # Restore the original NO ACTION (omit ondelete = NO ACTION).
        )
