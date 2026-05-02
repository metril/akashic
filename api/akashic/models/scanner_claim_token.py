"""ScannerClaimToken — single-use bootstrap secret for the
self-registering scanner flow.

An admin mints a token via POST /api/scanner-claim-tokens; the
plaintext crosses the wire once (in the response body), and only
its sha256 hash is persisted. The scanner host POSTs the plaintext
back at POST /api/scanners/claim along with a freshly-generated
public key — server creates the Scanner row, marks the token row
used, and never sees the private key.

The token also carries the scope dimensions that the resulting
scanner inherits: pool (always set), allowed_source_ids (optional
whitelist), allowed_scan_types (optional whitelist). Once a token
is redeemed those fields are copied onto the Scanner row; the
token itself is then irrelevant for ongoing auth.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class ScannerClaimToken(Base):
    __tablename__ = "scanner_claim_tokens"
    __table_args__ = (
        Index("ix_scanner_claim_tokens_token_hash", "token_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    # sha256 hex of the plaintext `akcl_…` token. Plaintext is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    pool: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    # Optional scope whitelists copied to the Scanner row at claim time.
    # NULL = unrestricted on this dimension.
    allowed_source_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True,
    )
    allowed_scan_types: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(16)), nullable=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    used_by_scanner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scanners.id", ondelete="SET NULL"),
        nullable=True,
    )
