"""Append-only history of reachability probe results.

Replaces the queue+results conflation in `reachability_checks` from the
old continuous-poll model. The new model is on-demand only — rows land
when:

  * a user clicks "Test scanners" (or the per-row Test) on a panel,
    and the API runs the probe inline (non-local) or routes it to a
    scanner agent over long-poll (local).
  * a scan completes successfully — the scanner just walked the source,
    which is the strongest reachability proof we'll ever get.

`scanner_id IS NULL` means the API itself probed (non-local source); a
scanner-attributed row means a specific agent ran the test.

A daily prune keeps the last N rows per (source, scanner) pair so the
history disclosure on the eligibility panels stays bounded.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class ReachabilityResult(Base):
    __tablename__ = "reachability_results"
    __table_args__ = (
        Index(
            "ix_reachability_results_pair_completed",
            "source_id", "scanner_id", text("completed_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    scanner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scanners.id", ondelete="CASCADE"),
        nullable=True,
    )
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    step: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
