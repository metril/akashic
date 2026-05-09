"""Discriminated-union ACL schemas — one shape per ACL model."""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, field_validator


# ---- POSIX ----

class PosixACE(BaseModel):
    tag: str
    qualifier: str = ""
    perms: str

    @field_validator("perms")
    @classmethod
    def _check_perms(cls, v: str) -> str:
        if len(v) != 3 or any(c not in "rwx-" for c in v):
            raise ValueError(f"perms must be 3 chars of rwx-, got {v!r}")
        return v


class PosixACL(BaseModel):
    type: Literal["posix"]
    entries: list[PosixACE]
    default_entries: list[PosixACE] | None = None


# ---- NFSv4 ----

class NfsV4ACE(BaseModel):
    principal: str
    ace_type: Literal["allow", "deny", "audit", "alarm"]
    flags: list[str] = Field(default_factory=list)
    mask: list[str] = Field(default_factory=list)


class NfsV4ACL(BaseModel):
    type: Literal["nfsv4"]
    entries: list[NfsV4ACE]


# ---- NT (CIFS) ----

class NtPrincipal(BaseModel):
    sid: str
    name: str = ""


class NtACE(BaseModel):
    sid: str
    name: str = ""
    ace_type: Literal["allow", "deny", "audit"]
    flags: list[str] = Field(default_factory=list)
    mask: list[str] = Field(default_factory=list)


class NtACL(BaseModel):
    type: Literal["nt"]
    owner: NtPrincipal | None = None
    group: NtPrincipal | None = None
    control: list[str] = Field(default_factory=list)
    entries: list[NtACE]


# ---- S3 ----

class S3Owner(BaseModel):
    id: str
    display_name: str = ""


class S3Grant(BaseModel):
    grantee_type: Literal["canonical_user", "group", "amazon_customer_by_email"]
    grantee_id: str = ""
    grantee_name: str = ""
    permission: Literal["FULL_CONTROL", "READ", "WRITE", "READ_ACP", "WRITE_ACP"]


class S3ACL(BaseModel):
    type: Literal["s3"]
    owner: S3Owner | None = None
    grants: list[S3Grant] = Field(default_factory=list)


# ---- Cloud drive (Google Drive / OneDrive / Dropbox) ----

class CloudDrivePrincipal(BaseModel):
    """One principal in a cloud-drive grant. ``id`` is whatever the provider
    uses to identify that principal (Drive permissionId, Microsoft Graph
    id, Dropbox account_id) — the connector keeps the raw value so we
    can call back into the provider when needed.

    ``email`` and ``name`` are present when the provider returns them with
    the grant, so the UI doesn't need a per-grant lookup. Anonymous
    "anyone with the link" grants come through as ``type='anyone'`` with
    no email/name set.
    """

    type: Literal["user", "group", "anyone", "domain"]
    id: str
    email: str | None = None
    name: str | None = None


class CloudDriveLink(BaseModel):
    """The shareable link an "anyone-with-link" grant rides on. Populated
    only when the provider exposes link metadata (Drive does; Dropbox
    surfaces it as a separate sharing/list_shared_links result we fold in
    here). ``scope`` mirrors Drive semantics: anyone unrestricted,
    domain-only, or restricted (named principals only)."""

    id: str
    scope: Literal["anyone", "domain", "restricted"]


class CloudDriveGrant(BaseModel):
    """One ``(principal, role)`` grant. ``inherited`` flags whether the
    grant comes from an ancestor folder rather than this entry directly;
    ``inherited_from`` lets the UI render "inherited from /Foo/Bar" the
    way the existing tag system does."""

    principal: CloudDrivePrincipal
    role: Literal["owner", "writer", "commenter", "reader", "file_organizer"]
    link: CloudDriveLink | None = None
    inherited: bool = False
    inherited_from_id: str | None = None
    inherited_from_path: str | None = None


class CloudDriveACL(BaseModel):
    """Cloud-drive ACL — fifth discriminator alongside posix/nfsv4/nt/s3.

    Fundamentally a list of per-principal grants rather than a POSIX-style
    owner+mode+ACE. Inheritance is per-grant rather than tree-wide, and
    "anyone with link" is a first-class principal type.
    """

    type: Literal["cloud_drive"]
    grants: list[CloudDriveGrant] = Field(default_factory=list)
    # When set, sharing is constrained to this domain (Drive's
    # domainAdminPolicy); the UI surfaces this as a banner so admins
    # know the deployment-level boundary applied at scan time.
    domain_restricted_to: str | None = None


# ---- Discriminated union ----

ACL = Annotated[
    Union[PosixACL, NfsV4ACL, NtACL, S3ACL, CloudDriveACL],
    Field(discriminator="type"),
]
