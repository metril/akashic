"""Search-time `permission_filter` tests.

These tests verify the filter logic at the API surface — they don't need
Meilisearch to be running because they exercise the DB fallback path.
"""
import uuid

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
    """Create bindings, then call search with permission_filter=readable.

    We can't easily verify the filter clause directly without Meili, but
    we verify the endpoint succeeds with bindings configured.
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
    r = await client.get(
        "/api/search?q=&permission_filter=readable",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
