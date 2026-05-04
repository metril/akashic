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
    # v0.5.6 reachability — same shape as Source, plus a roll-up.
    is_reachable: bool | None = None
    last_reachable_at: datetime | None = None
    last_reachability_check_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    def model_post_init(self, __context) -> None:
        # Mask secrets the same way SourceResponse does — the scrub
        # rules live in schemas/source.py and stay the single source
        # of truth for what counts as a secret-named key.
        self.connection_config = _scrub_config(self.connection_config)


class ListSharesResponse(BaseModel):
    """POST /api/hosts/{id}/list-shares result.

    `shares` is the enumerated list (possibly empty) on success. On
    failure `step`/`error` carry the same step:reason classification
    used by the source-tester (`connect`, `auth`, `mount`, `list`,
    `config`); `shares` is empty in that case.
    """

    shares: list[str] = []
    step: str | None = None
    error: str | None = None


class AddSharesItem(BaseModel):
    """One row in an /add-shares batch."""

    name: str          # Source.name (must be unique across all sources)
    share: str         # The path/share/export_path/bucket value


class AddSharesRequest(BaseModel):
    shares: list[AddSharesItem]
    scan_schedule: str | None = None
    max_parallel_scanners: int | None = None
    exclude_patterns: list[str] | None = None
    preferred_pool: str | None = None
    is_removable: bool | None = None


class AddSharesResponse(BaseModel):
    created: int
    skipped: int  # rows whose name already existed (unique-name conflict)
    sources: list[uuid.UUID]  # ids of the rows actually created


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
