import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, DateTime, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from akashic.database import Base


class Host(Base):
    """A reusable connection target.

    A single host (NAS, SMB server, S3 account) often exposes
    several shares. Hosts own the host-shaped fields (hostname, port,
    credentials, key material) so a credential rotation only touches one
    row, and a new share is just a new ``Source`` row pointing at the
    same ``host_id``.

    Local sources don't have a host (the API container's filesystem is
    the "host"); their ``Source.host_id`` stays NULL.
    """

    __tablename__ = "hosts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    # Host-only connection fields per type:
    #   smb: host, port, username, password, domain
    #   nfs: host, port, auth_method, krb5_*, auth_uid, auth_gid, auth_aux_gids
    #   s3:  endpoint, region, access_key_id, secret_access_key
    connection_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # v0.35.0 — scan-distribution controls inherited by every attached
    # source. NULL = no host-level setting; the built-in default applies
    # unless the source pins its own value. See
    # services/source_config.effective_max_parallel_scanners /
    # effective_chunk_size for the source ?? host ?? default resolution.
    max_parallel_scanners: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scan_chunk_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # v0.5.9 — optional reference to a reusable CredentialProfile.
    # NULL = inline credentials only; non-NULL = profile contributes
    # under inline values via resolve_connection_config.
    credential_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credential_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    credential_profile = relationship(
        "CredentialProfile", lazy="joined", foreign_keys=[credential_profile_id],
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
