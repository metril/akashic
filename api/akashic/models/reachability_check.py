import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class ReachabilityCheck(Base):
    """One claimable reachability probe for a single source.

    Mirrors the work-unit pattern from ``ScanWorkUnit`` but for a
    1-2 second ``test-connection`` probe instead of a tree walk.

    The scheduler enqueues one row per source whose
    ``last_reachability_check_at`` is older than the configured
    interval (default 5 min). Either a remote scanner agent or the
    api self-worker claims rows via SELECT ... FOR UPDATE SKIP
    LOCKED, runs the probe, and reports the result back. The result
    persists onto ``Source.is_reachable`` and rolls up to the host.

    The partial unique index on ``(source_id) WHERE status='pending'``
    makes the scheduler's INSERT loop race-safe with
    ``ON CONFLICT DO NOTHING`` so concurrent enqueue passes never
    duplicate work.

    For ``type=local`` sources, every successful probe doubles as
    an eligibility proof: the scan-lease query refuses to assign a
    scan unless the claiming scanner has a recent ``result_ok=true``
    row in this table. See routers/scanners.py.
    """

    __tablename__ = "reachability_checks"
    __table_args__ = (
        Index(
            "ix_reachability_checks_lease",
            "status", "lease_expires_at",
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
        Index(
            "uq_reachability_checks_pending",
            "source_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 'pending' | 'running' | 'completed' | 'failed'
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    assigned_scanner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scanners.id", ondelete="SET NULL"),
        nullable=True,
    )
    pool: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    result_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    result_step: Mapped[str | None] = mapped_column(String, nullable=True)
    result_error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
