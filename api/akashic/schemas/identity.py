"""Schemas for the /api/v1/identities CRUD endpoints."""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


IdentityType = Literal["posix_uid", "sid", "nfsv4_principal", "s3_canonical"]
GroupsSource = Literal["manual", "auto"]


class FsBindingIn(BaseModel):
    source_id: uuid.UUID = Field(description="Source the identity is bound on.")
    identity_type: IdentityType = Field(description="Kind of identifier: `posix_uid`, `sid`, `nfsv4_principal` or `s3_canonical`.")
    identifier: str = Field(description="The identifier itself (a uid, a SID, ...). Surrounding whitespace is trimmed; it must not be empty.")
    groups: list[str] = Field(default_factory=list, description="Identifiers of the groups the principal belongs to. Blank entries are dropped.")

    @field_validator("identifier")
    @classmethod
    def _strip_identifier(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("identifier must not be empty")
        return v

    @field_validator("groups")
    @classmethod
    def _strip_groups(cls, v: list[str]) -> list[str]:
        return [g.strip() for g in v if g.strip()]

    model_config = {"json_schema_extra": {'examples': [{'source_id': '0d4a9a0e-2b1c-4c5e-8f6d-1a2b3c4d5e6f', 'identity_type': 'sid', 'identifier': 'S-1-5-21-1111111111-2222222222-3333333333-1001', 'groups': ['S-1-5-21-1111111111-2222222222-3333333333-513']}]}}


class FsBindingPatch(BaseModel):
    identity_type: IdentityType | None = Field(default=None, description="New identifier kind.")
    identifier: str | None = Field(default=None, description="New identifier; must not be blank.")
    groups: list[str] | None = Field(default=None, description="New group identifiers, replacing the current ones.")
    groups_source: GroupsSource | None = Field(default=None, description="Where the groups came from: `manual` or `auto`. Only callers holding `users:write` may set it.")

    @field_validator("identifier")
    @classmethod
    def _strip_identifier(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("identifier must not be empty")
        return v

    @field_validator("groups")
    @classmethod
    def _strip_groups(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        return [g.strip() for g in v if g.strip()]

    model_config = {"json_schema_extra": {'examples': [{'groups': ['S-1-5-21-1111111111-2222222222-3333333333-513']}]}}


class FsBindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="Binding id.")
    fs_person_id: uuid.UUID = Field(description="Identity (person) the binding belongs to.")
    source_id: uuid.UUID = Field(description="Source the binding applies to.")
    identity_type: IdentityType = Field(description="Kind of identifier: `posix_uid`, `sid`, `nfsv4_principal` or `s3_canonical`.")
    identifier: str = Field(description="The identifier (a uid, a SID, ...).")
    groups: list[str] = Field(description="Identifiers of the groups the principal belongs to.")
    groups_source: GroupsSource = Field(description="Where the groups came from: `manual` (entered by hand) or `auto` (resolved from the backend).")
    groups_resolved_at: datetime | None = Field(description="When the groups were last resolved automatically; null if never.")
    created_at: datetime = Field(description="When the binding was created.")


class FsPersonIn(BaseModel):
    label: str = Field(description="Display label for the identity.")
    is_primary: bool = Field(default=False, description="Whether this is the user's primary identity.")

    model_config = {"json_schema_extra": {'examples': [{'label': 'Work account', 'is_primary': True}]}}


class FsPersonPatch(BaseModel):
    label: str | None = Field(default=None, description="New display label.")
    is_primary: bool | None = Field(default=None, description="New primary flag.")


class FsPersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="Identity id.")
    user_id: uuid.UUID = Field(description="User who owns the identity.")
    label: str = Field(description="Display label.")
    is_primary: bool = Field(description="Whether this is the user's primary identity.")
    created_at: datetime = Field(description="When the identity was created.")
    bindings: list[FsBindingOut] = Field(default_factory=list, description="The identity's per-source bindings.")
