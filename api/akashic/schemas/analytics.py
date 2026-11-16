"""Response models for /analytics."""
from __future__ import annotations

import uuid
from typing import Literal

from akashic.schemas.base import ApiModel
from pydantic import Field


class StorageByType(ApiModel):
    """Aggregate file count and bytes for one file extension."""

    extension: str | None = Field(description="Lower-case extension without the dot; null for files with none.")
    count: int = Field(description="Number of files with this extension.")
    total_size: int | None = Field(description="Sum of `size_bytes`, in bytes.")


class StorageBySource(ApiModel):
    """Aggregate file count and bytes for one source."""

    source_id: uuid.UUID = Field(description="Source id.")
    source_name: str = Field(description="Source display name.")
    count: int = Field(description="Number of files in the source.")
    total_size: int | None = Field(description="Sum of `size_bytes`, in bytes. Always 0 for a logical-view source.")
    logical_view: bool = Field(
        default=False,
        description="True when the source is the target of a mirror link; its bytes are counted on the backing source instead.",
    )


class StorageByLinkRole(ApiModel):
    """Files and bytes of one source in one mirror-link bucket."""

    link_role: Literal["original", "thumbnail", "encoded_video", "sidecar", "archive", "version", "unlinked"] = Field(
        description="Mirror-link role; `unlinked` for files with no link.",
    )
    count: int = Field(description="Number of non-deleted files in the bucket.")
    total_size: int = Field(description="Sum of `size_bytes`, in bytes.")


class LargestFile(ApiModel):
    """One of the largest files the caller can see."""

    id: uuid.UUID = Field(description="Entry id.")
    source_id: uuid.UUID = Field(description="Owning source id.")
    path: str = Field(description="Full path within the source.")
    filename: str = Field(description="Base name of the file.")
    size_bytes: int | None = Field(description="File size in bytes.")
    mime_type: str | None = Field(description="Detected MIME type, if known.")


class SeriesPoint(ApiModel):
    """One snapshot value in a time series."""

    taken_at: str = Field(description="Snapshot time, ISO 8601.")
    value: int = Field(description="Metric value at that time (bytes or file count).")


class ForecastPoint(ApiModel):
    """One projected point with its 95% interval."""

    taken_at: str = Field(description="Projected time, ISO 8601.")
    value: int = Field(description="Projected size in bytes.")
    low: int = Field(description="Lower bound of the 95% interval.")
    high: int = Field(description="Upper bound of the 95% interval.")


class ForecastProjection(ApiModel):
    """The linear projection computed from history."""

    points: list[ForecastPoint] = Field(description="Projected points, oldest to newest.")
    slope_bytes_per_day: int = Field(description="Fitted growth rate in bytes per day.")
    horizon_days: int = Field(description="Days projected beyond the last snapshot.")


class ForecastResponse(ApiModel):
    """Capacity forecast for one source."""

    history: list[SeriesPoint] = Field(description="Observed total size per snapshot, oldest to newest.")
    forecast: ForecastProjection | None = Field(description="Projection; null when there is too little history.")
    reason: str = Field(description="`ok`, or `insufficient_history` when fewer than 3 snapshots exist.")


class ExtensionTrendPoint(ApiModel):
    """One snapshot's totals for an extension."""

    taken_at: str = Field(description="Snapshot time, ISO 8601.")
    n: int = Field(description="File count with this extension.")
    bytes: int = Field(description="Total bytes of files with this extension.")


class OwnerShare(ApiModel):
    """One owner's totals in the latest snapshot."""

    owner: str = Field(description="Owner name (or SID/uid token when unresolved).")
    n: int = Field(description="Number of files owned.")
    bytes: int = Field(description="Total bytes owned.")


class OwnerDistribution(ApiModel):
    """Owners of the latest snapshot, largest first."""

    taken_at: str | None = Field(description="Snapshot time, ISO 8601; null when the source has no snapshot.")
    owners: list[OwnerShare] = Field(description="Owners ordered by bytes descending.")
