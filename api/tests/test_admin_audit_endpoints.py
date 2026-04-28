import uuid
from datetime import datetime, timedelta, timezone

import pytest


async def _register_login(client, username="alice", password="testpass123"):
    await client.post("/api/users/register", json={"username": username, "password": password})
    login = await client.post("/api/users/login", json={"username": username, "password": password})
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_admin_audit_requires_admin(client):
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
