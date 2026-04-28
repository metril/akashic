"""Schemas for the /api/identities CRUD endpoints."""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


IdentityType = Literal["posix_uid", "sid", "nfsv4_principal", "s3_canonical"]
GroupsSource = Literal["manual", "auto"]


class FsBindingIn(BaseModel):
    source_id: uuid.UUID
    identity_type: IdentityType
    identifier: str
    groups: list[str] = Field(default_factory=list)

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


class FsBindingPatch(BaseModel):
    identity_type: IdentityType | None = None
    identifier: str | None = None
    groups: list[str] | None = None
    groups_source: GroupsSource | None = None  # caller can pin to 'manual'

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


class FsBindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    fs_person_id: uuid.UUID
    source_id: uuid.UUID
    identity_type: IdentityType
    identifier: str
    groups: list[str]
    groups_source: GroupsSource
    groups_resolved_at: datetime | None
    created_at: datetime


class FsPersonIn(BaseModel):
    label: str
    is_primary: bool = False


class FsPersonPatch(BaseModel):
    label: str | None = None
    is_primary: bool | None = None


class FsPersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    label: str
    is_primary: bool
    created_at: datetime
    bindings: list[FsBindingOut] = Field(default_factory=list)
