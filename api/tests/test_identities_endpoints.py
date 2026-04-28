import uuid

import pytest


async def _register_login(client, username="alice", password="testpass123", admin_token=None):
    if admin_token:
        await client.post(
            "/api/users/create",
            json={"username": username, "password": password},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    else:
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
    token_b = await _register_login(client, username="bob", admin_token=token_a)

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


@pytest.mark.asyncio
async def test_binding_identifier_whitespace_trimmed(client, db_session):
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
    add = await client.post(
        f"/api/identities/{pid}/bindings",
        json={
            "source_id": str(source.id),
            "identity_type": "posix_uid",
            "identifier": "  1000  ",
            "groups": [" 100 ", "", "  1000 "],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert add.status_code == 201
    body = add.json()
    assert body["identifier"] == "1000"
    assert body["groups"] == ["100", "1000"]


@pytest.mark.asyncio
async def test_binding_empty_identifier_rejected(client, db_session):
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
    add = await client.post(
        f"/api/identities/{pid}/bindings",
        json={"source_id": str(source.id), "identity_type": "posix_uid", "identifier": "   ", "groups": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert add.status_code == 422


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
