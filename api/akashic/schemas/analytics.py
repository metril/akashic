"""Response models for /analytics."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class StorageByType(BaseModel):
    """Aggregate file count and bytes for one file extension."""

    extension: str | None = Field(description="Lower-case extension without the dot; null for files with none.")
    count: int = Field(description="Number of files with this extension.")
    total_size: int | None = Field(description="Sum of `size_bytes`, in bytes.")


class StorageBySource(BaseModel):
    """Aggregate file count and bytes for one source."""

    source_id: uuid.UUID = Field(description="Source id.")
    source_name: str = Field(description="Source display name.")
    count: int = Field(description="Number of files in the source.")
    total_size: int | None = Field(description="Sum of `size_bytes`, in bytes.")


class LargestFile(BaseModel):
    """One of the largest files the caller can see."""

    id: uuid.UUID = Field(description="Entry id.")
    source_id: uuid.UUID = Field(description="Owning source id.")
    path: str = Field(description="Full path within the source.")
    filename: str = Field(description="Base name of the file.")
    size_bytes: int | None = Field(description="File size in bytes.")
    mime_type: str | None = Field(description="Detected MIME type, if known.")


class SeriesPoint(BaseModel):
    """One snapshot value in a time series."""

    taken_at: str = Field(description="Snapshot time, ISO 8601.")
    value: int = Field(description="Metric value at that time (bytes or file count).")


class ForecastPoint(BaseModel):
    """One projected point with its 95% interval."""

    taken_at: str = Field(description="Projected time, ISO 8601.")
    value: int = Field(description="Projected size in bytes.")
    low: int = Field(description="Lower bound of the 95% interval.")
    high: int = Field(description="Upper bound of the 95% interval.")


class ForecastProjection(BaseModel):
    """The linear projection computed from history."""

    points: list[ForecastPoint] = Field(description="Projected points, oldest to newest.")
    slope_bytes_per_day: int = Field(description="Fitted growth rate in bytes per day.")
    horizon_days: int = Field(description="Days projected beyond the last snapshot.")


class ForecastResponse(BaseModel):
    """Capacity forecast for one source."""

    history: list[SeriesPoint] = Field(description="Observed total size per snapshot, oldest to newest.")
    forecast: ForecastProjection | None = Field(description="Projection; null when there is too little history.")
    reason: str = Field(description="`ok`, or `insufficient_history` when fewer than 3 snapshots exist.")


class ExtensionTrendPoint(BaseModel):
    """One snapshot's totals for an extension."""

    taken_at: str = Field(description="Snapshot time, ISO 8601.")
    n: int = Field(description="File count with this extension.")
    bytes: int = Field(description="Total bytes of files with this extension.")


class OwnerShare(BaseModel):
    """One owner's totals in the latest snapshot."""

    owner: str = Field(description="Owner name (or SID/uid token when unresolved).")
    n: int = Field(description="Number of files owned.")
    bytes: int = Field(description="Total bytes owned.")


class OwnerDistribution(BaseModel):
    """Owners of the latest snapshot, largest first."""

    taken_at: str | None = Field(description="Snapshot time, ISO 8601; null when the source has no snapshot.")
    owners: list[OwnerShare] = Field(description="Owners ordered by bytes descending.")
