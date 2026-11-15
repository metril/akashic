import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from akashic.schemas.entry import EntryIn


class ScanBatchIn(BaseModel):
    source_id: uuid.UUID = Field(description="Source being scanned.")
    scan_id: uuid.UUID = Field(description="Leased scan these entries belong to; must belong to `source_id`.")
    entries: list[EntryIn] = Field(description="Entries observed, at most 10,000. Duplicate paths keep the last.")
    is_final: bool = Field(default=False, description="True on the scanner's last batch: finalizes a scan that does not use parallel work units.")
    source_security_metadata: dict | None = Field(default=None, description="Source-level security data (for example an S3 bucket policy) to store on the source.")
    # v0.5.11 — entries the scanner silently skipped during walk (permission
    # denied, ENOENT mid-scan). Default 0 so legacy scanners that don't send
    # the field don't break ingest. The api accumulates these on the Scan
    # row, which makes the parallel-agent path (one IsFinal=true per unit)
    # produce the correct sum across units.
    inaccessible_dirs: int = Field(default=0, description="Directories skipped in this unit or scan because they could not be read; accumulated on the scan.")
    inaccessible_files: int = Field(default=0, description="Files skipped because they could not be read; accumulated on the scan.")

    model_config = {"json_schema_extra": {'examples': [{'source_id': '3fa85f64-5717-4562-b3fc-2c963f66afa6', 'scan_id': '7c9e6679-7425-40de-944b-e07fc1f90ae7', 'entries': [{'path': '/projects/report.pdf', 'name': 'report.pdf', 'kind': 'file', 'extension': 'pdf', 'size_bytes': 482113, 'mime_type': 'application/pdf', 'mode': 33188, 'uid': 1000, 'gid': 100}], 'is_final': False}]}}


class ExtractCandidate(BaseModel):
    """A new-or-changed file the scanner should extract text from.
    v0.30.0 — extraction moved into the scanner; the API tells it
    which files actually changed so it doesn't re-extract everything
    every scan."""

    path: str = Field(description="File path to read and extract text from.")
    mime_type: str = Field(default="", description="Detected MIME type; empty if unknown.")
    size_bytes: int = Field(default=0, description="File size in bytes.")


class ScanBatchResponse(BaseModel):
    files_processed: int = Field(description="Files in the batch that were processed.")
    scan_id: uuid.UUID = Field(description="Scan the batch belonged to.")
    # v0.30.0 — new/changed FILE entries from this batch. The scanner
    # reads each, extracts text (Tika for documents), and posts it to
    # /api/v1/ingest/content.
    extract_candidates: list[ExtractCandidate] = Field(default=[], description="New or changed files whose text the scanner should extract and send to `POST /ingest/content`.")

    model_config = {"json_schema_extra": {'examples': [{'files_processed': 1, 'scan_id': '7c9e6679-7425-40de-944b-e07fc1f90ae7', 'extract_candidates': [{'path': '/projects/report.pdf', 'mime_type': 'application/pdf', 'size_bytes': 482113}]}]}}


# Phase 1 — observability inputs from the scanner. Kept on a separate channel
# from batch ingest so a slow batch doesn't gate progress visibility.

ScanPhase = Literal["prewalk", "walk", "finalize"]
LogLevel = Literal["info", "warn", "error"]


class HeartbeatIn(BaseModel):
    """Periodic heartbeat from a running scanner. ~1 s cadence."""

    current_path: str | None = Field(default=None, description="Path currently being walked.")
    files_scanned: int = Field(default=0, description="Files scanned so far.")
    bytes_scanned: int = Field(default=0, description="Bytes read so far.")
    files_skipped: int = Field(default=0, description="Files skipped so far.")
    dirs_walked: int = Field(default=0, description="Directories walked so far.")
    dirs_queued: int = Field(default=0, description="Directories queued to walk.")
    total_estimated: int | None = Field(default=None, description="Estimated total files, if known.")
    phase: ScanPhase | None = Field(default=None, description="`prewalk`, `walk` or `finalize`.")
    # v0.29.2 — current adaptive batch size, surfaced so the Live Log
    # row tooltip can show "batch size: 1250" mid-scan. Nullable so
    # legacy scanners (pre-v0.29.2) without AIMD still post valid
    # heartbeats.
    current_batch_size: int | None = Field(default=None, description="Entries per ingest batch the scanner currently uses (adaptive).")

    model_config = {"json_schema_extra": {'examples': [{'current_path': '/projects/2026', 'files_scanned': 12840, 'bytes_scanned': 5368709120, 'files_skipped': 2, 'dirs_walked': 310, 'dirs_queued': 12, 'phase': 'walk', 'current_batch_size': 1250}]}}


class LogLineIn(BaseModel):
    ts: datetime = Field(description="When the line was produced.")
    level: LogLevel = Field(description="`info`, `warn` or `error`.")
    message: str = Field(..., max_length=8192, description="Log message, at most 8192 characters.")


class LogBatchIn(BaseModel):
    """Up to 200 lines per request — the scanner debounces 10 lines or 500 ms."""

    lines: list[LogLineIn] = Field(..., max_length=200, description="Up to 200 lines.")

    model_config = {"json_schema_extra": {'examples': [{'lines': [{'ts': '2026-04-27T22:00:01Z', 'level': 'info', 'message': 'walking /projects'}]}]}}


class StderrChunkIn(BaseModel):
    ts: datetime = Field(description="When the chunk was captured.")
    # 4 KB cap per chunk; the scanner debounces stderr the same way.
    chunk: str = Field(..., max_length=4096, description="Raw stderr text, at most 4096 characters.")


class StderrBatchIn(BaseModel):
    chunks: list[StderrChunkIn] = Field(..., max_length=200, description="Up to 200 chunks.")

    model_config = {"json_schema_extra": {'examples': [{'chunks': [{'ts': '2026-04-27T22:00:01Z', 'chunk': 'warning: permission denied: /projects/private\n'}]}]}}


class LogEntryOut(BaseModel):
    id: uuid.UUID = Field(description="Log line id.")
    ts: datetime = Field(description="When the line was produced.")
    level: str = Field(description="`info`, `warn`, `error` or `stderr`.")
    message: str = Field(description="Log message.")
    # v0.28.2 — scanner attribution for the Live Log per-row pill.
    # Both fields are nullable to keep legacy rows (pre-v0.28.2) valid.
    scanner_id: uuid.UUID | None = Field(default=None, description="Scanner that produced the line.")
    scanner_name: str | None = Field(default=None, description="Name of that scanner when the line was written.")

    model_config = {"from_attributes": True}


class ScanResponse(BaseModel):
    id: uuid.UUID = Field(description="Scan id.")
    source_id: uuid.UUID = Field(description="Source scanned.")
    scan_type: str = Field(description="`incremental` or `full`.")
    status: str = Field(description="`pending`, `running`, `completed`, `failed` or `cancelled`.")
    files_found: int = Field(description="Files seen so far (final once the scan ends).")
    files_new: int = Field(description="Files new since the previous scan.")
    files_changed: int = Field(description="Files whose metadata changed.")
    files_deleted: int = Field(description="Files no longer present.")
    started_at: datetime | None = Field(description="When the scan started; null while waiting for a scanner.")
    completed_at: datetime | None = Field(description="When the scan ended; null while active.")
    error_message: str | None = Field(default=None, description="Failure or cancellation detail.")

    # Phase 1 progress fields. Heartbeat-driven; nullable for legacy rows.
    current_path: str | None = Field(default=None, description="Path being walked, from the latest heartbeat.")
    last_heartbeat_at: datetime | None = Field(default=None, description="Time of the latest heartbeat.")
    bytes_scanned_so_far: int | None = Field(default=None, description="Bytes read so far, from the latest heartbeat.")
    files_skipped: int = Field(default=0, description="Files skipped (excluded or unreadable) so far.")
    dirs_walked: int = Field(default=0, description="Directories walked so far.")
    dirs_queued: int = Field(default=0, description="Directories waiting to be walked.")
    total_estimated: int | None = Field(default=None, description="Estimated total files, when the scanner could pre-count.")
    phase: str | None = Field(default=None, description="`prewalk`, `walk` or `finalize`.")
    previous_scan_files: int | None = Field(default=None, description="File count of the previous scan, for progress estimates.")

    # v0.5.11 — entries the walker silently skipped (permission denied,
    # ENOENT mid-scan). Surfaced in SourceDetail "Last scan" so users
    # know when a scan was incomplete vs. clean.
    inaccessible_dirs: int = Field(default=0, description="Directories the scanner could not read (permission denied or vanished mid-scan).")
    inaccessible_files: int = Field(default=0, description="Files the scanner could not read.")

    model_config = {"from_attributes": True, "json_schema_extra": {'examples': [{'id': '7c9e6679-7425-40de-944b-e07fc1f90ae7', 'source_id': '3fa85f64-5717-4562-b3fc-2c963f66afa6', 'scan_type': 'incremental', 'status': 'running', 'files_found': 12840, 'files_new': 120, 'files_changed': 14, 'files_deleted': 3, 'started_at': '2026-04-27T22:00:00Z', 'completed_at': None, 'current_path': '/projects/2026', 'phase': 'walk'}]}}
