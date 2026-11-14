"""drop source_permissions

Viewers read all sources; visibility is narrowed by filesystem ACLs only.

Revision ID: 0043_drop_source_perms
Revises: 0042_maint_job_running_uniq
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043_drop_source_perms"
down_revision: Union[str, None] = "0042_maint_job_running_uniq"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("source_permissions")


def downgrade() -> None:
    op.create_table(
        "source_permissions",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("access_level", sa.String(), nullable=True),
    )
