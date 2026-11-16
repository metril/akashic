"""entries: mirror-link columns (backing file -> Immich/Paperless entry)

Revision ID: 0047_mirror_links
Revises: 0046_user_roles
Create Date: 2026-11-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0047_mirror_links"
down_revision: Union[str, None] = "0046_user_roles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "entries",
        sa.Column("linked_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("entries", sa.Column("link_role", sa.String(), nullable=True))
    op.add_column(
        "entries",
        sa.Column("link_target_viewable", postgresql.ARRAY(sa.Text()), nullable=True),
    )
    op.add_column(
        "entries",
        sa.Column("link_target_source_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # Transactional part is metadata-only: constraints go in NOT VALID (no
    # table scan under the ACCESS EXCLUSIVE lock).
    op.execute(
        "ALTER TABLE entries ADD CONSTRAINT fk_entries_linked_entry_id "
        "FOREIGN KEY (linked_entry_id) REFERENCES entries (id) ON DELETE SET NULL NOT VALID"
    )
    op.execute(
        "ALTER TABLE entries ADD CONSTRAINT fk_entries_link_target_source_id "
        "FOREIGN KEY (link_target_source_id) REFERENCES sources (id) ON DELETE SET NULL NOT VALID"
    )
    op.execute(
        "ALTER TABLE entries ADD CONSTRAINT ck_entries_link_role CHECK "
        "(link_role IN ('original','thumbnail','encoded_video','sidecar','archive','version')) NOT VALID"
    )
    op.execute(
        "ALTER TABLE entries ADD CONSTRAINT ck_entries_link_role_when_linked CHECK "
        "(linked_entry_id IS NULL OR link_role IS NOT NULL) NOT VALID"
    )
    # Validation (SHARE UPDATE EXCLUSIVE) and CREATE INDEX CONCURRENTLY run
    # outside the migration transaction, each statement on its own.
    with op.get_context().autocommit_block():
        for name in (
            "fk_entries_linked_entry_id",
            "fk_entries_link_target_source_id",
            "ck_entries_link_role",
            "ck_entries_link_role_when_linked",
        ):
            op.execute(f"ALTER TABLE entries VALIDATE CONSTRAINT {name}")
        for name, cols, where in (
            ("ix_entries_linked_entry_id", ["linked_entry_id"], "linked_entry_id IS NOT NULL"),
            ("ix_entries_source_link_role", ["source_id", "link_role"], "link_role IS NOT NULL"),
        ):
            # A failed CONCURRENTLY build leaves an invalid index behind; drop
            # it so a re-run recovers.
            invalid = op.get_bind().execute(
                sa.text(
                    "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                    "WHERE c.relname = :n AND NOT i.indisvalid"
                ),
                {"n": name},
            ).first()
            if invalid:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.create_index(
                name, "entries", cols,
                postgresql_where=sa.text(where),
                postgresql_concurrently=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_entries_source_link_role", table_name="entries",
            postgresql_concurrently=True,
        )
        op.drop_index(
            "ix_entries_linked_entry_id", table_name="entries",
            postgresql_concurrently=True,
        )
    op.drop_constraint("ck_entries_link_role_when_linked", "entries", type_="check")
    op.drop_constraint("ck_entries_link_role", "entries", type_="check")
    op.drop_constraint("fk_entries_link_target_source_id", "entries", type_="foreignkey")
    op.drop_constraint("fk_entries_linked_entry_id", "entries", type_="foreignkey")
    op.drop_column("entries", "link_target_source_id")
    op.drop_column("entries", "link_target_viewable")
    op.drop_column("entries", "link_role")
    op.drop_column("entries", "linked_entry_id")
