import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class ScanLogEntry(Base):
    """A single line in a scan's live log feed.

    `level` is one of `"info"`, `"warn"`, `"error"` for structured logger
    output, or the special value `"stderr"` for raw passthrough chunks
    (anything written to the scanner's stderr that didn't go through the
    structured logger — third-party library output, panics, etc.).

    The same table backs both UI tabs ("Activity" filters out stderr; "Raw
    stderr" shows only stderr) so the cleanup path stays single-source.
    """

    __tablename__ = "scan_log_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_scan_log_entries_scan_ts", "scan_id", "ts"),
    )
