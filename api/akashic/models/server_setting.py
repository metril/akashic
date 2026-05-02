"""ServerSetting — runtime-mutable knobs that admins flip from the UI.

A small key-value table so a future toggle (e.g. "max scan
parallelism", "allow weak passwords") doesn't need its own column
or migration. JSONB lets each key carry whatever shape it needs.

First key shipped: `discovery_enabled` (bool). The
SCANNER_DISCOVERY_ENABLED env var, if set on first boot, seeds
this row when it doesn't exist yet — backward-compatible bootstrap
hook for IaC users who'd rather configure via env.
"""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class ServerSetting(Base):
    __tablename__ = "server_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
