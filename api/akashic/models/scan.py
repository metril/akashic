import uuid
from datetime import datetime

from sqlalchemy import String, BigInteger, Integer, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False,
    )
    scan_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending")
    files_found: Mapped[int] = mapped_column(Integer, default=0)
    files_new: Mapped[int] = mapped_column(Integer, default=0)
    files_changed: Mapped[int] = mapped_column(Integer, default=0)
    files_deleted: Mapped[int] = mapped_column(Integer, default=0)
    bytes_scanned: Mapped[int] = mapped_column(BigInteger, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)

    # Phase 1 observability — heartbeat-driven progress fields. Updated by the
    # scanner via POST /api/scans/{id}/heartbeat independently of batch ingest.
    current_path: Mapped[str | None] = mapped_column(String, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bytes_scanned_so_far: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    files_skipped: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    dirs_walked: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    dirs_queued: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_estimated: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # "prewalk" | "walk" | "finalize" | NULL (not started / legacy row).
    phase: Mapped[str | None] = mapped_column(String, nullable=True)
    # Snapshot of the last successful scan's files_found, captured at start.
    previous_scan_files: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Phase 2 multi-scanner — pool-tagged lease queue.
    # `pool` is a snapshot of the source's preferred_pool at enqueue time
    # (NULL = any scanner can claim). `assigned_scanner_id` + `lease_expires_at`
    # track the current lease; nullable so an unleased pending scan is
    # representable. The lease is renewed by per-scan heartbeat and released
    # by /api/scans/{id}/complete or by the watchdog when expiry overruns.
    pool: Mapped[str | None] = mapped_column(String, nullable=True)
    assigned_scanner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scanners.id", ondelete="SET NULL"),
        nullable=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
