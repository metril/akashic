"""ScannerDiscoveryRequest — pending self-registration requests.

A scanner running in "discovery" mode (no token, no scanner id)
POSTs its public key + hostname to /api/scanners/discover. The api
inserts a row here, generates a short pairing code, and waits for
an admin to either approve (creating a real Scanner row) or deny.

Idempotency: a partial unique index on `key_fingerprint WHERE
status='pending'` lets the discover endpoint upsert when a scanner
is restarted between POSTs. Only one pending row per public key
exists at a time.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class ScannerDiscoveryRequest(Base):
    __tablename__ = "scanner_discovery_requests"
    __table_args__ = (
        # Lookup-by-id is implicit via the PK index. Ordered listing of
        # pending rows in the admin UI hits this.
        Index(
            "ix_discovery_status_requested_at",
            "status", "requested_at",
        ),
        # Partial unique guarantees one pending request per public key,
        # so a scanner that restarts mid-discovery upserts instead of
        # accumulating ghost rows.
        Index(
            "ix_discovery_pubkey_pending",
            "key_fingerprint",
            unique=True,
            postgresql_where="status = 'pending'",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    public_key_pem: Mapped[str] = mapped_column(Text, nullable=False)
    key_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    pairing_code: Mapped[str] = mapped_column(String(9), nullable=False)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    agent_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    requested_pool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending",
    )  # pending | approved | denied | expired
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    deny_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_scanner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scanners.id", ondelete="SET NULL"),
        nullable=True,
    )
