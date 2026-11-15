"""Schemas for the effective-permissions endpoint."""
from typing import Literal

from akashic.schemas.base import ApiModel
from pydantic import Field


PrincipalType = Literal[
    "posix_uid",
    "sid",
    "nfsv4_principal",
    "s3_canonical",
    "cloud_drive_user",
]
RightName = Literal["read", "write", "execute", "delete", "change_perms"]


class PrincipalRef(ApiModel):
    type: PrincipalType = Field(description="Identity type: `posix_uid`, `sid`, `nfsv4_principal`, `s3_canonical` or `cloud_drive_user`.")
    identifier: str = Field(description="Identifier of that type, e.g. a uid, a SID or an NFSv4 principal.")
    name: str = Field(default="", description="Display name; informational only.")


class GroupRef(ApiModel):
    type: PrincipalType = Field(description="Identity type: `posix_uid`, `sid`, `nfsv4_principal`, `s3_canonical` or `cloud_drive_user`.")
    identifier: str = Field(description="Identifier of that type, e.g. a gid or a group SID.")
    name: str = Field(default="", description="Display name; informational only.")


class ACEReference(ApiModel):
    ace_index: int = Field(description="Position of the ACL entry that decided the result; -1 for a synthetic reason such as the base mode bits.")
    summary: str = Field(description="One-line, human-readable description of that entry.")


class RightResult(ApiModel):
    granted: bool = Field(description="Whether the right is granted.")
    by: list[ACEReference] = Field(default_factory=list, description="The ACL entries responsible for the decision.")


class EffectivePermsEvaluatedWith(ApiModel):
    model: Literal["posix", "nfsv4", "nt", "s3", "cloud_drive", "none"] = Field(description="Permission model used: `posix`, `nfsv4`, `nt`, `s3`, `cloud_drive`, or `none` when the entry has no usable permissions.")
    principal: PrincipalRef = Field(description="The principal that was evaluated.")
    groups: list[GroupRef] = Field(default_factory=list, description="The groups taken into account.")
    caveats: list[str] = Field(default_factory=list, description="Limits of the evaluation that may make the result differ from what the storage system enforces.")


class EffectivePerms(ApiModel):
    rights: dict[RightName, RightResult] = Field(description="Result per right: `read`, `write`, `execute`, `delete`, `change_perms`.")
    evaluated_with: EffectivePermsEvaluatedWith = Field(description="What the evaluation used.")


class EffectivePermsRequest(ApiModel):
    principal: PrincipalRef = Field(description="The principal to evaluate.")
    groups: list[GroupRef] = Field(default_factory=list, description="Groups the principal belongs to; they are not looked up for you.")
    principal_name_hint: str = Field(default="", description="Optional display name for the principal, used only to improve messages.")

    model_config = {"json_schema_extra": {'examples': [{'principal': {'type': 'posix_uid', 'identifier': '1000', 'name': 'alice'}, 'groups': [{'type': 'posix_uid', 'identifier': '100', 'name': 'staff'}]}]}}
