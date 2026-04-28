# Phase 14a — Group-Membership Auto-Resolution (POSIX + LDAP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users click a "Resolve groups" button on each FsBinding and have akashic auto-populate the binding's group list from the source. **In scope for Phase 14a:** Local POSIX (Python `pwd`/`grp` stdlib) and NFSv4 LDAP (using the existing `python-ldap` dep). **Out of scope (Phase 14b):** SSH POSIX (needs `paramiko` or subprocess+ssh), NT SMB (needs new SAMR Go package — substantial work).

**Architecture:** A new `services/group_resolver.py` exposes `resolve_groups(source, binding) -> ResolveResult` that dispatches on `source.type` + `binding.identity_type`. Each implementation is small and pure-ish. A new `PrincipalGroupsCache` table holds resolved groups with a 24h TTL — the resolve endpoint reads cache first, refreshes when stale, persists. A new `POST /api/identities/{id}/bindings/{bid}/resolve-groups` endpoint runs the resolution, updates the binding's `groups` + `groups_resolved_at` + `groups_source='auto'`, and emits a `groups_auto_resolved` audit event. Sources of `type='ssh'` and `type='smb'` return `ErrUnsupported` with a clear message.

**Tech Stack:** Python 3.12 (FastAPI/Pydantic v2/SQLAlchemy async), python-ldap (existing), TypeScript/React 18, Tailwind, pytest-asyncio.

---

## File structure

**Create**
- `api/akashic/models/principal_groups_cache.py` — cache table.
- `api/akashic/services/group_resolver.py` — resolution dispatcher + per-source-type implementations.
- `api/akashic/routers/group_resolution.py` — single endpoint (kept separate from `identities.py` to avoid bloat).
- `api/akashic/tools/warm_groups.py` — bulk warm CLI.
- `api/tests/test_group_resolver.py` — per-implementation unit tests with mocking.
- `api/tests/test_group_resolution_endpoint.py` — endpoint integration tests.

**Edit**
- `api/akashic/models/__init__.py` — export `PrincipalGroupsCache`.
- `api/akashic/main.py` — register router.
- `api/akashic/config.py` — LDAP config fields already exist; add `group_cache_ttl_hours: int = 24`.
- `web/src/lib/identityTypes.ts` — add `ResolveGroupsResult` shape.
- `web/src/types/index.ts` — re-export.
- `web/src/pages/SettingsIdentities.tsx` — add "Resolve groups" button per binding row.

**No deletes.** New schema via existing `Base.metadata.create_all`.

---

## Cross-task spec: dispatch table

| `source.type` | `binding.identity_type` | Implementation | Status |
|---|---|---|---|
| `local`, `nfs` | `posix_uid` | Python `pwd`/`grp` stdlib | ✅ in scope |
| any | `nfsv4_principal` | LDAP `(uid=<principal>) → memberOf` | ✅ in scope |
| `ssh` | any | (deferred) | ⛔ ErrUnsupported in 14a |
| `smb` | `sid` | (SAMR — Phase 14b) | ⛔ ErrUnsupported in 14a |
| `s3` | any | (no analog) | ⛔ ErrUnsupported |
| any other combo | — | clear error message | ⛔ ErrUnsupported |

`ResolveResult` shape:
```python
class ResolveResult(BaseModel):
    groups: list[str]
    source: Literal["nss", "ldap"]   # 'nss' for POSIX local, 'ldap' for LDAP
    resolved_at: datetime
```

When resolution fails (network, unsupported, principal not found), the endpoint returns 422 with a structured error: `{"detail": "...", "reason": "unsupported|not_found|backend_error"}`.

---

## Task 1 — `PrincipalGroupsCache` model

**Files:**
- Create: `api/akashic/models/principal_groups_cache.py`
- Modify: `api/akashic/models/__init__.py`

- [ ] **Step 1: Create model**

```python
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, PrimaryKeyConstraint, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class PrincipalGroupsCache(Base):
    """Time-bounded cache of resolved groups per (source, identity_type, identifier).

    Composite primary key — at most one row per principal per source.
    """
    __tablename__ = "principal_groups_cache"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False,
    )
    identity_type: Mapped[str] = mapped_column(String, nullable=False)
    identifier: Mapped[str] = mapped_column(String, nullable=False)
    groups: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        PrimaryKeyConstraint("source_id", "identity_type", "identifier", name="pk_principal_groups_cache"),
    )
```

- [ ] **Step 2: Re-export**

In `api/akashic/models/__init__.py`, add `from akashic.models.principal_groups_cache import PrincipalGroupsCache` and `"PrincipalGroupsCache"` to `__all__`.

- [ ] **Step 3: Smoke-test**

```bash
docker exec akashic-eff4-api python -c "
from akashic.database import Base, engine
from akashic import models  # noqa
import asyncio
async def go():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print('OK')
asyncio.run(go())
"
docker exec -e PGPASSWORD=changeme akashic-eff4-postgres-1 psql -U akashic -d akashic -c "\d principal_groups_cache"
```

Expected: `OK` then column listing showing the 5 columns + composite PK.

- [ ] **Step 4: Commit**

```bash
git add api/akashic/models/principal_groups_cache.py api/akashic/models/__init__.py
git commit -m "feat(api): PrincipalGroupsCache TTL cache table"
```

---

## Task 2 — `group_resolver.py` service (TDD)

Pure function dispatcher with per-source-type / per-identity-type implementations.

**Files:**
- Create: `api/akashic/services/group_resolver.py`
- Modify: `api/akashic/config.py` (add `group_cache_ttl_hours: int = 24`)
- Create: `api/tests/test_group_resolver.py`

- [ ] **Step 1: Add config setting**

In `api/akashic/config.py`, add inside `Settings`:

```python
    group_cache_ttl_hours: int = 24
```

- [ ] **Step 2: Write failing tests**

`api/tests/test_group_resolver.py`:

```python
"""Group resolver tests — mocks system calls / LDAP."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_posix_local_resolves_via_nss(monkeypatch):
    """For source.type=local + identity_type=posix_uid, resolve via pwd/grp."""
    from akashic.services.group_resolver import (
        ResolveResult, UnsupportedResolution, resolve_groups,
    )

    class _FakePwd:
        pw_name = "alice"
    monkeypatch.setattr("akashic.services.group_resolver._pwd_getpwuid", lambda uid: _FakePwd())
    monkeypatch.setattr("akashic.services.group_resolver._os_getgrouplist",
                         lambda name, base_gid: [100, 1000, 9999])

    class _FakeSource:
        type = "local"
        connection_config = {}
    class _FakeBinding:
        identity_type = "posix_uid"
        identifier = "1000"

    result = await resolve_groups(_FakeSource(), _FakeBinding())
    assert isinstance(result, ResolveResult)
    assert result.groups == ["100", "1000", "9999"]
    assert result.source == "nss"


@pytest.mark.asyncio
async def test_posix_unknown_uid_raises_not_found(monkeypatch):
    """When pwd.getpwuid raises KeyError, surface a clear error."""
    from akashic.services.group_resolver import (
        ResolutionFailed, resolve_groups,
    )

    def _raise(uid):
        raise KeyError("nope")
    monkeypatch.setattr("akashic.services.group_resolver._pwd_getpwuid", _raise)

    class _FakeSource:
        type = "local"
        connection_config = {}
    class _FakeBinding:
        identity_type = "posix_uid"
        identifier = "999999"

    with pytest.raises(ResolutionFailed) as exc:
        await resolve_groups(_FakeSource(), _FakeBinding())
    assert exc.value.reason == "not_found"


@pytest.mark.asyncio
async def test_ssh_unsupported(monkeypatch):
    """SSH sources are deferred to 14b; resolver must return Unsupported."""
    from akashic.services.group_resolver import UnsupportedResolution, resolve_groups

    class _FakeSource:
        type = "ssh"
        connection_config = {}
    class _FakeBinding:
        identity_type = "posix_uid"
        identifier = "1000"

    with pytest.raises(UnsupportedResolution):
        await resolve_groups(_FakeSource(), _FakeBinding())


@pytest.mark.asyncio
async def test_smb_unsupported(monkeypatch):
    """SMB sources defer to 14b (SAMR); resolver returns Unsupported."""
    from akashic.services.group_resolver import UnsupportedResolution, resolve_groups

    class _FakeSource:
        type = "smb"
        connection_config = {}
    class _FakeBinding:
        identity_type = "sid"
        identifier = "S-1-5-21-1-2-3-1013"

    with pytest.raises(UnsupportedResolution):
        await resolve_groups(_FakeSource(), _FakeBinding())


@pytest.mark.asyncio
async def test_s3_unsupported():
    from akashic.services.group_resolver import UnsupportedResolution, resolve_groups

    class _FakeSource:
        type = "s3"
        connection_config = {}
    class _FakeBinding:
        identity_type = "s3_canonical"
        identifier = "acct-1"

    with pytest.raises(UnsupportedResolution):
        await resolve_groups(_FakeSource(), _FakeBinding())


@pytest.mark.asyncio
async def test_ldap_resolves_memberof(monkeypatch):
    """For identity_type=nfsv4_principal, query LDAP for memberOf."""
    from akashic.services.group_resolver import ResolveResult, resolve_groups

    fake_ldap_results = [
        # python-ldap returns (dn, attrs) tuples
        ("uid=alice,ou=people,dc=example,dc=com", {
            "memberOf": [
                b"cn=engineers,ou=groups,dc=example,dc=com",
                b"cn=admins,ou=groups,dc=example,dc=com",
            ],
        }),
    ]

    class _FakeLdap:
        def simple_bind_s(self, *_a, **_k): pass
        def search_s(self, base, scope, filterstr=None, attrlist=None):
            return fake_ldap_results
        def unbind_s(self): pass

    monkeypatch.setattr("akashic.services.group_resolver._ldap_initialize",
                         lambda url: _FakeLdap())

    class _FakeSource:
        type = "nfs"
        connection_config = {
            "ldap_url": "ldap://ldap.example.com",
            "ldap_bind_dn": "cn=svc,dc=example,dc=com",
            "ldap_bind_password": "x",
            "ldap_user_search_base": "ou=people,dc=example,dc=com",
        }
    class _FakeBinding:
        identity_type = "nfsv4_principal"
        identifier = "alice@example.com"

    result = await resolve_groups(_FakeSource(), _FakeBinding())
    assert isinstance(result, ResolveResult)
    assert "engineers" in result.groups
    assert "admins" in result.groups
    assert result.source == "ldap"


@pytest.mark.asyncio
async def test_ldap_no_config_raises_unsupported():
    """LDAP-required type without LDAP config in source → Unsupported."""
    from akashic.services.group_resolver import UnsupportedResolution, resolve_groups

    class _FakeSource:
        type = "nfs"
        connection_config = {}  # no ldap_url
    class _FakeBinding:
        identity_type = "nfsv4_principal"
        identifier = "alice"

    with pytest.raises(UnsupportedResolution):
        await resolve_groups(_FakeSource(), _FakeBinding())
```

- [ ] **Step 3: Run tests, verify ImportError**

```bash
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff4-api pytest tests/test_group_resolver.py -v
```

- [ ] **Step 4: Implement service**

`api/akashic/services/group_resolver.py`:

```python
"""Group-membership auto-resolution for FsBindings.

Per the Phase 14a scope:
  - source.type=local|nfs + posix_uid → Python pwd/grp stdlib (NSS)
  - identity_type=nfsv4_principal      → LDAP (memberOf attribute)
  - everything else                    → UnsupportedResolution

Phase 14b will add SSH POSIX (subprocess) and NT SMB (SAMR over DCE/RPC).
"""
from __future__ import annotations

import logging
import os
import pwd
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ── Shapes / errors ─────────────────────────────────────────────────────────

class ResolveResult(BaseModel):
    groups: list[str]
    source: Literal["nss", "ldap"]
    resolved_at: datetime


class ResolutionFailed(Exception):
    """The resolver attempted resolution but the principal could not be
    authoritatively resolved (not_found, backend_error, etc.)."""
    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason


class UnsupportedResolution(Exception):
    """This (source.type, binding.identity_type) combination has no resolver."""
    pass


# ── Stdlib indirection (so tests can monkeypatch) ───────────────────────────

def _pwd_getpwuid(uid: int):
    return pwd.getpwuid(uid)


def _os_getgrouplist(name: str, base_gid: int):
    return os.getgrouplist(name, base_gid)


def _ldap_initialize(url: str):
    """Imported lazily because python-ldap doesn't ship on every dev box."""
    import ldap  # noqa
    return ldap.initialize(url)


# ── Per-implementation helpers ──────────────────────────────────────────────


def _resolve_posix_local(identifier: str) -> ResolveResult:
    try:
        uid = int(identifier)
    except ValueError as exc:
        raise ResolutionFailed("not_found", f"identifier {identifier!r} is not a uid")

    try:
        pw = _pwd_getpwuid(uid)
    except KeyError:
        raise ResolutionFailed("not_found", f"uid {uid} not in passwd")

    try:
        gids = _os_getgrouplist(pw.pw_name, pw.pw_gid if hasattr(pw, "pw_gid") else 0)
    except Exception as exc:  # noqa: BLE001
        raise ResolutionFailed("backend_error", str(exc))

    return ResolveResult(
        groups=[str(g) for g in gids],
        source="nss",
        resolved_at=datetime.now(timezone.utc),
    )


def _resolve_ldap(source, binding) -> ResolveResult:
    cfg = source.connection_config or {}
    url        = cfg.get("ldap_url")
    bind_dn    = cfg.get("ldap_bind_dn", "")
    bind_pw    = cfg.get("ldap_bind_password", "")
    search_base = cfg.get("ldap_user_search_base")
    group_attr = cfg.get("ldap_group_attr", "memberOf")

    if not url or not search_base:
        raise UnsupportedResolution(
            "Source missing ldap_url or ldap_user_search_base in connection_config"
        )

    try:
        conn = _ldap_initialize(url)
        conn.simple_bind_s(bind_dn, bind_pw)
        # Filter by uid attribute against the principal's local-part.
        local = binding.identifier.split("@", 1)[0]
        results = conn.search_s(
            search_base,
            2,  # ldap.SCOPE_SUBTREE
            filterstr=f"(uid={local})",
            attrlist=[group_attr],
        )
        try:
            conn.unbind_s()
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        raise ResolutionFailed("backend_error", str(exc))

    if not results:
        raise ResolutionFailed("not_found", f"no LDAP entry for uid={local!r}")

    _dn, attrs = results[0]
    raw_dns = attrs.get(group_attr, []) or []
    groups: list[str] = []
    for raw in raw_dns:
        s = raw.decode() if isinstance(raw, bytes) else raw
        # cn=engineers,ou=groups,dc=… → engineers
        cn = s.split(",", 1)[0]
        if cn.lower().startswith("cn="):
            groups.append(cn[3:])
        else:
            groups.append(s)

    return ResolveResult(
        groups=groups,
        source="ldap",
        resolved_at=datetime.now(timezone.utc),
    )


# ── Public dispatcher ───────────────────────────────────────────────────────


async def resolve_groups(source, binding) -> ResolveResult:
    """Resolve groups for a binding against its source. Raises:
       - UnsupportedResolution: combo isn't implemented (caller renders 422 hint)
       - ResolutionFailed: backend reachable but principal not findable
    """
    src_type = getattr(source, "type", None)
    id_type = getattr(binding, "identity_type", None)

    # NFSv4 always tries LDAP if available, regardless of source.type.
    if id_type == "nfsv4_principal":
        return _resolve_ldap(source, binding)

    if id_type == "posix_uid":
        if src_type in ("local", "nfs"):
            return _resolve_posix_local(binding.identifier)
        if src_type == "ssh":
            raise UnsupportedResolution(
                "SSH POSIX group resolution is not yet implemented (Phase 14b)"
            )
        raise UnsupportedResolution(
            f"posix_uid resolution not supported on source.type={src_type!r}"
        )

    if id_type == "sid":
        raise UnsupportedResolution(
            "NT/SID group resolution requires SAMR (Phase 14b)"
        )

    if id_type == "s3_canonical":
        raise UnsupportedResolution("S3 has no group concept")

    raise UnsupportedResolution(f"Unknown identity_type: {id_type!r}")
```

- [ ] **Step 5: Run tests, verify pass**

```bash
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff4-api pytest tests/test_group_resolver.py -v
```

Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add api/akashic/config.py api/akashic/services/group_resolver.py api/tests/test_group_resolver.py
git commit -m "feat(api): group_resolver service (POSIX local + LDAP; SSH/SMB deferred)"
```

---

## Task 3 — `POST /api/identities/{id}/bindings/{bid}/resolve-groups`

Endpoint flow:
1. Verify person belongs to user.
2. Load binding.
3. Check cache (`PrincipalGroupsCache` keyed on `(source_id, identity_type, identifier)`); if fresh (within `group_cache_ttl_hours`), return its groups without re-resolving.
4. Otherwise call `resolve_groups(source, binding)`.
5. On success: update binding.groups + groups_resolved_at + groups_source='auto', upsert cache, audit `groups_auto_resolved`.
6. On UnsupportedResolution: 422.
7. On ResolutionFailed: 422 with the reason.

Returns the `FsBindingOut` shape so the frontend can drop it straight into state.

**Files:**
- Create: `api/akashic/routers/group_resolution.py`
- Modify: `api/akashic/main.py`
- Create: `api/tests/test_group_resolution_endpoint.py`

- [ ] **Step 1: Write failing tests**

`api/tests/test_group_resolution_endpoint.py`:

```python
import uuid
from datetime import datetime, timedelta, timezone

import pytest


async def _register_login(client, username="alice", password="testpass123"):
    await client.post("/api/users/register", json={"username": username, "password": password})
    login = await client.post("/api/users/login", json={"username": username, "password": password})
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_resolve_groups_posix_local(client, db_session, monkeypatch):
    from akashic.models import Source

    # Patch the resolver helpers so we don't need real /etc/passwd.
    class _FakePwd:
        pw_name = "alice"
        pw_gid = 100
    monkeypatch.setattr("akashic.services.group_resolver._pwd_getpwuid", lambda uid: _FakePwd())
    monkeypatch.setattr("akashic.services.group_resolver._os_getgrouplist",
                         lambda name, base_gid: [100, 1000, 9999])

    token = await _register_login(client)
    src = Source(id=uuid.uuid4(), name="t", type="local", connection_config={"path": "/tmp"})
    db_session.add(src)
    await db_session.commit()

    pid = (await client.post(
        "/api/identities", json={"label": "P"},
        headers={"Authorization": f"Bearer {token}"},
    )).json()["id"]
    bid = (await client.post(
        f"/api/identities/{pid}/bindings",
        json={"source_id": str(src.id), "identity_type": "posix_uid", "identifier": "1000", "groups": []},
        headers={"Authorization": f"Bearer {token}"},
    )).json()["id"]

    r = await client.post(
        f"/api/identities/{pid}/bindings/{bid}/resolve-groups",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["groups"] == ["100", "1000", "9999"]
    assert body["groups_source"] == "auto"


@pytest.mark.asyncio
async def test_resolve_groups_records_audit(client, db_session, monkeypatch):
    from akashic.models import Source
    from akashic.models.audit_event import AuditEvent
    from sqlalchemy import select

    class _FakePwd:
        pw_name = "alice"
        pw_gid = 100
    monkeypatch.setattr("akashic.services.group_resolver._pwd_getpwuid", lambda uid: _FakePwd())
    monkeypatch.setattr("akashic.services.group_resolver._os_getgrouplist",
                         lambda *_a: [100])

    token = await _register_login(client)
    src = Source(id=uuid.uuid4(), name="t", type="local", connection_config={"path": "/tmp"})
    db_session.add(src)
    await db_session.commit()

    pid = (await client.post(
        "/api/identities", json={"label": "P"},
        headers={"Authorization": f"Bearer {token}"},
    )).json()["id"]
    bid = (await client.post(
        f"/api/identities/{pid}/bindings",
        json={"source_id": str(src.id), "identity_type": "posix_uid", "identifier": "1000", "groups": []},
        headers={"Authorization": f"Bearer {token}"},
    )).json()["id"]

    await client.post(
        f"/api/identities/{pid}/bindings/{bid}/resolve-groups",
        headers={"Authorization": f"Bearer {token}"},
    )

    rows = (await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "groups_auto_resolved")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload["resolved_count"] == 1


@pytest.mark.asyncio
async def test_resolve_groups_ssh_unsupported(client, db_session):
    from akashic.models import Source

    token = await _register_login(client)
    src = Source(id=uuid.uuid4(), name="t", type="ssh", connection_config={})
    db_session.add(src)
    await db_session.commit()

    pid = (await client.post(
        "/api/identities", json={"label": "P"},
        headers={"Authorization": f"Bearer {token}"},
    )).json()["id"]
    bid = (await client.post(
        f"/api/identities/{pid}/bindings",
        json={"source_id": str(src.id), "identity_type": "posix_uid", "identifier": "1000", "groups": []},
        headers={"Authorization": f"Bearer {token}"},
    )).json()["id"]

    r = await client.post(
        f"/api/identities/{pid}/bindings/{bid}/resolve-groups",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_resolve_groups_cache_hit_skips_backend(client, db_session, monkeypatch):
    """Second call within TTL hits cache, doesn't call resolver."""
    from akashic.models import Source

    call_count = {"n": 0}
    class _FakePwd:
        pw_name = "alice"
        pw_gid = 100
    def _spy(uid):
        call_count["n"] += 1
        return _FakePwd()
    monkeypatch.setattr("akashic.services.group_resolver._pwd_getpwuid", _spy)
    monkeypatch.setattr("akashic.services.group_resolver._os_getgrouplist",
                         lambda *_a: [100])

    token = await _register_login(client)
    src = Source(id=uuid.uuid4(), name="t", type="local", connection_config={"path": "/tmp"})
    db_session.add(src)
    await db_session.commit()

    pid = (await client.post(
        "/api/identities", json={"label": "P"},
        headers={"Authorization": f"Bearer {token}"},
    )).json()["id"]
    bid = (await client.post(
        f"/api/identities/{pid}/bindings",
        json={"source_id": str(src.id), "identity_type": "posix_uid", "identifier": "1000", "groups": []},
        headers={"Authorization": f"Bearer {token}"},
    )).json()["id"]

    await client.post(
        f"/api/identities/{pid}/bindings/{bid}/resolve-groups",
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        f"/api/identities/{pid}/bindings/{bid}/resolve-groups",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Two endpoint calls but only one backend hit.
    assert call_count["n"] == 1
```

- [ ] **Step 2: Run tests, verify failure**

```bash
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff4-api pytest tests/test_group_resolution_endpoint.py -v
```

- [ ] **Step 3: Implement router**

`api/akashic/routers/group_resolution.py`:

```python
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
```

- [ ] **Step 4: Register router**

In `api/akashic/main.py`, add `group_resolution` to the imports and `app.include_router(group_resolution.router)` alongside others.

- [ ] **Step 5: Restart, run tests**

```bash
docker restart akashic-eff4-api
sleep 3
docker exec -e TEST_DB_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_test akashic-eff4-api pytest tests/test_group_resolution_endpoint.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add api/akashic/routers/group_resolution.py api/akashic/main.py api/tests/test_group_resolution_endpoint.py
git commit -m "feat(api): POST /api/identities/.../resolve-groups with cache + audit"
```

---

## Task 4 — Bulk warm CLI

**Files:**
- Create: `api/akashic/tools/warm_groups.py`

- [ ] **Step 1: Create the script**

```python
"""Bulk warm the principal_groups_cache for every binding on a source.

Usage:
    python -m akashic.tools.warm_groups --source-id <UUID>
    python -m akashic.tools.warm_groups   # all sources

Calls resolve_groups for every distinct (source, identity_type, identifier)
that has at least one FsBinding. Writes results into the cache table.
Skips bindings whose source.type doesn't support resolution.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import uuid

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from akashic.config import settings
from akashic.models.fs_person import FsBinding
from akashic.models.principal_groups_cache import PrincipalGroupsCache
from akashic.models.source import Source
from akashic.services.group_resolver import (
    ResolutionFailed, UnsupportedResolution, resolve_groups,
)

logger = logging.getLogger(__name__)


async def _warm(source_id: uuid.UUID | None) -> int:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    n = 0
    try:
        async with session_factory() as db:
            stmt = select(FsBinding, Source).join(Source, FsBinding.source_id == Source.id)
            if source_id is not None:
                stmt = stmt.where(FsBinding.source_id == source_id)
            rows = (await db.execute(stmt)).all()

            for binding, source in rows:
                try:
                    result = await resolve_groups(source, binding)
                except (UnsupportedResolution, ResolutionFailed) as exc:
                    logger.info("skipped %s/%s: %s", source.name, binding.identifier, exc)
                    continue

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
                    set_={
                        "groups": cache_stmt.excluded.groups,
                        "resolved_at": cache_stmt.excluded.resolved_at,
                    },
                )
                await db.execute(cache_stmt)
                n += 1
            await db.commit()
    finally:
        await engine.dispose()
    return n


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Warm the principal_groups_cache.")
    parser.add_argument("--source-id", type=uuid.UUID, default=None)
    args = parser.parse_args()
    n = asyncio.run(_warm(args.source_id))
    print(f"Warmed {n} bindings.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test the import**

```bash
docker exec akashic-eff4-api python -c "from akashic.tools.warm_groups import _warm, main; print('OK')"
```

- [ ] **Step 3: Run against empty db**

```bash
docker exec akashic-eff4-api python -m akashic.tools.warm_groups 2>&1 | tail -3
```

Expected: `Warmed 0 bindings.` (or similar) with no traceback.

- [ ] **Step 4: Commit**

```bash
git add api/akashic/tools/warm_groups.py
git commit -m "feat(api): warm_groups bulk resolution CLI"
```

---

## Task 5 — Frontend: "Resolve groups" button

**Files:**
- Modify: `web/src/pages/SettingsIdentities.tsx`

- [ ] **Step 1: Add resolve-groups mutation in `PersonCard`**

In `PersonCard`, alongside `addBinding` and `deleteBinding`, add:

```tsx
const resolveGroups = useMutation<FsBinding, Error, string>({
  mutationFn: (bid) => api.post<FsBinding>(`/identities/${person.id}/bindings/${bid}/resolve-groups`, {}),
  onSuccess:  () => qc.invalidateQueries({ queryKey: ["identities"] }),
});
```

- [ ] **Step 2: Render the button per binding**

Find the existing per-binding `<li>` rendering. Add a "Resolve groups" link/button next to the existing × delete button:

```tsx
<button
  type="button"
  onClick={() => resolveGroups.mutate(b.id)}
  disabled={resolveGroups.isPending}
  className="text-xs text-accent-700 hover:text-accent-900 disabled:opacity-50"
  title="Auto-resolve groups from the source"
>
  {resolveGroups.isPending ? "Resolving…" : "Resolve"}
</button>
```

Place it just before the existing `×` button, separated by a thin gap.

- [ ] **Step 3: Display `resolved_at` and `groups_source` per binding**

In the same `<li>`, add a tiny indicator after the existing groups display:

```tsx
{b.groups_resolved_at && b.groups_source === "auto" && (
  <span className="text-[10px] text-gray-400">
    auto · {new Date(b.groups_resolved_at).toLocaleDateString()}
  </span>
)}
```

- [ ] **Step 4: Show resolveGroups error inline**

After the form rendering, add:

```tsx
{resolveGroups.error && (
  <div className="text-xs text-red-700 bg-red-50 rounded px-2 py-1 mt-2">
    {resolveGroups.error instanceof Error ? resolveGroups.error.message : "Resolve failed"}
  </div>
)}
```

- [ ] **Step 5: tsc + build**

```bash
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npx tsc --noEmit
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npm run build
```

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/SettingsIdentities.tsx
git commit -m "feat(web): Resolve groups button per FsBinding"
```

---

## Task 6 — End-to-end smoke test

- [ ] **Step 1: Set up identity with a real local-uid binding**

```bash
curl -s -X POST http://127.0.0.1:8003/api/users/register -H "Content-Type: application/json" -d '{"username":"admin14","email":"a@a","password":"testtest"}' >/dev/null
TOKEN=$(curl -s -X POST http://127.0.0.1:8003/api/users/login -H "Content-Type: application/json" -d '{"username":"admin14","password":"testtest"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
SRC=$(curl -s -X POST http://127.0.0.1:8003/api/sources -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"name":"local-src","type":"local","connection_config":{"path":"/tmp"}}' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
PID=$(curl -s -X POST http://127.0.0.1:8003/api/identities -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"label":"smoke14"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
BID=$(curl -s -X POST "http://127.0.0.1:8003/api/identities/$PID/bindings" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"source_id\":\"$SRC\",\"identity_type\":\"posix_uid\",\"identifier\":\"0\",\"groups\":[]}" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "binding=$BID"
```

(Use `identifier=0` for root — guaranteed to exist on every Linux box.)

- [ ] **Step 2: Resolve groups**

```bash
curl -s -X POST "http://127.0.0.1:8003/api/identities/$PID/bindings/$BID/resolve-groups" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Expected: `groups` array populated (root's groups), `groups_source: "auto"`, non-null `groups_resolved_at`.

- [ ] **Step 3: Confirm audit row**

```bash
curl -s "http://127.0.0.1:8003/api/admin/audit?event_type=groups_auto_resolved" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Expected: one row with `payload.binding_id` matching, `payload.resolved_count > 0`, `payload.source: "nss"`.

- [ ] **Step 4: Confirm cache hit on second call**

```bash
time curl -s -X POST "http://127.0.0.1:8003/api/identities/$PID/bindings/$BID/resolve-groups" -H "Authorization: Bearer $TOKEN" >/dev/null
```

Should return very fast (<50ms) — cache hit. Audit log should NOT have a second `groups_auto_resolved` event (cache hits don't audit, since no backend call happened).

Actually verify: spec calls for audit on every resolve. The current implementation only audits when the backend was called (cache miss). This is intentional — cache hits don't represent a fresh resolution, so they shouldn't log "groups_auto_resolved". Confirm by checking event count is still 1 after the second call.

- [ ] **Step 5: Confirm SSH/SMB return 422**

```bash
SSH_SRC=$(curl -s -X POST http://127.0.0.1:8003/api/sources -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"name":"ssh-src","type":"ssh","connection_config":{"host":"foo","username":"bar"}}' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
SSH_BID=$(curl -s -X POST "http://127.0.0.1:8003/api/identities/$PID/bindings" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"source_id\":\"$SSH_SRC\",\"identity_type\":\"posix_uid\",\"identifier\":\"1000\",\"groups\":[]}" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST "http://127.0.0.1:8003/api/identities/$PID/bindings/$SSH_BID/resolve-groups" -H "Authorization: Bearer $TOKEN"
```

Expected: `HTTP 422`.

No commit — verification only.

---

## Notes for the implementer

- **Cache hits skip the audit event** — they don't represent a fresh authoritative resolution. The audit event captures backend resolution events only.
- **`pwd.getpwuid` runs inside the api container.** If the api container's `/etc/passwd` doesn't have the uid, you'll get `not_found`. For real deployments this needs to be the same NSS source as the source machine — not a problem for local sources running on the same host, but won't work for "local" sources that happen to be NFS mounts of a different machine's UIDs. Document this limitation in the response message if you hit it.
- **LDAP `memberOf` parsing extracts the CN.** `cn=engineers,ou=groups,...` → `engineers`. If the directory uses a different attr (e.g. `groupMembership`), set `ldap_group_attr` in the source's connection_config.
- **`UnsupportedResolution` returns 422** with `{"reason": "unsupported", "message": "..."}`. Frontend should surface the message inline so users know to manually fill groups for SSH/SMB sources.
- **Phase 14b will replace the SSH/SMB stubs.** SSH adds a subprocess call to `id -G`; SMB adds the `scanner/internal/samr/` Go package. Both replace `UnsupportedResolution` with real implementations, no other callers change.
- **Bulk warm CLI doesn't audit.** Per-event auditing of bulk operations would create noise; users running bulk warms know what they're doing. The cache table itself is the audit trail.
- **No model/schema changes for FsBinding.** The existing `groups`, `groups_source`, `groups_resolved_at` fields cover everything Phase 14a needs.
