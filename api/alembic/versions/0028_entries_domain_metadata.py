"""entries.domain_metadata JSONB column

v0.6.0 — Tier 3 foundation. Self-hosted libraries (Paperless-ngx, Immich)
surface metadata that doesn't fit the existing filesystem fields:
  - Paperless: correspondent, document_type, custom_fields
  - Immich: camera EXIF, person/face IDs, GPS, datetime_original, album

Persisted in a single nullable JSONB column on `entries`. Schemaless on
purpose — the facet UI knows which keys to render. Filesystem-only sources
(local/NFS/SMB/SSH/S3) leave it NULL.

The Meilisearch index pulls a flat subset of well-known keys (see
services/search.ensure_index) into filterable_attributes so users can chip
on `domain_metadata.correspondent = "Bank"`.

Revision ID: 0028_entries_domain_metadata
Revises: 0027_scan_inaccessible
Create Date: 2026-05-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0028_entries_domain_metadata"
down_revision: Union[str, None] = "0027_scan_inaccessible"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "entries",
        sa.Column("domain_metadata", postgresql.JSONB(), nullable=True),
    )
    # Partial GIN index — only library-source rows populate the column,
    # so the index stays compact even on large filesystem-source corpora.
    # GIN gives us @> containment if we ever query by metadata key/value
    # in the SQL fallback path; the primary search path goes through
    # Meilisearch.
    op.create_index(
        "ix_entries_domain_metadata_gin",
        "entries",
        ["domain_metadata"],
        postgresql_using="gin",
        postgresql_where=sa.text("domain_metadata IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_entries_domain_metadata_gin", table_name="entries")
    op.drop_column("entries", "domain_metadata")
