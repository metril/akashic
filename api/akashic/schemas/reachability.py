"""Pydantic schemas for the on-demand probe long-poll flow.

Replaces the old `reachability_checks` queue schemas. The new model
delivers one probe at a time over a long-poll subscription; the agent
runs the probe and POSTs the result back. There's no batch or lease —
sub-second delivery makes batching pointless and lease bookkeeping
just adds failure modes.
"""
from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel


class ProbeRequest(BaseModel):
    """One probe delivered to the agent via /probes/long-poll.

    The agent dials the source with the merged host+source
    connection_config (secrets included; same envelope the scan-lease
    response uses) and POSTs back to /probes/{request_id}/report.
    """

    request_id: uuid.UUID
    source_id: uuid.UUID
    source_type: str
    connection_config: dict


class ProbeReport(BaseModel):
    """Result body for POST /probes/{request_id}/report.

    `source_id` is a copy of the value the API sent in the originating
    ProbeRequest. The agent forwards it back so the API doesn't need
    request → source state in memory between long-poll delivery and
    the report POST (the API process that delivered the request may not
    even be the same one receiving the report under horizontal scale).
    """

    ok: bool
    step: Optional[str] = None
    error: Optional[str] = None
    source_id: uuid.UUID
