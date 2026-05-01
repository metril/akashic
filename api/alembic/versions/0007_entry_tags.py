"""entry tags with directory inheritance

Replaces the original (entry_id, tag_id) join with a richer row that
records inherited-from-ancestor origin so directory-applied tags can be
materialised onto every descendant and cleanly cascaded back out on
removal.

Schema:
- own UUID primary key (an entry can have the same tag from multiple
  origins — direct + inherited from two ancestors — and each row is
  independent).
- `tag` is a denormalised string (not an FK to tags.id) so the Meili
  doc and SQL filter can both treat the field as a plain string array
  without joins.
- `inherited_from_entry_id` NULL → tag was applied directly to this
  entry; non-NULL → inherited from that ancestor directory.

Old rows were never exposed in the UI (catalogue management was the only
shipping path). Drop and recreate cleanly.

Revision ID: 0007_entry_tags
Revises: 0006_entry_subtree_aggregates
Create Date: 2026-05-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_entry_tags"
down_revision: Union[str, None] = "0006_entry_subtree_aggregates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("entry_tags")

    op.create_table(
        "entry_tags",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column("tag", sa.String(), nullable=False),
        sa.Column("inherited_from_entry_id", sa.UUID(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"], ["entries.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inherited_from_entry_id"], ["entries.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        # Identity-of-row: same entry can hold the same tag from
        # multiple origins; one row per (entry, tag, origin).
        # NULL inherited_from_entry_id is treated as the direct-apply
        # origin and Postgres collapses duplicate NULLs in unique
        # constraints, so the direct-tag row is unique on its own.
        sa.UniqueConstraint(
            "entry_id", "tag", "inherited_from_entry_id",
            name="uq_entry_tags_entry_tag_origin",
        ),
    )
    op.create_index("ix_entry_tags_entry_id", "entry_tags", ["entry_id"])
    op.create_index("ix_entry_tags_tag", "entry_tags", ["tag"])
    # Cascade-removal of an inherited tag pivots on this column.
    op.create_index(
        "ix_entry_tags_inherited_from",
        "entry_tags",
        ["inherited_from_entry_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_entry_tags_inherited_from", table_name="entry_tags")
    op.drop_index("ix_entry_tags_tag", table_name="entry_tags")
    op.drop_index("ix_entry_tags_entry_id", table_name="entry_tags")
    op.drop_table("entry_tags")

    # Recreate the old shape so downgrade is symmetrical.
    op.create_table(
        "entry_tags",
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column("tag_id", sa.UUID(), nullable=False),
        sa.Column("tagged_by", sa.UUID(), nullable=True),
        sa.Column(
            "tagged_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["entry_id"], ["entries.id"]),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"]),
        sa.ForeignKeyConstraint(["tagged_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("entry_id", "tag_id"),
    )
