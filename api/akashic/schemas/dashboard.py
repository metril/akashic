"""Response models for /dashboard."""
from __future__ import annotations

import uuid

from akashic.schemas.base import ApiModel
from pydantic import Field

from akashic.schemas.analytics import OwnerShare


class DashboardStorage(ApiModel):
    """Storage totals across visible sources."""

    total_bytes: int = Field(description="Total bytes in the latest snapshots.")
    total_files: int = Field(description="Total files in the latest snapshots.")
    delta_30d_bytes: int | None = Field(description="Byte change over 30 days; null without a 30-day-old snapshot.")
    delta_30d_files: int | None = Field(description="File-count change over 30 days; null without history.")


class DashboardScans(ApiModel):
    """Source and scan counters."""

    active: int = Field(description="Sources currently scanning.")
    total_sources: int = Field(description="Sources visible to the caller.")


class ForecastHint(ApiModel):
    """Recent growth rate for one of the largest sources."""

    source_id: uuid.UUID = Field(description="Source id.")
    source_name: str | None = Field(description="Source display name.")
    current_bytes: int = Field(description="Current total bytes.")
    slope_bytes_per_day: int = Field(description="Growth in bytes per day over the last 30 days.")


class ExtensionGrowth(ApiModel):
    """An extension's growth over 30 days."""

    extension: str = Field(description="Extension without the dot.")
    delta_bytes: int = Field(description="Bytes gained over 30 days.")
    current_bytes: int = Field(description="Current bytes.")


class RecentScan(ApiModel):
    """A recent scan row for the dashboard."""

    id: uuid.UUID = Field(description="Scan id.")
    source_id: uuid.UUID = Field(description="Source id.")
    source_name: str | None = Field(description="Source display name.")
    scan_type: str | None = Field(description="`full` or `incremental`.")
    status: str = Field(description="Scan status.")
    started_at: str | None = Field(description="Start time, ISO 8601.")
    completed_at: str | None = Field(description="Completion time, ISO 8601.")
    files_new: int | None = Field(description="Files added by the scan.")
    files_changed: int | None = Field(description="Files changed by the scan.")


class AccessRisks(ApiModel):
    """World-readable exposure counters."""

    public_read_count: int = Field(description="Files readable by everyone.")


class IdentityHealth(ApiModel):
    """Identity-mapping health counters."""

    unbound_count: int = Field(description="Filesystem identities not bound to a user.")
    unresolved_sid_count: int = Field(description="SIDs whose name is unresolved (admin only).")


class DashboardSummary(ApiModel):
    """Everything the dashboard tiles need in one call."""

    storage: DashboardStorage = Field(description="Storage totals.")
    scans: DashboardScans = Field(description="Scan counters.")
    forecast_hints: list[ForecastHint] = Field(description="Growth hints for the three largest sources.")
    top_owners: list[OwnerShare] = Field(description="Largest owners across sources.")
    top_extensions_growth_30d: list[ExtensionGrowth] = Field(description="Fastest-growing extensions.")
    recent_scans: list[RecentScan] = Field(description="Most recent scans.")
    access_risks: AccessRisks | None = Field(description="Always null here; use `/dashboard/access-risks`.")
    identity_health: IdentityHealth = Field(description="Identity-mapping counters.")


class AccessRisksResponse(ApiModel):
    """Cached world-readable file count."""

    access_risks: AccessRisks | None = Field(description="Null when the caller may not see it.")
    cache_age_seconds: int | None = Field(default=None, description="Age of the cached value; absent when not visible.")
