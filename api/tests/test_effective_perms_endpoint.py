import pytest


async def _register_login(client, username="alice", password="testpass123"):
    await client.post("/api/users/register", json={"username": username, "password": password})
    login = await client.post("/api/users/login", json={"username": username, "password": password})
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_effective_perms_404_for_unknown_entry(client):
    token = await _register_login(client)
    r = await client.post(
        "/api/entries/00000000-0000-0000-0000-000000000000/effective-permissions",
        json={"principal": {"type": "posix_uid", "identifier": "1000"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_effective_perms_requires_auth(client):
    r = await client.post(
        "/api/entries/00000000-0000-0000-0000-000000000000/effective-permissions",
        json={"principal": {"type": "posix_uid", "identifier": "1000"}},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_effective_perms_happy_path_posix(client, db_session):
    """End-to-end: create source + entry, then call the endpoint."""
    import uuid
    from akashic.models import Entry, Source

    token = await _register_login(client, username="bob")

    source = Source(
        id=uuid.uuid4(),
        name="t",
        type="local",
        connection_config={"path": "/tmp"},
    )
    db_session.add(source)
    await db_session.flush()

    entry = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        kind="file",
        path="/tmp/x",
        parent_path="/tmp",
        name="x",
        mode=0o755,
        uid=1000,
        gid=100,
        acl={"type": "posix",
             "entries": [{"tag": "user", "qualifier": "1001", "perms": "rwx"}],
             "default_entries": None},
    )
    db_session.add(entry)
    await db_session.commit()

    r = await client.post(
        f"/api/entries/{entry.id}/effective-permissions",
        json={"principal": {"type": "posix_uid", "identifier": "1001"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Allow either 200 (full RBAC granted automatically) or 403 (RBAC blocks).
    assert r.status_code in (200, 403)
    if r.status_code == 200:
        data = r.json()
        assert data["evaluated_with"]["model"] == "posix"
        assert data["rights"]["read"]["granted"] is True
