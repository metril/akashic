import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ACLEntry(BaseModel):
    tag: str  # user, group, mask, other, user_obj, group_obj
    qualifier: str = ""  # username/groupname when applicable
    perms: str  # rwx style


class EntryIn(BaseModel):
    """Inbound from the scanner; one row per file/directory observed in a scan."""

    path: str
    name: str
    kind: Literal["file", "directory"] = "file"

    # File-only
    extension: str | None = None
    size_bytes: int | None = None
    mime_type: str | None = None
    content_hash: str | None = None

    # Permissions
    mode: int | None = None
    uid: int | None = None
    gid: int | None = None
    owner_name: str | None = None
    group_name: str | None = None
    acl: list[ACLEntry] | None = None
    xattrs: dict[str, str] | None = None

    # Filesystem timestamps
    fs_created_at: datetime | None = None
    fs_modified_at: datetime | None = None
    fs_accessed_at: datetime | None = None


class EntryResponse(BaseModel):
    """Compact entry shape for list responses."""

    id: uuid.UUID
    source_id: uuid.UUID
    kind: str
    parent_path: str
    path: str
    name: str
    extension: str | None
    size_bytes: int | None
    mime_type: str | None
    content_hash: str | None
    mode: int | None
    uid: int | None
    gid: int | None
    owner_name: str | None
    group_name: str | None
    fs_modified_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    is_deleted: bool

    model_config = {"from_attributes": True}


class EntryVersionResponse(BaseModel):
    id: uuid.UUID
    entry_id: uuid.UUID
    scan_id: uuid.UUID | None
    content_hash: str | None
    size_bytes: int | None
    mode: int | None
    uid: int | None
    gid: int | None
    owner_name: str | None
    group_name: str | None
    acl: list[ACLEntry] | None
    xattrs: dict[str, str] | None
    detected_at: datetime

    model_config = {"from_attributes": True}


class EntryDetailResponse(EntryResponse):
    """Full entry detail; includes ACL, xattrs, version history."""

    acl: list[ACLEntry] | None = None
    xattrs: dict[str, str] | None = None
    fs_created_at: datetime | None = None
    fs_accessed_at: datetime | None = None
    versions: list[EntryVersionResponse] = Field(default_factory=list)


class BrowseEntry(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    path: str
    extension: str | None = None
    size_bytes: int | None = None
    mime_type: str | None = None
    content_hash: str | None = None
    mode: int | None = None
    owner_name: str | None = None
    group_name: str | None = None
    fs_modified_at: datetime | None = None
    child_count: int | None = None  # populated for directories


class BrowseResponse(BaseModel):
    source_id: uuid.UUID
    source_name: str
    path: str
    parent_path: str | None  # None when at root
    is_root: bool
    entries: list[BrowseEntry]
