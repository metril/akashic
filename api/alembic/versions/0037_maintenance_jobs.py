"""maintenance_jobs — durable record of admin-triggered maintenance runs

v0.31.0 — the Admin Maintenance page kicks off long-running tasks
(Meilisearch reindex, the three backfills). A synchronous HTTP request
would hang the browser and risk a reverse-proxy timeout, and a
fire-and-forget background task leaves no result visible. Each run is
recorded here: the endpoint inserts a `running` row, an in-process
async task does the work and flips the row to `succeeded`/`failed`,
and the page polls this table.

Revision ID: 0037_maintenance_jobs
Revises: 0036_scan_log_scanner_name
Create Date: 2026-05-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "0037_maintenance_jobs"
down_revision: Union[str, None] = "0036_scan_log_scanner_name"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "maintenance_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("params", JSONB(), nullable=False, server_default="{}"),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "triggered_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The page lists recent jobs newest-first; the duplicate-kind guard
    # looks up the running job of a given kind.
    op.create_index(
        "ix_maintenance_jobs_started_at", "maintenance_jobs", ["started_at"],
    )
    op.create_index(
        "ix_maintenance_jobs_kind_status", "maintenance_jobs", ["kind", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_maintenance_jobs_kind_status", table_name="maintenance_jobs")
    op.drop_index("ix_maintenance_jobs_started_at", table_name="maintenance_jobs")
    op.drop_table("maintenance_jobs")
