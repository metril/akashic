import uuid
from datetime import datetime

from sqlalchemy import Index, String, DateTime, ForeignKey, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class ScanWorkUnit(Base):
    """One claimable subtree of a scan.

    Parallel scanning splits a scan into many ``ScanWorkUnit`` rows
    (one per directory subtree), each leasable by a single scanner via
    the same ``SELECT … FOR UPDATE SKIP LOCKED`` primitive used for
    scan-level leasing. Multiple scanners can lease different units of
    the same scan simultaneously, capped by ``Source.max_parallel_scanners``.

    The walker decides at runtime whether to walk a directory inline
    (small / leaf) or split it off as a new pending unit (large /
    branchy), so the design adapts to uneven trees without requiring a
    static partition up front.
    """

    __tablename__ = "scan_work_units"
    __table_args__ = (
        UniqueConstraint("scan_id", "path", name="uq_scan_work_units_scan_path"),
        Index(
            "ix_scan_work_units_lease",
            "scan_id", "status", "lease_expires_at",
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Path within the source ("" = root). Unique per scan.
    path: Mapped[str] = mapped_column(String, nullable=False)
    parent_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scan_work_units.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 'pending' | 'running' | 'completed' | 'failed'
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    assigned_scanner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scanners.id", ondelete="SET NULL"),
        nullable=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
