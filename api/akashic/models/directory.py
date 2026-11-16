"""Directory cache for the access people & group picker.

`directory_entries` holds one row per (backend, ref) read from AD or
Authentik by the directory sync; rows are merged across backends at read
time. `directory_sync_runs` records each sync and doubles as the per-backend
lock (partial unique index on `status = 'running'`).
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class DirectoryEntry(Base):
    __tablename__ = "directory_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    backend: Mapped[str] = mapped_column(String, nullable=False)  # ad | authentik
    ref: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # person | group
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    sid: Mapped[str | None] = mapped_column(String, nullable=True)
    dn: Mapped[str | None] = mapped_column(String, nullable=True)
    group_sids: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default=text("'{}'"),
    )
    groups_status: Mapped[str] = mapped_column(String, nullable=False)  # ok | failed | none
    # Last successful group read; kept when a later read fails.
    groups_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    uid_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gid_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("backend", "ref", name="uq_directory_entries_backend_ref"),
        Index("ix_directory_entries_sid", "sid"),
        Index("ix_directory_entries_dn_lower", func.lower(text("dn"))),
        Index("ix_directory_entries_email_lower", func.lower(text("email"))),
        Index("ix_directory_entries_username_lower", func.lower(text("username"))),
    )


class DirectorySyncRun(Base):
    __tablename__ = "directory_sync_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    backend: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # running | succeeded | failed
    trigger: Mapped[str] = mapped_column(String, nullable=False)  # scheduled | manual
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # {"users", "groups", "removed", "groups_failed"}
    counts: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"),
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "uq_directory_sync_runs_backend_running", "backend", unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )
