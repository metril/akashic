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
    op.create_foreign_key(
        "fk_entries_linked_entry_id", "entries", "entries",
        ["linked_entry_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_entries_link_target_source_id", "entries", "sources",
        ["link_target_source_id"], ["id"], ondelete="SET NULL",
    )
    # Checks are added NOT VALID (no table scan under the ACCESS EXCLUSIVE
    # lock) and validated afterwards (SHARE UPDATE EXCLUSIVE only).
    op.execute(
        "ALTER TABLE entries ADD CONSTRAINT ck_entries_link_role CHECK "
        "(link_role IN ('original','thumbnail','encoded_video','sidecar','archive','version')) NOT VALID"
    )
    op.execute(
        "ALTER TABLE entries ADD CONSTRAINT ck_entries_link_role_when_linked CHECK "
        "(linked_entry_id IS NULL OR link_role IS NOT NULL) NOT VALID"
    )
    op.execute("ALTER TABLE entries VALIDATE CONSTRAINT ck_entries_link_role")
    op.execute("ALTER TABLE entries VALIDATE CONSTRAINT ck_entries_link_role_when_linked")
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_entries_linked_entry_id", "entries", ["linked_entry_id"],
            postgresql_where=sa.text("linked_entry_id IS NOT NULL"),
            postgresql_concurrently=True,
        )
        op.create_index(
            "ix_entries_source_link_role", "entries", ["source_id", "link_role"],
            postgresql_where=sa.text("link_role IS NOT NULL"),
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
