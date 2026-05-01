"""Identities pulled from an OIDC token's claims that didn't match any
source's principal_domain at login time.

Surfaced in Settings → Identities so an admin can see "this user has a
SID claim S-1-5-... but no source uses that domain prefix" and either
add the domain to a source, or attach the identity by hand.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String

from akashic.database import Base


class FsUnboundIdentity(Base):
    __tablename__ = "fs_unbound_identities"
    __table_args__ = (
        # Same identity-from-the-same-IdP-on-the-same-user shouldn't
        # double-up; we re-write rather than dup.
        UniqueConstraint(
            "user_id", "identity_type", "identifier",
            name="uq_fs_unbound_user_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 'sid' | 'posix_uid' | 'nfsv4_principal'. Mirrors the FsBinding vocabulary.
    identity_type: Mapped[str] = mapped_column(String, nullable=False)
    identifier: Mapped[str] = mapped_column(String, nullable=False)
    # 'claim' | 'ldap' | 'name' — which strategy produced this identity
    # before we discovered it doesn't match any source.
    confidence: Mapped[str] = mapped_column(String, nullable=False, default="claim")
    # Group identifiers in the same vocabulary, if known. Stored so an
    # admin attaching the identity to a source doesn't have to re-fetch.
    groups: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
