"""v0.30.0 — POST /api/ingest/content.

The scanner extracts file text and ships it back on this channel,
keyed by (source_id, path). The API resolves each path to its entry
and partial-updates the Meilisearch doc's content_text.

Covers: path→entry resolution, unknown-path skip, directory skip,
empty batch, and that the Meili write is a partial update of just
{id, content_text}.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import get_current_user, get_ingest_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services import search


@pytest_asyncio.fixture
async def admin_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(), username="admin", email="admin@example.com",
            password_hash="x", role="admin",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def fixture_source(setup_db, admin_user: User) -> Source:
    async with setup_db() as session:
        src = Source(
            id=uuid.uuid4(), name="src1", type="local",
            connection_config={"path": "/tmp"}, status="online",
        )
        session.add(src)
        await session.commit()
        await session.refresh(src)
        return src


@pytest_asyncio.fixture
async def client(setup_db, admin_user: User, monkeypatch, request) -> AsyncClient:
    # Capture the Meili partial-update docs instead of hitting a real
    # Meilisearch — the endpoint imports update_files_partial lazily,
    # so patching the module attribute is enough.
    captured: list[dict] = []

    async def _capture(docs):
        captured.extend(docs)

    monkeypatch.setattr(search, "update_files_partial", _capture)
    request.node._captured_docs = captured

    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_user():
        return admin_user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_ingest_user] = _override_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _make_entry(setup_db, source_id, path, *, kind="file") -> uuid.UUID:
    async with setup_db() as session:
        entry = Entry(
            id=uuid.uuid4(), source_id=source_id, kind=kind,
            parent_path=os.path.dirname(path) or "/",
            path=path, name=path.rsplit("/", 1)[-1],
        )
        session.add(entry)
        await session.commit()
        return entry.id


@pytest.mark.asyncio
async def test_content_resolves_and_partial_indexes(
    client: AsyncClient, fixture_source: Source, setup_db, request,
):
    id_a = await _make_entry(setup_db, fixture_source.id, "/tmp/a.pdf")
    id_b = await _make_entry(setup_db, fixture_source.id, "/tmp/b.txt")

    r = await client.post("/api/ingest/content", json={
        "source_id": str(fixture_source.id),
        "scan_id": str(uuid.uuid4()),
        "items": [
            {"path": "/tmp/a.pdf", "content_text": "alpha document"},
            {"path": "/tmp/b.txt", "content_text": "bravo text"},
        ],
    })
    assert r.status_code == 200, r.text
    assert r.json()["items_indexed"] == 2

    docs = {d["id"]: d for d in request.node._captured_docs}
    assert docs[str(id_a)]["content_text"] == "alpha document"
    assert docs[str(id_b)]["content_text"] == "bravo text"
    # Partial update — only id + content_text, no metadata fields that
    # would otherwise clobber the batch-ingest flush.
    assert set(docs[str(id_a)].keys()) == {"id", "content_text"}


@pytest.mark.asyncio
async def test_content_skips_unknown_path(
    client: AsyncClient, fixture_source: Source, setup_db, request,
):
    await _make_entry(setup_db, fixture_source.id, "/tmp/known.pdf")

    r = await client.post("/api/ingest/content", json={
        "source_id": str(fixture_source.id),
        "scan_id": str(uuid.uuid4()),
        "items": [
            {"path": "/tmp/known.pdf", "content_text": "x"},
            {"path": "/tmp/never-ingested.pdf", "content_text": "y"},
        ],
    })
    assert r.status_code == 200, r.text
    # Only the resolvable path is counted/indexed.
    assert r.json()["items_indexed"] == 1
    assert len(request.node._captured_docs) == 1


@pytest.mark.asyncio
async def test_content_skips_directory_entry(
    client: AsyncClient, fixture_source: Source, setup_db, request,
):
    """A path that resolves to a directory is not a content target —
    the endpoint filters kind == 'file'."""
    await _make_entry(setup_db, fixture_source.id, "/tmp/adir", kind="directory")

    r = await client.post("/api/ingest/content", json={
        "source_id": str(fixture_source.id),
        "scan_id": str(uuid.uuid4()),
        "items": [{"path": "/tmp/adir", "content_text": "x"}],
    })
    assert r.status_code == 200, r.text
    assert r.json()["items_indexed"] == 0
    assert request.node._captured_docs == []


@pytest.mark.asyncio
async def test_content_empty_batch(
    client: AsyncClient, fixture_source: Source, request,
):
    r = await client.post("/api/ingest/content", json={
        "source_id": str(fixture_source.id),
        "scan_id": str(uuid.uuid4()),
        "items": [],
    })
    assert r.status_code == 200, r.text
    assert r.json()["items_indexed"] == 0
    assert request.node._captured_docs == []
