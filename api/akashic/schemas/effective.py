"""Schemas for the effective-permissions endpoint."""
from typing import Literal

from pydantic import BaseModel, Field


PrincipalType = Literal["posix_uid", "sid", "nfsv4_principal", "s3_canonical"]
RightName = Literal["read", "write", "execute", "delete", "change_perms"]


class PrincipalRef(BaseModel):
    type: PrincipalType
    identifier: str
    name: str = ""


class GroupRef(BaseModel):
    type: PrincipalType
    identifier: str
    name: str = ""


class ACEReference(BaseModel):
    ace_index: int  # -1 means "synthetic" (e.g. POSIX base mode owner perms)
    summary: str   # human-readable one-liner describing the ACE


class RightResult(BaseModel):
    granted: bool
    by: list[ACEReference] = Field(default_factory=list)


class EffectivePermsEvaluatedWith(BaseModel):
    model: Literal["posix", "nfsv4", "nt", "s3", "none"]
    principal: PrincipalRef
    groups: list[GroupRef] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class EffectivePerms(BaseModel):
    rights: dict[RightName, RightResult]
    evaluated_with: EffectivePermsEvaluatedWith


class EffectivePermsRequest(BaseModel):
    principal: PrincipalRef
    groups: list[GroupRef] = Field(default_factory=list)
    principal_name_hint: str = ""
