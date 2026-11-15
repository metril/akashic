"""Response models for /storage (treemap explorer)."""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class StorageSource(BaseModel):
    """One source's size from its latest snapshot."""

    source_id: uuid.UUID = Field(description="Source id.")
    source_name: str = Field(description="Source display name.")
    source_type: str = Field(description="Connector type.")
    size_bytes: int = Field(description="Total bytes.")
    file_count: int = Field(description="Total files.")
    directory_count: int = Field(description="Total directories.")
    taken_at: str | None = Field(description="Snapshot time, ISO 8601; null when never scanned.")


class StorageSourcesResponse(BaseModel):
    """Cross-source treemap data."""

    sources: list[StorageSource] = Field(description="Sources ordered by size descending.")


class StorageChild(BaseModel):
    """An immediate child of a directory."""

    id: uuid.UUID = Field(description="Entry id.")
    kind: str = Field(description="`file` or `directory`.")
    name: str = Field(description="Base name.")
    path: str = Field(description="Full path.")
    size_bytes: int = Field(description="Size (subtree size for directories).")
    file_count: int = Field(description="Files contained (1 for a file).")
    color_key: str = Field(description="Bucket key for the requested `color_by` dimension.")


class StorageBucket(BaseModel):
    """Synthetic rectangle for the long tail (`other`) or filtered-out entries (`hidden`)."""

    kind: Literal["other", "hidden"] = Field(description="Bucket kind.")
    name: str = Field(description="`<other>` or `<hidden>`.")
    size_bytes: int = Field(description="Bytes in the bucket.")
    child_count: int = Field(description="Entries in the bucket.")


class StorageChildrenResponse(BaseModel):
    """Largest children of a directory plus tail buckets."""

    source_id: uuid.UUID = Field(description="Source id.")
    path: str = Field(description="Normalised directory path.")
    color_by: str = Field(description="Colour dimension used.")
    children: list[StorageChild] = Field(description="Top `limit` children by size.")
    other: StorageBucket | None = Field(description="Aggregate of children beyond `limit`.")
    hidden: StorageBucket | None = Field(description="Aggregate hidden by permission filtering (admins only).")
    enforced: bool = Field(description="Whether permission filtering was applied.")


class TreeNode(BaseModel):
    """A node of the nested treemap."""

    id: uuid.UUID | None = Field(default=None, description="Entry id; absent for synthetic nodes.")
    kind: str = Field(description="`file`, `directory` or `other`.")
    name: str = Field(description="Base name.")
    path: str = Field(description="Full path.")
    size_bytes: int = Field(description="Node size in bytes.")
    color_key: str | None = Field(default=None, description="Colour bucket key.")
    children: list[TreeNode] | None = Field(default=None, description="Child nodes, largest first.")


class StorageTreeResponse(BaseModel):
    """Recursive subtree bounded by a node budget."""

    source_id: uuid.UUID = Field(description="Source id.")
    path: str = Field(description="Normalised root path.")
    color_by: str = Field(description="Colour dimension used.")
    enforced: bool = Field(description="Whether permission filtering was applied.")
    node_count: int = Field(description="Nodes returned.")
    truncated: bool = Field(description="True when the node budget was hit.")
    root: TreeNode | None = Field(description="Root node; null when the path is empty or not visible.")
