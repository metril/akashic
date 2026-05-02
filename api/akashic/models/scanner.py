"""Registered scanner agents.

Each row represents one akashic-scanner process running anywhere on
the network — it auths via Ed25519 signed JWT (private key on the
scanner host, public key here). The api never holds the private key
beyond the brief moment of issuance.

`pool` is a free-text label. Sources may opt to be claimed only by
scanners in a matching pool; an unset `Source.preferred_pool` means
any scanner can claim. See routers/scanners.py for the lease logic.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class Scanner(Base):
    __tablename__ = "scanners"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    pool: Mapped[str] = mapped_column(String, nullable=False, default="default")
    public_key_pem: Mapped[str] = mapped_column(Text, nullable=False)
    key_fingerprint: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    hostname: Mapped[str | None] = mapped_column(String, nullable=True)
    version: Mapped[str | None] = mapped_column(String, nullable=True)
    protocol_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Optional scope whitelists. NULL = unrestricted on this dimension.
    # Enforced inside lease_scan's WHERE clause; admins can edit via PATCH.
    allowed_source_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True,
    )
    allowed_scan_types: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(16)), nullable=True,
    )
