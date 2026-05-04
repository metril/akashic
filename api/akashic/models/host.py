import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class Host(Base):
    """A reusable connection target.

    A single host (NAS, SMB server, SSH box, S3 account) often exposes
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
    #   ssh: host, port, username, password, key_path, key_passphrase, known_hosts_path
    #   smb: host, port, username, password, domain
    #   nfs: host, port, auth_method, krb5_*, auth_uid, auth_gid, auth_aux_gids
    #   s3:  endpoint, region, access_key_id, secret_access_key
    connection_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
