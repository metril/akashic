"""RefreshToken — long-lived companion to the short-lived access JWT.

We store hashes only. The plain token is returned to the client once
(at login), set in an HttpOnly cookie, and used at /api/auth/refresh
to mint a new access token. Each refresh rotates the row: the old hash
is marked `revoked_at` and a new hash issued. A presented token whose
hash matches a revoked row indicates either replay (the cookie was
used twice) or a stolen token — we revoke the entire chain to fail
closed.

Design choices that the comments alone don't capture:

- `chain_id` ties the original mint to every rotation that descends
  from it, so revocation of one rotation revokes the whole family.
- `token_hash` is sha256 (no peppered HMAC) because the secret-key
  pepper is already encoded in the access JWT signature, and stolen
  DB dumps already contain plenty of higher-value secrets. Keeping
  the hash simple makes lookups index-friendly.
- We do NOT delete revoked rows. A small audit trail of "this chain
  was revoked when token X was replayed" is more useful than space
  saved by purging.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_token_hash", "token_hash"),
        Index("ix_refresh_tokens_chain_id", "chain_id"),
        Index("ix_refresh_tokens_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    # All rotations of one initial login share a chain_id. Lets us revoke
    # an entire family on detected replay without scanning the whole table.
    chain_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # sha256 hex of the plain token. The plain value is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    # Set when this row is rotated (replaced by a new row in the same
    # chain) or revoked outright (replay detection / explicit logout).
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    # Why this row was revoked. Useful for auditing: "rotated" is the
    # happy path; "replayed" / "logout" / "manual" call out incidents.
    revoke_reason: Mapped[str | None] = mapped_column(String, nullable=True)
