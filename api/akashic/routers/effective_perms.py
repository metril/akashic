"""POST /api/v1/entries/{entry_id}/effective-permissions — read-only evaluator."""
import uuid

from akashic.routing import ApiRouter
from fastapi import Depends, HTTPException
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, check_source_access, require_scope
from akashic.auth.source_scope import ensure_source_allowed
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.acl import ACL
from akashic.schemas.effective import EffectivePerms, EffectivePermsRequest
from akashic.services.effective_perms import compute_effective

router = ApiRouter(prefix="/entries", tags=["effective-permissions"])
_acl_adapter = TypeAdapter(ACL)


@router.post(
    "/{entry_id}/effective-permissions",
    response_model=EffectivePerms,
)
async def post_effective_permissions(
    entry_id: uuid.UUID,
    request: EffectivePermsRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("identities:read", source_scoped=True),
) -> EffectivePerms:
    user = principal.user
    entry = (await db.execute(select(Entry).where(Entry.id == entry_id))).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    ensure_source_allowed(principal, entry.source_id, "Entry not found")
    await check_source_access(entry.source_id, principal, db)

    # entry.acl is JSONB; validate into the discriminated union (or None).
    acl_obj = _acl_adapter.validate_python(entry.acl) if entry.acl else None

    # source_security (for S3) — pull from the entry's source if present.
    source_security = None
    if acl_obj is not None and getattr(acl_obj, "type", None) == "s3":
        source = (await db.execute(select(Source).where(Source.id == entry.source_id))).scalar_one_or_none()
        if source is not None:
            source_security = source.security_metadata

    return compute_effective(
        acl=acl_obj,
        base_mode=entry.mode,
        base_uid=entry.uid,
        base_gid=entry.gid,
        principal=request.principal,
        groups=request.groups,
        source_security=source_security,
    )
