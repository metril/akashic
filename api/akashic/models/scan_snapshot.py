"""Point-in-time aggregate of a source's contents.

One row per (source, scan-completion or nightly-fallback). Powers the
storage-growth, capacity-forecast, file-type-trend, and owner-distribution
charts. We persist roll-ups (top-N + `_other` bucket) rather than raw
per-entry data because:

  - The entry table is the wrong primary source for time-series — it only
    holds *current* state and is rewritten on every scan.
  - EntryVersion captures changes but not totals; reconstructing
    "total bytes 30 days ago" from EntryVersion would scan the entire
    history table on every chart load.
  - 50 top-N entries × N scans is bounded; raw per-extension/per-owner
    data on a fully-indexed corpus would not be.
"""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class ScanSnapshot(Base):
    __tablename__ = "scan_snapshots"
    __table_args__ = (
        # Time-series charts page through (source, taken_at DESC).
        Index("ix_scan_snapshots_source_taken", "source_id", "taken_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False,
    )
    # Nullable so the nightly scheduler can write "no scan today" baselines.
    scan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="SET NULL"), nullable=True,
    )
    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    file_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    directory_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    total_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)

    # Top-N maps with an `_other` rollup bucket. Shape: {key: {n: int, bytes: int}}.
    # The writer caps the visible entries at TOP_N (50) and folds the long
    # tail into `_other` so a corpus with 50k unique extensions or 100k
    # AD users doesn't blow up the JSONB column.
    by_extension: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    by_owner: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    # Hot/warm/cold age buckets. Shape: {"hot": {n,bytes}, "warm": {...}, "cold": {...}}.
    # Boundaries are <30d (hot), <365d (warm), >=365d (cold) measured at
    # snapshot time against fs_modified_at.
    by_kind_and_age: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
