import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, PrimaryKeyConstraint, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class PrincipalGroupsCache(Base):
    """Time-bounded cache of resolved groups per (source, identity_type, identifier).

    Composite primary key — at most one row per principal per source.
    """
    __tablename__ = "principal_groups_cache"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False,
    )
    identity_type: Mapped[str] = mapped_column(String, nullable=False)
    identifier: Mapped[str] = mapped_column(String, nullable=False)
    groups: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        PrimaryKeyConstraint("source_id", "identity_type", "identifier", name="pk_principal_groups_cache"),
    )
