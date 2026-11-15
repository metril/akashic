"""CRUD for FsPerson + FsBinding (per-user identity claims)."""
from typing import Annotated
import uuid
from datetime import datetime

from akashic.routing import ApiRouter
from fastapi import Depends, HTTPException, Path, Query, Request, status
from akashic.schemas.base import ApiModel
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, require_scope
from akashic.database import get_db
from akashic.models.fs_person import FsBinding, FsPerson
from akashic.models.fs_unbound_identity import FsUnboundIdentity
from akashic.models.user import User
from akashic.schemas.identity import (
    FsBindingIn,
    FsBindingOut,
    FsBindingPatch,
    FsPersonIn,
    FsPersonOut,
    FsPersonPatch,
)
from akashic.services.audit import record_event

router = ApiRouter(prefix="/identities", tags=["identities"])


class UnboundIdentityOut(ApiModel):
    id: uuid.UUID = Field(description="Claim id.")
    user_id: uuid.UUID = Field(description="User the claim was seen for.")
    identity_type: str = Field(description="Kind of identifier: `posix_uid`, `sid`, `nfsv4_principal` or `s3_canonical`.")
    identifier: str = Field(description="The identifier value, e.g. a SID.")
    confidence: str = Field(description="How the claim was obtained, e.g. `claim`.")
    groups: list[str] = Field(description="Group identifiers that came with the claim.")
    first_seen_at: datetime = Field(description="When the claim was first seen at login.")
    last_seen_at: datetime = Field(description="When it was last seen.")

    model_config = {"json_schema_extra": {'examples': [{'id': '3b1f0c9e-5d0a-4e7c-8a41-9a6b2f5c7d10', 'user_id': '5a2d9f10-77c3-4b8e-9d21-4c0a8e1b6f33', 'identity_type': 'sid', 'identifier': 'S-1-5-21-1-2-3-1001', 'confidence': 'claim', 'groups': ['S-1-5-21-1-2-3-513'], 'first_seen_at': '2026-04-01T09:00:00Z', 'last_seen_at': '2026-04-27T22:00:00Z'}]}}


def _person_with_bindings(person: FsPerson, bindings: list[FsBinding]) -> FsPersonOut:
    return FsPersonOut(
        id=person.id,
        user_id=person.user_id,
        label=person.label,
        is_primary=person.is_primary,
        created_at=person.created_at,
        bindings=[FsBindingOut.model_validate(b) for b in bindings],
    )


async def _list_bindings(person_id: uuid.UUID, db: AsyncSession) -> list[FsBinding]:
    result = await db.execute(
        select(FsBinding).where(FsBinding.fs_person_id == person_id).order_by(FsBinding.created_at)
    )
    return list(result.scalars())


@router.get(
    "",
    response_model=list[FsPersonOut],
    summary="List your identities",
    description=(
        "The caller's own filesystem identities (personas), oldest first, each with its per- "
        "source bindings. Identities belong to a user; there is no way to list another user's. "
        "Requires scope `identities:read`."
    ),
)
async def list_identities(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("identities:read"),
) -> list[FsPersonOut]:
    user = principal.user
    persons = (await db.execute(
        select(FsPerson).where(FsPerson.user_id == user.id).order_by(FsPerson.created_at)
    )).scalars().all()
    if not persons:
        return []
    # Single bulk fetch of all bindings, then group in memory
    # (review D-I1). Pre-fix this loop ran one SELECT per persona —
    # 10–50 sequential round-trips on a typical SettingsIdentities
    # mount.
    person_ids = [p.id for p in persons]
    binding_rows = (await db.execute(
        select(FsBinding)
        .where(FsBinding.fs_person_id.in_(person_ids))
        .order_by(FsBinding.fs_person_id, FsBinding.created_at)
    )).scalars().all()
    bindings_by_person: dict[uuid.UUID, list[FsBinding]] = {}
    for b in binding_rows:
        bindings_by_person.setdefault(b.fs_person_id, []).append(b)
    return [
        _person_with_bindings(p, bindings_by_person.get(p.id, []))
        for p in persons
    ]


@router.post(
    "",
    response_model=FsPersonOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an identity",
    description=(
        "Creates a persona for the caller. Records an `identity_added` audit event. Requires "
        "scope `identities:write`."
    ),
)
async def create_identity(
    body: FsPersonIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("identities:write"),
) -> FsPersonOut:
    user = principal.user
    person = FsPerson(user_id=user.id, label=body.label, is_primary=body.is_primary)
    db.add(person)
    await db.commit()
    await db.refresh(person)
    await record_event(
        db=db, user=user,
        event_type="identity_added",
        payload={"fs_person_id": str(person.id), "fs_person_label": person.label},
        request=request,
    )
    return _person_with_bindings(person, [])


@router.patch(
    "/{person_id}",
    response_model=FsPersonOut,
    summary="Update an identity",
    description=(
        "Renames the identity or changes its primary flag. Requires scope `identities:write`.\n"
        "Errors: 404 if the identity does not exist or belongs to another user."
    ),
)
async def update_identity(
    person_id: Annotated[uuid.UUID, Path(description="Identity (person) id.")],
    body: FsPersonPatch,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("identities:write"),
) -> FsPersonOut:
    user = principal.user
    person = (await db.execute(
        select(FsPerson).where(FsPerson.id == person_id)
    )).scalar_one_or_none()
    if person is None or person.user_id != user.id:
        raise HTTPException(status_code=404, detail="Identity not found")
    if body.label is not None:
        person.label = body.label
    if body.is_primary is not None:
        person.is_primary = body.is_primary
    await db.commit()
    await db.refresh(person)
    bindings = await _list_bindings(person.id, db)
    return _person_with_bindings(person, bindings)


@router.delete(
    "/{person_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an identity",
    description=(
        "Deletes the identity and its bindings. Records an `identity_removed` audit event. "
        "Requires scope `identities:write`.\n"
        "Errors: 404 if the identity does not exist or belongs to another user."
    ),
)
async def delete_identity(
    person_id: Annotated[uuid.UUID, Path(description="Identity (person) id.")],
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("identities:write"),
) -> None:
    user = principal.user
    person = (await db.execute(
        select(FsPerson).where(FsPerson.id == person_id)
    )).scalar_one_or_none()
    if person is None or person.user_id != user.id:
        raise HTTPException(status_code=404, detail="Identity not found")
    label = person.label
    await db.delete(person)
    await db.commit()
    await record_event(
        db=db, user=user,
        event_type="identity_removed",
        payload={"fs_person_id": str(person_id), "fs_person_label": label},
        request=request,
    )


@router.post(
    "/{person_id}/bindings",
    response_model=FsBindingOut,
    status_code=status.HTTP_201_CREATED,
    summary="Bind an identity to a source",
    description=(
        "Declares who the identity is on one source (a POSIX uid, SID, NFSv4 principal or S3 "
        "canonical id) and which groups it belongs to. File listings are filtered by the bound "
        "principal and group tokens. Records a `binding_added` audit event.\n"
        "Requires scope `identities:write`.\n"
        "Errors: 404 if the identity does not exist or belongs to another user; 409 if a binding "
        "for this source already exists."
    ),
)
async def create_binding(
    person_id: Annotated[uuid.UUID, Path(description="Identity (person) id.")],
    body: FsBindingIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("identities:write"),
) -> FsBindingOut:
    user = principal.user
    person = (await db.execute(
        select(FsPerson).where(FsPerson.id == person_id)
    )).scalar_one_or_none()
    if person is None or person.user_id != user.id:
        raise HTTPException(status_code=404, detail="Identity not found")

    binding = FsBinding(
        fs_person_id=person.id,
        source_id=body.source_id,
        identity_type=body.identity_type,
        identifier=body.identifier,
        groups=body.groups,
        groups_source="manual",
        groups_resolved_at=None,
    )
    db.add(binding)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A binding for this source already exists")
    await db.refresh(binding)
    await record_event(
        db=db, user=user,
        event_type="binding_added",
        source_id=binding.source_id,
        payload={
            "fs_person_id": str(person.id),
            "source_id": str(binding.source_id),
            "identity_type": binding.identity_type,
            "identifier": binding.identifier,
        },
        request=request,
    )
    return FsBindingOut.model_validate(binding)


@router.patch(
    "/{person_id}/bindings/{binding_id}",
    response_model=FsBindingOut,
    summary="Update a binding",
    description=(
        "Changes the binding's identifier, identity type or groups. `groups_source` records where "
        "the groups came from and can only be changed by a caller holding `users:write`.\n"
        "Requires scope `identities:write`.\n"
        "Errors: 403 if a caller without `users:write` sets `groups_source`; 404 if the identity "
        "or binding does not exist."
    ),
)
async def update_binding(
    person_id: Annotated[uuid.UUID, Path(description="Identity (person) id.")],
    binding_id: Annotated[uuid.UUID, Path(description="Binding id.")],
    body: FsBindingPatch,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("identities:write"),
) -> FsBindingOut:
    user = principal.user
    person = (await db.execute(
        select(FsPerson).where(FsPerson.id == person_id)
    )).scalar_one_or_none()
    if person is None or person.user_id != user.id:
        raise HTTPException(status_code=404, detail="Identity not found")
    binding = (await db.execute(
        select(FsBinding).where(
            FsBinding.id == binding_id, FsBinding.fs_person_id == person.id
        )
    )).scalar_one_or_none()
    if binding is None:
        raise HTTPException(status_code=404, detail="Binding not found")

    if body.identity_type is not None:
        binding.identity_type = body.identity_type
    if body.identifier is not None:
        binding.identifier = body.identifier
    if body.groups is not None:
        binding.groups = body.groups
    # groups_source is the trust-provenance label ("claim", "ldap",
    # "name", "manual"). Only admins may patch it (review A-I8) —
    # otherwise a regular user can promote their manually-entered
    # binding to "claim" and any UI that gates on confidence (e.g.
    # SettingsIdentities' IdP-issued badge) is misled.
    if body.groups_source is not None:
        if not principal.has("users:write"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="groups_source can only be modified by an admin",
            )
        binding.groups_source = body.groups_source
    await db.commit()
    await db.refresh(binding)
    return FsBindingOut.model_validate(binding)


@router.delete(
    "/{person_id}/bindings/{binding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a binding",
    description=(
        "Removes the binding. Records a `binding_removed` audit event. Requires scope "
        "`identities:write`.\n"
        "Errors: 404 if the identity or binding does not exist."
    ),
)
async def delete_binding(
    person_id: Annotated[uuid.UUID, Path(description="Identity (person) id.")],
    binding_id: Annotated[uuid.UUID, Path(description="Binding id.")],
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("identities:write"),
) -> None:
    user = principal.user
    person = (await db.execute(
        select(FsPerson).where(FsPerson.id == person_id)
    )).scalar_one_or_none()
    if person is None or person.user_id != user.id:
        raise HTTPException(status_code=404, detail="Identity not found")
    binding = (await db.execute(
        select(FsBinding).where(
            FsBinding.id == binding_id, FsBinding.fs_person_id == person.id
        )
    )).scalar_one_or_none()
    if binding is None:
        raise HTTPException(status_code=404, detail="Binding not found")
    snapshot = {
        "fs_person_id": str(person.id),
        "source_id": str(binding.source_id),
        "identity_type": binding.identity_type,
        "identifier": binding.identifier,
    }
    binding_source_id = binding.source_id
    await db.delete(binding)
    await db.commit()
    await record_event(
        db=db, user=user,
        event_type="binding_removed",
        source_id=binding_source_id,
        payload=snapshot,
        request=request,
    )


# ── Unbound identities (Phase 2a/2b) ──────────────────────────────────────


@router.get(
    "/unbound",
    response_model=list[UnboundIdentityOut],
    summary="List unbound identity claims",
    description=(
        "OIDC identity claims that could not be bound to any source at login, most recently seen "
        "first. By default the caller's own are returned. Callers holding `users:read` may pass "
        "`user_id` for another user's, or omit it to see the whole backlog.\n"
        "Requires scope `identities:read`.\n"
        "Errors: 403 if a caller without `users:read` passes `user_id` for someone else."
    ),
)
async def list_unbound(
    user_id: Annotated[uuid.UUID | None, Query(description="Only claims of this user (administrators only for users other than yourself).")] = None,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("identities:read"),
) -> list[UnboundIdentityOut]:
    """OIDC identities Akashic couldn't bind to a source at login.

    Default scope is the requesting user's own unbound identities — so
    a regular OIDC user landing on Settings → Identities sees "your AD
    account has SID claim X but no Akashic source uses that domain"
    without needing admin rights.

    Admins additionally pass `?user_id=<uuid>` to see anyone's, or
    leave it unset to see the entire backlog (`spot the user with the
    weirdest unbound claims`). Non-admins passing `user_id` get a 403
    rather than a silent filter to their own — surfacing rights bugs
    is more important than hiding them. Ordered by last-seen
    descending."""
    user = principal.user
    sees_all_users = principal.has("users:read")

    if user_id is not None and not sees_all_users and user_id != user.id:
        raise HTTPException(
            status_code=403,
            detail="Only admins can view another user's unbound identities",
        )

    stmt = select(FsUnboundIdentity).order_by(FsUnboundIdentity.last_seen_at.desc())
    if user_id is not None:
        stmt = stmt.where(FsUnboundIdentity.user_id == user_id)
    elif not sees_all_users:
        # Non-admin without explicit user_id sees their own only.
        stmt = stmt.where(FsUnboundIdentity.user_id == user.id)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        UnboundIdentityOut(
            id=r.id,
            user_id=r.user_id,
            identity_type=r.identity_type,
            identifier=r.identifier,
            confidence=r.confidence,
            groups=list(r.groups or []),
            first_seen_at=r.first_seen_at,
            last_seen_at=r.last_seen_at,
        )
        for r in rows
    ]
