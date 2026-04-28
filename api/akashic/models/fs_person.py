import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Boolean, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class FsPerson(Base):
    """One real-world identity-set claimed by a user.

    A user can have multiple FsPersons (e.g. "My Work Account", "My Home Account").
    Each FsPerson contains zero or more FsBindings, one per source.
    """
    __tablename__ = "fs_persons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    label: Mapped[str] = mapped_column(String, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FsBinding(Base):
    """A FsPerson's identifier on a specific source, with optional cached groups."""
    __tablename__ = "fs_bindings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fs_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fs_persons.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    identity_type: Mapped[str] = mapped_column(String, nullable=False)  # 'posix_uid' | 'sid' | 'nfsv4_principal' | 's3_canonical'
    identifier: Mapped[str] = mapped_column(String, nullable=False)
    groups: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    groups_source: Mapped[str] = mapped_column(String, nullable=False, default="manual")  # 'manual' | 'auto'
    groups_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("fs_person_id", "source_id", name="uq_fs_bindings_person_source"),
    )
