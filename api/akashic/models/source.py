import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, DateTime, func
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from akashic.database import Base


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    # Optional FK to a Host row that owns the connection-level config
    # (hostname, credentials). NULL only for `local` sources, which
    # have no remote host. ON DELETE RESTRICT — deleting a host with
    # attached sources requires explicit per-source action first.
    host_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hosts.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    host = relationship("Host", lazy="joined", foreign_keys=[host_id])
    # Share-only fields per type — `path` (local), `share` (smb),
    # `export_path` (nfs), `bucket`+`prefix` (s3). The host's
    # connection_config is merged in at scan/test time.
    connection_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    scan_schedule: Mapped[str | None] = mapped_column(String, nullable=True)
    exclude_patterns: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, default="offline")
    security_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Optional pool restriction. NULL = any registered scanner may claim
    # this source's scans. Set to a pool name (e.g. "site-amsterdam")
    # to lock the source to scanners in that pool.
    preferred_pool: Mapped[str | None] = mapped_column(String, nullable=True)
    # Multi-scanner cap — max distinct scanners that may cooperate on
    # one scan via scan_work_units leases. v0.35.0 made this nullable:
    # NULL = inherit from the parent Host (or the built-in default 1
    # when the host is unset / NULL too). A non-NULL value pins the cap
    # for this source. See services/source_config.
    max_parallel_scanners: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    # v0.35.0 — work-unit entry budget: how many entries a scanner walks
    # before splitting the rest of its frontier back to the shared queue.
    # NULL = inherit from the host (or the built-in default 2000).
    scan_chunk_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # External / removable storage: USB drives, network shares whose host
    # may be offline. Hint only — the scan failure path surfaces "actually
    # offline" at scan time; this column just lets the UI label the
    # source's intermittent nature so users aren't surprised when it's
    # unreachable.
    is_removable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # v0.5.9 — optional reference to a reusable CredentialProfile.
    # See models/credential_profile.py and
    # services/source_config.resolve_connection_config for the
    # layered resolution order.
    credential_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credential_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    credential_profile = relationship(
        "CredentialProfile", lazy="joined", foreign_keys=[credential_profile_id],
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
