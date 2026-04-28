import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class AuditEvent(Base):
    """Append-only audit record. Never updated; deleted only by retention job."""
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    request_ip: Mapped[str] = mapped_column(String, nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String, nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
