"""CRUD for reusable credential profiles.

Hosts and sources reference profiles via `credential_profile_id`.
The layered resolver in services/source_config applies a profile's
credentials under the inline values at scan time. See v0.5.9.

Admin-only writes; reads are open to authenticated users so the
host/source create forms can populate the profile picker.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from akashic.config import settings

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
    assert_smb_has_password,
    merge_sentinel_credentials,
)
from akashic.services.audit import record_event


router = APIRouter(prefix="/api/credential-profiles", tags=["credential-profiles"])


# v0.29.5 — fresh session for the post-PATCH retest. The request-
# scoped `db` is closed when the response returns; BackgroundTasks
# run after that, so a new engine + session is the right pattern
# (same one ingest.py uses for its background paths).
_bg_engine = None
_bg_sessionmaker: async_sessionmaker | None = None


def _bg_session() -> async_sessionmaker:
    global _bg_engine, _bg_sessionmaker
    if _bg_sessionmaker is None:
        _bg_engine = create_async_engine(settings.database_url)
        _bg_sessionmaker = async_sessionmaker(
            _bg_engine, class_=AsyncSession, expire_on_commit=False,
        )
    return _bg_sessionmaker


async def _bg_retest_for_profile(profile_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Background task wrapper for credential_retest.
    retest_sources_for_profile that owns its own session."""
    from akashic.services.credential_retest import retest_sources_for_profile
    try:
        async with _bg_session()() as db:
            await retest_sources_for_profile(
                db, profile_id=profile_id, user_id=user_id,
            )
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "credential_retest background task failed profile=%s: %s",
            profile_id, exc,
        )


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
    if data.type == "smb":
        assert_smb_has_password(data.credentials, where="credential_profile")
    # v0.29.5 — store credentials encrypted-at-rest. Plaintext column
    # left NULL; read path (services/source_config) decrypts on demand.
    from akashic.services.credential_crypto import encrypt_credentials
    profile = CredentialProfile(
        name=data.name,
        type=data.type,
        credentials=None,
        credentials_encrypted=encrypt_credentials(data.credentials),
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
    return CredentialProfileResponse.from_model(profile)


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
    return CredentialProfileResponse.from_model(p)


@router.patch("/{profile_id}", response_model=CredentialProfileResponse)
async def update_profile(
    profile_id: uuid.UUID,
    data: CredentialProfileUpdate,
    request: Request,
    background_tasks: BackgroundTasks,
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
    credentials_changed = False
    if data.credentials is not None:
        # v0.29.5 — sentinel-aware partial update against the
        # DECRYPTED current state. The plaintext column is gone post-
        # migration; load from `credentials_encrypted` to compute the
        # merge base, then re-encrypt the merged result.
        from akashic.services.credential_crypto import (
            decrypt_credentials,
            encrypt_credentials,
        )
        if p.credentials_encrypted is not None:
            current_creds = decrypt_credentials(bytes(p.credentials_encrypted))
        else:
            # Legacy row (pre-migration or pre-encryption write).
            current_creds = dict(p.credentials or {})
        merged = merge_sentinel_credentials(current_creds, data.credentials)
        if p.type == "smb":
            assert_smb_has_password(merged, where="credential_profile")
        if merged != current_creds:
            credentials_changed = True
        p.credentials_encrypted = encrypt_credentials(merged)
        p.credentials = None  # ensure legacy plaintext can't drift

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

    # v0.29.5 — when credentials actually changed, fan out a fresh
    # reachability probe against every source that derives its
    # credentials from this profile. Background task so the PATCH
    # response doesn't block on the probe round-trips.
    if credentials_changed:
        background_tasks.add_task(
            _bg_retest_for_profile, p.id, user.id,
        )

    return CredentialProfileResponse.from_model(p)


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
