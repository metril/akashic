"""Response models for /access (admin blast-radius lookups)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from akashic.schemas.base import ApiModel
from pydantic import ConfigDict, Field

from akashic.schemas.directory import IdentityResult


class AccessPrincipal(ApiModel):
    """The principal that was looked up."""

    token: str = Field(description="Canonical principal token, e.g. `sid:S-1-5-...` or `posix:uid:1000`.")
    name: str | None = Field(description="Resolved name, when known.")
    domain: str | None = Field(description="Resolved domain, when known.")
    kind: str = Field(description="`user`, `group`, `wildcard` or `unknown`.")


class AccessSummary(ApiModel):
    """Totals across everything the principal can reach."""

    file_count: int = Field(description="Files reachable.")
    total_size_bytes: int = Field(description="Total bytes reachable.")
    source_count: int = Field(description="Sources containing reachable files.")


class AccessSourceRollup(ApiModel):
    """Reachable files within one source."""

    source_id: str = Field(description="Source id.")
    source_name: str = Field(description="Source display name.")
    file_count: int = Field(description="Reachable files in the source.")


class AccessSampleHit(ApiModel):
    """One reachable file from the search index."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(description="Entry id.")
    source_id: str = Field(description="Source id.")
    path: str = Field(description="Full path.")
    filename: str = Field(description="Base name.")
    size_bytes: int | None = Field(default=None, description="Size in bytes.")
    owner_name: str | None = Field(default=None, description="Owner name.")
    fs_modified_at: int | None = Field(default=None, description="Modified time, epoch seconds.")
    has_deny_ace: bool | None = Field(
        default=None,
        description="Whether the file's stored ACL contains any deny ACE (NT and NFSv4 ACLs only). Present on principal and identity lookups.")


class TokenUsed(ApiModel):
    """One token an identity lookup evaluated."""

    token: str = Field(description="Canonical principal token.")
    origin: Literal["ad", "authentik", "akashic_binding", "email", "builtin"] = Field(
        description="Where the token came from.")
    name: str | None = Field(default=None, description="Resolved name (SIDs only).")


class AccessExpansion(ApiModel):
    """How completely the identity's tokens were expanded."""

    status: Literal["ok", "cached", "partial", "failed"] = Field(
        description="`ok` fresh, `cached` older group data, `partial` group membership incomplete, `failed` no file-system identity.")
    cached_at: datetime | None = Field(description="Oldest successful directory read behind the tokens, when any.")
    detail: str | None = Field(description="Why the expansion is not `ok`, when it is not.")
    missing_identity_kinds: list[str] = Field(description="Token kinds the identity lacks: `sid`, `posix`.")


class PrincipalAccessResponse(ApiModel):
    """Principal-to-files lookup (`?principal=`)."""

    principal: AccessPrincipal = Field(description="The looked-up principal.")
    right: str = Field(description="`read`, `write` or `delete`.")
    summary: AccessSummary = Field(description="Totals.")
    by_source: list[AccessSourceRollup] = Field(description="Per-source rollup, largest first.")
    sample: list[AccessSampleHit] = Field(description="One page of reachable files.")
    next_cursor: str | None = Field(description="Opaque cursor for the next page of `sample`; null at the end.")
    limit: int = Field(description="Page size requested.")
    identity: IdentityResult | None = Field(default=None, description="The merged identity looked up (`?identity=` only).")
    tokens_used: list[TokenUsed] | None = Field(default=None, description="Every token evaluated, with its origin (`?identity=` only).")
    expansion: AccessExpansion | None = Field(default=None, description="Token expansion state (`?identity=` only).")
    warnings: list[str] = Field(default_factory=list, description="Caveats about the results, e.g. deny ACEs not evaluated across an identity's tokens.")


class FilePrincipal(ApiModel):
    """A principal holding the right on a file."""

    token: str = Field(description="Canonical principal token.")
    kind: str = Field(description="`user`, `group`, `wildcard` or `unknown`.")
    source: str = Field(description="Where the grant comes from (`acl`).")
    name: str | None = Field(default=None, description="Resolved name (SIDs only).")
    domain: str | None = Field(default=None, description="Resolved domain (SIDs only).")


class FileAccessResponse(ApiModel):
    """File-to-principals lookup (`?file=`)."""

    entry_id: uuid.UUID = Field(description="Entry id.")
    path: str = Field(description="Full path.")
    filename: str = Field(description="Base name.")
    right: str = Field(description="`read`, `write` or `delete`.")
    principals: list[FilePrincipal] = Field(description="Principals that hold the right.")
