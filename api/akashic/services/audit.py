"""Audit-event helper.

`record_event` writes through the caller's session. Failures are logged but
NEVER raise — audit must not break the user-facing operation it logs.
"""
from __future__ import annotations

import logging
from typing import Any

from akashic.models.audit_event import AuditEvent
from akashic.models.user import User

logger = logging.getLogger(__name__)


async def record_event(
    *,
    db: Any,
    user: User | None,
    event_type: str,
    payload: dict,
    request: Any | None = None,
    source_id: Any | None = None,
) -> None:
    try:
        request_ip = ""
        user_agent = ""
        if request is not None:
            client = getattr(request, "client", None)
            if client is not None:
                request_ip = getattr(client, "host", "") or ""
            headers = getattr(request, "headers", {}) or {}
            user_agent = headers.get("user-agent", "") or ""
        evt = AuditEvent(
            user_id=user.id if user is not None else None,
            event_type=event_type,
            source_id=source_id,
            request_ip=request_ip,
            user_agent=user_agent,
            payload=payload,
        )
        db.add(evt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit: failed to record %s: %s", event_type, exc)
