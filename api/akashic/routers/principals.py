"""HTTP surface for SID-to-principal resolution.

POST /api/principals/resolve
    Body:  {"source_id": UUID, "sids": ["S-1-...", ...]}
    Auth:  authenticated user with read-or-better access on the source
    Reply: {"resolved": {sid: {name, domain, kind, status, last_attempt_at}}}

Used by the web app's NtACL renderer (PR2): when the entry's stored
ACL JSON contains principals with empty names (LSARPC was unreachable
at scan time), the page POSTs the unresolved SIDs here, gets back
fresh names from the source's domain controller, and merges them into
the displayed ACL. The api caches results so the second open is
free.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import check_source_access, get_current_user
from akashic.database import get_db
from akashic.models.user import User
from akashic.services.principal_resolver import (
    ResolvedPrincipal,
    resolve_principals,
)


router = APIRouter(prefix="/api/principals", tags=["principals"])


class ResolveRequest(BaseModel):
    source_id: uuid.UUID
    sids: list[str]


class ResolvedPrincipalOut(BaseModel):
    sid: str
    name: str | None
    domain: str | None
    kind: str | None
    status: str
    last_attempt_at: str | None


class ResolveResponse(BaseModel):
    # Map keyed by SID for O(1) merge on the web side. FastAPI's
    # default Pydantic serialization handles this fine.
    resolved: dict[str, ResolvedPrincipalOut]


def _to_out(p: ResolvedPrincipal) -> ResolvedPrincipalOut:
    return ResolvedPrincipalOut(
        sid=p.sid,
        name=p.name,
        domain=p.domain,
        kind=p.kind,
        status=p.status,
        last_attempt_at=p.last_attempt_at.isoformat() if p.last_attempt_at else None,
    )


# Bounds the cost of a single request: a pathological caller asking
# for 10,000 SIDs would run a single 10,000-element LSARPC LookupSids2
# call which most servers reject anyway. 256 is well above any real
# ACL we've seen and matches the cap used elsewhere in the api.
_MAX_SIDS_PER_REQUEST = 256


@router.post("/resolve", response_model=ResolveResponse)
async def resolve(
    body: ResolveRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ResolveResponse:
    """Resolve SIDs against a source's LSARPC, with cache."""
    # Read access is the right level: anyone who can browse the
    # source's entries can already SEE the SIDs in their ACLs, so
    # there's no information-disclosure asymmetry from letting them
    # name those SIDs.
    await check_source_access(body.source_id, user, db, required_level="read")

    if len(body.sids) > _MAX_SIDS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"too many SIDs in one request (max {_MAX_SIDS_PER_REQUEST})",
        )

    results = await resolve_principals(db, body.source_id, body.sids)
    return ResolveResponse(
        resolved={sid: _to_out(p) for sid, p in results.items()},
    )
