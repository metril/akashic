"""Pydantic schemas for the on-demand probe long-poll flow.

Replaces the old `reachability_checks` queue schemas. The new model
delivers one probe at a time over a long-poll subscription; the agent
runs the probe and POSTs the result back. There's no batch or lease —
sub-second delivery makes batching pointless and lease bookkeeping
just adds failure modes.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ProbeRequest(BaseModel):
    """One probe delivered to the agent via /probes/long-poll.

    The agent dials the source with the merged host+source
    connection_config (secrets included; same envelope the scan-lease
    response uses) and POSTs back to /probes/{request_id}/report.
    """

    request_id: uuid.UUID = Field(description="Probe request id; echoed back in the report URL.")
    source_id: uuid.UUID = Field(description="Source to probe.")
    source_type: str = Field(description="Connector type of the source.")
    connection_config: dict = Field(description="Merged host and source connection config, secrets included.")


class ProbeReport(BaseModel):
    """Result body for POST /probes/{request_id}/report.

    `source_id` is a copy of the value the API sent in the originating
    ProbeRequest. The agent forwards it back so the API doesn't need
    request → source state in memory between long-poll delivery and
    the report POST (the API process that delivered the request may not
    even be the same one receiving the report under horizontal scale).
    """

    ok: bool = Field(description="Whether the source was reachable.")
    step: Optional[str] = Field(default=None, description="Step that failed, e.g. `connect` or `auth`.")
    error: Optional[str] = Field(default=None, description="Error message when it failed.")
    source_id: uuid.UUID = Field(description="Source that was probed.")


# v0.41.0 — response shape for GET /api/v1/sources/{id}/reachability-history.
# Drives the per-scanner "Reachability" tab in SourceDetail.


class ReachabilityOutcome(BaseModel):
    ok: bool = Field(description="Whether the source was reachable.")
    step: Optional[str] = Field(default=None, description="Step that failed, if any.")
    error: Optional[str] = Field(default=None, description="Error message, if any.")
    started_at: datetime = Field(description="When the probe started.")
    completed_at: datetime = Field(description="When the probe finished.")


class PerScannerHistory(BaseModel):
    scanner_id: Optional[uuid.UUID] = Field(default=None, description="Scanner id; null for results not tied to a scanner.")
    scanner_name: Optional[str] = Field(default=None, description="Scanner name.")
    outcomes: list[ReachabilityOutcome] = Field(description="Recent outcomes, newest first.")


class ReachabilityHistory(BaseModel):
    per_scanner: list[PerScannerHistory] = Field(description="History grouped by scanner.")
