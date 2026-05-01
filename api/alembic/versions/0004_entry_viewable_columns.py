"""entry viewable_by_* columns

Adds three TEXT[] columns to entries holding the denormalized ACL projection
per CRUDS right (read / write / delete) — the same per-token sets that
already live in Meilisearch's `viewable_by_*` filterable fields. With these
columns the SQL side gets a fast permission filter (array-overlap on the
GIN index) so Browse can apply the same trim Search does, and the Search
DB-fallback path can stop being a permission-policy escape hatch.

Schema written here matches the model at api/akashic/models/entry.py;
populated at ingest by `services/search.build_entry_doc` →
`denormalize_acl(...)` (single source of truth that feeds both sinks).
Existing entries get NULL until the backfill tool fills them in.

Revision ID: 0004_entry_viewable_columns
Revises: 0003_fs_unbound_identities
Create Date: 2026-05-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0004_entry_viewable_columns'
down_revision: Union[str, None] = '0003_fs_unbound_identities'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'entries',
        sa.Column('viewable_by_read', postgresql.ARRAY(sa.Text()), nullable=True),
    )
    op.add_column(
        'entries',
        sa.Column('viewable_by_write', postgresql.ARRAY(sa.Text()), nullable=True),
    )
    op.add_column(
        'entries',
        sa.Column('viewable_by_delete', postgresql.ARRAY(sa.Text()), nullable=True),
    )
    op.create_index(
        'ix_entries_viewable_read_gin',
        'entries',
        ['viewable_by_read'],
        postgresql_using='gin',
    )
    op.create_index(
        'ix_entries_viewable_write_gin',
        'entries',
        ['viewable_by_write'],
        postgresql_using='gin',
    )
    op.create_index(
        'ix_entries_viewable_delete_gin',
        'entries',
        ['viewable_by_delete'],
        postgresql_using='gin',
    )


def downgrade() -> None:
    op.drop_index('ix_entries_viewable_delete_gin', table_name='entries')
    op.drop_index('ix_entries_viewable_write_gin', table_name='entries')
    op.drop_index('ix_entries_viewable_read_gin', table_name='entries')
    op.drop_column('entries', 'viewable_by_delete')
    op.drop_column('entries', 'viewable_by_write')
    op.drop_column('entries', 'viewable_by_read')
