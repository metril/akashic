import uuid
from datetime import datetime

from pydantic import BaseModel

from akashic.schemas.source import _scrub_config


class HostCreate(BaseModel):
    name: str
    type: str  # "ssh" | "smb" | "nfs" | "s3" — local has no host
    connection_config: dict


class HostUpdate(BaseModel):
    name: str | None = None
    connection_config: dict | None = None


class HostResponse(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    connection_config: dict
    source_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    def model_post_init(self, __context) -> None:
        # Mask secrets the same way SourceResponse does — the scrub
        # rules live in schemas/source.py and stay the single source
        # of truth for what counts as a secret-named key.
        self.connection_config = _scrub_config(self.connection_config)


class HostSummary(BaseModel):
    """Inlined-on-source shape: name + type only.

    Inlining the full HostResponse (with credentials, even masked)
    everywhere a source is returned would be both heavy and
    information-leaky. Callers that need the full row fetch the
    host via /api/hosts/{id}.
    """

    id: uuid.UUID
    name: str
    type: str

    model_config = {"from_attributes": True}
