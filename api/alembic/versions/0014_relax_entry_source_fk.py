"""Relax entries+scans → sources FK from CASCADE to SET NULL.

Pre-v0.4.0, deleting a source took every indexed entry and every
historical scan with it (via 0009's CASCADE constraint). The
cascade was a v0.2.1 bug fix to make `DELETE /api/sources/{id}`
work at all — but it locked in a heavier semantic than the bug
required. v0.4.0 introduces a `?purge_entries=true|false` flag on
the delete endpoint; the default (false) needs the FK to SET NULL
on dependent rows so the entries / scans survive their source.

Idempotent: re-running on a schema where the FK is already SET NULL
is a no-op (alembic detects the same constraint def).

Revision ID: 0014_relax_entry_source_fk
Revises: 0013_server_settings
Create Date: 2026-05-02
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0014_relax_entry_source_fk"
down_revision: Union[str, None] = "0013_server_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # entries.source_id: NOT NULL → NULL, CASCADE → SET NULL
    op.alter_column("entries", "source_id", nullable=True)
    op.drop_constraint("entries_source_id_fkey", "entries", type_="foreignkey")
    op.create_foreign_key(
        "entries_source_id_fkey", "entries", "sources",
        ["source_id"], ["id"], ondelete="SET NULL",
    )

    # scans.source_id: same. Without this, the delete-source path
    # would either still try to cascade-delete the scans OR (post-FK
    # change for entries only) leave scan rows pointing at a deleted
    # FK target, which Postgres rejects. SET NULL keeps the historical
    # scan record alive without its source pointer.
    op.alter_column("scans", "source_id", nullable=True)
    op.drop_constraint("scans_source_id_fkey", "scans", type_="foreignkey")
    op.create_foreign_key(
        "scans_source_id_fkey", "scans", "sources",
        ["source_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    # Restore CASCADE behaviour. NOTE: re-tightening to NOT NULL
    # would fail if any orphan rows exist; we leave the column
    # nullable on downgrade — application code that relied on
    # NOT NULL would have already broken if rows are NULL anyway.
    op.drop_constraint("scans_source_id_fkey", "scans", type_="foreignkey")
    op.create_foreign_key(
        "scans_source_id_fkey", "scans", "sources",
        ["source_id"], ["id"], ondelete="CASCADE",
    )
    op.drop_constraint("entries_source_id_fkey", "entries", type_="foreignkey")
    op.create_foreign_key(
        "entries_source_id_fkey", "entries", "sources",
        ["source_id"], ["id"], ondelete="CASCADE",
    )
