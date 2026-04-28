import json
import uuid

import pytest


async def _register_login(client, username="alice", password="testpass123"):
    await client.post("/api/users/register", json={"username": username, "password": password})
    login = await client.post("/api/users/login", json={"username": username, "password": password})
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_search_as_records_audit_event(client, db_session):
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
    from akashic.models import Source
    from akashic.models.audit_event import AuditEvent
    from sqlalchemy import select
    token = await _register_login(client)

    src = Source(id=uuid.uuid4(), name="t", type="local", connection_config={"path": "/tmp"})
    db_session.add(src)
    await db_session.commit()

    pid = (await client.post(
        "/api/identities", json={"label": "self"},
        headers={"Authorization": f"Bearer {token}"},
    )).json()["id"]
    await client.post(
        f"/api/identities/{pid}/bindings",
        json={"source_id": str(src.id), "identity_type": "posix_uid", "identifier": "1000", "groups": []},
        headers={"Authorization": f"Bearer {token}"},
    )

    override = json.dumps({"type": "posix_uid", "identifier": "9999", "groups": []})
    await client.get(
        f"/api/search?q=&search_as={override}",
        headers={"Authorization": f"Bearer {token}"},
    )

    rows = (await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "search_as_used")
    )).scalars().all()
    assert any(r.payload["search_as"]["identifier"] == "9999" for r in rows)
