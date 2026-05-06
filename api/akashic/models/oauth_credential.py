"""Per-source OAuth refresh-token grant.

The API holds the long-lived refresh token; scanners only ever see
ephemeral access tokens minted on demand. See
``services.source_oauth.mint_access_token``.

``source_id`` is nullable so the OAuth foundation's smoke-test path
can persist a grant before the user has actually filled in source
form fields. Tier-1 PR-C wires the AddSourceForm "Sign in with X"
step to associate the credential with the resulting source row.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from akashic.database import Base


class SourceOAuthCredential(Base):
    __tablename__ = "source_oauth_credentials"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=True,
    )
    source = relationship("Source", lazy="joined", foreign_keys=[source_id])
    provider: Mapped[str] = mapped_column(String, nullable=False, index=True)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    access_token_cached: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_email: Mapped[str | None] = mapped_column(String, nullable=True)
    account_label: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
