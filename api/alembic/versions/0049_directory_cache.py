"""directory cache tables for the access people & group picker

Revision ID: 0049_directory_cache
Revises: 0048_mirrors_on_app
Create Date: 2026-11-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0049_directory_cache"
down_revision: Union[str, None] = "0048_mirrors_on_app"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "directory_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("backend", sa.String(), nullable=False),
        sa.Column("ref", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("sid", sa.String(), nullable=True),
        sa.Column("dn", sa.String(), nullable=True),
        sa.Column(
            "group_sids", postgresql.ARRAY(sa.String()), nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("groups_status", sa.String(), nullable=False),
        sa.Column("groups_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uid_number", sa.Integer(), nullable=True),
        sa.Column("gid_number", sa.Integer(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("backend", "ref", name="uq_directory_entries_backend_ref"),
    )
    op.create_index("ix_directory_entries_sid", "directory_entries", ["sid"])
    op.create_index("ix_directory_entries_dn_lower", "directory_entries", [sa.text("lower(dn)")])
    op.create_index("ix_directory_entries_email_lower", "directory_entries", [sa.text("lower(email)")])
    op.create_index("ix_directory_entries_username_lower", "directory_entries", [sa.text("lower(username)")])

    op.create_table(
        "directory_sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("backend", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.Column(
            "triggered_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "counts", postgresql.JSONB(), nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index(
        "uq_directory_sync_runs_backend_running", "directory_sync_runs", ["backend"],
        unique=True, postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("uq_directory_sync_runs_backend_running", table_name="directory_sync_runs")
    op.drop_table("directory_sync_runs")
    op.drop_table("directory_entries")
