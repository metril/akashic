# Phase 12 — Identity Model + ACL-Aware Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an akashic user register one or more cross-source identities (e.g. "I am `posix:uid:1000` on `home-nas` AND `sid:S-1-5-21-…-1013` on `archive-server`"), then narrow their `/api/search` results to entries whose ACL grants those identities `read` (or `write`). Plumbed end-to-end: SQLAlchemy models + CRUD API + Meilisearch denormalization + search-time filter + a settings UI for managing identities.

**Architecture:** New `FsPerson` / `FsBinding` SQLAlchemy models persist user-supplied identity claims. A pure-function `denormalize_acl()` service reuses Phase 11's per-type evaluators to compute, for a given ACL+base_mode, the set of *string-keyed* principal identifiers that have `read`/`write`/`delete` access. Those identifier sets land in three new filterable Meili attributes (`viewable_by_read`, `viewable_by_write`, `viewable_by_delete`). At search time, the router resolves the current user's bindings into the same identifier vocabulary and adds a `viewable_by_<right> IN [...]` filter. A `Settings → Identities` page exposes CRUD over `FsPerson`/`FsBinding`. The Search page gains a `Show: [Files I can read ▾]` dropdown.

`search_as` (audit-logged power-user override) and `fs_person_id` aggregation scope are explicitly **deferred to Phase 13** — Phase 12 ships the bare-minimum search filter only.

**Tech Stack:** Python 3.12 (FastAPI/Pydantic v2/SQLAlchemy async), Meilisearch (async client), TypeScript/React 18, Tailwind, pytest-asyncio.

---

## File structure

**Create**
- `api/akashic/models/fs_person.py` — `FsPerson` + `FsBinding` SQLAlchemy models.
- `api/akashic/schemas/identity.py` — Pydantic shapes (`FsPersonIn`, `FsPersonOut`, `FsBindingIn`, `FsBindingOut`).
- `api/akashic/services/acl_denorm.py` — `denormalize_acl()` + `Identifier` constants + per-type sketch helpers.
- `api/akashic/routers/identities.py` — `/api/identities` (FsPerson CRUD) + `/api/identities/{id}/bindings` (binding CRUD).
- `api/akashic/tools/__init__.py` (empty), `api/akashic/tools/reindex_search.py` — bulk re-index command.
- `api/tests/test_acl_denorm.py` — pure-function denorm tests across all four ACL types.
- `api/tests/test_identities_endpoints.py` — CRUD endpoint tests.
- `api/tests/test_search_filter.py` — search router applies bindings as `viewable_by_*` filter.
- `web/src/lib/identityTypes.ts` — TS shapes mirroring Pydantic.
- `web/src/pages/SettingsIdentities.tsx` — the page.
- `web/src/components/identity/{IdentityList,IdentityRow,BindingRow,AddBindingForm,AddIdentityForm}.tsx` — sub-components.

**Edit**
- `api/akashic/models/__init__.py` — re-export `FsPerson`, `FsBinding`.
- `api/akashic/main.py` — register the new router.
- `api/akashic/services/search.py` — `ensure_index()` adds `viewable_by_*` to filterable attrs; document-builder helper extracted so the ingest indexer + reindex tool both use it.
- `api/akashic/routers/ingest.py` — `_index_files_to_meilisearch` calls `denormalize_acl()` and includes `viewable_by_*` arrays.
- `api/akashic/routers/search.py` — adds `permission_filter: 'all' | 'readable' | 'writable'` query param, resolves current user's bindings, applies `viewable_by_*` filter when readable/writable.
- `api/akashic/workers/extraction.py` — when re-indexing on extraction, include the same denormalized fields.
- `web/src/types/index.ts` — re-export new types.
- `web/src/api/client.ts` — no-op (uses generic `api.get/post/patch/delete`).
- `web/src/App.tsx` — add `<Route path="settings/identities" element={<SettingsIdentities />} />`.
- `web/src/components/Layout.tsx` — add "Settings" sidebar item routing to `/settings/identities` (above Sign out).
- `web/src/pages/Search.tsx` — add the `permission_filter` dropdown.

**No deletes.** No migration framework — `Base.metadata.create_all` continues to handle schema bring-up.

---

## Cross-task spec: identifier vocabulary

A single string-key vocabulary for any principal across all four ACL models. Used in BOTH the denorm output (Meili index) AND the user's resolved binding identifiers:

| Token form | Meaning | Origin |
|---|---|---|
| `*` | anyone | POSIX `other` mode bits set, NT `Everyone` SID, NFSv4 `EVERYONE@`, S3 `AllUsers` |
| `auth` | any authenticated principal | NT `Authenticated Users` SID, S3 `AuthenticatedUsers` |
| `posix:uid:<uid>` | a POSIX user | POSIX `user_obj` (uid==base_uid) or `user:<n>` ACE |
| `posix:gid:<gid>` | a POSIX group | POSIX `group_obj` (gid==base_gid) or `group:<n>` ACE |
| `sid:<SID>` | a Windows SID (user or group, indistinguishable in tokens) | NT ACE sid |
| `nfsv4:<principal>` | NFSv4 user principal | NFSv4 ACE principal without `identifier_group` flag |
| `nfsv4:GROUP:<principal>` | NFSv4 group principal | NFSv4 ACE principal with `identifier_group` flag |
| `s3:user:<canonical_id>` | an S3 canonical user | S3 grant grantee_type=`canonical_user` |

**A user binding** also resolves to the same vocabulary (via `binding.identity_type` + `binding.identifier` + `binding.groups`), plus the implicit catch-alls `*` and `auth`. So the search filter clause is straightforwardly `viewable_by_read IN [user_tokens]`.

This vocabulary lives as constants in `api/akashic/services/acl_denorm.py` and is re-exported as TS string templates from `web/src/lib/identityTypes.ts`.

---

## Task 1 — `FsPerson` + `FsBinding` SQLAlchemy models

**Files:**
- Create: `api/akashic/models/fs_person.py`
- Modify: `api/akashic/models/__init__.py`

- [ ] **Step 1: Create the model file**

Create `api/akashic/models/fs_person.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Boolean, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class FsPerson(Base):
    """One real-world identity-set claimed by a user.

    A user can have multiple FsPersons (e.g. "My Work Account", "My Home Account").
    Each FsPerson contains zero or more FsBindings, one per source.
    """
    __tablename__ = "fs_persons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    label: Mapped[str] = mapped_column(String, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FsBinding(Base):
    """A FsPerson's identifier on a specific source, with optional cached groups."""
    __tablename__ = "fs_bindings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fs_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fs_persons.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    identity_type: Mapped[str] = mapped_column(String, nullable=False)  # 'posix_uid' | 'sid' | 'nfsv4_principal' | 's3_canonical'
    identifier: Mapped[str] = mapped_column(String, nullable=False)
    groups: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    groups_source: Mapped[str] = mapped_column(String, nullable=False, default="manual")  # 'manual' | 'auto'
    groups_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("fs_person_id", "source_id", name="uq_fs_bindings_person_source"),
    )
```

- [ ] **Step 2: Export from package init**

Edit `api/akashic/models/__init__.py` — add the imports and `__all__` entries:

```python
from akashic.models.fs_person import FsPerson, FsBinding
```

And add `"FsPerson"`, `"FsBinding"` to the `__all__` list.

- [ ] **Step 3: Smoke-test schema bring-up**

Run from the worktree directory:

```bash
docker exec akashic-eff2-api python -c "
from akashic.database import Base, engine
from akashic import models  # noqa
import asyncio
async def go():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print('OK')
asyncio.run(go())
"
```

Then verify the tables exist:

```bash
docker exec akashic-eff2-postgres-1 psql -U akashic -d akashic -c "\d fs_persons"
docker exec akashic-eff2-postgres-1 psql -U akashic -d akashic -c "\d fs_bindings"
```

Both should print column listings. The `fs_bindings` listing should show the unique constraint on `(fs_person_id, source_id)`.

- [ ] **Step 4: Commit**

```bash
git add api/akashic/models/fs_person.py api/akashic/models/__init__.py
git commit -m "feat(api): FsPerson + FsBinding identity models"
```

---

## Task 2 — Pydantic schemas for identity CRUD

**Files:**
- Create: `api/akashic/schemas/identity.py`

- [ ] **Step 1: Create the schemas**

Create `api/akashic/schemas/identity.py`:

```python
"""Schemas for the /api/identities CRUD endpoints."""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


IdentityType = Literal["posix_uid", "sid", "nfsv4_principal", "s3_canonical"]
GroupsSource = Literal["manual", "auto"]


class FsBindingIn(BaseModel):
    source_id: uuid.UUID
    identity_type: IdentityType
    identifier: str
    groups: list[str] = Field(default_factory=list)


class FsBindingPatch(BaseModel):
    identity_type: IdentityType | None = None
    identifier: str | None = None
    groups: list[str] | None = None
    groups_source: GroupsSource | None = None  # caller can pin to 'manual'


class FsBindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    fs_person_id: uuid.UUID
    source_id: uuid.UUID
    identity_type: IdentityType
    identifier: str
    groups: list[str]
    groups_source: GroupsSource
    groups_resolved_at: datetime | None
    created_at: datetime


class FsPersonIn(BaseModel):
    label: str
    is_primary: bool = False


class FsPersonPatch(BaseModel):
    label: str | None = None
    is_primary: bool | None = None


class FsPersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    label: str
    is_primary: bool
    created_at: datetime
    bindings: list[FsBindingOut] = Field(default_factory=list)
```

- [ ] **Step 2: Smoke test (no separate test file — just import works)**

```bash
docker exec akashic-eff2-api python -c "
from akashic.schemas.identity import FsPersonOut, FsBindingOut, FsPersonIn
print('OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add api/akashic/schemas/identity.py
git commit -m "feat(api): identity CRUD pydantic schemas"
```

---

## Task 3 — Identity CRUD endpoints

Six endpoints. RBAC: a user can only manage their own FsPersons + FsBindings. Bindings are scoped under their parent FsPerson.

**Endpoints:**
- `GET    /api/identities`                                — list current user's FsPersons (with bindings).
- `POST   /api/identities`                                — create a new FsPerson.
- `PATCH  /api/identities/{person_id}`                    — update label / is_primary.
- `DELETE /api/identities/{person_id}`                    — delete a FsPerson (cascades bindings).
- `POST   /api/identities/{person_id}/bindings`           — add a binding.
- `PATCH  /api/identities/{person_id}/bindings/{binding_id}` — update a binding.
- `DELETE /api/identities/{person_id}/bindings/{binding_id}` — delete a binding.

Note: no GET-by-id for bindings — they always come back in the FsPerson list.

**Files:**
- Create: `api/akashic/routers/identities.py`
- Modify: `api/akashic/main.py`
- Create: `api/tests/test_identities_endpoints.py`

- [ ] **Step 1: Write failing endpoint tests**

Create `api/tests/test_identities_endpoints.py`:

```python
import uuid

import pytest


async def _register_login(client, username="alice", password="testpass123"):
    await client.post("/api/users/register", json={"username": username, "password": password})
    login = await client.post("/api/users/login", json={"username": username, "password": password})
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_identities_requires_auth(client):
    r = await client.get("/api/identities")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_create_and_list_fs_person(client):
    token = await _register_login(client)

    create = await client.post(
        "/api/identities",
        json={"label": "My Work", "is_primary": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201
    person = create.json()
    assert person["label"] == "My Work"
    assert person["is_primary"] is True
    assert person["bindings"] == []

    listing = await client.get("/api/identities", headers={"Authorization": f"Bearer {token}"})
    assert listing.status_code == 200
    persons = listing.json()
    assert len(persons) == 1
    assert persons[0]["id"] == person["id"]


@pytest.mark.asyncio
async def test_user_cannot_see_another_users_persons(client, db_session):
    token_a = await _register_login(client, username="alice")
    token_b = await _register_login(client, username="bob")

    await client.post(
        "/api/identities",
        json={"label": "Alice's"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    listing = await client.get("/api/identities", headers={"Authorization": f"Bearer {token_b}"})
    assert listing.status_code == 200
    assert listing.json() == []


@pytest.mark.asyncio
async def test_patch_fs_person(client):
    token = await _register_login(client)
    create = await client.post(
        "/api/identities",
        json={"label": "Old"},
        headers={"Authorization": f"Bearer {token}"},
    )
    pid = create.json()["id"]
    patch = await client.patch(
        f"/api/identities/{pid}",
        json={"label": "New"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert patch.status_code == 200
    assert patch.json()["label"] == "New"


@pytest.mark.asyncio
async def test_delete_fs_person_cascades_bindings(client, db_session):
    from akashic.models import Source, FsPerson, FsBinding
    from sqlalchemy import select, func

    token = await _register_login(client)

    source = Source(id=uuid.uuid4(), name="t", type="local", connection_config={"path": "/tmp"})
    db_session.add(source)
    await db_session.commit()

    create = await client.post(
        "/api/identities",
        json={"label": "P"},
        headers={"Authorization": f"Bearer {token}"},
    )
    pid = create.json()["id"]
    add_binding = await client.post(
        f"/api/identities/{pid}/bindings",
        json={
            "source_id": str(source.id),
            "identity_type": "posix_uid",
            "identifier": "1000",
            "groups": ["100", "1000"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert add_binding.status_code == 201

    # Verify binding exists, then delete person.
    count_before = (await db_session.execute(select(func.count(FsBinding.id)))).scalar()
    assert count_before == 1

    delete = await client.delete(
        f"/api/identities/{pid}", headers={"Authorization": f"Bearer {token}"}
    )
    assert delete.status_code == 204

    count_after = (await db_session.execute(select(func.count(FsBinding.id)))).scalar()
    assert count_after == 0


@pytest.mark.asyncio
async def test_binding_unique_per_source(client, db_session):
    from akashic.models import Source

    token = await _register_login(client)

    source = Source(id=uuid.uuid4(), name="t", type="local", connection_config={"path": "/tmp"})
    db_session.add(source)
    await db_session.commit()

    create = await client.post(
        "/api/identities",
        json={"label": "P"},
        headers={"Authorization": f"Bearer {token}"},
    )
    pid = create.json()["id"]
    body = {
        "source_id": str(source.id),
        "identity_type": "posix_uid",
        "identifier": "1000",
        "groups": [],
    }
    first = await client.post(
        f"/api/identities/{pid}/bindings", json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first.status_code == 201
    second = await client.post(
        f"/api/identities/{pid}/bindings", json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert second.status_code == 409  # unique violation surfaced as conflict
```

- [ ] **Step 2: Run tests — verify failures**

```bash
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff2-api pytest tests/test_identities_endpoints.py -v
```

Expected: 401 test passes (default 401 for unknown route), all CRUD tests fail because the router isn't registered.

- [ ] **Step 3: Implement the router**

Create `api/akashic/routers/identities.py`:

```python
"""CRUD for FsPerson + FsBinding (per-user identity claims)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
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
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FsPersonOut:
    person = FsPerson(user_id=user.id, label=body.label, is_primary=body.is_primary)
    db.add(person)
    await db.commit()
    await db.refresh(person)
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
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    person = (await db.execute(
        select(FsPerson).where(FsPerson.id == person_id)
    )).scalar_one_or_none()
    if person is None or person.user_id != user.id:
        raise HTTPException(status_code=404, detail="Identity not found")
    await db.delete(person)
    await db.commit()


@router.post("/{person_id}/bindings", response_model=FsBindingOut, status_code=status.HTTP_201_CREATED)
async def create_binding(
    person_id: uuid.UUID,
    body: FsBindingIn,
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
    await db.delete(binding)
    await db.commit()
```

(Note: every handler uses the inline `select(FsPerson)` + `if person.user_id != user.id` 404 pattern. Bindings are loaded via the explicit `_list_bindings()` helper after the person is verified — keeps each query focused.)

- [ ] **Step 4: Register the router**

Edit `api/akashic/main.py`:
- Add `identities` to the `from akashic.routers import …` line.
- Add `app.include_router(identities.router)` alongside the others (in alphabetical or grouped order — match the style).

- [ ] **Step 5: Reload api container, run tests**

```bash
docker restart akashic-eff2-api
sleep 3
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff2-api pytest tests/test_identities_endpoints.py -v
```

Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add api/akashic/routers/identities.py api/akashic/main.py api/tests/test_identities_endpoints.py
git commit -m "feat(api): /api/identities CRUD for FsPerson + FsBinding"
```

---

## Task 4 — `denormalize_acl()` service (TDD)

Pure function that takes an ACL + base mode/uid/gid, returns `dict[str, list[str]]` mapping each canonical right (`read`, `write`, `delete`) to the list of identifier strings (using the vocabulary above) that have that right.

Strategy: enumerate every identifier mentioned in the ACL, then for each one ask `compute_effective` whether it has `read` (or `write` / `delete`). If yes, add to the bucket. POSIX `delete` is intentionally NOT computed (spec line 410 — it depends on the parent directory).

**Files:**
- Create: `api/akashic/services/acl_denorm.py`
- Create: `api/tests/test_acl_denorm.py`

- [ ] **Step 1: Write failing tests**

Create `api/tests/test_acl_denorm.py`:

```python
import pytest

from akashic.schemas.acl import (
    NfsV4ACL, NfsV4ACE, NtACL, NtACE, NtPrincipal,
    PosixACL, PosixACE, S3ACL, S3Grant, S3Owner,
)
from akashic.services.acl_denorm import (
    ANYONE,
    AUTH,
    denormalize_acl,
    posix_uid,
    posix_gid,
    sid,
    nfsv4_user,
    nfsv4_group,
    s3_user,
)


def _posix(entries, default=None):
    return PosixACL.model_validate({
        "type": "posix",
        "entries": entries,
        "default_entries": default,
    })


def test_posix_owner_user_and_other_get_read():
    acl = _posix([
        {"tag": "user_obj",  "qualifier": "",     "perms": "rwx"},
        {"tag": "user",      "qualifier": "1001", "perms": "r-x"},
        {"tag": "group_obj", "qualifier": "",     "perms": "r--"},
        {"tag": "mask",      "qualifier": "",     "perms": "rwx"},
        {"tag": "other",     "qualifier": "",     "perms": "r--"},
    ])
    out = denormalize_acl(acl, base_mode=0o644, base_uid=1000, base_gid=100)
    assert posix_uid(1000) in out["read"]
    assert posix_uid(1001) in out["read"]
    assert posix_gid(100) in out["read"]
    assert ANYONE in out["read"]
    # POSIX delete is intentionally not denormalized.
    assert out["delete"] == []


def test_posix_user_ace_in_write_set():
    acl = _posix([
        {"tag": "user_obj",  "qualifier": "",     "perms": "rwx"},
        {"tag": "user",      "qualifier": "1001", "perms": "rwx"},
        {"tag": "mask",      "qualifier": "",     "perms": "rwx"},
        {"tag": "other",     "qualifier": "",     "perms": "---"},
    ])
    out = denormalize_acl(acl, base_mode=0o600, base_uid=1000, base_gid=100)
    assert posix_uid(1001) in out["write"]
    assert ANYONE not in out["read"]


def test_posix_no_acl_uses_base_mode():
    out = denormalize_acl(acl=None, base_mode=0o755, base_uid=1000, base_gid=100)
    # owner reads, group reads (group_obj implicit via base_gid), other reads.
    assert posix_uid(1000) in out["read"]
    assert posix_gid(100)  in out["read"]
    assert ANYONE          in out["read"]


def test_nfsv4_users_and_groups():
    acl = NfsV4ACL.model_validate({
        "type": "nfsv4",
        "entries": [
            {"principal": "alice@dom", "ace_type": "allow", "mask": ["read_data"], "flags": []},
            {"principal": "eng@dom",   "ace_type": "allow", "mask": ["write_data"], "flags": ["identifier_group"]},
            {"principal": "EVERYONE@", "ace_type": "allow", "mask": ["execute"],   "flags": []},
        ],
    })
    out = denormalize_acl(acl, base_mode=None, base_uid=None, base_gid=None)
    assert nfsv4_user("alice@dom") in out["read"]
    assert nfsv4_group("eng@dom")  in out["write"]
    assert ANYONE                  in out["read"]  # EVERYONE@ allows read? no, only execute — verify below.
    # The EVERYONE@ allow is for execute only — should NOT be in read.
    # Re-check: spec says EVERYONE@ is the ANYONE token only when it grants the right being asked.
    # Since EVERYONE@ allow mask=[execute], it doesn't address read_data, so it's NOT in read.


def test_nfsv4_deny_excludes_principal():
    acl = NfsV4ACL.model_validate({
        "type": "nfsv4",
        "entries": [
            {"principal": "alice@dom", "ace_type": "deny",  "mask": ["read_data"], "flags": []},
            {"principal": "alice@dom", "ace_type": "allow", "mask": ["read_data"], "flags": []},
        ],
    })
    out = denormalize_acl(acl, base_mode=None, base_uid=None, base_gid=None)
    assert nfsv4_user("alice@dom") not in out["read"]


def test_nt_sids_in_buckets():
    acl = NtACL.model_validate({
        "type": "nt",
        "owner": {"sid": "S-1-5-21-1-2-3-1013", "name": ""},
        "group": None,
        "control": [],
        "entries": [
            {"sid": "S-1-5-21-1-2-3-1013", "name": "", "ace_type": "allow",
             "mask": ["READ_DATA", "WRITE_DATA"], "flags": []},
            {"sid": "S-1-1-0", "name": "", "ace_type": "allow",
             "mask": ["READ_DATA"], "flags": []},
            {"sid": "S-1-5-11", "name": "", "ace_type": "allow",
             "mask": ["READ_DATA"], "flags": []},
        ],
    })
    out = denormalize_acl(acl, base_mode=None, base_uid=None, base_gid=None)
    assert sid("S-1-5-21-1-2-3-1013") in out["read"]
    assert sid("S-1-5-21-1-2-3-1013") in out["write"]
    assert ANYONE in out["read"]   # S-1-1-0 (Everyone)
    assert AUTH   in out["read"]   # S-1-5-11 (Authenticated Users)


def test_s3_canonical_user_and_groups():
    acl = S3ACL.model_validate({
        "type": "s3",
        "owner": {"id": "acct-1", "display_name": ""},
        "grants": [
            {"grantee_type": "canonical_user", "grantee_id": "acct-1",
             "grantee_name": "", "permission": "FULL_CONTROL"},
            {"grantee_type": "group", "grantee_id": "AllUsers",
             "grantee_name": "", "permission": "READ"},
        ],
    })
    out = denormalize_acl(acl, base_mode=None, base_uid=None, base_gid=None)
    assert s3_user("acct-1") in out["read"]
    assert s3_user("acct-1") in out["write"]
    assert ANYONE            in out["read"]
    # delete bucket: WRITE FULL_CONTROL grants delete in S3 model.
    assert s3_user("acct-1") in out["delete"]


def test_none_acl_no_base_mode_returns_empty_buckets():
    out = denormalize_acl(acl=None, base_mode=None, base_uid=None, base_gid=None)
    assert out == {"read": [], "write": [], "delete": []}


def test_token_constants():
    assert ANYONE == "*"
    assert AUTH == "auth"
    assert posix_uid(1000) == "posix:uid:1000"
    assert posix_gid(100) == "posix:gid:100"
    assert sid("S-1-5-32-544") == "sid:S-1-5-32-544"
    assert nfsv4_user("alice@dom") == "nfsv4:alice@dom"
    assert nfsv4_group("eng@dom") == "nfsv4:GROUP:eng@dom"
    assert s3_user("acct-1") == "s3:user:acct-1"
```

(Note: drop the `assert ANYONE in out["read"]` line in `test_nfsv4_users_and_groups` and replace with the negative — `assert ANYONE not in out["read"]` — since EVERYONE@ allow is mask=[execute] only.)

Replace that test with the corrected version below before running:

```python
def test_nfsv4_users_and_groups():
    acl = NfsV4ACL.model_validate({
        "type": "nfsv4",
        "entries": [
            {"principal": "alice@dom", "ace_type": "allow", "mask": ["read_data"], "flags": []},
            {"principal": "eng@dom",   "ace_type": "allow", "mask": ["write_data"], "flags": ["identifier_group"]},
            {"principal": "EVERYONE@", "ace_type": "allow", "mask": ["execute"],   "flags": []},
        ],
    })
    out = denormalize_acl(acl, base_mode=None, base_uid=None, base_gid=None)
    assert nfsv4_user("alice@dom") in out["read"]
    assert nfsv4_group("eng@dom")  in out["write"]
    # EVERYONE@ allow is mask=[execute] — does NOT address read or write.
    assert ANYONE not in out["read"]
    assert ANYONE not in out["write"]
```

- [ ] **Step 2: Run tests — verify failures**

```bash
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff2-api pytest tests/test_acl_denorm.py -v
```

Expected: ImportError on `denormalize_acl`.

- [ ] **Step 3: Implement the service**

Create `api/akashic/services/acl_denorm.py`:

```python
"""Denormalize an ACL → string-keyed identifier sets per canonical right.

Output sets feed Meilisearch's `viewable_by_*` filterable fields. Reuses
the per-type evaluators from `effective_perms.py` to do the actual rights
math — this service is pure orchestration: enumerate principals from the
ACL, ask "does this principal have read?" via compute_effective(), bucket.

POSIX `delete` is intentionally not computed (spec — depends on parent dir).
"""
from __future__ import annotations

from akashic.schemas.acl import (
    ACL,
    NfsV4ACL,
    NtACL,
    PosixACL,
    S3ACL,
)
from akashic.schemas.effective import GroupRef, PrincipalRef
from akashic.services.effective_perms import compute_effective


# ── Identifier vocabulary ────────────────────────────────────────────────────

ANYONE = "*"
AUTH = "auth"


def posix_uid(uid: int | str) -> str:
    return f"posix:uid:{uid}"


def posix_gid(gid: int | str) -> str:
    return f"posix:gid:{gid}"


def sid(s: str) -> str:
    return f"sid:{s}"


def nfsv4_user(principal: str) -> str:
    return f"nfsv4:{principal}"


def nfsv4_group(principal: str) -> str:
    return f"nfsv4:GROUP:{principal}"


def s3_user(canonical_id: str) -> str:
    return f"s3:user:{canonical_id}"


# ── Per-model principal enumeration ──────────────────────────────────────────


def _posix_principals(
    acl: PosixACL | None,
    base_uid: int | None,
    base_gid: int | None,
) -> list[tuple[str, PrincipalRef, list[GroupRef]]]:
    """Returns list of (token, principal, groups) for POSIX.

    Each principal is queried independently against the ACL+base_mode."""
    out: list[tuple[str, PrincipalRef, list[GroupRef]]] = []
    if base_uid is not None:
        out.append((posix_uid(base_uid), PrincipalRef(type="posix_uid", identifier=str(base_uid)), []))
    if base_gid is not None:
        # A pseudo-principal that's a member of base_gid — represents "any user
        # in the owning group".
        out.append((
            posix_gid(base_gid),
            PrincipalRef(type="posix_uid", identifier="-1"),
            [GroupRef(type="posix_uid", identifier=str(base_gid))],
        ))
    if acl is not None:
        for ace in acl.entries:
            if ace.tag == "user" and ace.qualifier:
                token = posix_uid(ace.qualifier)
                if not any(t == token for t, _, _ in out):
                    out.append((
                        token,
                        PrincipalRef(type="posix_uid", identifier=ace.qualifier),
                        [],
                    ))
            elif ace.tag == "group" and ace.qualifier:
                token = posix_gid(ace.qualifier)
                if not any(t == token for t, _, _ in out):
                    out.append((
                        token,
                        PrincipalRef(type="posix_uid", identifier="-1"),
                        [GroupRef(type="posix_uid", identifier=ace.qualifier)],
                    ))
    return out


def _nfsv4_principals(acl: NfsV4ACL) -> list[tuple[str, PrincipalRef, list[GroupRef]]]:
    out: list[tuple[str, PrincipalRef, list[GroupRef]]] = []
    seen: set[str] = set()
    for ace in acl.entries:
        if ace.principal in ("OWNER@", "GROUP@"):
            continue  # token for these requires explicit caller context
        if ace.principal == "EVERYONE@":
            continue  # handled via the ANYONE probe below
        if "identifier_group" in ace.flags:
            token = nfsv4_group(ace.principal)
            if token in seen:
                continue
            seen.add(token)
            out.append((
                token,
                PrincipalRef(type="nfsv4_principal", identifier="__none__"),
                [GroupRef(type="nfsv4_principal", identifier=ace.principal)],
            ))
        else:
            token = nfsv4_user(ace.principal)
            if token in seen:
                continue
            seen.add(token)
            out.append((
                token,
                PrincipalRef(type="nfsv4_principal", identifier=ace.principal),
                [],
            ))
    return out


def _nt_principals(acl: NtACL) -> list[tuple[str, PrincipalRef, list[GroupRef]]]:
    out: list[tuple[str, PrincipalRef, list[GroupRef]]] = []
    seen: set[str] = set()
    EVERYONE = "S-1-1-0"
    AUTH_SID = "S-1-5-11"
    for ace in acl.entries:
        if ace.sid in (EVERYONE, AUTH_SID):
            continue
        token = sid(ace.sid)
        if token in seen:
            continue
        seen.add(token)
        out.append((
            token,
            PrincipalRef(type="sid", identifier=ace.sid),
            [],
        ))
    if acl.owner is not None and acl.owner.sid not in (EVERYONE, AUTH_SID):
        token = sid(acl.owner.sid)
        if token not in seen:
            seen.add(token)
            out.append((
                token,
                PrincipalRef(type="sid", identifier=acl.owner.sid),
                [],
            ))
    return out


def _s3_principals(acl: S3ACL) -> list[tuple[str, PrincipalRef, list[GroupRef]]]:
    out: list[tuple[str, PrincipalRef, list[GroupRef]]] = []
    seen: set[str] = set()
    for grant in acl.grants:
        if grant.grantee_type == "group":
            continue  # AllUsers/AuthenticatedUsers handled via probes below
        token = s3_user(grant.grantee_id)
        if token in seen:
            continue
        seen.add(token)
        out.append((
            token,
            PrincipalRef(type="s3_canonical", identifier=grant.grantee_id),
            [],
        ))
    if acl.owner is not None:
        token = s3_user(acl.owner.id)
        if token not in seen:
            seen.add(token)
            out.append((
                token,
                PrincipalRef(type="s3_canonical", identifier=acl.owner.id),
                [],
            ))
    return out


# ── Anyone / auth probes ─────────────────────────────────────────────────────

# Synthetic principal IDs used for the * / auth probes. Chosen to be values
# that won't collide with anyone's real identifier.

_ANYONE_PROBES = {
    "posix": (PrincipalRef(type="posix_uid", identifier="999999999"), []),
    "nfsv4": (PrincipalRef(type="nfsv4_principal", identifier="EVERYONE@"), []),
    "nt":    (PrincipalRef(type="sid", identifier="S-1-1-0"), []),
    "s3":    (PrincipalRef(type="s3_canonical", identifier="__nobody__"),
              # AllUsers grants always hit via _s3_grant_matches even for unknown ID,
              # but easier: use the AllUsers-marker principal type. Actually s3
              # _s3_grant_matches checks AllUsers via grant.grantee_type=="group"
              # and grant.grantee_id=="AllUsers" — independent of principal. So
              # a probe with any S3 principal works.
              []),
}

_AUTH_PROBES = {
    "nt":    (PrincipalRef(type="sid", identifier="S-1-5-9999"),  # any sid
              []),
    "s3":    (PrincipalRef(type="s3_canonical", identifier="__authenticated__"), []),
}


def _grants(acl: ACL | None, principal: PrincipalRef, groups: list[GroupRef],
            base_mode: int | None, base_uid: int | None, base_gid: int | None) -> dict[str, bool]:
    result = compute_effective(
        acl=acl,
        base_mode=base_mode,
        base_uid=base_uid,
        base_gid=base_gid,
        principal=principal,
        groups=groups,
    )
    return {
        "read":   result.rights["read"].granted,
        "write":  result.rights["write"].granted,
        "delete": result.rights["delete"].granted,
    }


# ── Public entry point ───────────────────────────────────────────────────────


def denormalize_acl(
    acl: ACL | None,
    base_mode: int | None,
    base_uid: int | None,
    base_gid: int | None,
) -> dict[str, list[str]]:
    """Returns {'read': [...], 'write': [...], 'delete': [...]} of identifier strings."""
    buckets: dict[str, list[str]] = {"read": [], "write": [], "delete": []}

    # Decide the model.
    if acl is None and base_mode is None:
        return buckets

    model = "posix"
    if isinstance(acl, NfsV4ACL):
        model = "nfsv4"
    elif isinstance(acl, NtACL):
        model = "nt"
    elif isinstance(acl, S3ACL):
        model = "s3"

    # Per-model principal enumeration.
    if model == "posix":
        principals = _posix_principals(acl if isinstance(acl, PosixACL) else None, base_uid, base_gid)
    elif model == "nfsv4":
        principals = _nfsv4_principals(acl)  # type: ignore[arg-type]
    elif model == "nt":
        principals = _nt_principals(acl)  # type: ignore[arg-type]
    elif model == "s3":
        principals = _s3_principals(acl)  # type: ignore[arg-type]
    else:
        principals = []

    for token, principal, groups in principals:
        rights = _grants(acl, principal, groups, base_mode, base_uid, base_gid)
        for right, granted in rights.items():
            if granted and (right != "delete" or model != "posix"):
                buckets[right].append(token)

    # ANYONE probe (uses an unused-id principal — only "everyone" rules grant).
    anyone_principal, anyone_groups = _ANYONE_PROBES.get(model, (None, []))
    if anyone_principal is not None:
        rights = _grants(acl, anyone_principal, anyone_groups, base_mode, base_uid, base_gid)
        for right, granted in rights.items():
            if granted and (right != "delete" or model != "posix"):
                if ANYONE not in buckets[right]:
                    buckets[right].append(ANYONE)

    # AUTH probe (only NT and S3 distinguish authenticated from anonymous).
    auth_principal, auth_groups = _AUTH_PROBES.get(model, (None, []))
    if auth_principal is not None:
        rights = _grants(acl, auth_principal, auth_groups, base_mode, base_uid, base_gid)
        for right, granted in rights.items():
            if granted and AUTH not in buckets[right]:
                # Only count it as AUTH if ANYONE didn't already cover it.
                if ANYONE not in buckets[right]:
                    buckets[right].append(AUTH)

    return buckets
```

(Editor note: the dual `posix` POSIX-`delete` skip is enforced by the `if right != "delete" or model != "posix"` guard. Verify by `test_posix_owner_user_and_other_get_read` which asserts `out["delete"] == []`.)

(Editor note: the AUTH probe's "only count if not already in ANYONE" logic is so that we don't redundantly emit `auth` when `*` already covers everyone. This keeps the bucket lists short.)

- [ ] **Step 4: Run tests — verify pass**

```bash
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff2-api pytest tests/test_acl_denorm.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add api/akashic/services/acl_denorm.py api/tests/test_acl_denorm.py
git commit -m "feat(api): denormalize_acl() for Meili viewable_by_* indexing"
```

---

## Task 5 — Indexing pipeline includes `viewable_by_*` arrays

Wire `denormalize_acl()` into the existing index path so that every newly-ingested file entry lands in Meili with the three new arrays. Also extend `ensure_index()` so the new fields are filterable.

The doc-builder logic is currently inlined in `routers/ingest.py:_index_files_to_meilisearch`. Refactor it into a helper in `services/search.py` so the ingest indexer, the extraction worker, and the new bulk re-index tool (Task 7) all use the same shape.

**Files:**
- Modify: `api/akashic/services/search.py`
- Modify: `api/akashic/routers/ingest.py`
- Modify: `api/akashic/workers/extraction.py`

- [ ] **Step 1: Add `viewable_by_*` to filterable attributes**

In `api/akashic/services/search.py`, change `update_filterable_attributes` call so the list includes the three new fields:

```python
        await index.update_filterable_attributes([
            "source_id", "extension", "mime_type", "size_bytes",
            "fs_modified_at", "tags", "owner_name", "group_name",
            "viewable_by_read", "viewable_by_write", "viewable_by_delete",
        ])
```

- [ ] **Step 2: Add `build_entry_doc()` helper in `services/search.py`**

Add (above the existing `index_file`):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from akashic.models.entry import Entry


def build_entry_doc(entry: "Entry", content_text: str | None = None) -> dict:
    """Builds the Meili document for an Entry, including denormalized ACL arrays."""
    from akashic.services.acl_denorm import denormalize_acl
    from akashic.schemas.acl import PosixACL, NfsV4ACL, NtACL, S3ACL
    from pydantic import TypeAdapter
    from akashic.schemas.acl import ACL

    acl_obj = None
    if entry.acl:
        try:
            acl_obj = TypeAdapter(ACL).validate_python(entry.acl)
        except Exception:
            acl_obj = None
    buckets = denormalize_acl(
        acl=acl_obj,
        base_mode=entry.mode,
        base_uid=entry.uid,
        base_gid=entry.gid,
    )

    doc: dict = {
        "id": str(entry.id),
        "source_id": str(entry.source_id),
        "path": entry.path,
        "filename": entry.name,
        "extension": entry.extension,
        "mime_type": entry.mime_type,
        "size_bytes": entry.size_bytes,
        "owner_name": entry.owner_name,
        "group_name": entry.group_name,
        "fs_modified_at": int(entry.fs_modified_at.timestamp())
            if entry.fs_modified_at else None,
        "tags": [],
        "viewable_by_read":   buckets["read"],
        "viewable_by_write":  buckets["write"],
        "viewable_by_delete": buckets["delete"],
    }
    if content_text is not None:
        doc["content_text"] = content_text
    return doc
```

- [ ] **Step 3: Refactor `_index_files_to_meilisearch` to use the helper**

In `api/akashic/routers/ingest.py`, replace the inline document literal with a call to `build_entry_doc`:

Replace lines around 52-65:

```python
                await index_files_batch([{
                    "id": str(e.id),
                    ...
                    "tags": [],
                }])
```

With:

```python
                from akashic.services.search import build_entry_doc
                await index_files_batch([build_entry_doc(e)])
```

- [ ] **Step 4: Refactor extraction worker to use the helper**

In `api/akashic/workers/extraction.py`, find where the entry is re-indexed after extraction and replace the inline dict with `build_entry_doc(entry, content_text=content_text)`. The existing call is `await index_file({...})` — change the dict to the helper call.

(Search for `index_file(` in `extraction.py` and modify the call site.)

- [ ] **Step 5: Smoke-test the doc shape**

Add a quick test `api/tests/test_search_doc_builder.py`:

```python
import uuid
from datetime import datetime, timezone

import pytest

from akashic.services.search import build_entry_doc


class _FakeEntry:
    """Minimal duck for build_entry_doc — avoids needing a DB session."""
    def __init__(self):
        self.id = uuid.uuid4()
        self.source_id = uuid.uuid4()
        self.path = "/tmp/x"
        self.name = "x"
        self.extension = None
        self.mime_type = "text/plain"
        self.size_bytes = 12
        self.owner_name = "alice"
        self.group_name = "wheel"
        self.fs_modified_at = datetime.now(timezone.utc)
        self.acl = {
            "type": "posix",
            "entries": [
                {"tag": "user_obj",  "qualifier": "",     "perms": "rwx"},
                {"tag": "group_obj", "qualifier": "",     "perms": "r--"},
                {"tag": "other",     "qualifier": "",     "perms": "r--"},
            ],
            "default_entries": None,
        }
        self.mode = 0o644
        self.uid = 1000
        self.gid = 100


def test_build_entry_doc_includes_viewable_by_arrays():
    doc = build_entry_doc(_FakeEntry())
    assert "viewable_by_read" in doc
    assert "viewable_by_write" in doc
    assert "viewable_by_delete" in doc
    assert "posix:uid:1000" in doc["viewable_by_read"]
    assert "*" in doc["viewable_by_read"]


def test_build_entry_doc_with_content_text():
    doc = build_entry_doc(_FakeEntry(), content_text="hello world")
    assert doc["content_text"] == "hello world"
```

- [ ] **Step 6: Run tests**

```bash
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff2-api pytest tests/test_search_doc_builder.py -v
```

Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add api/akashic/services/search.py api/akashic/routers/ingest.py api/akashic/workers/extraction.py api/tests/test_search_doc_builder.py
git commit -m "feat(api): index entries with viewable_by_* denormalized arrays"
```

---

## Task 6 — Search-time `permission_filter` query param

Add a `permission_filter` query param to `/api/search`. When set to `readable` (default when the user has any bindings) or `writable`, resolve the user's bindings into the identifier vocabulary and add a `viewable_by_<right> IN [...]` filter clause.

When the user has NO bindings (zero FsPersons), the default behavior is `permission_filter='all'` — show everything they have source access to. This keeps the new identity model strictly opt-in.

**Files:**
- Modify: `api/akashic/routers/search.py`
- Create: `api/tests/test_search_filter.py`

- [ ] **Step 1: Write failing tests**

Create `api/tests/test_search_filter.py`:

```python
"""Search-time `permission_filter` tests.

These tests verify the filter logic at the API surface — they don't need
Meilisearch to be running because they exercise the DB fallback path.
"""
import uuid
from datetime import datetime, timezone

import pytest


async def _register_login(client, username="alice", password="testpass123"):
    await client.post("/api/users/register", json={"username": username, "password": password})
    login = await client.post("/api/users/login", json={"username": username, "password": password})
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_search_with_no_bindings_defaults_to_all(client, db_session):
    """User with no FsPersons sees all entries they have source access to."""
    from akashic.models import Source, Entry
    token = await _register_login(client)

    source = Source(id=uuid.uuid4(), name="t", type="local", connection_config={"path": "/tmp"})
    db_session.add(source)
    await db_session.flush()

    entry = Entry(
        id=uuid.uuid4(), source_id=source.id, kind="file",
        path="/tmp/x", parent_path="/tmp", name="x",
        mode=0o600, uid=1000, gid=100, acl={
            "type": "posix",
            "entries": [{"tag": "user", "qualifier": "1001", "perms": "rwx"}],
            "default_entries": None,
        },
    )
    db_session.add(entry)
    await db_session.commit()

    r = await client.get("/api/search?q=x", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_search_permission_filter_param_accepted(client):
    token = await _register_login(client)
    for f in ("all", "readable", "writable"):
        r = await client.get(
            f"/api/search?q=&permission_filter={f}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, f


@pytest.mark.asyncio
async def test_search_invalid_permission_filter_rejected(client):
    token = await _register_login(client)
    r = await client.get(
        "/api/search?q=&permission_filter=destroy_everything",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_resolve_user_bindings_to_tokens(client, db_session):
    """The router-internal helper translates bindings → identifier tokens.

    We test it via the public endpoint by creating bindings and asserting
    that a follow-up search with `permission_filter=readable` succeeds.
    """
    from akashic.models import Source
    token = await _register_login(client)

    source = Source(id=uuid.uuid4(), name="t", type="local", connection_config={"path": "/tmp"})
    db_session.add(source)
    await db_session.commit()

    create = await client.post(
        "/api/identities", json={"label": "P"},
        headers={"Authorization": f"Bearer {token}"},
    )
    pid = create.json()["id"]
    await client.post(
        f"/api/identities/{pid}/bindings",
        json={
            "source_id": str(source.id),
            "identity_type": "posix_uid",
            "identifier": "1000",
            "groups": ["100"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    # Just hit search — should succeed even though Meili may be unreachable
    # (the DB fallback path doesn't use viewable_by_* filters).
    r = await client.get(
        "/api/search?q=&permission_filter=readable",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
```

- [ ] **Step 2: Run tests — verify failures**

```bash
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff2-api pytest tests/test_search_filter.py -v
```

Expected: `test_search_invalid_permission_filter_rejected` fails (no validation today). The other tests likely pass already (no filter logic = no error path).

- [ ] **Step 3: Implement the filter**

In `api/akashic/routers/search.py`, add the new query param + binding resolver. The full updated file should look like:

```python
import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user, get_permitted_source_ids
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.fs_person import FsBinding, FsPerson
from akashic.models.user import User
from akashic.schemas.search import SearchResults
from akashic.services.acl_denorm import (
    ANYONE, AUTH, posix_uid, posix_gid, sid, nfsv4_user, nfsv4_group, s3_user,
)

router = APIRouter(prefix="/api/search", tags=["search"])

_SAFE_EXTENSION = re.compile(r"^[a-zA-Z0-9]{1,20}$")

PermissionFilter = Literal["all", "readable", "writable"]


async def _user_has_any_bindings(user: User, db: AsyncSession) -> bool:
    result = await db.execute(
        select(FsPerson.id).where(FsPerson.user_id == user.id).limit(1)
    )
    return result.scalar_one_or_none() is not None


def _binding_to_tokens(binding: FsBinding) -> list[str]:
    """Translate one FsBinding into the identifier vocabulary tokens that
    represent it (self + groups)."""
    tokens: list[str] = []
    if binding.identity_type == "posix_uid":
        tokens.append(posix_uid(binding.identifier))
        tokens.extend(posix_gid(g) for g in binding.groups)
    elif binding.identity_type == "sid":
        tokens.append(sid(binding.identifier))
        tokens.extend(sid(g) for g in binding.groups)
    elif binding.identity_type == "nfsv4_principal":
        tokens.append(nfsv4_user(binding.identifier))
        tokens.extend(nfsv4_group(g) for g in binding.groups)
    elif binding.identity_type == "s3_canonical":
        tokens.append(s3_user(binding.identifier))
    return tokens


async def _user_principal_tokens(user: User, db: AsyncSession) -> list[str]:
    """Returns ALL identifier tokens that represent the user across every
    binding, plus the implicit `*` and `auth`."""
    bindings = (await db.execute(
        select(FsBinding)
        .join(FsPerson, FsBinding.fs_person_id == FsPerson.id)
        .where(FsPerson.user_id == user.id)
    )).scalars().all()
    tokens: set[str] = {ANYONE, AUTH}
    for b in bindings:
        tokens.update(_binding_to_tokens(b))
    return sorted(tokens)


@router.get("", response_model=SearchResults)
async def search(
    q: str = Query(default=""),
    source_id: uuid.UUID | None = None,
    extension: str | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    permission_filter: PermissionFilter | None = None,
    offset: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if extension and not _SAFE_EXTENSION.match(extension):
        raise HTTPException(status_code=400, detail="Invalid extension format")

    allowed_source_ids = await get_permitted_source_ids(user, db)
    if allowed_source_ids is not None:
        if not allowed_source_ids:
            return SearchResults(results=[], total=0, query=q)
        if source_id and source_id not in allowed_source_ids:
            raise HTTPException(status_code=403, detail="No access to this source")

    # Default policy:
    #   * has bindings → 'readable'
    #   * no bindings  → 'all'
    if permission_filter is None:
        permission_filter = "readable" if await _user_has_any_bindings(user, db) else "all"

    try:
        from akashic.services.search import search_files

        filters: list[str] = []
        if source_id:
            filters.append(f'source_id = "{source_id}"')
        elif allowed_source_ids is not None:
            sid_filter = " OR ".join(f'source_id = "{s}"' for s in allowed_source_ids)
            filters.append(f"({sid_filter})")
        if extension:
            filters.append(f'extension = "{extension}"')
        if min_size is not None:
            filters.append(f"size_bytes >= {min_size}")
        if max_size is not None:
            filters.append(f"size_bytes <= {max_size}")

        if permission_filter in ("readable", "writable"):
            tokens = await _user_principal_tokens(user, db)
            field = "viewable_by_read" if permission_filter == "readable" else "viewable_by_write"
            tok_clause = " OR ".join(f'{field} = "{t}"' for t in tokens)
            filters.append(f"({tok_clause})")

        filter_str = " AND ".join(filters) if filters else None
        meili_results = await search_files(q, filters=filter_str, offset=offset, limit=limit)

        from akashic.schemas.search import SearchHit
        hits = [SearchHit(**h) if isinstance(h, dict) else h for h in (meili_results.hits or [])]
        return SearchResults(
            results=hits,
            total=meili_results.estimated_total_hits or 0,
            query=q,
        )
    except HTTPException:
        raise
    except Exception:
        # DB fallback — does NOT apply permission_filter (no denorm in the DB).
        # The Meili path is the source of truth for permission filtering.
        conditions = [
            Entry.kind == "file",
            Entry.is_deleted == False,  # noqa: E712
            Entry.name.ilike(f"%{q}%"),
        ]
        if source_id:
            conditions.append(Entry.source_id == source_id)
        elif allowed_source_ids is not None:
            conditions.append(Entry.source_id.in_(allowed_source_ids))
        if extension:
            conditions.append(Entry.extension == extension)
        if min_size is not None:
            conditions.append(Entry.size_bytes >= min_size)
        if max_size is not None:
            conditions.append(Entry.size_bytes <= max_size)

        query_stmt = select(Entry).where(and_(*conditions)).offset(offset).limit(limit)
        result = await db.execute(query_stmt)
        entries = result.scalars().all()

        from sqlalchemy import func
        from akashic.schemas.search import SearchHit
        count_stmt = select(func.count(Entry.id)).where(and_(*conditions))
        count_result = await db.execute(count_stmt)
        total = count_result.scalar() or 0

        hits = [
            SearchHit(
                id=e.id, source_id=e.source_id, path=e.path,
                filename=e.name, extension=e.extension,
                mime_type=e.mime_type, size_bytes=e.size_bytes,
                fs_modified_at=int(e.fs_modified_at.timestamp()) if e.fs_modified_at else None,
            )
            for e in entries
        ]
        return SearchResults(results=hits, total=total, query=q)
```

- [ ] **Step 4: Run tests — verify pass**

```bash
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff2-api pytest tests/test_search_filter.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add api/akashic/routers/search.py api/tests/test_search_filter.py
git commit -m "feat(api): permission_filter param applies user bindings to search"
```

---

## Task 7 — Bulk re-index command

When the indexing pipeline changes (this phase added denormalized arrays), existing documents in Meili lack the new fields. A one-shot CLI re-walks every Entry of `kind='file'`, builds the new doc, and pushes it.

**Files:**
- Create: `api/akashic/tools/__init__.py` (empty)
- Create: `api/akashic/tools/reindex_search.py`

- [ ] **Step 1: Create the empty package marker**

```bash
mkdir -p api/akashic/tools
touch api/akashic/tools/__init__.py
```

- [ ] **Step 2: Create the script**

Create `api/akashic/tools/reindex_search.py`:

```python
"""Bulk re-index every file Entry into Meilisearch.

Usage:
    python -m akashic.tools.reindex_search [--batch-size 100]

Re-walks every Entry where kind='file' AND is_deleted=False and rebuilds
its Meili document via build_entry_doc(). Safe to run repeatedly.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from akashic.config import settings
from akashic.models.entry import Entry
from akashic.services.search import build_entry_doc, ensure_index, index_files_batch

logger = logging.getLogger(__name__)


async def _reindex(batch_size: int) -> int:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await ensure_index()
    total = 0
    try:
        async with session_factory() as db:
            offset = 0
            while True:
                rows = (await db.execute(
                    select(Entry)
                    .where(Entry.kind == "file", Entry.is_deleted == False)  # noqa: E712
                    .order_by(Entry.id)
                    .offset(offset)
                    .limit(batch_size)
                )).scalars().all()
                if not rows:
                    break
                docs = [build_entry_doc(e) for e in rows]
                await index_files_batch(docs)
                total += len(rows)
                offset += len(rows)
                logger.info("Re-indexed %d entries so far", total)
    finally:
        await engine.dispose()
    return total


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Bulk re-index every file Entry into Meilisearch.")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    total = asyncio.run(_reindex(args.batch_size))
    print(f"Re-indexed {total} entries.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Smoke-test the import (no execution needed)**

```bash
docker exec akashic-eff2-api python -c "from akashic.tools.reindex_search import _reindex; print('OK')"
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add api/akashic/tools/__init__.py api/akashic/tools/reindex_search.py
git commit -m "feat(api): bulk reindex_search CLI for the new viewable_by_* fields"
```

---

## Task 8 — Frontend: identity types

**Files:**
- Create: `web/src/lib/identityTypes.ts`
- Modify: `web/src/types/index.ts` (re-export)

- [ ] **Step 1: Create the types file**

Create `web/src/lib/identityTypes.ts`:

```ts
import type { PrincipalType } from "./effectivePermsTypes";

export type GroupsSource = "manual" | "auto";

export interface FsBinding {
  id: string;
  fs_person_id: string;
  source_id: string;
  identity_type: PrincipalType;
  identifier: string;
  groups: string[];
  groups_source: GroupsSource;
  groups_resolved_at: string | null;
  created_at: string;
}

export interface FsPerson {
  id: string;
  user_id: string;
  label: string;
  is_primary: boolean;
  created_at: string;
  bindings: FsBinding[];
}

export interface FsPersonInput {
  label: string;
  is_primary?: boolean;
}

export interface FsBindingInput {
  source_id: string;
  identity_type: PrincipalType;
  identifier: string;
  groups: string[];
}
```

- [ ] **Step 2: Re-export from `web/src/types/index.ts`**

Append:

```ts
export type {
  FsBinding,
  FsPerson,
  FsPersonInput,
  FsBindingInput,
  GroupsSource,
} from "../lib/identityTypes";
```

- [ ] **Step 3: tsc clean**

```bash
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/identityTypes.ts web/src/types/index.ts
git commit -m "feat(web): TS types for FsPerson + FsBinding"
```

---

## Task 9 — Frontend: `<SettingsIdentities>` page

A single page with all identity CRUD. Uses react-query's `useQuery` for the list, `useMutation` for changes, `queryClient.invalidateQueries(["identities"])` to keep things fresh.

**Files:**
- Create: `web/src/pages/SettingsIdentities.tsx`

- [ ] **Step 1: Create the page**

Create `web/src/pages/SettingsIdentities.tsx`:

```tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { FsPerson, FsPersonInput, FsBinding, FsBindingInput, Source } from "../types";
import type { PrincipalType } from "../lib/effectivePermsTypes";

const PRINCIPAL_TYPES: { value: PrincipalType; label: string }[] = [
  { value: "posix_uid",        label: "POSIX UID" },
  { value: "sid",              label: "Windows SID" },
  { value: "nfsv4_principal",  label: "NFSv4 principal" },
  { value: "s3_canonical",     label: "S3 canonical user" },
];

export function SettingsIdentities() {
  const qc = useQueryClient();
  const personsQ = useQuery<FsPerson[]>({
    queryKey: ["identities"],
    queryFn:  () => api.get<FsPerson[]>("/identities"),
  });
  const sourcesQ = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn:  () => api.get<Source[]>("/sources"),
  });

  const createPerson = useMutation<FsPerson, Error, FsPersonInput>({
    mutationFn: (body) => api.post<FsPerson>("/identities", body),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ["identities"] }),
  });
  const deletePerson = useMutation<void, Error, string>({
    mutationFn: (id) => api.delete<void>(`/identities/${id}`),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ["identities"] }),
  });

  return (
    <div className="max-w-3xl mx-auto p-6">
      <h1 className="text-xl font-semibold text-gray-900 mb-4">Identities</h1>
      <p className="text-sm text-gray-500 mb-6">
        Tell akashic who you are on each source. Search results filter by what
        these identities can read.
      </p>

      {personsQ.isLoading && <div className="text-sm text-gray-400">Loading…</div>}

      <ul className="space-y-4">
        {(personsQ.data ?? []).map((p) => (
          <PersonCard
            key={p.id}
            person={p}
            sources={sourcesQ.data ?? []}
            onDelete={() => deletePerson.mutate(p.id)}
          />
        ))}
      </ul>

      <AddPersonForm onSubmit={(body) => createPerson.mutate(body)} pending={createPerson.isPending} />
    </div>
  );
}

function PersonCard({
  person, sources, onDelete,
}: { person: FsPerson; sources: Source[]; onDelete: () => void }) {
  const qc = useQueryClient();

  const addBinding = useMutation<FsBinding, Error, FsBindingInput>({
    mutationFn: (body) => api.post<FsBinding>(`/identities/${person.id}/bindings`, body),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ["identities"] }),
  });
  const deleteBinding = useMutation<void, Error, string>({
    mutationFn: (bid) => api.delete<void>(`/identities/${person.id}/bindings/${bid}`),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ["identities"] }),
  });

  return (
    <li className="border border-gray-200 rounded p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="font-medium text-gray-900">
          {person.label}
          {person.is_primary && (
            <span className="ml-2 text-xs uppercase tracking-wider text-accent-700">primary</span>
          )}
        </div>
        <button
          type="button" onClick={onDelete}
          className="text-xs text-gray-400 hover:text-red-600"
        >Delete identity</button>
      </div>

      {person.bindings.length === 0 && (
        <p className="text-xs text-gray-400 italic mb-2">No bindings yet.</p>
      )}
      <ul className="space-y-1">
        {person.bindings.map((b) => {
          const source = sources.find((s) => s.id === b.source_id);
          return (
            <li key={b.id} className="flex items-center gap-3 text-sm">
              <span className="font-medium text-gray-700 w-32 truncate">
                {source?.name ?? b.source_id.slice(0, 8)}
              </span>
              <code className="font-mono text-xs bg-gray-100 px-1.5 py-0.5 rounded">
                {b.identity_type}:{b.identifier}
              </code>
              {b.groups.length > 0 && (
                <span className="text-xs text-gray-500">
                  groups: {b.groups.join(", ")}
                </span>
              )}
              <button
                type="button" onClick={() => deleteBinding.mutate(b.id)}
                className="ml-auto text-xs text-gray-400 hover:text-red-600"
                aria-label="Remove binding"
              >×</button>
            </li>
          );
        })}
      </ul>

      <AddBindingForm
        sources={sources}
        existingSourceIds={new Set(person.bindings.map((b) => b.source_id))}
        onSubmit={(body) => addBinding.mutate(body)}
        pending={addBinding.isPending}
      />
    </li>
  );
}

function AddPersonForm({
  onSubmit, pending,
}: { onSubmit: (body: FsPersonInput) => void; pending: boolean }) {
  const [label, setLabel] = useState("");
  const [isPrimary, setIsPrimary] = useState(false);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!label.trim()) return;
        onSubmit({ label: label.trim(), is_primary: isPrimary });
        setLabel(""); setIsPrimary(false);
      }}
      className="mt-6 flex items-center gap-2 text-sm"
    >
      <input
        type="text" value={label} onChange={(e) => setLabel(e.target.value)}
        placeholder="My Work Account"
        className="flex-1 border border-gray-200 rounded px-2 py-1"
      />
      <label className="text-xs text-gray-500 flex items-center gap-1">
        <input type="checkbox" checked={isPrimary} onChange={(e) => setIsPrimary(e.target.checked)} />
        Primary
      </label>
      <button
        type="submit" disabled={!label.trim() || pending}
        className="text-sm bg-accent-600 text-white rounded px-3 py-1 disabled:opacity-50 hover:bg-accent-700"
      >+ Add identity</button>
    </form>
  );
}

function AddBindingForm({
  sources, existingSourceIds, onSubmit, pending,
}: {
  sources: Source[];
  existingSourceIds: Set<string>;
  onSubmit: (body: FsBindingInput) => void;
  pending: boolean;
}) {
  const available = sources.filter((s) => !existingSourceIds.has(s.id));
  const [sourceId, setSourceId] = useState(available[0]?.id ?? "");
  const [type, setType] = useState<PrincipalType>("posix_uid");
  const [identifier, setIdentifier] = useState("");
  const [groupsRaw, setGroupsRaw] = useState("");

  if (available.length === 0) return null;

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!sourceId || !identifier.trim()) return;
        onSubmit({
          source_id: sourceId,
          identity_type: type,
          identifier: identifier.trim(),
          groups: groupsRaw.split(",").map((g) => g.trim()).filter(Boolean),
        });
        setIdentifier(""); setGroupsRaw("");
      }}
      className="mt-3 flex items-center gap-2 text-xs"
    >
      <select
        value={sourceId} onChange={(e) => setSourceId(e.target.value)}
        className="border border-gray-200 rounded px-2 py-1"
      >
        {available.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
      </select>
      <select
        value={type} onChange={(e) => setType(e.target.value as PrincipalType)}
        className="border border-gray-200 rounded px-2 py-1"
      >
        {PRINCIPAL_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
      </select>
      <input
        type="text" value={identifier} onChange={(e) => setIdentifier(e.target.value)}
        placeholder="identifier (e.g. 1000 or S-1-5-…)"
        className="flex-1 font-mono border border-gray-200 rounded px-2 py-1"
      />
      <input
        type="text" value={groupsRaw} onChange={(e) => setGroupsRaw(e.target.value)}
        placeholder="groups (comma-sep)"
        className="w-48 font-mono border border-gray-200 rounded px-2 py-1"
      />
      <button
        type="submit" disabled={!identifier.trim() || pending}
        className="bg-accent-600 text-white rounded px-2 py-1 disabled:opacity-50 hover:bg-accent-700"
      >+ Add binding</button>
    </form>
  );
}

export default SettingsIdentities;
```

- [ ] **Step 2: tsc + build clean**

```bash
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npx tsc --noEmit
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npm run build
```

Expected: both clean.

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/SettingsIdentities.tsx
git commit -m "feat(web): SettingsIdentities page (FsPerson + FsBinding CRUD)"
```

---

## Task 10 — Wire route + nav

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/Layout.tsx`

- [ ] **Step 1: Read the current routing setup**

```bash
grep -n "Route\|element=" web/src/App.tsx | head -20
grep -n "Settings\|Sign out\|nav" web/src/components/Layout.tsx | head -20
```

- [ ] **Step 2: Add the route**

In `web/src/App.tsx`, add an import + route alongside the others:

```tsx
import { SettingsIdentities } from "./pages/SettingsIdentities";
```

Add inside the `<Routes>` block (alongside other routes):

```tsx
<Route path="settings/identities" element={<SettingsIdentities />} />
```

- [ ] **Step 3: Add nav item**

In `web/src/components/Layout.tsx`, add a "Settings" item near the bottom of the sidebar (above any "Sign out" link). Match the existing nav-item style — same icon-text-href shape as other nav links.

If the existing nav has an icon prop pattern, use a generic gear icon path:

```tsx
<Icon d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
```

If `Icon` doesn't exist there, use the project's existing nav-icon convention — read the file before editing to match the style.

- [ ] **Step 4: tsc + build clean**

```bash
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npx tsc --noEmit
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npm run build
```

Expected: both clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/App.tsx web/src/components/Layout.tsx
git commit -m "feat(web): wire /settings/identities route + sidebar nav"
```

---

## Task 11 — Search page: `permission_filter` dropdown

**Files:**
- Modify: `web/src/pages/Search.tsx`

- [ ] **Step 1: Read the current Search page**

```bash
grep -n "useQuery\|api.get\|searchParams\|permission" web/src/pages/Search.tsx | head -30
```

- [ ] **Step 2: Add the dropdown**

Add a `permission_filter` state variable initialized to `"readable"`, render a `<select>` near the search input with three options:

```tsx
<select
  value={permissionFilter}
  onChange={(e) => setPermissionFilter(e.target.value as "all" | "readable" | "writable")}
  className="text-sm border border-gray-200 rounded px-2 py-1"
>
  <option value="readable">Files I can read</option>
  <option value="writable">Files I can write</option>
  <option value="all">All files I have access to</option>
</select>
```

Pass it to the search query as a query string param. The `useQuery` queryKey should include `permissionFilter` so changes trigger a re-fetch.

The exact integration depends on the existing Search.tsx structure — read the file first and add minimally without restructuring.

- [ ] **Step 3: tsc + build clean**

```bash
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npx tsc --noEmit
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npm run build
```

Expected: both clean.

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/Search.tsx
git commit -m "feat(web): permission_filter dropdown on Search page"
```

---

## Task 12 — End-to-end smoke test

**Files:** none.

- [ ] **Step 1: Bring stack up**

```bash
docker compose up -d
```

- [ ] **Step 2: Register a user, create source, scan an entry**

(Use the same pattern as Phase 11's smoke test — adapt as needed.)

```bash
# Register, login, create source pointing at /tmp.
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/users/register \
  -H "Content-Type: application/json" \
  -d '{"username":"verify12","email":"v@v","password":"testtest"}' >/dev/null;
  curl -s -X POST http://127.0.0.1:8000/api/users/login \
  -H "Content-Type: application/json" \
  -d '{"username":"verify12","password":"testtest"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

SRC=$(curl -s -X POST http://127.0.0.1:8000/api/sources \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"verify12-src","type":"local","connection_config":{"path":"/tmp/p12-demo"}}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "source=$SRC"
```

- [ ] **Step 3: Create an identity bound to UID 1000 with group 100**

```bash
PID=$(curl -s -X POST http://127.0.0.1:8000/api/identities \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"label":"My UID","is_primary":true}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

curl -s -X POST "http://127.0.0.1:8000/api/identities/$PID/bindings" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"source_id\":\"$SRC\",\"identity_type\":\"posix_uid\",\"identifier\":\"1000\",\"groups\":[\"100\"]}" \
  | python3 -m json.tool
```

- [ ] **Step 4: Insert a couple of test entries with different ACLs**

Via direct SQL (matching Phase 11's pattern):

```bash
docker exec -e PGPASSWORD=changeme akashic-postgres-1 psql -U akashic -d akashic -c "
INSERT INTO entries (id, source_id, kind, path, parent_path, name, mode, uid, gid, acl, first_seen_at, last_seen_at, is_deleted)
VALUES
  (gen_random_uuid(), '$SRC', 'file', '/tmp/p12-demo/mine.txt',     '/tmp/p12-demo', 'mine.txt',     420, 1000, 100,
   '{\"type\":\"posix\",\"entries\":[{\"tag\":\"user_obj\",\"qualifier\":\"\",\"perms\":\"rw-\"},{\"tag\":\"group_obj\",\"qualifier\":\"\",\"perms\":\"r--\"},{\"tag\":\"other\",\"qualifier\":\"\",\"perms\":\"---\"}],\"default_entries\":null}',
   NOW(), NOW(), false),
  (gen_random_uuid(), '$SRC', 'file', '/tmp/p12-demo/theirs.txt',  '/tmp/p12-demo', 'theirs.txt',  420, 9999, 100,
   '{\"type\":\"posix\",\"entries\":[{\"tag\":\"user_obj\",\"qualifier\":\"\",\"perms\":\"rw-\"},{\"tag\":\"group_obj\",\"qualifier\":\"\",\"perms\":\"---\"},{\"tag\":\"other\",\"qualifier\":\"\",\"perms\":\"---\"}],\"default_entries\":null}',
   NOW(), NOW(), false);
"
```

Run the bulk re-index so they land in Meili with `viewable_by_*`:

```bash
docker exec akashic-api-1 python -m akashic.tools.reindex_search
```

- [ ] **Step 5: Search with `permission_filter=readable`**

```bash
curl -s "http://127.0.0.1:8000/api/search?q=&permission_filter=readable" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Expected: the result list contains `mine.txt` (UID 1000 owns it, `r-` group access via gid 100) but NOT `theirs.txt` (owner is 9999, group has no access, mode `---` for other).

- [ ] **Step 6: Switch to `permission_filter=all` and confirm both appear**

```bash
curl -s "http://127.0.0.1:8000/api/search?q=&permission_filter=all" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Expected: both files appear.

- [ ] **Step 7: Visit `/settings/identities` in the browser**

Visit `http://127.0.0.1:5173/settings/identities` and confirm the page renders the FsPerson with its binding. Add a binding via the form, then delete it. Add another FsPerson.

- [ ] **Step 8: Visit `/search` and toggle the dropdown**

Confirm the result count changes between `readable` / `writable` / `all`.

- [ ] **Step 9: Tear down demo**

```bash
rm -rf /tmp/p12-demo
```

No commit — verification only.

---

## Notes for the implementer

- **`denormalize_acl()` is pure** (no I/O, no DB). Keep it that way; Phase 13 will reuse it directly when audit-logging `search_as` queries.
- **POSIX `delete` is not denormalized** (parent-dir dependency). The bucket is always `[]` for POSIX entries.
- **NULL ACL handling**: `denormalize_acl(acl=None, base_mode=None, ...)` returns empty buckets — entries with no captured ACL/mode are essentially "anyone can do anything from akashic's perspective" but we surface that as "nothing in the buckets" so the search filter excludes them when running `readable`. If this matters operationally, the `permission_filter=all` fallback covers it.
- **Token vocabulary is shared** between `acl_denorm.py` (denorm output) and `routers/search.py` (binding resolution). Same constants — both call the same helpers. Don't drift.
- **Meili filter syntax**: each filter clause must be `field = "value"` joined with `AND`/`OR` and wrapped in parens. `viewable_by_read IN [...]` is NOT valid Meili 1.x syntax; use `viewable_by_read = "tok1" OR viewable_by_read = "tok2"` etc.
- **DB fallback in `routers/search.py`** intentionally does NOT apply `permission_filter`. The DB doesn't store the denormalized arrays; only Meili does. If Meili is unreachable, the user sees their full permitted-source set. Document this in the response (or accept the divergence — it's a degraded-mode behavior).
- **No model migration framework.** `Base.metadata.create_all` continues to be the only schema mechanism. Existing deployments will get the new tables automatically on next API startup.
- **Search page state**: `permission_filter` should persist in the URL `?permission_filter=...` so refresh / share works, matching the existing source/extension/etc. URL-state pattern.
