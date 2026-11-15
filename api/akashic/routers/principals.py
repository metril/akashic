"""HTTP surface for SID-to-principal resolution.

POST /api/v1/principals/resolve
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

from akashic.routing import ApiRouter
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, check_source_access, require_scope
from akashic.database import get_db
from akashic.models.user import User
from akashic.services.principal_resolver import (
    ResolvedPrincipal,
    resolve_principals,
)


router = ApiRouter(prefix="/principals", tags=["principals"])


class ResolveRequest(BaseModel):
    source_id: uuid.UUID = Field(description="Source whose domain controller answers the lookup.")
    sids: list[str] = Field(description="SIDs to resolve, at most 256.")

    model_config = {"json_schema_extra": {'examples': [{'source_id': '3fa85f64-5717-4562-b3fc-2c963f66afa6', 'sids': ['S-1-5-21-1-2-3-1001']}]}}


class ResolvedPrincipalOut(BaseModel):
    sid: str = Field(description="The SID that was looked up.")
    name: str | None = Field(description="Account name, when resolved.")
    domain: str | None = Field(description="Domain of the account, when resolved.")
    kind: str | None = Field(description="Account kind reported by the domain (`user`, `group`, `alias`, ...), when resolved.")
    status: str = Field(description="`resolved`, `unresolved`, `skipped` (source type has no LSARPC) or `error` (lookup failed).")
    last_attempt_at: str | None = Field(description="ISO-8601 time of the last lookup attempt; null if none was made.")


class ResolveResponse(BaseModel):
    # Map keyed by SID for O(1) merge on the web side. FastAPI's
    # default Pydantic serialization handles this fine.
    resolved: dict[str, ResolvedPrincipalOut] = Field(description="Result for each requested SID, keyed by SID.")


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


@router.post(
    "/resolve",
    response_model=ResolveResponse,
    summary="Resolve SIDs to names",
    description=(
        "Resolves Windows SIDs to account names through the source's domain controller (LSARPC), with "
        "a server-side cache so repeat lookups are free. Used to name principals in an ACL whose "
        "names were not available at scan time.\n"
        "The response maps every requested SID to a result whose `status` is `resolved`, "
        "`unresolved`, `skipped` (source type has no LSARPC) or `error` (lookup failed); failures "
        "never fail the request.\n"
        "Requires scope `identities:read`. Not available to source-limited API tokens (403 "
        "`source_limited`). Errors: 400 if more than 256 SIDs are sent; 422 for a malformed body."
    ),
)
async def resolve(
    body: ResolveRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("identities:read"),
) -> ResolveResponse:
    """Resolve SIDs against a source's LSARPC, with cache."""
    user = principal.user
    # Read access is the right level: anyone who can browse the
    # source's entries can already SEE the SIDs in their ACLs, so
    # there's no information-disclosure asymmetry from letting them
    # name those SIDs.
    await check_source_access(body.source_id, principal, db)

    if len(body.sids) > _MAX_SIDS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"too many SIDs in one request (max {_MAX_SIDS_PER_REQUEST})",
        )

    results = await resolve_principals(db, body.source_id, body.sids)
    return ResolveResponse(
        resolved={sid: _to_out(p) for sid, p in results.items()},
    )
