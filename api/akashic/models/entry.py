"""Unified Entry model.

Every row represents a single inode-like thing on a source: a file or a directory.
Permissions (mode/uid/gid + ACL + xattrs) are versioned alongside content via
EntryVersion so changes can be audited over time.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class Entry(Base):
    """A file or directory observed on a source."""

    __tablename__ = "entries"
    __table_args__ = (
        UniqueConstraint("source_id", "path", name="uq_entries_source_path"),
        Index("ix_entries_browse", "source_id", "parent_path", "kind"),
        Index("ix_entries_content_hash", "content_hash"),
        Index(
            "ix_entries_viewable_read_gin",
            "viewable_by_read",
            postgresql_using="gin",
        ),
        Index(
            "ix_entries_viewable_write_gin",
            "viewable_by_write",
            postgresql_using="gin",
        ),
        Index(
            "ix_entries_viewable_delete_gin",
            "viewable_by_delete",
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)  # 'file' | 'directory'

    parent_path: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    # File-only fields (NULL for directories)
    extension: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    # Permissions
    mode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String, nullable=True)
    group_name: Mapped[str | None] = mapped_column(String, nullable=True)
    acl: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    xattrs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Denormalized ACL projections — array of canonical principal tokens
    # (`posix:uid:N`, `sid:S-…`, `*`, `auth`, etc.) granted each right.
    # Populated at ingest from `denormalize_acl(...)`. Indexed with GIN so
    # `viewable_by_read && ARRAY[…]::text[]` is an index scan, not a seqscan.
    viewable_by_read: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )
    viewable_by_write: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )
    viewable_by_delete: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )

    # Filesystem timestamps
    fs_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fs_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fs_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Bookkeeping
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EntryVersion(Base):
    """Snapshot of an Entry's versioned fields, written when any of them changed."""

    __tablename__ = "entry_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entries.id"), nullable=False
    )
    scan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id"), nullable=True
    )

    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String, nullable=True)
    group_name: Mapped[str | None] = mapped_column(String, nullable=True)
    acl: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    xattrs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EntryEvent(Base):
    """Tracks moves of a content_hash from one (source, path) to another."""

    __tablename__ = "entry_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    old_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=True
    )
    old_path: Mapped[str | None] = mapped_column(String, nullable=True)
    new_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=True
    )
    new_path: Mapped[str | None] = mapped_column(String, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    scan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id"), nullable=True
    )
