# Phase 13 — `search_as` Override + Structured Audit Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two thin layers on top of Phase 12. **(1) `search_as`** — a power-user override that lets a user run a single search query as a different principal (different identity_type + identifier + groups), bypassing their own bindings. **(2) Structured audit log** — append-only `audit_events` table that captures `search_as_used` and identity-mutation events; admin-only `/api/admin/audit` endpoint and `/admin/audit` page for review.

**Architecture:** A new `AuditEvent` SQLAlchemy model stores `(user_id, event_type, occurred_at, source_id, request_ip, user_agent, payload JSONB)`. A small `services/audit.py` exposes `record_event(db, user, event_type, payload, request)` — uses the request's existing session, swallows write failures (audit must not fail the user-facing operation). `routers/search.py` gains a `search_as` query param: when present, the binding-resolution function returns the override's tokens instead of the user's own; `record_event` fires unconditionally with the override + result count. `routers/identities.py` calls `record_event` on every successful create/delete (`identity_added`, `identity_removed`, `binding_added`, `binding_removed`). A new admin-only `routers/admin_audit.py` exposes paginated list + by-id endpoints. The frontend gets a "Search as…" overflow toggle on the Search page (ephemeral inline form mirroring the identity-binding shape) and a new `/admin/audit` page guarded by the user's `role==admin`.

Optional retention: `audit_retention_days` config setting (default `0` = forever) plus a daily scheduler job that deletes events older than the threshold.

**Tech Stack:** Python 3.12 (FastAPI/Pydantic v2/SQLAlchemy async), TypeScript/React 18, Tailwind, pytest-asyncio.

---

## File structure

**Create**
- `api/akashic/models/audit_event.py` — `AuditEvent` SQLAlchemy model.
- `api/akashic/schemas/audit.py` — Pydantic shapes (`AuditEventOut`, `AuditEventList`, `SearchAsOverride`).
- `api/akashic/services/audit.py` — `record_event()` helper.
- `api/akashic/routers/admin_audit.py` — `/api/admin/audit` and `/api/admin/audit/{id}`.
- `api/tests/test_audit_service.py` — `record_event` swallows failures, captures fields.
- `api/tests/test_admin_audit_endpoints.py` — admin-only access, list filtering, by-id.
- `api/tests/test_search_as.py` — `search_as` returns override-scoped results AND records `search_as_used` event.
- `web/src/lib/auditTypes.ts` — TS shapes.
- `web/src/pages/AdminAudit.tsx` — admin audit page.
- `web/src/components/search/SearchAsForm.tsx` — the inline override form (separate file because it has non-trivial state).

**Edit**
- `api/akashic/models/__init__.py` — export `AuditEvent`.
- `api/akashic/main.py` — register `admin_audit` router.
- `api/akashic/config.py` — add `audit_retention_days: int = 0`.
- `api/akashic/scheduler.py` — add daily retention job.
- `api/akashic/routers/search.py` — accept `search_as` param, override token resolution, call `record_event`.
- `api/akashic/routers/identities.py` — call `record_event` on identity/binding mutation.
- `web/src/types/index.ts` — re-export audit types.
- `web/src/App.tsx` — register `/admin/audit` route.
- `web/src/components/Layout.tsx` — admin-only nav item.
- `web/src/pages/Search.tsx` — add "Search as…" toggle that mounts `SearchAsForm`.

**No deletes.** Schema added via existing `Base.metadata.create_all` path.

---

## Cross-task spec: event types and payload shapes

The `AuditEvent.payload` column is `JSONB` — schemaless. A small set of well-known shapes makes the admin UI's pretty-printer useful and keeps client/server in sync:

| `event_type` | Payload fields |
|---|---|
| `search_as_used` | `query: str`, `search_as: {type, identifier, groups: list[str]}`, `results_count: int`, `source_filter: str?` |
| `identity_added` | `fs_person_id: UUID`, `fs_person_label: str` |
| `identity_removed` | `fs_person_id: UUID`, `fs_person_label: str` |
| `binding_added` | `fs_person_id: UUID`, `source_id: UUID`, `identity_type: str`, `identifier: str` |
| `binding_removed` | `fs_person_id: UUID`, `source_id: UUID`, `identity_type: str`, `identifier: str` |

The schema accepts ANY string in `event_type` — the table above is documentation. Future events can be added without touching the schema.

---

## Task 1 — `AuditEvent` model

**Files:**
- Create: `api/akashic/models/audit_event.py`
- Modify: `api/akashic/models/__init__.py`

- [ ] **Step 1: Create the model**

```python
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class AuditEvent(Base):
    """Append-only audit record. Never updated; deleted only by retention job."""
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    request_ip: Mapped[str] = mapped_column(String, nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String, nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
```

(`user_id` is `SET NULL` not `CASCADE` — deleting a user must not orphan or delete audit history.)

- [ ] **Step 2: Re-export**

Edit `api/akashic/models/__init__.py` — add `from akashic.models.audit_event import AuditEvent` and `"AuditEvent"` to `__all__`.

- [ ] **Step 3: Smoke-test schema bring-up**

```bash
docker exec akashic-eff3-api python -c "
from akashic.database import Base, engine
from akashic import models  # noqa
import asyncio
async def go():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print('OK')
asyncio.run(go())
"
docker exec -e PGPASSWORD=changeme akashic-eff3-postgres-1 psql -U akashic -d akashic -c "\d audit_events"
```

Expected: `OK` then a column listing showing the 7 columns + the three indexes (user_id, event_type, occurred_at).

- [ ] **Step 4: Commit**

```bash
git add api/akashic/models/audit_event.py api/akashic/models/__init__.py
git commit -m "feat(api): AuditEvent append-only model"
```

---

## Task 2 — `record_event()` service + Pydantic schemas

**Files:**
- Create: `api/akashic/schemas/audit.py`
- Create: `api/akashic/services/audit.py`
- Create: `api/tests/test_audit_service.py`

- [ ] **Step 1: Create the schemas**

`api/akashic/schemas/audit.py`:

```python
"""Schemas for audit events."""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SearchAsOverride(BaseModel):
    """The override-principal payload used by both the search endpoint and the
    audit log. `type` matches IdentityType from schemas/identity.py."""
    type: Literal["posix_uid", "sid", "nfsv4_principal", "s3_canonical"]
    identifier: str
    groups: list[str] = Field(default_factory=list)


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    event_type: str
    occurred_at: datetime
    source_id: uuid.UUID | None
    request_ip: str
    user_agent: str
    payload: dict


class AuditEventList(BaseModel):
    items: list[AuditEventOut]
    total: int
    page: int
    page_size: int
```

- [ ] **Step 2: Write failing service tests**

`api/tests/test_audit_service.py`:

```python
import pytest

from akashic.models.audit_event import AuditEvent
from akashic.services.audit import record_event


@pytest.mark.asyncio
async def test_record_event_persists_minimal(db_session):
    from akashic.models.user import User
    user = User(username="alice", email="a@a", password_hash="x", role="user")
    db_session.add(user)
    await db_session.flush()

    await record_event(
        db=db_session,
        user=user,
        event_type="identity_added",
        payload={"fs_person_label": "My Work"},
        request=None,
    )
    await db_session.commit()

    from sqlalchemy import select
    rows = (await db_session.execute(select(AuditEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "identity_added"
    assert rows[0].user_id == user.id
    assert rows[0].payload == {"fs_person_label": "My Work"}
    assert rows[0].request_ip == ""
    assert rows[0].user_agent == ""


@pytest.mark.asyncio
async def test_record_event_captures_request_metadata(db_session):
    from akashic.models.user import User
    user = User(username="bob", email="b@b", password_hash="x", role="user")
    db_session.add(user)
    await db_session.flush()

    class _FakeRequest:
        client = type("c", (), {"host": "10.0.0.5"})()
        headers = {"user-agent": "curl/8.0"}

    await record_event(
        db=db_session,
        user=user,
        event_type="search_as_used",
        payload={"query": "foo", "results_count": 7},
        request=_FakeRequest(),
    )
    await db_session.commit()

    from sqlalchemy import select
    row = (await db_session.execute(select(AuditEvent))).scalar_one()
    assert row.request_ip == "10.0.0.5"
    assert row.user_agent == "curl/8.0"


@pytest.mark.asyncio
async def test_record_event_swallows_failures(db_session, caplog):
    """A broken db should NOT raise. Caller's user-facing op continues."""
    import logging
    caplog.set_level(logging.WARNING, logger="akashic.services.audit")

    class _BrokenSession:
        def add(self, _):
            raise RuntimeError("db on fire")

    # Should not raise.
    await record_event(
        db=_BrokenSession(),
        user=None,
        event_type="identity_added",
        payload={},
        request=None,
    )
    assert any("audit" in rec.message.lower() for rec in caplog.records)
```

- [ ] **Step 3: Run tests, verify failure**

```bash
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff3-api pytest tests/test_audit_service.py -v
```

Expected: ImportError on `record_event`.

- [ ] **Step 4: Implement the service**

`api/akashic/services/audit.py`:

```python
"""Audit-event helper.

`record_event` writes through the caller's session. Failures are logged but
NEVER raise — audit must not break the user-facing operation it logs.
"""
from __future__ import annotations

import logging
from typing import Any

from akashic.models.audit_event import AuditEvent
from akashic.models.user import User

logger = logging.getLogger(__name__)


async def record_event(
    *,
    db: Any,
    user: User | None,
    event_type: str,
    payload: dict,
    request: Any | None = None,
    source_id: Any | None = None,
) -> None:
    try:
        request_ip = ""
        user_agent = ""
        if request is not None:
            client = getattr(request, "client", None)
            if client is not None:
                request_ip = getattr(client, "host", "") or ""
            headers = getattr(request, "headers", {}) or {}
            user_agent = headers.get("user-agent", "") or ""
        evt = AuditEvent(
            user_id=user.id if user is not None else None,
            event_type=event_type,
            source_id=source_id,
            request_ip=request_ip,
            user_agent=user_agent,
            payload=payload,
        )
        db.add(evt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit: failed to record %s: %s", event_type, exc)
```

(Uses `Any` for `db` and `request` to avoid SQLAlchemy/FastAPI imports here — keeps the service trivially mockable in tests.)

- [ ] **Step 5: Run tests — verify pass**

```bash
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff3-api pytest tests/test_audit_service.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add api/akashic/schemas/audit.py api/akashic/services/audit.py api/tests/test_audit_service.py
git commit -m "feat(api): record_event() audit service with no-throw semantics"
```

---

## Task 3 — Hook `record_event` into identity CRUD

**Files:**
- Modify: `api/akashic/routers/identities.py`

Add `record_event` calls at the end of every successful create/delete handler, AFTER `commit()`. Each call uses the request session so the audit row lands on the next commit (we explicitly commit again at the end). Failures from `record_event` are swallowed by the service — the user-facing 201/204 always returns.

- [ ] **Step 1: Add the import**

In `api/akashic/routers/identities.py`, alongside other imports:

```python
from fastapi import Request
from akashic.services.audit import record_event
```

- [ ] **Step 2: Inject `Request` into all four mutating handlers**

Each handler signature (create_identity, delete_identity, create_binding, delete_binding) gains:

```python
    request: Request,
```

Add it as a required parameter (FastAPI auto-injects). Place it immediately after the path/body params, before `db` and `user`.

- [ ] **Step 3: Record events**

In `create_identity`, after `await db.commit()` and `await db.refresh(person)`:

```python
    await record_event(
        db=db, user=user,
        event_type="identity_added",
        payload={"fs_person_id": str(person.id), "fs_person_label": person.label},
        request=request,
    )
    await db.commit()
```

In `delete_identity`, before the existing `await db.commit()` of the delete, capture the label:

```python
    label = person.label
    await db.delete(person)
    await db.commit()
    await record_event(
        db=db, user=user,
        event_type="identity_removed",
        payload={"fs_person_id": str(person_id), "fs_person_label": label},
        request=request,
    )
    await db.commit()
```

In `create_binding`, after the IntegrityError handling (so we only audit successful creates), and after `await db.refresh(binding)`:

```python
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
    await db.commit()
```

In `delete_binding`, capture binding metadata before delete:

```python
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
    await db.commit()
```

(The PATCH handlers are intentionally skipped from audit — they're rare and not security-relevant.)

- [ ] **Step 4: Add a regression test**

Append to `api/tests/test_identities_endpoints.py`:

```python
@pytest.mark.asyncio
async def test_identity_create_records_audit_event(client, db_session):
    from akashic.models.audit_event import AuditEvent
    from sqlalchemy import select

    token = await _register_login(client)
    create = await client.post(
        "/api/identities", json={"label": "Audited"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201

    rows = (await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "identity_added")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload["fs_person_label"] == "Audited"


@pytest.mark.asyncio
async def test_binding_delete_records_audit_event(client, db_session):
    from akashic.models import Source
    from akashic.models.audit_event import AuditEvent
    from sqlalchemy import select
    import uuid

    token = await _register_login(client)
    source = Source(id=uuid.uuid4(), name="t", type="local", connection_config={"path": "/tmp"})
    db_session.add(source)
    await db_session.commit()

    pid = (await client.post(
        "/api/identities", json={"label": "P"},
        headers={"Authorization": f"Bearer {token}"},
    )).json()["id"]
    bid = (await client.post(
        f"/api/identities/{pid}/bindings",
        json={"source_id": str(source.id), "identity_type": "posix_uid", "identifier": "1000", "groups": []},
        headers={"Authorization": f"Bearer {token}"},
    )).json()["id"]

    delete = await client.delete(
        f"/api/identities/{pid}/bindings/{bid}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert delete.status_code == 204

    rows = (await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "binding_removed")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload["identifier"] == "1000"
```

- [ ] **Step 5: Restart api, run tests**

```bash
docker restart akashic-eff3-api
sleep 3
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff3-api pytest tests/test_identities_endpoints.py -v
```

Expected: all existing identity tests + 2 new audit-regression tests pass.

- [ ] **Step 6: Commit**

```bash
git add api/akashic/routers/identities.py api/tests/test_identities_endpoints.py
git commit -m "feat(api): audit identity_added/removed and binding_added/removed events"
```

---

## Task 4 — `search_as` query param + `search_as_used` event

**Files:**
- Modify: `api/akashic/routers/search.py`
- Create: `api/tests/test_search_as.py`

`search_as` is a JSON-encoded `SearchAsOverride` passed as a query param. When present, the binding-token resolution returns the override's tokens INSTEAD of the user's own. A `search_as_used` event is recorded UNCONDITIONALLY (even on zero-result queries) with `query`, the override, the result count, and the source filter.

The override is JSON-in-querystring rather than three separate params because the `groups` list otherwise needs awkward repeated `groups=` params. Frontend posts it as a single URL-encoded JSON blob.

- [ ] **Step 1: Write failing tests**

`api/tests/test_search_as.py`:

```python
import json
import uuid

import pytest


async def _register_login(client, username="alice", password="testpass123"):
    await client.post("/api/users/register", json={"username": username, "password": password})
    login = await client.post("/api/users/login", json={"username": username, "password": password})
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_search_as_records_audit_event(client, db_session):
    """Calling search with a search_as override creates a search_as_used row."""
    from akashic.models.audit_event import AuditEvent
    from sqlalchemy import select

    token = await _register_login(client)

    override = json.dumps({
        "type": "posix_uid",
        "identifier": "1234",
        "groups": ["100"],
    })
    r = await client.get(
        f"/api/search?q=hello&search_as={override}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200

    rows = (await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "search_as_used")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload["query"] == "hello"
    assert rows[0].payload["search_as"]["identifier"] == "1234"
    assert rows[0].payload["search_as"]["groups"] == ["100"]


@pytest.mark.asyncio
async def test_search_as_invalid_json_rejected(client):
    token = await _register_login(client)
    r = await client.get(
        "/api/search?q=&search_as=not-json",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_search_as_uses_override_tokens_not_user_bindings(client, db_session):
    """When search_as is set, the search uses override tokens, not user's
    own bindings. We can't directly inspect the Meili filter, but we verify
    the audit row has the override's identifier (not the user's)."""
    from akashic.models import Source
    from akashic.models.audit_event import AuditEvent
    from sqlalchemy import select
    token = await _register_login(client)

    src = Source(id=uuid.uuid4(), name="t", type="local", connection_config={"path": "/tmp"})
    db_session.add(src)
    await db_session.commit()

    # User registers as posix_uid:1000.
    pid = (await client.post(
        "/api/identities", json={"label": "self"},
        headers={"Authorization": f"Bearer {token}"},
    )).json()["id"]
    await client.post(
        f"/api/identities/{pid}/bindings",
        json={"source_id": str(src.id), "identity_type": "posix_uid", "identifier": "1000", "groups": []},
        headers={"Authorization": f"Bearer {token}"},
    )

    # But searches as posix_uid:9999.
    override = json.dumps({"type": "posix_uid", "identifier": "9999", "groups": []})
    await client.get(
        f"/api/search?q=&search_as={override}",
        headers={"Authorization": f"Bearer {token}"},
    )

    rows = (await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "search_as_used")
    )).scalars().all()
    assert any(r.payload["search_as"]["identifier"] == "9999" for r in rows)
```

- [ ] **Step 2: Run tests, verify failure**

```bash
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff3-api pytest tests/test_search_as.py -v
```

Expected: tests fail (search_as not honored, no audit row written).

- [ ] **Step 3: Implement search_as in `api/akashic/routers/search.py`**

Add the imports near the top alongside existing ones:

```python
import json
from fastapi import Request
from pydantic import ValidationError

from akashic.schemas.audit import SearchAsOverride
from akashic.services.acl_denorm import (
    ANYONE, AUTH, posix_uid, posix_gid, sid, nfsv4_user, nfsv4_group, s3_user,
)
from akashic.services.audit import record_event
```

Add a helper to parse the override (above the `search` endpoint):

```python
def _parse_search_as(raw: str | None) -> SearchAsOverride | None:
    if raw is None:
        return None
    try:
        return SearchAsOverride.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid search_as: {exc}")


def _override_tokens(override: SearchAsOverride) -> list[str]:
    """Return identifier tokens that represent the override principal."""
    tokens: set[str] = {ANYONE, AUTH}
    if override.type == "posix_uid":
        tokens.add(posix_uid(override.identifier))
        tokens.update(posix_gid(g) for g in override.groups)
    elif override.type == "sid":
        tokens.add(sid(override.identifier))
        tokens.update(sid(g) for g in override.groups)
    elif override.type == "nfsv4_principal":
        tokens.add(nfsv4_user(override.identifier))
        tokens.update(nfsv4_group(g) for g in override.groups)
    elif override.type == "s3_canonical":
        tokens.add(s3_user(override.identifier))
    return sorted(tokens)
```

Modify the `search` endpoint signature — add `request: Request` and `search_as: str | None = None` query params:

```python
@router.get("", response_model=SearchResults)
async def search(
    q: str = Query(default=""),
    source_id: uuid.UUID | None = None,
    extension: str | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    permission_filter: PermissionFilter | None = None,
    search_as: str | None = Query(default=None),
    offset: int = 0,
    limit: int = 20,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
```

Inside, before the Meili-path try block, parse the override:

```python
    override = _parse_search_as(search_as)
```

In the Meili filter-building block, when `permission_filter` is "readable"/"writable", choose the token source:

```python
        if permission_filter in ("readable", "writable"):
            if override is not None:
                tokens = _override_tokens(override)
            else:
                tokens = await _user_principal_tokens(user, db)
            field = "viewable_by_read" if permission_filter == "readable" else "viewable_by_write"
            tok_clause = " OR ".join(f'{field} = "{_escape_meili_value(t)}"' for t in tokens)
            filters.append(f"({tok_clause})")
```

Default policy: when `override` is set, force `permission_filter = "readable"` if the caller left it unset (else it'd be a no-op):

```python
    if permission_filter is None:
        if override is not None:
            permission_filter = "readable"
        else:
            permission_filter = "readable" if await _user_has_any_bindings(user, db) else "all"
```

After the search response is built (in BOTH the Meili-path success branch AND the DB-fallback branch), if `override is not None`, record the event:

```python
    # End of Meili success path, just before `return SearchResults(...)`:
    if override is not None:
        await record_event(
            db=db, user=user,
            event_type="search_as_used",
            payload={
                "query": q,
                "search_as": override.model_dump(),
                "results_count": len(hits),
                "source_filter": str(source_id) if source_id else None,
            },
            request=request,
            source_id=source_id,
        )
        await db.commit()
```

(Same shape in the DB-fallback path. The recorded `results_count` reflects the actual returned hits.)

- [ ] **Step 4: Restart api, run tests**

```bash
docker restart akashic-eff3-api
sleep 3
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff3-api pytest tests/test_search_as.py tests/test_search_filter.py -v
```

Expected: all 3 search_as tests + the existing 4 search_filter tests pass.

- [ ] **Step 5: Commit**

```bash
git add api/akashic/routers/search.py api/tests/test_search_as.py
git commit -m "feat(api): search_as override with audit capture"
```

---

## Task 5 — `/api/admin/audit` endpoints

**Files:**
- Create: `api/akashic/routers/admin_audit.py`
- Modify: `api/akashic/main.py`
- Create: `api/tests/test_admin_audit_endpoints.py`

Two endpoints:
- `GET /api/admin/audit` — paginated list with filters (`user_id`, `event_type`, `source_id`, `from`, `to`, `page`, `page_size`).
- `GET /api/admin/audit/{event_id}` — single event.

Both gated by `require_admin`.

- [ ] **Step 1: Write failing tests**

`api/tests/test_admin_audit_endpoints.py`:

```python
import uuid
from datetime import datetime, timedelta, timezone

import pytest


async def _register_login(client, username="alice", password="testpass123"):
    await client.post("/api/users/register", json={"username": username, "password": password})
    login = await client.post("/api/users/login", json={"username": username, "password": password})
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_admin_audit_requires_admin(client):
    # Bootstrap user is admin (first user). Need a second non-admin.
    admin_token = await _register_login(client, username="admin")
    await client.post(
        "/api/users/create",
        json={"username": "regular", "password": "testpass123", "role": "user"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    login = await client.post(
        "/api/users/login",
        json={"username": "regular", "password": "testpass123"},
    )
    user_token = login.json()["access_token"]

    r = await client.get("/api/admin/audit", headers={"Authorization": f"Bearer {user_token}"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_audit_lists_events(client, db_session):
    from akashic.models.audit_event import AuditEvent
    from akashic.models.user import User
    from sqlalchemy import select

    token = await _register_login(client, username="admin")
    me = (await db_session.execute(select(User).where(User.username == "admin"))).scalar_one()

    db_session.add(AuditEvent(user_id=me.id, event_type="identity_added", payload={"x": 1}))
    db_session.add(AuditEvent(user_id=me.id, event_type="search_as_used", payload={"q": "y"}))
    await db_session.commit()

    r = await client.get("/api/admin/audit", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_admin_audit_filters_by_event_type(client, db_session):
    from akashic.models.audit_event import AuditEvent
    from akashic.models.user import User
    from sqlalchemy import select

    token = await _register_login(client, username="admin")
    me = (await db_session.execute(select(User).where(User.username == "admin"))).scalar_one()
    db_session.add(AuditEvent(user_id=me.id, event_type="identity_added", payload={}))
    db_session.add(AuditEvent(user_id=me.id, event_type="search_as_used", payload={}))
    await db_session.commit()

    r = await client.get(
        "/api/admin/audit?event_type=search_as_used",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["event_type"] == "search_as_used"


@pytest.mark.asyncio
async def test_admin_audit_filters_by_date_range(client, db_session):
    from akashic.models.audit_event import AuditEvent
    from akashic.models.user import User
    from sqlalchemy import select

    token = await _register_login(client, username="admin")
    me = (await db_session.execute(select(User).where(User.username == "admin"))).scalar_one()
    old = AuditEvent(
        user_id=me.id, event_type="identity_added", payload={},
        occurred_at=datetime.now(timezone.utc) - timedelta(days=10),
    )
    new = AuditEvent(
        user_id=me.id, event_type="identity_added", payload={},
    )
    db_session.add(old)
    db_session.add(new)
    await db_session.commit()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    r = await client.get(
        f"/api/admin/audit?from={cutoff}",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = r.json()
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_admin_audit_get_by_id(client, db_session):
    from akashic.models.audit_event import AuditEvent
    from akashic.models.user import User
    from sqlalchemy import select

    token = await _register_login(client, username="admin")
    me = (await db_session.execute(select(User).where(User.username == "admin"))).scalar_one()
    evt = AuditEvent(user_id=me.id, event_type="identity_added", payload={"k": "v"})
    db_session.add(evt)
    await db_session.commit()

    r = await client.get(
        f"/api/admin/audit/{evt.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["payload"] == {"k": "v"}


@pytest.mark.asyncio
async def test_admin_audit_get_by_id_404(client):
    token = await _register_login(client, username="admin")
    r = await client.get(
        "/api/admin/audit/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests, verify failure**

```bash
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff3-api pytest tests/test_admin_audit_endpoints.py -v
```

Expected: tests fail with 404 for unknown route.

- [ ] **Step 3: Implement the router**

`api/akashic/routers/admin_audit.py`:

```python
"""Admin-only audit log read endpoints."""
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import require_admin
from akashic.database import get_db
from akashic.models.audit_event import AuditEvent
from akashic.models.user import User
from akashic.schemas.audit import AuditEventList, AuditEventOut

router = APIRouter(prefix="/api/admin/audit", tags=["admin-audit"])


@router.get("", response_model=AuditEventList)
async def list_audit_events(
    user_id: uuid.UUID | None = None,
    event_type: str | None = None,
    source_id: uuid.UUID | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> AuditEventList:
    page = max(1, page)
    page_size = max(1, min(page_size, 200))

    conditions = []
    if user_id is not None:
        conditions.append(AuditEvent.user_id == user_id)
    if event_type is not None:
        conditions.append(AuditEvent.event_type == event_type)
    if source_id is not None:
        conditions.append(AuditEvent.source_id == source_id)
    if from_ is not None:
        conditions.append(AuditEvent.occurred_at >= from_)
    if to is not None:
        conditions.append(AuditEvent.occurred_at <= to)

    base = select(AuditEvent)
    if conditions:
        base = base.where(*conditions)

    total = (await db.execute(
        select(func.count(AuditEvent.id)).select_from(
            base.with_only_columns(AuditEvent.id).order_by(None).subquery()
        )
    )).scalar() or 0

    rows = (await db.execute(
        base.order_by(desc(AuditEvent.occurred_at))
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()

    return AuditEventList(
        items=[AuditEventOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{event_id}", response_model=AuditEventOut)
async def get_audit_event(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> AuditEventOut:
    row = (await db.execute(
        select(AuditEvent).where(AuditEvent.id == event_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Audit event not found")
    return AuditEventOut.model_validate(row)
```

- [ ] **Step 4: Register router**

In `api/akashic/main.py`, add `admin_audit` to the import line and `app.include_router(admin_audit.router)` alongside other routers.

- [ ] **Step 5: Restart, run tests**

```bash
docker restart akashic-eff3-api
sleep 3
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff3-api pytest tests/test_admin_audit_endpoints.py -v
```

Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add api/akashic/routers/admin_audit.py api/akashic/main.py api/tests/test_admin_audit_endpoints.py
git commit -m "feat(api): /api/admin/audit list + by-id endpoints (admin-only)"
```

---

## Task 6 — Optional retention job

**Files:**
- Modify: `api/akashic/config.py`
- Modify: `api/akashic/scheduler.py`

Default `audit_retention_days = 0` means never delete. When > 0, a background task runs daily and deletes events older than the threshold.

- [ ] **Step 1: Add config setting**

In `api/akashic/config.py`, add to the `Settings` class:

```python
    audit_retention_days: int = 0  # 0 = forever
```

- [ ] **Step 2: Add the retention loop to scheduler**

In `api/akashic/scheduler.py`, after the existing scan-scheduler loop function, add:

```python
async def _audit_retention_loop():
    """Daily: delete audit events older than `settings.audit_retention_days`.
    No-op when the setting is 0."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import delete
    from akashic.config import settings
    from akashic.database import async_session
    from akashic.models.audit_event import AuditEvent

    while True:
        try:
            if settings.audit_retention_days > 0:
                cutoff = datetime.now(timezone.utc) - timedelta(days=settings.audit_retention_days)
                async with async_session() as db:
                    result = await db.execute(
                        delete(AuditEvent).where(AuditEvent.occurred_at < cutoff)
                    )
                    await db.commit()
                    if result.rowcount:
                        logger.info("Pruned %d audit events older than %s", result.rowcount, cutoff)
        except Exception as exc:  # noqa: BLE001
            logger.warning("audit retention pass failed: %s", exc)
        await asyncio.sleep(24 * 3600)  # daily


_retention_task: asyncio.Task | None = None


def start_scheduler():
    """Start the background scheduler tasks."""
    global _scheduler_task, _retention_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_scheduler_loop())
        logger.info("Scan scheduler started")
    if _retention_task is None or _retention_task.done():
        _retention_task = asyncio.create_task(_audit_retention_loop())
        logger.info("Audit retention scheduler started")


def stop_scheduler():
    global _scheduler_task, _retention_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
    if _retention_task and not _retention_task.done():
        _retention_task.cancel()
```

(Read the existing `start_scheduler` / `stop_scheduler` functions first — replace them with the versions above. The existing globals and `_scheduler_loop` are unchanged.)

- [ ] **Step 3: Smoke-test the import**

```bash
docker restart akashic-eff3-api
sleep 3
docker logs akashic-eff3-api 2>&1 | grep -E "scheduler started" | head -5
```

Expected: both "Scan scheduler started" AND "Audit retention scheduler started" appear in the logs.

- [ ] **Step 4: Commit**

```bash
git add api/akashic/config.py api/akashic/scheduler.py
git commit -m "feat(api): audit_retention_days config + daily prune task"
```

---

## Task 7 — Frontend types

**Files:**
- Create: `web/src/lib/auditTypes.ts`
- Modify: `web/src/types/index.ts`

- [ ] **Step 1: Create types**

`web/src/lib/auditTypes.ts`:

```ts
import type { PrincipalType } from "./effectivePermsTypes";

export interface SearchAsOverride {
  type: PrincipalType;
  identifier: string;
  groups: string[];
}

export interface AuditEvent {
  id: string;
  user_id: string | null;
  event_type: string;
  occurred_at: string;
  source_id: string | null;
  request_ip: string;
  user_agent: string;
  payload: Record<string, unknown>;
}

export interface AuditEventList {
  items: AuditEvent[];
  total: number;
  page: number;
  page_size: number;
}
```

- [ ] **Step 2: Re-export**

Append to `web/src/types/index.ts`:

```ts
export type {
  SearchAsOverride,
  AuditEvent,
  AuditEventList,
} from "../lib/auditTypes";
```

- [ ] **Step 3: tsc clean**

```bash
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npx tsc --noEmit
```

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/auditTypes.ts web/src/types/index.ts
git commit -m "feat(web): TS types for audit events and SearchAsOverride"
```

---

## Task 8 — `<SearchAsForm>` component + Search-page integration

**Files:**
- Create: `web/src/components/search/SearchAsForm.tsx`
- Modify: `web/src/pages/Search.tsx`

The form has the same shape as a single FsBinding row: type dropdown, identifier input, comma-separated groups input. Mounted inline above the result list when "Search as…" is toggled. When non-empty AND submitted, the URL gets a `search_as` param with JSON-encoded body.

- [ ] **Step 1: Create the component**

`web/src/components/search/SearchAsForm.tsx`:

```tsx
import { useState } from "react";
import type { SearchAsOverride } from "../../types";
import type { PrincipalType } from "../../lib/effectivePermsTypes";

const PRINCIPAL_TYPES: { value: PrincipalType; label: string }[] = [
  { value: "posix_uid",        label: "POSIX UID" },
  { value: "sid",              label: "Windows SID" },
  { value: "nfsv4_principal",  label: "NFSv4 principal" },
  { value: "s3_canonical",     label: "S3 canonical user" },
];

export function SearchAsForm({
  value, onChange,
}: {
  value: SearchAsOverride | null;
  onChange: (v: SearchAsOverride | null) => void;
}) {
  const [type, setType]             = useState<PrincipalType>(value?.type ?? "posix_uid");
  const [identifier, setIdentifier] = useState(value?.identifier ?? "");
  const [groupsRaw, setGroupsRaw]   = useState((value?.groups ?? []).join(", "));

  function apply() {
    if (!identifier.trim()) {
      onChange(null);
      return;
    }
    onChange({
      type, identifier: identifier.trim(),
      groups: groupsRaw.split(",").map((g) => g.trim()).filter(Boolean),
    });
  }

  function clear() {
    setIdentifier(""); setGroupsRaw("");
    onChange(null);
  }

  return (
    <div className="border border-amber-200 bg-amber-50 rounded p-3 mb-3">
      <div className="text-xs font-medium text-amber-900 mb-2">
        Search as another principal (audit-logged)
      </div>
      <div className="flex flex-wrap items-end gap-2 text-xs">
        <select
          value={type} onChange={(e) => setType(e.target.value as PrincipalType)}
          className="border border-amber-200 rounded px-2 py-1 bg-white"
        >
          {PRINCIPAL_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
        <input
          type="text" value={identifier} onChange={(e) => setIdentifier(e.target.value)}
          placeholder="identifier (e.g. 1000 or S-1-5-…)"
          className="flex-1 min-w-[160px] font-mono border border-amber-200 rounded px-2 py-1 bg-white"
        />
        <input
          type="text" value={groupsRaw} onChange={(e) => setGroupsRaw(e.target.value)}
          placeholder="groups (comma-sep)"
          className="w-48 font-mono border border-amber-200 rounded px-2 py-1 bg-white"
        />
        <button
          type="button" onClick={apply}
          disabled={!identifier.trim()}
          className="bg-amber-600 text-white rounded px-3 py-1 disabled:opacity-50 hover:bg-amber-700"
        >Apply</button>
        {value !== null && (
          <button
            type="button" onClick={clear}
            className="text-amber-700 hover:text-amber-900 px-2 py-1"
          >Clear</button>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wire it into Search.tsx**

Read `web/src/pages/Search.tsx`. Add state for the override and a "Search as…" toggle.

Imports near the top:

```tsx
import { SearchAsForm } from "../components/search/SearchAsForm";
import type { SearchAsOverride } from "../types";
```

State (alongside other useState):

```tsx
const [searchAs, setSearchAs] = useState<SearchAsOverride | null>(null);
const [showSearchAs, setShowSearchAs] = useState(false);
```

Add to URLSearchParams in the queryFn:

```tsx
if (searchAs) params.set("search_as", JSON.stringify(searchAs));
```

Add `searchAs` to the queryKey.

Add the toggle button next to the existing filter row, and the form below the filter card when the toggle is active. Place the toggle just above the filter Card (line ~97):

```tsx
<div className="flex items-center justify-end mb-2">
  <button
    type="button"
    onClick={() => setShowSearchAs((v) => !v)}
    className="text-xs text-gray-500 hover:text-gray-700"
  >
    {showSearchAs ? "▾" : "▸"} Search as…
  </button>
</div>
{showSearchAs && (
  <SearchAsForm value={searchAs} onChange={setSearchAs} />
)}
```

Update the result-count banner to indicate when search_as is active:

```tsx
<div className="text-xs text-gray-500 mb-3">
  {searchQuery.data?.total.toLocaleString()} result
  {searchQuery.data?.total !== 1 && "s"}
  {searchAs && (
    <span className="ml-2 text-amber-700">
      (filtered as {searchAs.type}:{searchAs.identifier})
    </span>
  )}
</div>
```

- [ ] **Step 3: tsc + build**

```bash
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npx tsc --noEmit
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npm run build
```

- [ ] **Step 4: Commit**

```bash
git add web/src/components/search/SearchAsForm.tsx web/src/pages/Search.tsx
git commit -m "feat(web): SearchAsForm + 'Search as…' toggle on Search page"
```

---

## Task 9 — `<AdminAudit>` page + nav

**Files:**
- Create: `web/src/pages/AdminAudit.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/Layout.tsx`

The page lists audit events with filters: date range pickers (`from`, `to`), event-type dropdown (free-text or known set), expand row to see full payload JSON. Admin-only — the nav item only renders when the user has `role === "admin"`.

The Layout currently doesn't fetch the user — it'd need a `useQuery(["me"])` or similar. The simplest approach: add a small query that hits `/api/users/me`, gate the nav item on that.

- [ ] **Step 1: Create the page**

`web/src/pages/AdminAudit.tsx`:

```tsx
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { AuditEventList, AuditEvent } from "../types";

const KNOWN_EVENT_TYPES = [
  "search_as_used",
  "identity_added",
  "identity_removed",
  "binding_added",
  "binding_removed",
  "groups_auto_resolved",
];

export default function AdminAudit() {
  const [eventType, setEventType] = useState("");
  const [fromDate, setFromDate]   = useState("");
  const [toDate, setToDate]       = useState("");
  const [expanded, setExpanded]   = useState<string | null>(null);
  const [page, setPage]           = useState(1);

  const audit = useQuery<AuditEventList>({
    queryKey: ["admin-audit", eventType, fromDate, toDate, page],
    queryFn: () => {
      const p = new URLSearchParams();
      if (eventType) p.set("event_type", eventType);
      if (fromDate)  p.set("from", new Date(fromDate).toISOString());
      if (toDate)    p.set("to", new Date(toDate).toISOString());
      p.set("page", String(page));
      return api.get<AuditEventList>(`/admin/audit?${p.toString()}`);
    },
  });

  const items = audit.data?.items ?? [];

  return (
    <div className="px-8 py-7 max-w-5xl">
      <h1 className="text-2xl font-semibold text-gray-900 tracking-tight mb-1">Audit log</h1>
      <p className="text-sm text-gray-500 mb-6">
        Recent identity-management and search_as events.
      </p>

      <div className="flex flex-wrap gap-3 mb-4 text-xs">
        <label className="text-gray-500 flex flex-col">
          Event type
          <select
            value={eventType} onChange={(e) => { setEventType(e.target.value); setPage(1); }}
            className="mt-1 border border-gray-200 rounded px-2 py-1 text-sm"
          >
            <option value="">All</option>
            {KNOWN_EVENT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        <label className="text-gray-500 flex flex-col">
          From
          <input
            type="datetime-local" value={fromDate}
            onChange={(e) => { setFromDate(e.target.value); setPage(1); }}
            className="mt-1 border border-gray-200 rounded px-2 py-1 text-sm"
          />
        </label>
        <label className="text-gray-500 flex flex-col">
          To
          <input
            type="datetime-local" value={toDate}
            onChange={(e) => { setToDate(e.target.value); setPage(1); }}
            className="mt-1 border border-gray-200 rounded px-2 py-1 text-sm"
          />
        </label>
      </div>

      {audit.isLoading && <div className="text-sm text-gray-400">Loading…</div>}
      {audit.error && (
        <div className="text-sm text-red-700 bg-red-50 rounded px-3 py-2 mb-3">
          {audit.error instanceof Error ? audit.error.message : "Error"}
        </div>
      )}

      <div className="border border-gray-200 rounded">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-200">
              <th className="text-left px-3 py-2 font-semibold">Time</th>
              <th className="text-left px-3 py-2 font-semibold">User</th>
              <th className="text-left px-3 py-2 font-semibold">Event</th>
              <th className="text-left px-3 py-2 font-semibold">IP</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {items.map((e) => (
              <Row key={e.id} event={e} expanded={expanded === e.id}
                   onToggle={() => setExpanded(expanded === e.id ? null : e.id)} />
            ))}
            {items.length === 0 && !audit.isLoading && (
              <tr><td colSpan={5} className="px-3 py-6 text-center text-sm text-gray-400">No events.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {audit.data && audit.data.total > audit.data.page_size && (
        <div className="flex items-center justify-between mt-3 text-xs text-gray-500">
          <div>{audit.data.total} total</div>
          <div className="flex gap-2">
            <button
              type="button" disabled={page === 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="border border-gray-200 rounded px-2 py-1 disabled:opacity-50 hover:bg-gray-50"
            >Prev</button>
            <button
              type="button"
              disabled={page * audit.data.page_size >= audit.data.total}
              onClick={() => setPage((p) => p + 1)}
              className="border border-gray-200 rounded px-2 py-1 disabled:opacity-50 hover:bg-gray-50"
            >Next</button>
          </div>
        </div>
      )}
    </div>
  );
}

function Row({
  event, expanded, onToggle,
}: { event: AuditEvent; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className="border-b border-gray-100 last:border-b-0 hover:bg-gray-50">
        <td className="px-3 py-1.5 text-gray-600 font-mono text-xs">
          {new Date(event.occurred_at).toLocaleString()}
        </td>
        <td className="px-3 py-1.5 text-gray-700 font-mono text-xs">
          {event.user_id ? event.user_id.slice(0, 8) : "—"}
        </td>
        <td className="px-3 py-1.5 text-gray-800">{event.event_type}</td>
        <td className="px-3 py-1.5 text-gray-500 font-mono text-xs">{event.request_ip}</td>
        <td className="px-3 py-1.5 text-right">
          <button
            type="button" onClick={onToggle}
            className="text-xs text-gray-500 hover:text-gray-800"
          >{expanded ? "▾" : "▸"}</button>
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-gray-100 bg-gray-50">
          <td colSpan={5} className="px-3 py-3">
            <pre className="text-xs font-mono text-gray-700 whitespace-pre-wrap break-all">
              {JSON.stringify(event.payload, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}
```

- [ ] **Step 2: Add the route**

In `web/src/App.tsx`:

```tsx
import AdminAudit from "./pages/AdminAudit";
```

Inside the `<Routes>` block:

```tsx
<Route path="admin/audit" element={<AdminAudit />} />
```

- [ ] **Step 3: Add admin-only nav item**

In `web/src/components/Layout.tsx`:

1. Import useQuery + api at the top:

```tsx
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
```

2. Inside the `Layout` component, add:

```tsx
const me = useQuery<{ role: string }>({
  queryKey: ["me"],
  queryFn:  () => api.get<{ role: string }>("/users/me"),
});
const isAdmin = me.data?.role === "admin";
```

3. Render an "Audit log" nav item conditionally — alongside (or below) the existing Settings item:

After the regular `navItems.map(...)` block, add:

```tsx
{isAdmin && (
  <NavLink
    to="/admin/audit"
    className={({ isActive }) =>
      cn(
        "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium",
        "transition-colors duration-100",
        isActive
          ? "bg-accent-50 text-accent-700"
          : "text-gray-600 hover:bg-gray-50 hover:text-gray-900",
      )
    }
  >
    <Icon d="M9 12l2 2 4-4M21 12c0 4.97-4.03 9-9 9s-9-4.03-9-9 4.03-9 9-9 9 4.03 9 9z" />
    <span>Audit log</span>
  </NavLink>
)}
```

- [ ] **Step 4: tsc + build**

```bash
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npx tsc --noEmit
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npm run build
```

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/AdminAudit.tsx web/src/App.tsx web/src/components/Layout.tsx
git commit -m "feat(web): /admin/audit page + admin-only nav item"
```

---

## Task 10 — End-to-end smoke test

- [ ] **Step 1: Set up identity, run search_as, expect audit row**

Bootstrap user (admin):

```bash
curl -s -X POST http://127.0.0.1:8002/api/users/register -H "Content-Type: application/json" -d '{"username":"admin13","email":"a@a","password":"testtest"}' >/dev/null
TOKEN=$(curl -s -X POST http://127.0.0.1:8002/api/users/login -H "Content-Type: application/json" -d '{"username":"admin13","password":"testtest"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Search as posix_uid:9999.
OVERRIDE='{"type":"posix_uid","identifier":"9999","groups":[]}'
curl -s -G "http://127.0.0.1:8002/api/search" \
  --data-urlencode "q=" \
  --data-urlencode "search_as=$OVERRIDE" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Expected: empty results (no entries grant 9999), 200 OK.
```

- [ ] **Step 2: Verify audit row**

```bash
curl -s "http://127.0.0.1:8002/api/admin/audit" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Expected: at least one row with `event_type=search_as_used` and `payload.search_as.identifier=9999`.

- [ ] **Step 3: Filter to search_as_used only**

```bash
curl -s "http://127.0.0.1:8002/api/admin/audit?event_type=search_as_used" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Expected: only the one row.

- [ ] **Step 4: Non-admin denied**

```bash
# Create a non-admin via the admin-only create endpoint.
curl -s -X POST http://127.0.0.1:8002/api/users/create \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"username":"plain","password":"testtest","role":"user"}' >/dev/null

USER_TOKEN=$(curl -s -X POST http://127.0.0.1:8002/api/users/login -H "Content-Type: application/json" -d '{"username":"plain","password":"testtest"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8002/api/admin/audit" -H "Authorization: Bearer $USER_TOKEN"
```

Expected: `403`.

- [ ] **Step 5: UI walkthrough** (optional, document in report)

If a frontend dev server is running: open `http://127.0.0.1:5173/search`, click "Search as…", fill the form, run a search; verify the result count badge shows `(filtered as posix_uid:9999)`. Click "Audit log" in the sidebar (admin-only); verify the page lists the recent `search_as_used` event and the payload expand works.

No commit — verification only.

---

## Notes for the implementer

- **`record_event` must NOT raise.** It uses a try/except wrapper. Any audit-write failure logs a warning but the user-facing 200/201/204 still returns. This is non-negotiable — audit logging must not be a vector for breaking real operations.
- **`record_event` adds to the SAME session** as the request handler. Caller must commit afterwards. The pattern in identity handlers is: do the real work + commit, then call `record_event` + commit again. Two commits is fine — the audit row's existence is independent of the prior commit's outcome.
- **`search_as` is JSON-in-querystring.** Frontend uses `JSON.stringify`, backend uses `json.loads` + Pydantic validation. Malformed JSON → 422. Empty groups list is acceptable.
- **Admin-only nav and endpoints.** Both layers — frontend hides the link, backend `require_admin` enforces. Don't rely solely on the frontend hide.
- **Pagination defaults: page=1, page_size=50, max=200.** Tune later if real usage needs different.
- **Retention default = 0 = forever.** Existing deployments that don't set the env var keep all history.
- **Subset-of-events for v1.** Future events (group resolution, scan triggers, source mutations) extend the schema by adding new `event_type` values — no migration needed.
- **search_as does NOT alter source RBAC.** The user must still have access to the source(s) being searched. The override only swaps the principal-token set. This matters: an admin searching as a low-privilege user across sources they themselves have access to is the supported case; an admin can't see a source they were denied just by spoofing a privileged principal.
