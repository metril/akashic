"""Bulk-delete-copies endpoint tests.

Mocks `akashic.services.duplicate_delete.delete_copy` so the tests don't
actually spawn an `akashic-scanner delete` subprocess. The connector
behavior is covered separately by Go-side tests.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select


async def _register_login(client, username="admin", password="testpass123"):
    """First-registered user becomes admin per akashic.routers.users.register."""
    await client.post("/api/users/register", json={"username": username, "password": password})
    login = await client.post(
        "/api/users/login", json={"username": username, "password": password},
    )
    return login.json()["access_token"]


async def _make_dup_group(db_session, hash_, n=3, source_type="local"):
    """Create one source plus N entries with the same content_hash. Returns
    (source, [entry, …]) so the test can drive whichever entries it wants."""
    from akashic.models.entry import Entry
    from akashic.models.source import Source

    src = Source(
        name=f"src-{hash_[:6]}",
        type=source_type,
        connection_config={"path": "/tmp"},
        status="online",
    )
    db_session.add(src)
    await db_session.flush()

    entries = []
    for i in range(n):
        e = Entry(
            source_id=src.id,
            kind="file",
            parent_path="/",
            path=f"/dup_{i}.bin",
            name=f"dup_{i}.bin",
            extension="bin",
            size_bytes=1024,
            content_hash=hash_,
        )
        db_session.add(e)
        entries.append(e)
    await db_session.commit()
    for e in entries:
        await db_session.refresh(e)
    await db_session.refresh(src)
    return src, entries


@pytest.mark.asyncio
async def test_delete_copies_requires_admin(client, db_session):
    # First user is admin; create a regular user via /create.
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

    _, entries = await _make_dup_group(db_session, "abc123def456")
    keep, victim = entries[0], entries[1]

    r = await client.post(
        f"/api/duplicates/abc123def456/delete-copies",
        json={
            "keep_entry_id": str(keep.id),
            "delete_entry_ids": [str(victim.id)],
        },
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_copies_rejects_empty_delete_list(client, db_session):
    admin_token = await _register_login(client)
    _, entries = await _make_dup_group(db_session, "hash_empty")
    r = await client.post(
        "/api/duplicates/hash_empty/delete-copies",
        json={
            "keep_entry_id": str(entries[0].id),
            "delete_entry_ids": [],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_copies_rejects_keep_in_delete_list(client, db_session):
    admin_token = await _register_login(client)
    _, entries = await _make_dup_group(db_session, "hash_overlap")
    keep_id = str(entries[0].id)
    r = await client.post(
        "/api/duplicates/hash_overlap/delete-copies",
        json={
            "keep_entry_id": keep_id,
            "delete_entry_ids": [keep_id],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_copies_rejects_wrong_hash(client, db_session):
    """Smuggle: entries from group A but request claims group B. Endpoint
    must refuse so this can't be used as a generic delete-by-id backdoor."""
    admin_token = await _register_login(client)
    _, entries = await _make_dup_group(db_session, "real_hash_aa")
    r = await client.post(
        "/api/duplicates/wrong_hash_bb/delete-copies",
        json={
            "keep_entry_id": str(entries[0].id),
            "delete_entry_ids": [str(entries[1].id)],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_copies_happy_path(client, db_session):
    from akashic.models.entry import Entry
    from akashic.models.audit_event import AuditEvent

    admin_token = await _register_login(client)
    _, entries = await _make_dup_group(db_session, "hash_happy", n=3)
    keep, drop1, drop2 = entries

    # Mock both the scanner subprocess and the Meilisearch delete so the
    # test runs in pure-DB mode.
    from akashic.services.duplicate_delete import DeleteResult

    mock_delete = AsyncMock(return_value=DeleteResult(ok=True, step="", message=""))
    with patch("akashic.routers.duplicates.delete_copy", mock_delete), \
         patch("akashic.routers.duplicates.delete_file_from_index", AsyncMock()):
        r = await client.post(
            "/api/duplicates/hash_happy/delete-copies",
            json={
                "keep_entry_id": str(keep.id),
                "delete_entry_ids": [str(drop1.id), str(drop2.id)],
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert r.status_code == 200
    body = r.json()
    assert len(body["deleted"]) == 2
    assert len(body["failed"]) == 0
    assert {d["entry_id"] for d in body["deleted"]} == {str(drop1.id), str(drop2.id)}

    # The keep entry survives; the dropped entries are gone.
    keeper = (await db_session.execute(
        select(Entry).where(Entry.id == keep.id)
    )).scalar_one_or_none()
    assert keeper is not None

    survivors = (await db_session.execute(
        select(Entry).where(Entry.id.in_([drop1.id, drop2.id]))
    )).scalars().all()
    assert survivors == []

    # Audit rows recorded — one per successful deletion.
    audit_rows = (await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "duplicate_copy_deleted")
    )).scalars().all()
    assert len(audit_rows) == 2

    # delete_copy was invoked once per victim with the expected paths.
    assert mock_delete.call_count == 2


@pytest.mark.asyncio
async def test_delete_copies_partial_failure(client, db_session):
    """One copy deletes, one fails — Postgres reflects exactly that."""
    from akashic.models.entry import Entry
    from akashic.services.duplicate_delete import DeleteResult

    admin_token = await _register_login(client)
    _, entries = await _make_dup_group(db_session, "hash_partial", n=3)
    keep, success, fail = entries

    async def selective_delete(source, path):
        if path == fail.path:
            return DeleteResult(ok=False, step="auth", message="permission denied")
        return DeleteResult(ok=True, step="", message="")

    with patch("akashic.routers.duplicates.delete_copy", side_effect=selective_delete), \
         patch("akashic.routers.duplicates.delete_file_from_index", AsyncMock()):
        r = await client.post(
            "/api/duplicates/hash_partial/delete-copies",
            json={
                "keep_entry_id": str(keep.id),
                "delete_entry_ids": [str(success.id), str(fail.id)],
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert r.status_code == 200
    body = r.json()
    assert len(body["deleted"]) == 1
    assert len(body["failed"]) == 1
    assert body["deleted"][0]["entry_id"] == str(success.id)
    assert body["failed"][0]["entry_id"] == str(fail.id)
    assert body["failed"][0]["step"] == "auth"
    assert "permission" in body["failed"][0]["message"]

    # `success` is gone; `fail` is still in the DB.
    remaining = (await db_session.execute(
        select(Entry).where(Entry.id.in_([success.id, fail.id]))
    )).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].id == fail.id


@pytest.mark.asyncio
async def test_delete_copies_404_on_missing_entry(client, db_session):
    admin_token = await _register_login(client)
    _, entries = await _make_dup_group(db_session, "hash_404")

    bogus_id = str(uuid.uuid4())
    r = await client.post(
        "/api/duplicates/hash_404/delete-copies",
        json={
            "keep_entry_id": str(entries[0].id),
            "delete_entry_ids": [bogus_id],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 404
