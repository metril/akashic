"""Discriminated-union ACL schemas — one shape per ACL model."""
from typing import Annotated, Literal, Union

from akashic.schemas.base import ApiModel
from pydantic import Field, field_validator


# ---- POSIX ----

class PosixACE(ApiModel):
    tag: str = Field(description="POSIX ACL tag, e.g. `user_obj`, `user`, `group_obj`, `group`, `mask` or `other`.")
    qualifier: str = Field(default="", description="Uid or gid the entry applies to; empty for the owner, owning group, mask and other.")
    perms: str = Field(description="Three characters from `rwx-`, e.g. `r-x`.")

    @field_validator("perms")
    @classmethod
    def _check_perms(cls, v: str) -> str:
        if len(v) != 3 or any(c not in "rwx-" for c in v):
            raise ValueError(f"perms must be 3 chars of rwx-, got {v!r}")
        return v


class PosixACL(ApiModel):
    type: Literal["posix"] = Field(description="Discriminator, always `posix`.")
    entries: list[PosixACE] = Field(description="Access ACL entries.")
    default_entries: list[PosixACE] | None = Field(default=None, description="Default ACL entries inherited by new children of a directory; null when none.")


# ---- NFSv4 ----

class NfsV4ACE(ApiModel):
    principal: str = Field(description="Principal, e.g. `alice@example.com`, `GROUP:staff@example.com` or `EVERYONE@`.")
    ace_type: Literal["allow", "deny", "audit", "alarm"] = Field(description="`allow`, `deny`, `audit` or `alarm`.")
    flags: list[str] = Field(default_factory=list, description="Flags on the entry, e.g. `identifier_group` for a group principal.")
    mask: list[str] = Field(default_factory=list, description="Permission names granted or denied, e.g. `READ_DATA`, `WRITE_DATA`.")


class NfsV4ACL(ApiModel):
    type: Literal["nfsv4"] = Field(description="Discriminator, always `nfsv4`.")
    entries: list[NfsV4ACE] = Field(description="ACL entries in evaluation order.")


# ---- NT (CIFS) ----

class NtPrincipal(ApiModel):
    sid: str = Field(description="Security identifier, e.g. `S-1-5-21-...`.")
    name: str = Field(default="", description="Resolved account name, when known; empty otherwise.")


class NtACE(ApiModel):
    sid: str = Field(description="Security identifier the entry applies to.")
    name: str = Field(default="", description="Resolved account name, when known; empty otherwise.")
    ace_type: Literal["allow", "deny", "audit"] = Field(description="`allow`, `deny` or `audit`.")
    flags: list[str] = Field(default_factory=list, description="Inheritance and audit flags on the entry, as reported by the scanner.")
    mask: list[str] = Field(default_factory=list, description="Access right names granted or denied, e.g. `READ_DATA`.")


class NtACL(ApiModel):
    type: Literal["nt"] = Field(description="Discriminator, always `nt`.")
    owner: NtPrincipal | None = Field(default=None, description="Owner of the object; null when not reported.")
    group: NtPrincipal | None = Field(default=None, description="Primary group of the object; null when not reported.")
    control: list[str] = Field(default_factory=list, description="Security descriptor control flags, e.g. `owner_defaulted`.")
    entries: list[NtACE] = Field(description="ACL entries in evaluation order.")


# ---- S3 ----

class S3Owner(ApiModel):
    id: str = Field(description="Canonical user id of the owner.")
    display_name: str = Field(default="", description="Owner display name; empty when not reported.")


class S3Grant(ApiModel):
    grantee_type: Literal["canonical_user", "group", "amazon_customer_by_email"] = Field(description="`canonical_user`, `group` or `amazon_customer_by_email`.")
    grantee_id: str = Field(default="", description="Canonical user id or group URI; empty when not applicable.")
    grantee_name: str = Field(default="", description="Display name or email address; empty when not reported.")
    permission: Literal["FULL_CONTROL", "READ", "WRITE", "READ_ACP", "WRITE_ACP"] = Field(description="`FULL_CONTROL`, `READ`, `WRITE`, `READ_ACP` or `WRITE_ACP`.")


class S3ACL(ApiModel):
    type: Literal["s3"] = Field(description="Discriminator, always `s3`.")
    owner: S3Owner | None = Field(default=None, description="Object owner; null when not reported.")
    grants: list[S3Grant] = Field(default_factory=list, description="Grants on the object.")


# ---- Cloud drive (Google Drive / OneDrive / Dropbox) ----

class CloudDrivePrincipal(ApiModel):
    """One principal in a cloud-drive grant. ``id`` is whatever the provider
    uses to identify that principal (Drive permissionId, Microsoft Graph
    id, Dropbox account_id) — the connector keeps the raw value so we
    can call back into the provider when needed.

    ``email`` and ``name`` are present when the provider returns them with
    the grant, so the UI doesn't need a per-grant lookup. Anonymous
    "anyone with the link" grants come through as ``type='anyone'`` with
    no email/name set.
    """

    type: Literal["user", "group", "anyone", "domain"] = Field(description="`user`, `group`, `anyone` (public link) or `domain`.")
    id: str = Field(description="Provider's identifier for the principal (Drive permission id, Microsoft Graph id or Dropbox account id).")
    email: str | None = Field(default=None, description="Email address, when the provider returns one.")
    name: str | None = Field(default=None, description="Display name, when the provider returns one.")


class CloudDriveLink(ApiModel):
    """The shareable link an "anyone-with-link" grant rides on. Populated
    only when the provider exposes link metadata (Drive does; Dropbox
    surfaces it as a separate sharing/list_shared_links result we fold in
    here). ``scope`` mirrors Drive semantics: anyone unrestricted,
    domain-only, or restricted (named principals only)."""

    id: str = Field(description="Provider's identifier for the shared link.")
    scope: Literal["anyone", "domain", "restricted"] = Field(description="Who can use the link: `anyone`, `domain` or `restricted` (named principals only).")


class CloudDriveGrant(ApiModel):
    """One ``(principal, role)`` grant. ``inherited`` flags whether the
    grant comes from an ancestor folder rather than this entry directly;
    ``inherited_from`` lets the UI render "inherited from /Foo/Bar" the
    way the existing tag system does."""

    principal: CloudDrivePrincipal = Field(description="Who the grant is for.")
    role: Literal["owner", "writer", "commenter", "reader", "file_organizer"] = Field(description="`owner`, `writer`, `commenter`, `reader` or `file_organizer`.")
    link: CloudDriveLink | None = Field(default=None, description="The shared link the grant rides on, for link-based grants; null otherwise.")
    inherited: bool = Field(default=False, description="True when the grant comes from an ancestor folder.")
    inherited_from_id: str | None = Field(default=None, description="Provider id of the ancestor the grant is inherited from.")
    inherited_from_path: str | None = Field(default=None, description="Path of the ancestor the grant is inherited from.")


class CloudDriveACL(ApiModel):
    """Cloud-drive ACL — fifth discriminator alongside posix/nfsv4/nt/s3.

    Fundamentally a list of per-principal grants rather than a POSIX-style
    owner+mode+ACE. Inheritance is per-grant rather than tree-wide, and
    "anyone with link" is a first-class principal type.
    """

    type: Literal["cloud_drive"] = Field(description="Discriminator, always `cloud_drive`.")
    grants: list[CloudDriveGrant] = Field(default_factory=list, description="Per-principal grants on the entry.")
    # When set, sharing is constrained to this domain (Drive's
    # domainAdminPolicy); the UI surfaces this as a banner so admins
    # know the deployment-level boundary applied at scan time.
    domain_restricted_to: str | None = Field(default=None, description="Domain that sharing is restricted to, when the provider enforces such a boundary; null otherwise.")


# ---- Discriminated union ----

ACL = Annotated[
    Union[PosixACL, NfsV4ACL, NtACL, S3ACL, CloudDriveACL],
    Field(discriminator="type"),
]
