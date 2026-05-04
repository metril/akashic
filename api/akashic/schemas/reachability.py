"""Pydantic schemas for the reachability work-item flow."""
from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field


class ReachabilityCheckClaim(BaseModel):
    """One leased reachability check, returned to the scanner agent."""

    id: uuid.UUID
    source_id: uuid.UUID
    source_type: str
    # Merged host + source connection_config; secrets included so the
    # scanner can run the probe.
    connection_config: dict


class ReachabilityPollResponse(BaseModel):
    """Response body for POST /api/scanners/{id}/reachability/poll."""

    checks: list[ReachabilityCheckClaim] = []


class ReachabilityPollRequest(BaseModel):
    limit: int = Field(default=8, ge=1, le=32)


class ReachabilityReport(BaseModel):
    """Body for POST /api/scanners/{id}/reachability/{check_id}/report."""

    ok: bool
    step: Optional[str] = None
    error: Optional[str] = None
