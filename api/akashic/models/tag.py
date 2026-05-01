import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class Tag(Base):
    """The tag *catalogue* — the set of names known to the system.

    Catalogue rows track colour and creator metadata, and the Settings →
    Tags page lists / counts / cleans them up. The applied-to-entry
    relation lives on `EntryTag` (the join row), and `EntryTag.tag` is a
    denormalised copy of `Tag.name` so Meili filters and SQL LIKE on
    `tag` don't need a join.
    """

    __tablename__ = "tags"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    color: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)


class EntryTag(Base):
    """Tag applied to an Entry, with materialised inheritance.

    `inherited_from_entry_id` records the ancestor directory the tag
    was originally applied to. NULL → the tag was applied directly to
    this entry. Non-NULL → the row exists because the ancestor's
    direct tag was propagated down. Both kinds carry the same `tag`
    string and are returned by the same filter — inheritance is
    invisible to the read path. See services/tag_inheritance.py for
    the apply / remove / propagate logic.
    """

    __tablename__ = "entry_tags"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entries.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    tag: Mapped[str] = mapped_column(String, nullable=False, index=True)
    inherited_from_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entries.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "entry_id", "tag", "inherited_from_entry_id",
            name="uq_entry_tags_entry_tag_origin",
        ),
    )
