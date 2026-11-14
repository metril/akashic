"""scanners: admin-managed tika_url / extract_workers / max_concurrent_units

max_concurrent_units already exists (0040, previously handshake-reported);
it is now admin-set and NULL means "agent default". Adds tika_url and
extract_workers.

Revision ID: 0044_scanner_runtime_config
Revises: 0043_drop_source_perms
Create Date: 2026-11-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0044_scanner_runtime_config"
down_revision: Union[str, None] = "0043_drop_source_perms"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scanners", sa.Column("tika_url", sa.String(), nullable=True))
    op.add_column("scanners", sa.Column("extract_workers", sa.Integer(), nullable=True))
    # Previously self-reported values must not become admin overrides.
    op.execute("UPDATE scanners SET max_concurrent_units = NULL")


def downgrade() -> None:
    op.drop_column("scanners", "extract_workers")
    op.drop_column("scanners", "tika_url")
