"""Schemas for WebSocket event frames. REST routes don't reference these,
so `akashic.openapi` publishes them into `components.schemas` explicitly."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class WsPing(BaseModel):
    """Server keep-alive frame."""

    kind: Literal["ping"] = Field(description="Frame discriminator.")


class WsError(BaseModel):
    """Live stream failed; clients should fall back to polling."""

    kind: Literal["error"] = Field(description="Frame discriminator.")
    message: str = Field(description="Why the live stream is unavailable.")


class WsScanLogLine(BaseModel):
    """One scan log line."""

    id: str = Field(description="Log entry id.")
    ts: str = Field(description="ISO-8601 timestamp.")
    level: str = Field(description="Log level, or `stderr` for scanner stderr chunks.")
    message: str = Field(description="Log text.")
    scanner_id: str | None = Field(default=None, description="Scanner that produced the line.")
    scanner_name: str | None = Field(default=None, description="Scanner name snapshotted at write time.")


class WsScanSnapshot(BaseModel):
    """First frame on `/ws/v1/scans/{id}`: current scan state plus recent log lines."""

    kind: Literal["snapshot"] = Field(description="Frame discriminator.")
    scan_id: str = Field(description="Scan id.")
    source_id: str = Field(description="Source being scanned.")
    status: str = Field(description="Scan status.")
    phase: str | None = Field(default=None, description="Current scan phase.")
    current_path: str | None = Field(default=None, description="Path currently being walked.")
    files_found: int = Field(description="Files found so far.")
    files_new: int = Field(description="Files new since the previous scan.")
    files_changed: int = Field(description="Files changed since the previous scan.")
    files_deleted: int = Field(description="Files deleted since the previous scan.")
    files_skipped: int = Field(description="Files skipped.")
    bytes_scanned_so_far: int = Field(description="Bytes scanned so far.")
    dirs_walked: int = Field(description="Directories walked.")
    dirs_queued: int = Field(description="Directories queued.")
    total_estimated: int | None = Field(default=None, description="Estimated total files, if known.")
    previous_scan_files: int | None = Field(default=None, description="File count of the previous scan.")
    started_at: str | None = Field(default=None, description="ISO-8601 start time.")
    completed_at: str | None = Field(default=None, description="ISO-8601 completion time.")
    last_heartbeat_at: str | None = Field(default=None, description="ISO-8601 time of the last scanner heartbeat.")
    error_message: str | None = Field(default=None, description="Failure reason, if any.")
    current_batch_size: int | None = Field(default=None, description="Latest adaptive batch size.")
    recent_lines: list[WsScanLogLine] = Field(description="Most recent log lines, oldest first.")


class WsScanProgress(BaseModel):
    """Throttled progress frame on `/ws/v1/scans/{id}`."""

    kind: Literal["progress"] = Field(description="Frame discriminator.")
    scan_id: str = Field(description="Scan id.")
    current_path: str | None = Field(default=None, description="Path currently being walked.")
    files_scanned: int = Field(description="Files scanned so far.")
    bytes_scanned: int = Field(description="Bytes scanned so far.")
    files_skipped: int = Field(description="Files skipped.")
    dirs_walked: int = Field(description="Directories walked.")
    dirs_queued: int = Field(description="Directories queued.")
    total_estimated: int | None = Field(default=None, description="Estimated total files, if known.")
    phase: str | None = Field(default=None, description="Current scan phase.")
    ts: str = Field(description="ISO-8601 timestamp.")
    current_batch_size: int | None = Field(default=None, description="Latest adaptive batch size.")


class WsScanLog(BaseModel):
    """New log (or stderr) lines on `/ws/v1/scans/{id}`."""

    kind: Literal["log", "stderr"] = Field(description="Frame discriminator.")
    scan_id: str = Field(description="Scan id.")
    lines: list[WsScanLogLine] = Field(description="Newly persisted lines.")


class WsScanState(BaseModel):
    """Scan state change on `/ws/v1/scans` (the source-list stream)."""

    kind: Literal["scan.state"] = Field(description="Frame discriminator.")
    source_id: str = Field(description="Source id.")
    scan_id: str = Field(description="Scan id.")
    scan_status: str = Field(description="Scan status.")
    source_status: str = Field(description="Source status.")
    scanner_id: str | None = Field(default=None, description="Assigned scanner id.")
    scanner_name: str | None = Field(default=None, description="Assigned scanner name.")
    scan_type: str | None = Field(default=None, description="Scan type (`incremental` or `full`).")
    files_found: int | None = Field(default=None, description="Files found so far.")
    current_path: str | None = Field(default=None, description="Path currently being walked.")


WS_EVENT_MODELS: tuple[type[BaseModel], ...] = (
    WsPing, WsError, WsScanLogLine, WsScanSnapshot, WsScanProgress, WsScanLog, WsScanState,
)
