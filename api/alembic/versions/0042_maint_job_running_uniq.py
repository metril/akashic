"""maintenance_jobs: at most one running job per kind

Closes the check-then-insert race in start_job with a partial unique index.

Revision ID: 0042_maint_job_running_uniq
Revises: 0041_users_provider_ext_uniq
Create Date: 2026-11-14
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0042_maint_job_running_uniq"
down_revision: Union[str, None] = "0041_users_provider_ext_uniq"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Collapse any pre-existing duplicate running rows so the index builds.
    op.execute(
        """
        UPDATE maintenance_jobs SET status = 'failed',
               error = 'superseded duplicate running job',
               finished_at = now()
        WHERE status = 'running' AND id NOT IN (
            SELECT DISTINCT ON (kind) id FROM maintenance_jobs
            WHERE status = 'running' ORDER BY kind, started_at DESC
        )
        """
    )
    op.create_index(
        "uq_maintenance_jobs_kind_running", "maintenance_jobs", ["kind"],
        unique=True, postgresql_where="status = 'running'",
    )


def downgrade() -> None:
    op.drop_index("uq_maintenance_jobs_kind_running", table_name="maintenance_jobs")
