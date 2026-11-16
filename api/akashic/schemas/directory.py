"""Models for the directory (AD / Authentik) identity picker endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from akashic.schemas.base import ApiModel
from pydantic import ConfigDict, Field


class IdentityBackendRef(ApiModel):
    """One backend record merged into an identity."""

    backend: Literal["ad", "authentik", "akashic"] = Field(description="Backend that holds the record.")
    ref: str = Field(description="Backend-specific record reference (AD objectGUID hex, Authentik pk, Akashic user id).")


class IdentityResult(ApiModel):
    """A person or group merged across AD, Authentik and Akashic users."""

    id: str = Field(description="Stable merge id: `sid:<SID>`, `dn:<dn>`, `email:<email>` or `<backend>:<ref>`.")
    kind: Literal["person", "group"] = Field(description="`person` or `group`.")
    display_name: str | None = Field(description="Display name, when known.")
    username: str | None = Field(description="Login name, when known.")
    email: str | None = Field(description="Email address, when known.")
    sid: str | None = Field(description="Windows SID (lowest of the merged records), when known.")
    dn: str | None = Field(description="LDAP distinguished name, when known.")
    backends: list[IdentityBackendRef] = Field(description="The backend records merged into this identity.")
    match_quality: Literal["exact", "possible"] = Field(
        description="`possible` when only the username matches another result; such results are never merged.")
    possible_match_of: list[str] = Field(description="Ids of the results this one possibly matches.")
    akashic_user_id: uuid.UUID | None = Field(description="Akashic user id, when the identity has one.")
    groups_synced_at: datetime | None = Field(
        description="Oldest successful group read among the identity's directory records, when any.")
    has_fs_identity: bool = Field(
        description="Whether the identity carries a SID, POSIX id or file-system binding usable in access lookups.")


class BackendStatus(ApiModel):
    """Availability of one identity backend."""

    backend: Literal["ad", "authentik", "akashic"] = Field(description="Backend name.")
    status: Literal["ok", "unavailable", "not_configured"] = Field(description="Backend availability.")
    detail: str | None = Field(description="Why the backend is unavailable, when it is.")


class IdentitySearchResponse(ApiModel):
    """Identity search results and the state of each backend."""

    results: list[IdentityResult] = Field(description="Matching identities, best match first.")
    backends_status: list[BackendStatus] = Field(description="Availability of every backend.")


class IdentityRefreshRequest(ApiModel):
    """Identity to re-read live from its backends."""

    id: str = Field(min_length=1, max_length=1000, description="Merge id of the identity, as returned by search.")


class DirectorySyncRunOut(ApiModel):
    """One directory sync run for one backend."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="Run id.")
    backend: Literal["ad", "authentik"] = Field(description="Backend synced.")
    status: Literal["running", "succeeded", "failed"] = Field(description="Run state.")
    trigger: Literal["scheduled", "manual"] = Field(description="What started the run.")
    started_at: datetime = Field(description="When the run started.")
    finished_at: datetime | None = Field(description="When the run ended; null while running.")
    counts: dict[str, int] = Field(description="Run counters: `users`, `groups`, `removed`, `groups_failed`; empty until the run ends.")
    error: str | None = Field(description="Redacted failure reason, when the run failed.")


class DirectorySyncStarted(ApiModel):
    """The sync runs that were started."""

    runs: list[DirectorySyncRunOut] = Field(description="One run per configured backend.")


class DirectoryBackendState(ApiModel):
    """Sync state of one directory backend."""

    backend: Literal["ad", "authentik"] = Field(description="Backend name.")
    configured: bool = Field(description="Whether the backend has the settings it needs.")
    running: bool = Field(description="Whether a sync is in progress.")
    last_run: DirectorySyncRunOut | None = Field(description="Most recent run, when any.")
    last_success_at: datetime | None = Field(description="When the most recent successful run finished.")


class DirectoryStatusResponse(ApiModel):
    """Directory sync state and schedule."""

    backends: list[DirectoryBackendState] = Field(description="State of each directory backend.")
    interval_minutes: int = Field(description="Scheduled sync interval in minutes; 0 means manual only.")
    next_run_at: datetime | None = Field(
        description="Latest run start plus the interval, or now when nothing has run yet; null when the schedule is manual-only.")
