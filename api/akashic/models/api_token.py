"""ApiToken: a long-lived personal access token (``akp_...``).

Only the sha256 of the secret is stored (same pattern as RefreshToken); the
plaintext is shown once at creation. ``token_prefix`` is the first characters
of the secret, indexed, so a presented token is found without scanning hashes.

A token's scopes are a ceiling, not a grant: the effective scopes on each
request are ``scopes`` intersected with the owner's *current* role bundle.
``source_ids`` NULL means every source.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class ApiToken(Base):
    __tablename__ = "api_tokens"
    __table_args__ = (
        Index("ix_api_tokens_token_prefix", "token_prefix"),
        Index("ix_api_tokens_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    # sha256 hex of the full token. The plain value is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    source_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_ip: Mapped[str | None] = mapped_column(String, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
