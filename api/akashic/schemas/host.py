import uuid
from datetime import datetime

from akashic.schemas.base import ApiModel
from pydantic import Field

from akashic.schemas.source import _scrub_config


# Scan-distribution controls (v0.35.0). A host value is inherited by
# every attached source that leaves its own field NULL. None on create
# = no host-level setting (the built-in default applies).
class HostCreate(ApiModel):
    name: str = Field(description="Unique host name.")
    type: str = Field(description="Host type: `smb`, `nfs` or `s3`. Local storage has no host.")
    connection_config: dict = Field(description="Type-specific connection settings shared by the attached sources, including any inline credentials. Secrets are masked in responses. Free-form: keys depend on the source or host type.", json_schema_extra={"x-free-form": True})
    credential_profile_id: uuid.UUID | None = Field(default=None, description="Credential profile to take credentials from; its type must match the host type.")
    max_parallel_scanners: int | None = Field(default=None, description="Most distinct scanners that may work on one scan at once (1 to 16), inherited by attached sources that leave theirs unset. Null uses the built-in default.")
    scan_chunk_size: int | None = Field(default=None, description="Entries per scan work unit (100 to 1,000,000), inherited by attached sources that leave theirs unset. Null uses the built-in default (2000).")

    model_config = {"json_schema_extra": {'examples': [{'name': 'fileserver01', 'type': 'smb', 'connection_config': {'host': 'fileserver01.example.com'}, 'credential_profile_id': '7c9e6679-7425-40de-944b-e07fc1f90ae7', 'max_parallel_scanners': 4, 'scan_chunk_size': 2000}]}}


class HostUpdate(ApiModel):
    name: str | None = Field(default=None, description="New unique host name.")
    connection_config: dict | None = Field(default=None, description="Connection settings to change. A masked value (`***`) keeps the stored secret. Free-form: keys depend on the source or host type.", json_schema_extra={"x-free-form": True})
    credential_profile_id: uuid.UUID | None = Field(default=None, description="Credential profile to use; null detaches the current one.")
    max_parallel_scanners: int | None = Field(default=None, description="New cap (1 to 16); null clears it so the built-in default applies.")
    scan_chunk_size: int | None = Field(default=None, description="New work-unit size (100 to 1,000,000); null clears it so the built-in default applies.")

    model_config = {"json_schema_extra": {'examples': [{'name': 'fileserver01-new', 'max_parallel_scanners': None}]}}


class HostResponse(ApiModel):
    id: uuid.UUID = Field(description="Host id.")
    name: str = Field(description="Host name.")
    type: str = Field(description="Host type: `smb`, `nfs` or `s3`.")
    connection_config: dict = Field(description="Connection settings with secrets masked as `***`. Free-form: keys depend on the source or host type.", json_schema_extra={"x-free-form": True})
    credential_profile_id: uuid.UUID | None = Field(default=None, description="Credential profile in use; null when none.")
    max_parallel_scanners: int | None = Field(default=None, description="Scanner cap for the host's sources; null means the built-in default.")
    scan_chunk_size: int | None = Field(default=None, description="Work-unit size for the host's sources; null means the built-in default.")
    source_count: int = Field(default=0, description="Number of sources attached to the host.")
    created_at: datetime = Field(description="When the host was created.")
    updated_at: datetime = Field(description="When the host was last changed.")

    model_config = {"from_attributes": True}

    def model_post_init(self, __context) -> None:
        # Mask secrets the same way SourceResponse does — the scrub
        # rules live in schemas/source.py and stay the single source
        # of truth for what counts as a secret-named key.
        self.connection_config = _scrub_config(self.connection_config)


class ListSharesResponse(ApiModel):
    """POST /api/v1/hosts/{id}/list-shares result.

    `shares` is the enumerated list (possibly empty) on success. On
    failure `step`/`error` carry the same step:reason classification
    used by the source-tester (`connect`, `auth`, `mount`, `list`,
    `config`); `shares` is empty in that case.
    """

    shares: list[str] = Field(default=[], description="Names of the shares, exports or buckets found; empty on failure.")
    step: str | None = Field(default=None, description="Step that failed (`connect`, `auth`, `mount`, `list` or `config`); null on success.")
    error: str | None = Field(default=None, description="Failure message; null on success.")


class AddSharesItem(ApiModel):
    """One row in an /add-shares batch."""

    name: str = Field(description="Name for the new source; must be unique across all sources.")
    share: str = Field(description="Share name (SMB), export path (NFS) or bucket (S3).")


class AddSharesRequest(ApiModel):
    shares: list[AddSharesItem] = Field(description="Shares to create as sources.")
    scan_schedule: str | None = Field(default=None, description="Cron expression applied to every created source; null for no schedule.")
    max_parallel_scanners: int | None = Field(default=None, description="Scanner cap (1 to 16) for every created source; null inherits from the host.")
    exclude_patterns: list[str] | None = Field(default=None, description="Path patterns to exclude from scans of every created source.")
    preferred_pool: str | None = Field(default=None, description="Restrict every created source to this scanner pool; null lets any scanner claim it.")
    is_removable: bool | None = Field(default=None, description="Whether the created sources are removable or external storage; null infers it from the host type.")

    model_config = {"json_schema_extra": {'examples': [{'shares': [{'name': 'fileserver01/finance', 'share': 'finance'}, {'name': 'fileserver01/hr', 'share': 'hr'}], 'scan_schedule': '0 2 * * *', 'preferred_pool': 'site-amsterdam'}]}}


class AddSharesResponse(ApiModel):
    created: int = Field(description="Number of sources created.")
    skipped: int = Field(description="Number of entries skipped because a source with that name already exists.")
    sources: list[uuid.UUID] = Field(description="Ids of the sources that were created.")


class HostSummary(ApiModel):
    """Inlined-on-source shape: name + type only.

    Inlining the full HostResponse (with credentials, even masked)
    everywhere a source is returned would be both heavy and
    information-leaky. Callers that need the full row fetch the
    host via /api/v1/hosts/{id}.
    """

    id: uuid.UUID
    name: str
    type: str

    model_config = {"from_attributes": True}
