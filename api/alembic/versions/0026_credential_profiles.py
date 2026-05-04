"""credential_profiles table + Host/Source FKs

Introduces named, reusable credential profiles so the same SSH key
or SMB credential set can be referenced from many hosts and sources
without copy-pasting the secrets. Inline `connection_config` keys
keep working — the layered resolver in
services/source_config.resolve_connection_config picks them up as
overrides on top of the profile's contribution.

Resolution order (last write wins):
    host_profile < host_inline < source_profile < source_inline.

Revision ID: 0026_credential_profiles
Revises: 0025_host_reachability
Create Date: 2026-05-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "0026_credential_profiles"
down_revision: Union[str, None] = "0025_host_reachability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "credential_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        # ssh / smb / s3 / nfs — discriminator. Profile fields are
        # validated against this on PATCH/POST.
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("credentials", JSONB(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_credential_profiles_type",
        "credential_profiles", ["type"],
    )

    op.add_column(
        "hosts",
        sa.Column(
            "credential_profile_id", UUID(as_uuid=True),
            sa.ForeignKey("credential_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "credential_profile_id", UUID(as_uuid=True),
            sa.ForeignKey("credential_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("sources", "credential_profile_id")
    op.drop_column("hosts", "credential_profile_id")
    op.drop_index(
        "ix_credential_profiles_type", table_name="credential_profiles",
    )
    op.drop_table("credential_profiles")
