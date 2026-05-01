"""entry subtree aggregates

Adds three BIGINT columns to entries holding pre-computed per-directory
totals (size / file count / directory count of all descendants). The
StorageExplorer treemap queries read these directly so no recursive
traversal happens at request time. Columns are NULL for file rows.

Populated by a bottom-up CTE that runs at the end of every scan; a
backfill tool exists for legacy data. See
api/akashic/services/subtree_rollup.py.

Composite index `(source_id, parent_path, subtree_size_bytes)` is the
seam the treemap drill-down uses — "top-N children of parent_path
by size" becomes one ordered range scan.

Revision ID: 0006_entry_subtree_aggregates
Revises: 0005_refresh_tokens
Create Date: 2026-05-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0006_entry_subtree_aggregates'
down_revision: Union[str, None] = '0005_refresh_tokens'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('entries', sa.Column('subtree_size_bytes', sa.BigInteger(), nullable=True))
    op.add_column('entries', sa.Column('subtree_file_count', sa.BigInteger(), nullable=True))
    op.add_column('entries', sa.Column('subtree_dir_count', sa.BigInteger(), nullable=True))
    op.create_index(
        'ix_entries_subtree_topN',
        'entries',
        ['source_id', 'parent_path', 'subtree_size_bytes'],
    )


def downgrade() -> None:
    op.drop_index('ix_entries_subtree_topN', table_name='entries')
    op.drop_column('entries', 'subtree_dir_count')
    op.drop_column('entries', 'subtree_file_count')
    op.drop_column('entries', 'subtree_size_bytes')
