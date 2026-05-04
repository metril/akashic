"""CRUD for reusable credential profiles.

Hosts and sources reference profiles via `credential_profile_id`.
The layered resolver in services/source_config applies a profile's
credentials under the inline values at scan time. See v0.5.9.

Admin-only writes; reads are open to authenticated users so the
host/source create forms can populate the profile picker.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user, require_admin
from akashic.database import get_db
from akashic.models.credential_profile import CredentialProfile
from akashic.models.host import Host
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.credential_profile import (
    SUPPORTED_TYPES,
    CredentialProfileCreate,
    CredentialProfileResponse,
    CredentialProfileSummary,
    CredentialProfileUpdate,
    merge_sentinel_credentials,
)
from akashic.services.audit import record_event


router = APIRouter(prefix="/api/credential-profiles", tags=["credential-profiles"])


def _validate_type(t: str) -> None:
    if t not in SUPPORTED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported type {t!r}. Expected one of {sorted(SUPPORTED_TYPES)}.",
        )


@router.post(
    "",
    response_model=CredentialProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_profile(
    data: CredentialProfileCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    _validate_type(data.type)
    profile = CredentialProfile(
        name=data.name,
        type=data.type,
        credentials=data.credentials,
        description=data.description,
    )
    db.add(profile)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail=f"profile name {data.name!r} already in use",
        )
    await db.refresh(profile)
    await record_event(
        db=db, user=user, event_type="credential_profile_created",
        request=request,
        payload={
            "profile_id": str(profile.id),
            "name": profile.name,
            "type": profile.type,
        },
    )
    await db.commit()
    return CredentialProfileResponse.model_validate(profile)


@router.get("", response_model=list[CredentialProfileSummary])
async def list_profiles(
    type: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(CredentialProfile).order_by(CredentialProfile.name)
    if type is not None:
        _validate_type(type)
        stmt = stmt.where(CredentialProfile.type == type)
    rows = (await db.execute(stmt)).scalars().all()
    return [CredentialProfileSummary.model_validate(r) for r in rows]


@router.get("/{profile_id}", response_model=CredentialProfileResponse)
async def get_profile(
    profile_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    p = (await db.execute(
        select(CredentialProfile).where(CredentialProfile.id == profile_id)
    )).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail="Credential profile not found")
    return CredentialProfileResponse.model_validate(p)


@router.patch("/{profile_id}", response_model=CredentialProfileResponse)
async def update_profile(
    profile_id: uuid.UUID,
    data: CredentialProfileUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    p = (await db.execute(
        select(CredentialProfile).where(CredentialProfile.id == profile_id)
    )).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail="Credential profile not found")

    # type is immutable — changing it would invalidate every host /
    # source reference. Force a delete-and-create instead.
    if data.name is not None:
        p.name = data.name
    if data.description is not None:
        p.description = data.description
    if data.credentials is not None:
        # Sentinel-aware partial update: keys with value "***" / "********"
        # don't overwrite the stored value.
        p.credentials = merge_sentinel_credentials(p.credentials, data.credentials)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=409, detail="profile name already in use")
    await db.refresh(p)
    await record_event(
        db=db, user=user, event_type="credential_profile_updated",
        request=request,
        payload={"profile_id": str(p.id), "name": p.name},
    )
    await db.commit()
    return CredentialProfileResponse.model_validate(p)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    profile_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    p = (await db.execute(
        select(CredentialProfile).where(CredentialProfile.id == profile_id)
    )).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail="Credential profile not found")

    host_count = (await db.execute(
        select(func.count(Host.id)).where(Host.credential_profile_id == profile_id)
    )).scalar_one()
    source_count = (await db.execute(
        select(func.count(Source.id)).where(Source.credential_profile_id == profile_id)
    )).scalar_one()
    if host_count or source_count:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Profile is referenced by {host_count} host(s) and "
                f"{source_count} source(s). Reassign or unlink them first."
            ),
        )

    await db.delete(p)
    await record_event(
        db=db, user=user, event_type="credential_profile_deleted",
        request=request,
        payload={"profile_id": str(profile_id), "name": p.name},
    )
    await db.commit()
