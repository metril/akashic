"""Reusable named credential bundles.

A CredentialProfile carries the credential-only subset of a
connection_config (username, password, key material, etc.) and can
be referenced from any number of Hosts and Sources. The layered
resolver in `services/source_config.resolve_connection_config`
applies them under the inline values, so a profile is a default and
inline keys override it.

Type discriminator (`ssh`/`smb`/`s3`/`nfs`) keeps the picker UI from
offering an SMB profile for an SSH host — validated server-side on
write, not enforced at the schema level since types are open-ended.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class CredentialProfile(Base):
    __tablename__ = "credential_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    credentials: Mapped[dict] = mapped_column(JSONB, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(), onupdate=func.now(), nullable=False,
    )
