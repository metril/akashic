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


# ---- Discriminated union ----

ACL = Annotated[
    Union[PosixACL, NfsV4ACL, NtACL, S3ACL],
    Field(discriminator="type"),
]
