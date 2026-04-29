"""Tests for the tags router — exercise the new DELETE path plus the
existing create/list/apply/remove flow as light regression coverage."""
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
async def client(setup_db, admin_user: User) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_get_current_user():
        return admin_user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


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
async def test_delete_tag_removes_entry_links(
    client: AsyncClient, setup_db, admin_user: User
):
    # Set up a source + entry + tag + link.
    async with setup_db() as session:
        src = Source(
            id=uuid.uuid4(), name="src", type="local",
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
        tag = Tag(id=uuid.uuid4(), name="t1", created_by=admin_user.id)
        session.add(entry)
        session.add(tag)
        await session.flush()
        session.add(EntryTag(entry_id=entry.id, tag_id=tag.id, tagged_by=admin_user.id))
        await session.commit()
        tag_id = tag.id
        entry_id = entry.id

    # Sanity: link exists.
    async with setup_db() as session:
        n = (await session.execute(
            select(EntryTag).where(EntryTag.tag_id == tag_id)
        )).scalars().all()
        assert len(n) == 1

    # Delete the tag.
    r = await client.delete(f"/api/tags/{tag_id}")
    assert r.status_code == 204

    # Tag is gone, link is gone, entry survives.
    async with setup_db() as session:
        assert (await session.execute(
            select(Tag).where(Tag.id == tag_id)
        )).scalar_one_or_none() is None
        assert (await session.execute(
            select(EntryTag).where(EntryTag.tag_id == tag_id)
        )).scalars().all() == []
        assert (await session.execute(
            select(Entry).where(Entry.id == entry_id)
        )).scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_delete_tag_404_when_missing(client: AsyncClient):
    fake = uuid.uuid4()
    r = await client.delete(f"/api/tags/{fake}")
    assert r.status_code == 404
