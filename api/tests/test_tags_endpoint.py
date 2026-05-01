"""Tag catalogue + applied-tag endpoint coverage.

Phase C reshaped the EntryTag table — `(entry_id, tag, inherited_from)`
instead of `(entry_id, tag_id)`. These tests cover catalogue CRUD plus
the new admin-only apply / remove / bulk-apply / get endpoints.
Inheritance correctness lives in test_tag_inheritance.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.tag import Tag, EntryTag
from akashic.models.user import User


@pytest_asyncio.fixture
async def admin_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(), username="admin",
            email="a@b.c", password_hash="x", role="admin",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def viewer_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(), username="viewer",
            email="v@b.c", password_hash="x", role="viewer",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def _build_client(setup_db, user: User) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_get_current_user():
        return user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def client(setup_db, admin_user: User):
    async with _build_client(setup_db, admin_user) as ac:
        yield ac


@pytest_asyncio.fixture
async def viewer_client(setup_db, viewer_user: User):
    async with _build_client(setup_db, viewer_user) as ac:
        yield ac


async def _make_file(setup_db, source_name: str = "src") -> tuple[uuid.UUID, uuid.UUID]:
    async with setup_db() as session:
        src = Source(
            id=uuid.uuid4(), name=source_name, type="local",
            connection_config={"path": "/tmp"},
        )
        session.add(src)
        await session.flush()
        entry = Entry(
            id=uuid.uuid4(),
            source_id=src.id,
            kind="file",
            path="/tmp/x.txt",
            parent_path="/tmp",
            name="x.txt",
            mode=33188, uid=0, gid=0,
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        session.add(entry)
        await session.commit()
        return src.id, entry.id


# ── Catalogue ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_list_tag(client: AsyncClient):
    r = await client.post("/api/tags", json={"name": "urgent", "color": "#ef4444"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "urgent"
    assert body["color"] == "#ef4444"

    r = await client.get("/api/tags")
    assert r.status_code == 200
    names = [t["name"] for t in r.json()]
    assert "urgent" in names


@pytest.mark.asyncio
async def test_create_tag_duplicate_returns_409(client: AsyncClient):
    r = await client.post("/api/tags", json={"name": "dup"})
    assert r.status_code == 201
    r = await client.post("/api/tags", json={"name": "dup"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_create_tag_forbidden_for_viewer(viewer_client: AsyncClient):
    r = await viewer_client.post("/api/tags", json={"name": "no"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_tag_removes_links_keeps_entry(
    client: AsyncClient, setup_db, admin_user: User,
):
    src_id, entry_id = await _make_file(setup_db)
    async with setup_db() as session:
        tag = Tag(id=uuid.uuid4(), name="t1", created_by=admin_user.id)
        session.add(tag)
        await session.flush()
        session.add(EntryTag(
            entry_id=entry_id, tag="t1",
            inherited_from_entry_id=None,
            created_by_user_id=admin_user.id,
        ))
        await session.commit()
        tag_id = tag.id

    r = await client.delete(f"/api/tags/{tag_id}")
    assert r.status_code == 204

    async with setup_db() as session:
        assert (await session.execute(
            select(Tag).where(Tag.id == tag_id)
        )).scalar_one_or_none() is None
        assert (await session.execute(
            select(EntryTag).where(EntryTag.tag == "t1")
        )).scalars().all() == []
        assert (await session.execute(
            select(Entry).where(Entry.id == entry_id)
        )).scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_delete_tag_404_when_missing(client: AsyncClient):
    r = await client.delete(f"/api/tags/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_tag_forbidden_for_viewer(
    viewer_client: AsyncClient, setup_db,
):
    async with setup_db() as session:
        tag = Tag(id=uuid.uuid4(), name="t1", created_by=None)
        session.add(tag)
        await session.commit()
        tag_id = tag.id
    r = await viewer_client.delete(f"/api/tags/{tag_id}")
    assert r.status_code == 403


# ── Apply / remove (admin-only) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_tags_round_trip(
    client: AsyncClient, setup_db, admin_user: User,
):
    """Apply two tags via the body-list endpoint, confirm via GET."""
    _, entry_id = await _make_file(setup_db)

    r = await client.post(
        f"/api/entries/{entry_id}/tags",
        json={"tags": ["pii", "archive"]},
    )
    assert r.status_code == 204

    r = await client.get(f"/api/entries/{entry_id}/tags")
    assert r.status_code == 200
    tags = sorted(t["tag"] for t in r.json())
    assert tags == ["archive", "pii"]
    # Direct rows have inherited=False.
    assert all(t["inherited"] is False for t in r.json())


@pytest.mark.asyncio
async def test_apply_auto_creates_catalogue_entries(
    client: AsyncClient, setup_db,
):
    """Apply with a never-seen tag name auto-creates the catalogue row."""
    _, entry_id = await _make_file(setup_db)
    r = await client.post(
        f"/api/entries/{entry_id}/tags", json={"tags": ["fresh-label"]},
    )
    assert r.status_code == 204

    r = await client.get("/api/tags")
    assert "fresh-label" in [t["name"] for t in r.json()]


@pytest.mark.asyncio
async def test_apply_tags_forbidden_for_viewer(
    viewer_client: AsyncClient, setup_db,
):
    _, entry_id = await _make_file(setup_db)
    r = await viewer_client.post(
        f"/api/entries/{entry_id}/tags", json={"tags": ["nope"]},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_remove_tag_round_trip(
    client: AsyncClient, setup_db, admin_user: User,
):
    _, entry_id = await _make_file(setup_db)
    await client.post(
        f"/api/entries/{entry_id}/tags", json={"tags": ["t1"]},
    )
    r = await client.delete(f"/api/entries/{entry_id}/tags/t1")
    assert r.status_code == 204

    r = await client.get(f"/api/entries/{entry_id}/tags")
    assert r.json() == []


@pytest.mark.asyncio
async def test_remove_tag_forbidden_for_viewer(
    client: AsyncClient, viewer_client: AsyncClient, setup_db,
):
    _, entry_id = await _make_file(setup_db)
    await client.post(f"/api/entries/{entry_id}/tags", json={"tags": ["t1"]})
    r = await viewer_client.delete(f"/api/entries/{entry_id}/tags/t1")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_apply_404_when_entry_missing(client: AsyncClient):
    r = await client.post(
        f"/api/entries/{uuid.uuid4()}/tags", json={"tags": ["x"]},
    )
    assert r.status_code == 404


# ── Bulk apply ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_apply_tags_to_many(
    client: AsyncClient, setup_db, admin_user: User,
):
    """A search-result-set bulk-tag flow: one POST stamps each tag onto
    each entry."""
    src_id, entry_a = await _make_file(setup_db, source_name="A")
    async with setup_db() as session:
        e_b = Entry(
            id=uuid.uuid4(),
            source_id=src_id,
            kind="file",
            path="/tmp/y.txt",
            parent_path="/tmp",
            name="y.txt",
            mode=33188, uid=0, gid=0,
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        session.add(e_b)
        await session.commit()
        entry_b = e_b.id

    r = await client.post(
        "/api/tags/bulk-apply",
        json={
            "entry_ids": [str(entry_a), str(entry_b)],
            "tags": ["report", "fy26"],
        },
    )
    assert r.status_code == 204

    for eid in (entry_a, entry_b):
        r = await client.get(f"/api/entries/{eid}/tags")
        assert sorted(t["tag"] for t in r.json()) == ["fy26", "report"]


@pytest.mark.asyncio
async def test_bulk_apply_404_when_entry_missing(
    client: AsyncClient, setup_db,
):
    _, entry_id = await _make_file(setup_db)
    r = await client.post(
        "/api/tags/bulk-apply",
        json={
            "entry_ids": [str(entry_id), str(uuid.uuid4())],
            "tags": ["x"],
        },
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_bulk_apply_forbidden_for_viewer(
    viewer_client: AsyncClient, setup_db,
):
    _, entry_id = await _make_file(setup_db)
    r = await viewer_client.post(
        "/api/tags/bulk-apply",
        json={"entry_ids": [str(entry_id)], "tags": ["x"]},
    )
    assert r.status_code == 403


# ── Catalogue usage stats ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tag_usage_splits_direct_vs_inherited(
    client: AsyncClient, setup_db, admin_user: User,
):
    """The Settings → Tags page wants to show how many direct vs
    inherited applications a tag has."""
    src_id, entry_id = await _make_file(setup_db)
    async with setup_db() as session:
        # Add a directory + child file under it.
        d = Entry(
            id=uuid.uuid4(),
            source_id=src_id,
            kind="directory",
            path="/tmp/d",
            parent_path="/tmp",
            name="d",
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        f = Entry(
            id=uuid.uuid4(),
            source_id=src_id,
            kind="file",
            path="/tmp/d/inner.txt",
            parent_path="/tmp/d",
            name="inner.txt",
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        session.add_all([d, f])
        await session.commit()
        dir_id = d.id

    # Apply T directly to the file (1 direct).
    await client.post(
        f"/api/entries/{entry_id}/tags", json={"tags": ["T"]},
    )
    # Apply T to the directory (1 direct + 1 inherited on the child).
    await client.post(
        f"/api/entries/{dir_id}/tags", json={"tags": ["T"]},
    )

    r = await client.get("/api/tags/usage")
    assert r.status_code == 200
    rows = {row["name"]: row for row in r.json()}
    assert rows["T"]["direct_count"] == 2
    assert rows["T"]["inherited_count"] == 1
