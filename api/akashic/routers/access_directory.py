"""Directory-backed identity picker for the access lookups.

Search people and groups across AD, Authentik and Akashic users, refresh one
person live, and trigger / inspect the periodic directory sync.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, require_scope
from akashic.database import get_db
from akashic.models.directory import DirectorySyncRun
from akashic.routing import ApiRouter
from akashic.schemas.directory import (
    BackendStatus, DirectoryBackendState, DirectoryStatusResponse, DirectorySyncRunOut,
    DirectorySyncStarted, IdentityBackendRef, IdentityRefreshRequest, IdentityResult,
    IdentitySearchResponse,
)
from akashic.services.audit import record_event
from akashic.services.directory import sync
from akashic.services.directory.ad import ad_configured
from akashic.services.directory.authentik import authentik_configured
from akashic.services.directory.lookup import (
    backends_status, identity_result, load_identity, search_identities,
)
from akashic.services.directory.merge import MergedIdentity
from akashic.services.runtime_settings import rt

router = ApiRouter(prefix="/access", tags=["access"])


@router.get(
    "/identities",
    response_model=IdentitySearchResponse,
    summary="Search people and groups",
    description=(
        "Searches the directory cache (Active Directory and Authentik, refreshed by the directory sync) "
        "and Akashic users for people and groups whose name, username, email or DN contains `q`. "
        "Records of the same person found in several backends are merged into one result. "
        "`backends_status` reports whether each backend is configured and whether its last sync succeeded.\n"
        "Requires scope `access:read`; not available to source-limited API tokens. "
        "Errors: 422 if `q` is empty or longer than 200 characters or another parameter is invalid."
    ),
)
async def search_identities_route(
    q: str = Query(min_length=1, max_length=200, description="Text to match (case-insensitive substring; `%`, `_` and `\\` are literal)."),
    kind: Literal["any", "person", "group"] = Query(default="any", description="Restrict results to people or groups."),
    limit: int = Query(default=20, ge=1, le=50, description="Maximum number of results."),
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("access:read"),
) -> IdentitySearchResponse:
    idents = await search_identities(db, q, kind, limit)
    return IdentitySearchResponse(
        results=[identity_result(i) for i in idents],
        backends_status=[BackendStatus(**s) for s in await backends_status(db)],
    )


@router.post(
    "/identities/refresh",
    response_model=IdentityResult,
    summary="Refresh one person from the directory",
    description=(
        "Re-reads the person's AD and Authentik records live (with a short timeout per backend), "
        "updates the directory cache and returns the identity as it now stands. "
        "Groups are not re-read; their cached membership is kept. A person that no longer exists in a "
        "backend is removed from the cache. The merge id is sent in the body because it contains "
        "characters that do not survive a URL path.\n"
        "Requires scope `access:read`; not available to source-limited API tokens. "
        "Errors: 400 if the identity is a group or has no AD or Authentik record; 404 if the identity is unknown, or no longer exists after the refresh; 502 if every live read "
        "failed (including when the person's directory backend is not configured), with a redacted reason in `detail` (the cache is left untouched)."
    ),
)
async def refresh_identity(
    body: IdentityRefreshRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("access:read"),
) -> IdentityResult:
    ident = await load_identity(db, body.id)
    if ident is None:
        raise HTTPException(status_code=404, detail="Identity not found")
    if ident.kind != "person" or not any(r.backend in ("ad", "authentik") for r in ident.records):
        raise HTTPException(status_code=400, detail="Only people with a directory record can be refreshed")
    try:
        await sync.refresh_records(db, ident.records)
    except sync.RefreshFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    refreshed = await load_identity(db, body.id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Identity not found")
    return identity_result(refreshed)


@router.post(
    "/directory/sync",
    response_model=DirectorySyncStarted,
    status_code=202,
    summary="Start a directory sync",
    description=(
        "Starts a background sync of every configured directory backend (Active Directory, Authentik) "
        "and returns the started runs; poll `GET /access/directory/status` for progress. "
        "Records a `directory_sync` audit event.\n"
        "Requires scope `maintenance:write`. "
        "Errors: 409 if a sync is already running; 400 if no directory backend is configured."
    ),
)
async def start_directory_sync(
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("maintenance:write"),
) -> DirectorySyncStarted:
    try:
        runs = await sync.start_sync(trigger="manual", triggered_by=principal.user.id)
    except sync.SyncAlreadyRunning:
        raise HTTPException(status_code=409, detail="A directory sync is already running") from None
    except sync.NoBackendConfigured:
        raise HTTPException(status_code=400, detail="No directory backend is configured") from None
    await record_event(
        db=db, user=principal.user, event_type="directory_sync",
        payload={"runs": [{"id": str(r.id), "backend": r.backend} for r in runs]},
        request=request,
    )
    return DirectorySyncStarted(runs=[DirectorySyncRunOut.model_validate(r) for r in runs])


@router.get(
    "/directory/status",
    response_model=DirectoryStatusResponse,
    summary="Directory sync status",
    description=(
        "Returns, per directory backend, whether it is configured, whether a sync is running, "
        "its latest run and when it last succeeded, plus the sync interval and the next scheduled start "
        "(latest run start plus the interval, or now when nothing has run yet; null when the interval is 0).\n"
        "Requires scope `access:read`; not available to source-limited API tokens."
    ),
)
async def directory_status(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("access:read"),
) -> DirectoryStatusResponse:
    states: list[DirectoryBackendState] = []
    for backend, configured in (("ad", ad_configured()), ("authentik", authentik_configured())):
        last = (await db.execute(
            select(DirectorySyncRun).where(DirectorySyncRun.backend == backend)
            .order_by(DirectorySyncRun.started_at.desc()).limit(1)
        )).scalar_one_or_none()
        last_success = (await db.execute(
            select(func.max(DirectorySyncRun.finished_at)).where(
                DirectorySyncRun.backend == backend, DirectorySyncRun.status == "succeeded")
        )).scalar_one()
        running = (await db.execute(
            select(DirectorySyncRun.id).where(
                DirectorySyncRun.backend == backend, DirectorySyncRun.status == "running").limit(1)
        )).scalar_one_or_none() is not None
        states.append(DirectoryBackendState(
            backend=backend, configured=configured, running=running,
            last_run=DirectorySyncRunOut.model_validate(last) if last else None,
            last_success_at=last_success,
        ))
    interval = rt().directory_sync_interval_minutes
    latest = (await db.execute(select(func.max(DirectorySyncRun.started_at)))).scalar_one()
    next_run = None
    if interval > 0:
        next_run = latest + timedelta(minutes=interval) if latest else datetime.now(timezone.utc)
    return DirectoryStatusResponse(backends=states, interval_minutes=interval, next_run_at=next_run)
