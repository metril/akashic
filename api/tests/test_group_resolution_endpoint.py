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


@pytest.mark.asyncio
async def test_resolve_groups_cache_expires_after_ttl(client, db_session, monkeypatch):
    """A cache row older than `group_cache_ttl_hours` is ignored; backend re-called."""
    from datetime import datetime, timedelta, timezone
    from akashic.models import Source
    from akashic.models.principal_groups_cache import PrincipalGroupsCache

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

    # Pre-seed an EXPIRED cache row.
    db_session.add(PrincipalGroupsCache(
        source_id=src.id,
        identity_type="posix_uid",
        identifier="1000",
        groups=["stale"],
        resolved_at=datetime.now(timezone.utc) - timedelta(hours=48),
    ))
    await db_session.commit()

    # Endpoint should bypass the stale cache and re-resolve.
    r = await client.post(
        f"/api/identities/{pid}/bindings/{bid}/resolve-groups",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["groups"] == ["100"]
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_resolve_groups_audit_payload_has_source_nss(client, db_session, monkeypatch):
    """Lock in the audit payload `source` field shape ('nss' for POSIX)."""
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
    assert rows[0].payload["source"] == "nss"
