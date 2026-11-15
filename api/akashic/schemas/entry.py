import uuid
from datetime import datetime
from typing import Literal

from akashic.schemas.base import ApiModel
from pydantic import Field

from akashic.schemas.acl import ACL
from akashic.schemas.common import Page
from akashic.schemas.source import SourceSecurityMetadata


class _EntrySourceRef(ApiModel):
    id: uuid.UUID = Field(description="Source id.")
    name: str = Field(description="Source name.")
    type: str = Field(description="Source type, e.g. `local`, `smb`, `nfs`, `s3`.")
    security_metadata: SourceSecurityMetadata | None = Field(default=None, description="Source-level security data used when evaluating permissions (S3 sources only); null when none.")

    model_config = {"from_attributes": True}


class EntryIn(ApiModel):
    """Inbound from the scanner; one row per file/directory observed in a scan."""

    path: str = Field(description="Full path within the source, starting with `/`.")
    name: str = Field(description="Base name.")
    kind: Literal["file", "directory"] = Field(default="file", description="`file` or `directory`.")

    extension: str | None = Field(default=None, description="File extension without the dot; null for directories and files without one.")
    size_bytes: int | None = Field(default=None, description="Size in bytes; null when unknown.")
    mime_type: str | None = Field(default=None, description="Detected MIME type; null when not detected.")
    content_hash: str | None = Field(default=None, description="Content hash. Files with identical content share it; null until computed.")

    mode: int | None = Field(default=None, description="POSIX mode bits (file type and permissions) as an integer; null where the connector reports none.")
    uid: int | None = Field(default=None, description="Numeric owner id on POSIX storage; null elsewhere.")
    gid: int | None = Field(default=None, description="Numeric group id on POSIX storage; null elsewhere.")
    owner_name: str | None = Field(default=None, description="Owner name or SID as reported by the storage system.")
    group_name: str | None = Field(default=None, description="Group name or SID as reported by the storage system.")
    acl: ACL | None = Field(default=None, description="Access control list as read from the storage system; null when it reports none.")
    xattrs: dict[str, str] | None = Field(default=None, description="Extended attributes as name to value strings.")

    fs_created_at: datetime | None = Field(default=None, description="Creation time on the storage system.")
    fs_modified_at: datetime | None = Field(default=None, description="Modification time on the storage system.")
    fs_accessed_at: datetime | None = Field(default=None, description="Last access time on the storage system.")

    # Phase B — connectors that walk depth-first emit per-directory
    # subtree totals. Optional; the API-side rollup CTE backfills NULL
    # rows after ingest as a safety net for connectors that can't
    # compute these cheaply.
    subtree_size_bytes: int | None = Field(default=None, description="Directories only: total bytes below it, when the connector computes this; otherwise filled in after the scan.")
    subtree_file_count: int | None = Field(default=None, description="Directories only: files below it, when computed by the connector.")
    subtree_dir_count: int | None = Field(default=None, description="Directories only: directories below it, when computed by the connector.")

    # v0.6.0 — Tier 3 self-hosted libraries (Paperless-ngx, Immich)
    # carry provider-specific metadata that doesn't fit the filesystem
    # fields. Filesystem connectors leave this None.
    domain_metadata: dict | None = Field(default=None, description="Provider-specific metadata for document libraries (such as Paperless or Immich); null for filesystems. Free-form: keys depend on the provider.", json_schema_extra={"x-free-form": True})

    # v0.13.0 — opaque cloud-drive identifier (Drive/OneDrive/Dropbox).
    # Filesystem connectors leave this None.
    native_id: str | None = Field(default=None, description="Provider's own identifier for cloud-drive entries; null for filesystems.")


class EntryResponse(ApiModel):
    id: uuid.UUID = Field(description="Entry id.")
    # Nullable since v0.4.0: orphaned entries (source deleted via the
    # default preserve flavour) come back with source_id=None.
    source_id: uuid.UUID | None = Field(description="Owning source id; null for an orphaned entry whose source was deleted with its entries preserved.")
    kind: str = Field(description="`file` or `directory`.")
    parent_path: str = Field(description="Path of the containing directory; `/` for top-level entries.")
    path: str = Field(description="Full path within the source, starting with `/`.")
    name: str = Field(description="Base name.")
    extension: str | None = Field(description="File extension without the dot; null for directories and files without one.")
    size_bytes: int | None = Field(description="Size in bytes; null when unknown.")
    mime_type: str | None = Field(description="Detected MIME type; null when not detected.")
    content_hash: str | None = Field(description="Content hash. Files with identical content share it; null until computed.")
    mode: int | None = Field(description="POSIX mode bits (file type and permissions) as an integer; null where the connector reports none.")
    uid: int | None = Field(description="Numeric owner id on POSIX storage; null elsewhere.")
    gid: int | None = Field(description="Numeric group id on POSIX storage; null elsewhere.")
    owner_name: str | None = Field(description="Owner name or SID as reported by the storage system.")
    group_name: str | None = Field(description="Group name or SID as reported by the storage system.")
    fs_modified_at: datetime | None = Field(description="Modification time on the storage system.")
    first_seen_at: datetime = Field(description="When a scan first saw the entry.")
    last_seen_at: datetime = Field(description="When a scan most recently saw the entry.")
    is_deleted: bool = Field(description="True once a scan no longer finds the entry (soft delete).")

    model_config = {"from_attributes": True, 'json_schema_extra': {'examples': [{'id': '6f1c1a52-3c77-4a4e-9d0e-0f3f4a8f9c11', 'source_id': '0d4a9a0e-2b1c-4c5e-8f6d-1a2b3c4d5e6f', 'kind': 'file', 'parent_path': '/Reports/2026', 'path': '/Reports/2026/q1.pdf', 'name': 'q1.pdf', 'extension': 'pdf', 'size_bytes': 482133, 'mime_type': 'application/pdf', 'content_hash': '9b74c9897bac770ffc029102a200c5de', 'mode': 33188, 'uid': 1000, 'gid': 1000, 'owner_name': 'alice', 'group_name': 'staff', 'fs_modified_at': '2026-04-02T09:15:00Z', 'first_seen_at': '2026-04-03T01:00:12Z', 'last_seen_at': '2026-04-27T01:00:09Z', 'is_deleted': False}]}}


class EntryVersionResponse(ApiModel):
    id: uuid.UUID = Field(description="Version id.")
    entry_id: uuid.UUID = Field(description="Entry this version belongs to.")
    scan_id: uuid.UUID | None = Field(description="Scan that detected this version; null if that scan has since been removed.")
    content_hash: str | None = Field(description="Content hash. Files with identical content share it; null until computed.")
    size_bytes: int | None = Field(description="Size in bytes; null when unknown.")
    mode: int | None = Field(description="POSIX mode bits (file type and permissions) as an integer; null where the connector reports none.")
    uid: int | None = Field(description="Numeric owner id on POSIX storage; null elsewhere.")
    gid: int | None = Field(description="Numeric group id on POSIX storage; null elsewhere.")
    owner_name: str | None = Field(description="Owner name or SID as reported by the storage system.")
    group_name: str | None = Field(description="Group name or SID as reported by the storage system.")
    acl: ACL | None = Field(description="ACL at the time of this version; null when none was recorded.")
    xattrs: dict[str, str] | None = Field(description="Extended attributes at the time of this version.")
    detected_at: datetime = Field(description="When a scan detected this version.")

    model_config = {"from_attributes": True}


class EntryTagAssignment(ApiModel):
    """One tag on an entry, with origin metadata. Direct tags (the user
    applied this tag here) carry `inherited=False, inherited_from_path=None`;
    inherited tags carry `inherited=True` and the ancestor directory's
    path so the UI can render "Inherited from /Reports" tooltips and let
    the user navigate to the source.
    """

    tag: str = Field(description="Tag name.")
    inherited: bool = Field(default=False, description="True when the tag comes from an ancestor directory rather than being applied to this entry.")
    inherited_from_path: str | None = Field(default=None, description="Path of the ancestor directory the tag is inherited from; null for a direct tag.")


class EntryDetailResponse(EntryResponse):
    """Full entry detail; includes ACL, xattrs, version history."""

    acl: ACL | None = Field(default=None, description="Access control list as scanned, in the model of the source (`posix`, `nfsv4`, `nt`, `s3` or `cloud_drive`); null when none.")
    xattrs: dict[str, str] | None = Field(default=None, description="Extended attributes as name/value pairs; null when none.")
    domain_metadata: dict | None = Field(default=None, description="Provider-specific metadata for self-hosted libraries (Paperless-ngx, Immich); null for filesystem sources. Free-form: keys depend on the provider.", json_schema_extra={"x-free-form": True})
    native_id: str | None = Field(default=None, description="Provider identifier for cloud-drive entries (Drive, OneDrive, Dropbox); null otherwise.")
    fs_created_at: datetime | None = Field(default=None, description="Creation time on the storage system.")
    fs_accessed_at: datetime | None = Field(default=None, description="Last access time on the storage system.")
    versions: list[EntryVersionResponse] = Field(default_factory=list, description="Recorded versions of the entry, newest first.")
    source: _EntrySourceRef | None = Field(default=None, description="The owning source; null for an orphaned entry.")
    tags: list[EntryTagAssignment] = Field(default_factory=list, description="Tags on the entry, direct and inherited.")

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {"examples": [{
            **EntryResponse.model_config["json_schema_extra"]["examples"][0],
            "acl": None, "xattrs": None, "domain_metadata": None, "native_id": None,
            "fs_created_at": None, "fs_accessed_at": None,
            "versions": [], "source": None, "tags": [],
        }]},
    }


class BrowseEntry(ApiModel):
    id: uuid.UUID = Field(description="Entry id.")
    kind: str = Field(description="`file` or `directory`.")
    name: str = Field(description="Base name.")
    path: str = Field(description="Full path within the source, starting with `/`.")
    extension: str | None = Field(default=None, description="File extension without the dot; null for directories and files without one.")
    size_bytes: int | None = Field(default=None, description="Size in bytes; null when unknown.")
    mime_type: str | None = Field(default=None, description="Detected MIME type; null when not detected.")
    content_hash: str | None = Field(default=None, description="Content hash. Files with identical content share it; null until computed.")
    mode: int | None = Field(default=None, description="POSIX mode bits (file type and permissions) as an integer; null where the connector reports none.")
    owner_name: str | None = Field(default=None, description="Owner name or SID as reported by the storage system.")
    group_name: str | None = Field(default=None, description="Group name or SID as reported by the storage system.")
    fs_modified_at: datetime | None = Field(default=None, description="Modification time on the storage system.")
    child_count: int | None = Field(default=None, description="For directories, the number of non-deleted direct children; null for files.")


class BrowsePage(Page[BrowseEntry]):
    """One page of a directory listing."""

    source_id: uuid.UUID = Field(description="Source being browsed.")
    source_name: str = Field(description="Source display name.")
    path: str = Field(description="Normalised directory path.")
    parent_path: str | None = Field(description="Parent directory path; null at the root.")
    is_root: bool = Field(description="Whether `path` is the source root.")
