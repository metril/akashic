"""CRUD for FsPerson + FsBinding (per-user identity claims)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.models.fs_person import FsBinding, FsPerson
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

router = APIRouter(prefix="/api/identities", tags=["identities"])


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


@router.get("", response_model=list[FsPersonOut])
async def list_identities(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[FsPersonOut]:
    persons = (await db.execute(
        select(FsPerson).where(FsPerson.user_id == user.id).order_by(FsPerson.created_at)
    )).scalars().all()
    out = []
    for p in persons:
        bindings = await _list_bindings(p.id, db)
        out.append(_person_with_bindings(p, bindings))
    return out


@router.post("", response_model=FsPersonOut, status_code=status.HTTP_201_CREATED)
async def create_identity(
    body: FsPersonIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FsPersonOut:
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


@router.patch("/{person_id}", response_model=FsPersonOut)
async def update_identity(
    person_id: uuid.UUID,
    body: FsPersonPatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FsPersonOut:
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


@router.delete("/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_identity(
    person_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
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


@router.post("/{person_id}/bindings", response_model=FsBindingOut, status_code=status.HTTP_201_CREATED)
async def create_binding(
    person_id: uuid.UUID,
    body: FsBindingIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FsBindingOut:
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
    "/{person_id}/bindings/{binding_id}", response_model=FsBindingOut,
)
async def update_binding(
    person_id: uuid.UUID,
    binding_id: uuid.UUID,
    body: FsBindingPatch,
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
    if body.groups_source is not None:
        binding.groups_source = body.groups_source
    await db.commit()
    await db.refresh(binding)
    return FsBindingOut.model_validate(binding)


@router.delete(
    "/{person_id}/bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_binding(
    person_id: uuid.UUID,
    binding_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
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
