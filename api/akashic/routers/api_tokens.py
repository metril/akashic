"""Personal API tokens: your own under /me/tokens, everyone's under /admin/tokens."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.api_tokens import generate_token
from akashic.auth.dependencies import Principal, require_scope
from akashic.auth.scopes import SCOPES, forbidden_with_source_limit
from akashic.database import get_db
from akashic.models.api_token import ApiToken
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.api_token import (
    AdminApiTokenResponse,
    ApiTokenCreate,
    ApiTokenCreated,
    ApiTokenResponse,
    ApiTokenUpdate,
)
from akashic.services.audit import record_event

me_router = APIRouter(prefix="/me/tokens", tags=["tokens"])
admin_router = APIRouter(prefix="/admin/tokens", tags=["tokens"])


def _unprocessable(msg: str) -> HTTPException:
    return HTTPException(status_code=422, detail=msg)


def _check_scopes(scopes: list[str], allowed: frozenset[str], who: str) -> list[str]:
    unique = sorted(set(scopes))
    unknown = [s for s in unique if s not in SCOPES]
    if unknown:
        raise _unprocessable(f"Unknown scopes: {', '.join(unknown)}")
    beyond = [s for s in unique if s not in allowed]
    if beyond:
        raise _unprocessable(f"Scopes beyond {who}: {', '.join(beyond)}")
    return unique


async def _owned(token_id: uuid.UUID, user: User, db: AsyncSession) -> ApiToken:
    row = (
        await db.execute(select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user.id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Token not found")
    return row


async def _revoke(row: ApiToken, actor: User, db: AsyncSession, request: Request, *, admin: bool) -> None:
    if row.revoked_at is not None:
        return  # already revoked; idempotent
    row.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    await record_event(
        db=db, user=actor, event_type="api_token_revoked",
        payload={"token_id": str(row.id), "name": row.name, "owner_id": str(row.user_id), "by_admin": admin},
        request=request,
    )


@me_router.get("", response_model=list[ApiTokenResponse], summary="List your API tokens")
async def list_my_tokens(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("tokens:read"),
):
    rows = (
        await db.execute(
            select(ApiToken).where(ApiToken.user_id == principal.user.id).order_by(ApiToken.created_at.desc())
        )
    ).scalars().all()
    return rows


@me_router.post(
    "", response_model=ApiTokenCreated, status_code=status.HTTP_201_CREATED,
    summary="Create an API token",
    description=(
        "Returns the token once; it cannot be retrieved later. Its scopes must lie within yours. "
        "A token limited to sources (`source_ids`) cannot hold write scopes on other resources."
    ),
)
async def create_my_token(
    data: ApiTokenCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("tokens:write"),
):
    scopes = _check_scopes(data.scopes, principal.scopes, "your own")
    source_ids = sorted(set(data.source_ids)) if data.source_ids is not None else None
    if source_ids is not None:
        bad = forbidden_with_source_limit(scopes)
        if bad:
            raise _unprocessable(
                f"A token limited to sources cannot hold write scopes on other resources: {', '.join(bad)}"
            )
        found = set((await db.execute(select(Source.id).where(Source.id.in_(source_ids)))).scalars().all())
        missing = [str(s) for s in source_ids if s not in found]
        if missing:
            raise _unprocessable(f"Unknown sources: {', '.join(missing)}")
    expires_at = data.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise _unprocessable("expires_at must be in the future")

    plaintext, prefix, digest = generate_token()
    row = ApiToken(
        user_id=principal.user.id, name=data.name, token_prefix=prefix, token_hash=digest,
        scopes=scopes, source_ids=source_ids, expires_at=expires_at,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await record_event(
        db=db, user=principal.user, event_type="api_token_created",
        payload={
            "token_id": str(row.id), "name": row.name, "scopes": scopes,
            "source_ids": [str(s) for s in source_ids] if source_ids is not None else None,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        },
        request=request,
    )
    return ApiTokenCreated(**ApiTokenResponse.model_validate(row).model_dump(), token=plaintext)


@me_router.patch(
    "/{token_id}", response_model=ApiTokenResponse, summary="Rename or narrow an API token",
    description="Scopes can only be narrowed, never widened. To widen, create a new token.",
)
async def update_my_token(
    token_id: uuid.UUID,
    data: ApiTokenUpdate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("tokens:write"),
):
    row = await _owned(token_id, principal.user, db)
    if row.revoked_at is not None:
        raise HTTPException(status_code=409, detail="Token is revoked")
    if data.scopes is not None:
        row.scopes = _check_scopes(data.scopes, frozenset(row.scopes), "the token's current scopes")
    if data.name is not None:
        row.name = data.name
    await db.commit()
    await db.refresh(row)
    return row


@me_router.delete(
    "/{token_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Revoke an API token",
)
async def revoke_my_token(
    token_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("tokens:write"),
):
    row = await _owned(token_id, principal.user, db)
    await _revoke(row, principal.user, db, request, admin=False)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@admin_router.get("", response_model=list[AdminApiTokenResponse], summary="List every user's API tokens")
async def list_all_tokens(
    user_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("tokens:admin"),
):
    stmt = select(ApiToken, User.username).join(User, User.id == ApiToken.user_id)
    if user_id is not None:
        stmt = stmt.where(ApiToken.user_id == user_id)
    rows = (await db.execute(stmt.order_by(ApiToken.created_at.desc()))).all()
    return [
        AdminApiTokenResponse(
            **ApiTokenResponse.model_validate(t).model_dump(), user_id=t.user_id, username=username,
        )
        for t, username in rows
    ]


@admin_router.delete(
    "/{token_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Revoke any user's API token",
)
async def revoke_any_token(
    token_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("tokens:admin"),
):
    row = (await db.execute(select(ApiToken).where(ApiToken.id == token_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Token not found")
    await _revoke(row, principal.user, db, request, admin=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
