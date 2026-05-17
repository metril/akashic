import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class MaintenanceJob(Base):
    """A record of one admin-triggered long-running maintenance task.

    v0.31.0 — the Admin Maintenance page kicks off the Meilisearch
    reindex and the backfill tools. Those take minutes, so they run as
    an in-process async task rather than blocking the HTTP request: the
    endpoint inserts a `running` row, `_run` flips it to a terminal
    state with the row count (or the error), and the page polls.

    `kind` is one of `reindex_search`, `backfill_subtree_sizes`,
    `backfill_viewable`, `warm_groups`. `status` is `running`,
    `succeeded` or `failed`. A row left `running` after an API restart
    is reconciled to `failed` on the next startup — the task does not
    survive the process.
    """

    __tablename__ = "maintenance_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
