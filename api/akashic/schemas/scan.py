import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from akashic.schemas.entry import EntryIn


class ScanBatchIn(BaseModel):
    source_id: uuid.UUID
    scan_id: uuid.UUID
    entries: list[EntryIn]
    is_final: bool = False
    source_security_metadata: dict | None = None


class ScanBatchResponse(BaseModel):
    files_processed: int
    scan_id: uuid.UUID


# Phase 1 — observability inputs from the scanner. Kept on a separate channel
# from batch ingest so a slow batch doesn't gate progress visibility.

ScanPhase = Literal["prewalk", "walk", "finalize"]
LogLevel = Literal["info", "warn", "error"]


class HeartbeatIn(BaseModel):
    """Periodic heartbeat from a running scanner. ~1 s cadence."""

    current_path: str | None = None
    files_scanned: int = 0
    bytes_scanned: int = 0
    files_skipped: int = 0
    dirs_walked: int = 0
    dirs_queued: int = 0
    total_estimated: int | None = None
    phase: ScanPhase | None = None


class LogLineIn(BaseModel):
    ts: datetime
    level: LogLevel
    message: str = Field(..., max_length=8192)


class LogBatchIn(BaseModel):
    """Up to 200 lines per request — the scanner debounces 10 lines or 500 ms."""

    lines: list[LogLineIn] = Field(..., max_length=200)


class StderrChunkIn(BaseModel):
    ts: datetime
    # 4 KB cap per chunk; the scanner debounces stderr the same way.
    chunk: str = Field(..., max_length=4096)


class StderrBatchIn(BaseModel):
    chunks: list[StderrChunkIn] = Field(..., max_length=200)


class LogEntryOut(BaseModel):
    id: uuid.UUID
    ts: datetime
    level: str
    message: str

    model_config = {"from_attributes": True}


class ScanResponse(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    scan_type: str
    status: str
    files_found: int
    files_new: int
    files_changed: int
    files_deleted: int
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None = None

    # Phase 1 progress fields. Heartbeat-driven; nullable for legacy rows.
    current_path: str | None = None
    last_heartbeat_at: datetime | None = None
    bytes_scanned_so_far: int | None = None
    files_skipped: int = 0
    dirs_walked: int = 0
    dirs_queued: int = 0
    total_estimated: int | None = None
    phase: str | None = None
    previous_scan_files: int | None = None

    model_config = {"from_attributes": True}
