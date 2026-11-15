"""Schemas for WebSocket event frames. REST routes don't reference these,
so `akashic.openapi` publishes them into `components.schemas` explicitly."""
from __future__ import annotations

from typing import Literal

from akashic.schemas.base import ApiModel
from pydantic import BaseModel, Field


class WsPing(ApiModel):
    """Server keep-alive frame."""

    kind: Literal["ping"] = Field(description="Frame discriminator.")


class WsError(ApiModel):
    """Live stream failed; clients should fall back to polling."""

    kind: Literal["error"] = Field(description="Frame discriminator.")
    message: str = Field(description="Why the live stream is unavailable.")


class WsScanLogLine(ApiModel):
    """One scan log line."""

    id: str = Field(description="Log entry id.")
    ts: str = Field(description="ISO-8601 timestamp.")
    level: str = Field(description="Log level, or `stderr` for scanner stderr chunks.")
    message: str = Field(description="Log text.")
    scanner_id: str | None = Field(default=None, description="Scanner that produced the line.")
    scanner_name: str | None = Field(default=None, description="Scanner name snapshotted at write time.")


class WsScanSnapshot(ApiModel):
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


class WsScanProgress(ApiModel):
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


class WsScanLog(ApiModel):
    """New log (or stderr) lines on `/ws/v1/scans/{id}`."""

    kind: Literal["log", "stderr"] = Field(description="Frame discriminator.")
    scan_id: str = Field(description="Scan id.")
    lines: list[WsScanLogLine] = Field(description="Newly persisted lines.")


class WsScanState(ApiModel):
    """Scan state change on `/ws/v1/scans` (the source-list stream)."""

    kind: Literal["scan.state"] = Field(description="Frame discriminator.")
    source_id: str = Field(description="Source id.")
    scan_id: str = Field(description="Scan id.")
    scan_status: str = Field(description="Scan status.")
    source_status: str = Field(description="Source status.")
    scanner_id: str | None = Field(default=None, description="Assigned scanner id.")
    scanner_name: str | None = Field(default=None, description="Assigned scanner name.")
    scan_type: str = Field(description="Scan type (`incremental` or `full`).")
    files_found: int = Field(description="Files found so far.")
    current_path: str | None = Field(default=None, description="Path currently being walked.")
    started_at: str | None = Field(default=None, description="ISO-8601 start time; null until a scanner has started the scan.")
    current_batch_size: int | None = Field(default=None, description="Latest adaptive batch size, when the scanner reported one.")


class WsScanListEntry(ApiModel):
    """One active scan in the `/ws/v1/scans` snapshot."""

    scan_id: str = Field(description="Scan id.")
    source_id: str = Field(description="Source id.")
    scan_status: str = Field(description="Scan status.")
    source_status: str = Field(description="Source status.")
    scanner_id: str | None = Field(default=None, description="Assigned scanner id.")
    scanner_name: str | None = Field(default=None, description="Assigned scanner name.")
    scan_type: str = Field(description="Scan type (`incremental` or `full`).")
    files_found: int = Field(description="Files found so far.")
    current_path: str | None = Field(default=None, description="Path currently being walked.")
    started_at: str | None = Field(default=None, description="ISO-8601 start time.")


class WsScanListSnapshot(ApiModel):
    """First frame on `/ws/v1/scans`: the pending, running and failed scans the caller can see."""

    kind: Literal["snapshot"] = Field(description="Frame discriminator.")
    scans: list[WsScanListEntry] = Field(description="Pending, running and recently failed scans.")


class WsSourceCreated(ApiModel):
    """A source was created (`/ws/v1/scans`)."""

    kind: Literal["source.created"] = Field(description="Frame discriminator.")
    source_id: str = Field(description="Source id.")
    source_status: str = Field(description="Source status.")
    name: str = Field(description="Source name.")
    type: str = Field(description="Source type.")


class WsSourceUpdated(ApiModel):
    """A source was edited; refetch it (`/ws/v1/scans`)."""

    kind: Literal["source.updated"] = Field(description="Frame discriminator.")
    source_id: str = Field(description="Source id.")


class WsSourceDeleted(ApiModel):
    """A source was deleted (`/ws/v1/scans`)."""

    kind: Literal["source.deleted"] = Field(description="Frame discriminator.")
    source_id: str = Field(description="Source id.")


class WsHostChanged(ApiModel):
    """A host was created, edited or deleted; refetch hosts (`/ws/v1/scans`)."""

    kind: Literal["host.changed"] = Field(description="Frame discriminator.")
    host_id: str = Field(description="Host id.")


class WsPendingDiscovery(ApiModel):
    """A scanner discovery request awaiting approval."""

    id: str = Field(description="Discovery request id.")
    pairing_code: str = Field(description="Code the operator matches against the scanner's console.")
    hostname: str | None = Field(default=None, description="Scanner host name.")
    agent_version: str | None = Field(default=None, description="Scanner version.")
    requested_pool: str | None = Field(default=None, description="Pool the scanner asked to join.")
    requested_at: str = Field(description="ISO-8601 request time.")
    expires_at: str = Field(description="ISO-8601 expiry time.")
    key_fingerprint: str = Field(description="Fingerprint of the scanner's public key.")


class WsScannersSnapshot(ApiModel):
    """First frame on `/ws/v1/scanners`: pending discovery requests."""

    kind: Literal["snapshot"] = Field(description="Frame discriminator.")
    pending_discoveries: list[WsPendingDiscovery] = Field(description="Discovery requests awaiting approval, newest first.")


class WsScannerClaimRedeemed(ApiModel):
    """A claim token was redeemed and a scanner registered (`/ws/v1/scanners`)."""

    kind: Literal["scanner.claim_redeemed"] = Field(description="Frame discriminator.")
    scanner_id: str = Field(description="New scanner id.")
    scanner_name: str = Field(description="Scanner name.")
    pool: str = Field(description="Scanner pool.")
    token_id: str = Field(description="Claim token id.")


class WsScannerDiscoveryRequested(ApiModel):
    """A scanner asked to be approved (`/ws/v1/scanners`)."""

    kind: Literal["scanner.discovery_requested"] = Field(description="Frame discriminator.")
    discovery_id: str = Field(description="Discovery request id.")
    pairing_code: str = Field(description="Pairing code.")
    hostname: str | None = Field(default=None, description="Scanner host name.")
    agent_version: str | None = Field(default=None, description="Scanner version.")
    requested_pool: str | None = Field(default=None, description="Requested pool.")
    expires_at: str = Field(description="ISO-8601 expiry time.")
    key_fingerprint: str = Field(description="Fingerprint of the scanner's public key.")


class WsScannerDiscoveryApproved(ApiModel):
    """A discovery request was approved (`/ws/v1/scanners`)."""

    kind: Literal["scanner.discovery_approved"] = Field(description="Frame discriminator.")
    discovery_id: str = Field(description="Discovery request id.")
    scanner_id: str = Field(description="New scanner id.")
    scanner_name: str = Field(description="Scanner name.")
    pool: str = Field(description="Scanner pool.")


class WsScannerDiscoveryDenied(ApiModel):
    """A discovery request was denied (`/ws/v1/scanners`)."""

    kind: Literal["scanner.discovery_denied"] = Field(description="Frame discriminator.")
    discovery_id: str = Field(description="Discovery request id.")
    deny_reason: str | None = Field(default=None, description="Operator-supplied reason.")


class WsScannerDiscoveryExpired(ApiModel):
    """A discovery request expired unanswered (`/ws/v1/scanners`)."""

    kind: Literal["scanner.discovery_expired"] = Field(description="Frame discriminator.")
    discovery_id: str = Field(description="Discovery request id.")


class WsScannerUpdated(ApiModel):
    """A scanner's settings or reported metadata changed; refetch it (`/ws/v1/scanners`)."""

    kind: Literal["scanner.updated"] = Field(description="Frame discriminator.")
    scanner_id: str = Field(description="Scanner id.")


class WsScannerDeleted(ApiModel):
    """A scanner was removed (`/ws/v1/scanners`)."""

    kind: Literal["scanner.deleted"] = Field(description="Frame discriminator.")
    scanner_id: str = Field(description="Scanner id.")


WS_EVENT_MODELS: tuple[type[BaseModel], ...] = (
    WsPing, WsError, WsScanLogLine, WsScanSnapshot, WsScanProgress, WsScanLog,
    WsScanState, WsScanListEntry, WsScanListSnapshot,
    WsSourceCreated, WsSourceUpdated, WsSourceDeleted, WsHostChanged,
    WsPendingDiscovery, WsScannersSnapshot, WsScannerClaimRedeemed,
    WsScannerDiscoveryRequested, WsScannerDiscoveryApproved,
    WsScannerDiscoveryDenied, WsScannerDiscoveryExpired,
    WsScannerUpdated, WsScannerDeleted,
)

# Frame models per endpoint, for the generated reference docs.
WS_ENDPOINTS: tuple[tuple[str, str, str, tuple[type[BaseModel], ...]], ...] = (
    (
        "/ws/v1/scans/{scan_id}", "scans:read",
        "Live progress and log lines of one scan. Requires `scans:read`; a source-limited token sees only its sources.",
        (WsScanSnapshot, WsScanProgress, WsScanLog, WsPing, WsError),
    ),
    (
        "/ws/v1/scans", "scans:read",
        "Scan and source state changes for every source the caller can see. Requires `scans:read`.",
        (WsScanListSnapshot, WsScanState, WsSourceCreated, WsSourceUpdated, WsSourceDeleted, WsHostChanged, WsPing, WsError),
    ),
    (
        "/ws/v1/scanners", "scanners:read",
        "Scanner lifecycle events: enrolment, discovery requests and scanner changes. Requires `scanners:read`.",
        (WsScannersSnapshot, WsScannerClaimRedeemed, WsScannerDiscoveryRequested, WsScannerDiscoveryApproved,
         WsScannerDiscoveryDenied, WsScannerDiscoveryExpired, WsScannerUpdated, WsScannerDeleted, WsPing, WsError),
    ),
)
