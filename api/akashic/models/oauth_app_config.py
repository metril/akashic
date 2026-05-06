"""OAuth client-app configuration set by the deployment owner.

Each deployment registers its own OAuth app with each provider
(Google Cloud Console, Azure App Registration, etc.) and pastes the
``client_id`` / ``client_secret`` into the akashic settings UI. The
``client_secret`` is encrypted at rest with the project secret key —
see ``services.secret_encryption``.

There is no shared akashic-branded OAuth app — first-party trust is
preferred and the regulatory story is cleaner.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class OAuthAppConfig(Base):
    __tablename__ = "oauth_app_configs"

    provider: Mapped[str] = mapped_column(String, primary_key=True)
    client_id: Mapped[str] = mapped_column(String, nullable=False)
    client_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String, nullable=False)
    configured_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    configured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
