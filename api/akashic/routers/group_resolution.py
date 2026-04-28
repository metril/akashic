"""POST /api/identities/{person_id}/bindings/{binding_id}/resolve-groups."""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.config import settings
from akashic.database import get_db
from akashic.models.fs_person import FsBinding, FsPerson
from akashic.models.principal_groups_cache import PrincipalGroupsCache
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.identity import FsBindingOut
from akashic.services.audit import record_event
from akashic.services.group_resolver import (
    ResolutionFailed,
    UnsupportedResolution,
    resolve_groups,
)

router = APIRouter(prefix="/api/identities", tags=["identities"])


@router.post(
    "/{person_id}/bindings/{binding_id}/resolve-groups",
    response_model=FsBindingOut,
)
async def post_resolve_groups(
    person_id: uuid.UUID,
    binding_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FsBindingOut:
    person = (await db.execute(
        select(FsPerson).where(FsPerson.id == person_id)
    )).scalar_one_or_none()
    if person is None or person.user_id != user.id:
        raise HTTPException(status_code=404, detail="Identity not found")

    binding = (await db.execute(
        select(FsBinding).where(
            FsBinding.id == binding_id, FsBinding.fs_person_id == person.id,
        )
    )).scalar_one_or_none()
    if binding is None:
        raise HTTPException(status_code=404, detail="Binding not found")

    source = (await db.execute(
        select(Source).where(Source.id == binding.source_id)
    )).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Cache check.
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.group_cache_ttl_hours)
    cache_row = (await db.execute(
        select(PrincipalGroupsCache).where(
            PrincipalGroupsCache.source_id == source.id,
            PrincipalGroupsCache.identity_type == binding.identity_type,
            PrincipalGroupsCache.identifier == binding.identifier,
        )
    )).scalar_one_or_none()
    if cache_row is not None and cache_row.resolved_at >= cutoff:
        # Fresh — apply cached groups and return without backend hit.
        binding.groups = list(cache_row.groups)
        binding.groups_source = "auto"
        binding.groups_resolved_at = cache_row.resolved_at
        await db.commit()
        await db.refresh(binding)
        return FsBindingOut.model_validate(binding)

    # Resolve.
    try:
        result = await resolve_groups(source, binding)
    except UnsupportedResolution as exc:
        raise HTTPException(status_code=422, detail={"reason": "unsupported", "message": str(exc)})
    except ResolutionFailed as exc:
        raise HTTPException(status_code=422, detail={"reason": exc.reason, "message": str(exc)})

    binding.groups = list(result.groups)
    binding.groups_source = "auto"
    binding.groups_resolved_at = result.resolved_at

    # Upsert cache.
    cache_stmt = pg_insert(PrincipalGroupsCache).values(
        source_id=source.id,
        identity_type=binding.identity_type,
        identifier=binding.identifier,
        groups=list(result.groups),
        resolved_at=result.resolved_at,
    )
    cache_stmt = cache_stmt.on_conflict_do_update(
        index_elements=[
            PrincipalGroupsCache.source_id,
            PrincipalGroupsCache.identity_type,
            PrincipalGroupsCache.identifier,
        ],
        set_={"groups": cache_stmt.excluded.groups, "resolved_at": cache_stmt.excluded.resolved_at},
    )
    await db.execute(cache_stmt)
    await db.commit()
    await db.refresh(binding)

    await record_event(
        db=db, user=user,
        event_type="groups_auto_resolved",
        source_id=source.id,
        payload={
            "binding_id": str(binding.id),
            "fs_person_id": str(person.id),
            "identity_type": binding.identity_type,
            "identifier": binding.identifier,
            "resolved_count": len(result.groups),
            "source": result.source,
        },
        request=request,
    )

    return FsBindingOut.model_validate(binding)
