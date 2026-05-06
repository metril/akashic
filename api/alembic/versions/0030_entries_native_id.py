"""entries.native_id — opaque cloud-drive identifier

v0.13.0 — Tier 1 PR-B. Cloud drives (Google Drive, OneDrive, SharePoint, Box,
Dropbox) address files and folders by opaque IDs rather than paths. The
synthesized display path (e.g. ``/My Drive/Foo/Bar.docx``) is what akashic
shows to the user and what its existing ACL/browse machinery walks; the
provider-specific ID lives next to it for permission API calls and
metadata refreshes.

The column is nullable: filesystem-shape sources (local, NFS, SMB, SSH, S3,
WebDAV) leave it NULL. A composite index supports the cloud connectors'
"resolve native_id → entry row" lookup (used during permission fetches and
incremental change polls); it's partial so it stays compact on
filesystem-source-heavy deployments.

Revision ID: 0030_entries_native_id
Revises: 0029_oauth_foundation
Create Date: 2026-05-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0030_entries_native_id"
down_revision: Union[str, None] = "0029_oauth_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "entries",
        sa.Column("native_id", sa.String(), nullable=True),
    )
    # Cloud connectors look up by (source, native_id). Partial — only
    # cloud-source rows populate this; filesystem rows stay out of the index.
    op.create_index(
        "ix_entries_source_native_id",
        "entries",
        ["source_id", "native_id"],
        postgresql_where=sa.text("native_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_entries_source_native_id", table_name="entries")
    op.drop_column("entries", "native_id")
