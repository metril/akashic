"""scan_work_units.attempt_count — transient-stall requeue counter

v0.34.0 — a multi-scanner work unit whose walk fails on a transient SMB
stall is requeued for retry rather than abandoned. `attempt_count` bounds
those retries: after _MAX_UNIT_ATTEMPTS the requeue falls back to a
permanent `failed` so the scan can still finalize.

Revision ID: 0038_unit_attempt_count
Revises: 0037_maintenance_jobs
Create Date: 2026-05-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0038_unit_attempt_count"
down_revision: Union[str, None] = "0037_maintenance_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scan_work_units",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("scan_work_units", "attempt_count")
