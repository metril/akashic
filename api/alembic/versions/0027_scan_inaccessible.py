"""scans.inaccessible_dirs / inaccessible_files

Tracks how many directory and file entries the scanner silently skipped
during a walk (permission denied, ENOENT mid-scan). The api accumulates
these across IsFinal=true batches so the parallel-agent path (one final
batch per work unit) sums correctly across all units. SourceDetail
surfaces the totals as "N inaccessible items skipped" so users know
when a scan was incomplete vs. clean.

Revision ID: 0027_scan_inaccessible
Revises: 0026_credential_profiles
Create Date: 2026-05-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0027_scan_inaccessible"
down_revision: Union[str, None] = "0026_credential_profiles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column(
            "inaccessible_dirs",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "scans",
        sa.Column(
            "inaccessible_files",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("scans", "inaccessible_files")
    op.drop_column("scans", "inaccessible_dirs")
