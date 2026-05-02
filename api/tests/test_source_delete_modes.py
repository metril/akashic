"""DELETE /api/sources/{id} two-flavour behaviour (v0.4.0+).

Default `purge_entries=false`:
  - source row gone
  - entries survive with source_id=NULL
  - GET /entries/{id}/content returns 404
  - GET /entries/{id} returns the entry with source: null

With `purge_entries=true`:
  - source row gone
  - entries also gone
  - search index updated accordingly

Plus the new GET /sources/{id}/entry-count endpoint that powers
the delete modal's blast-radius display.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.entry import Entry
from akashic.models.scan import Scan
from akashic.models.source import Source
from akashic.models.user import User


@pytest_asyncio.fixture
async def admin_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(), username="admin", email="a@b.c",
            password_hash="x", role="admin",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def _admin_client(setup_db, user: User) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_get_current_user():
        return user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_source_with_entries(setup_db, n_entries: int = 3) -> uuid.UUID:
    """Create a source with N file entries pointing at it. Returns
    the source id."""
    async with setup_db() as db:
        src = Source(
            id=uuid.uuid4(), name=f"src-{uuid.uuid4().hex[:6]}",
            type="local", connection_config={"path": "/tmp"},
        )
        db.add(src)
        await db.flush()
        for i in range(n_entries):
            db.add(Entry(
                id=uuid.uuid4(),
                source_id=src.id,
                kind="file",
                parent_path="/tmp",
                path=f"/tmp/file-{i}.txt",
                name=f"file-{i}.txt",
                size_bytes=100,
            ))
        # And one scan history row to verify it survives too.
        db.add(Scan(
            id=uuid.uuid4(), source_id=src.id,
            scan_type="incremental", status="completed",
        ))
        await db.commit()
        return src.id


@pytest.fixture(autouse=True)
def _stub_meili(monkeypatch):
    """The delete-source path calls into the Meilisearch helpers.
    Tests don't run Meili — stub the helpers to no-ops."""
    from akashic.services import search

    async def _noop_delete_batch(_ids):
        pass

    async def _noop_update(_docs):
        pass

    monkeypatch.setattr(search, "delete_files_batch", _noop_delete_batch)
    monkeypatch.setattr(search, "update_files_partial", _noop_update)


@pytest.mark.asyncio
async def test_delete_default_preserves_entries(setup_db, admin_user):
    src_id = await _seed_source_with_entries(setup_db, n_entries=4)
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.delete(f"/api/sources/{src_id}")
        assert r.status_code == 204, r.text

    async with setup_db() as db:
        # Source row gone.
        src = (await db.execute(
            select(Source).where(Source.id == src_id)
        )).scalar_one_or_none()
        assert src is None

        # Entries survive with source_id=NULL.
        rows = (await db.execute(select(Entry))).scalars().all()
        assert len(rows) == 4
        assert all(e.source_id is None for e in rows)

        # Scan row survives with source_id=NULL too.
        scans = (await db.execute(select(Scan))).scalars().all()
        assert len(scans) == 1
        assert scans[0].source_id is None


@pytest.mark.asyncio
async def test_delete_with_purge_removes_entries(setup_db, admin_user):
    src_id = await _seed_source_with_entries(setup_db, n_entries=4)
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.delete(f"/api/sources/{src_id}?purge_entries=true")
        assert r.status_code == 204, r.text

    async with setup_db() as db:
        rows = (await db.execute(select(Entry))).scalars().all()
        assert rows == []
        # Scans also lose their source_id (FK SET NULL) — not deleted,
        # since the operator only purged ENTRIES, not historical scans.
        scans = (await db.execute(select(Scan))).scalars().all()
        assert len(scans) == 1
        assert scans[0].source_id is None


@pytest.mark.asyncio
async def test_get_entry_on_orphaned_returns_with_null_source(setup_db, admin_user):
    src_id = await _seed_source_with_entries(setup_db, n_entries=1)
    async with _admin_client(setup_db, admin_user) as ac:
        await ac.delete(f"/api/sources/{src_id}")
        # Now find the orphaned entry id and fetch it.
        async with setup_db() as db:
            entry_id = (await db.execute(select(Entry.id))).scalar_one()
        r = await ac.get(f"/api/entries/{entry_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_id"] is None
    assert body["source"] is None


@pytest.mark.asyncio
async def test_content_fetch_on_orphaned_returns_404(setup_db, admin_user):
    src_id = await _seed_source_with_entries(setup_db, n_entries=1)
    async with _admin_client(setup_db, admin_user) as ac:
        await ac.delete(f"/api/sources/{src_id}")
        async with setup_db() as db:
            entry_id = (await db.execute(select(Entry.id))).scalar_one()
        r = await ac.get(f"/api/entries/{entry_id}/content")
    assert r.status_code == 404
    assert "source has been deleted" in r.text


@pytest.mark.asyncio
async def test_entry_count_endpoint(setup_db, admin_user):
    src_id = await _seed_source_with_entries(setup_db, n_entries=7)
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.get(f"/api/sources/{src_id}/entry-count")
    assert r.status_code == 200
    assert r.json() == {"count": 7}


@pytest.mark.asyncio
async def test_audit_event_records_flavour(setup_db, admin_user):
    """Audit row should distinguish the two flavours so the timeline
    explains what actually happened."""
    src_id = await _seed_source_with_entries(setup_db, n_entries=3)
    async with _admin_client(setup_db, admin_user) as ac:
        await ac.delete(f"/api/sources/{src_id}?purge_entries=true")

    from akashic.models.audit_event import AuditEvent
    async with setup_db() as db:
        evt = (await db.execute(
            select(AuditEvent).where(AuditEvent.event_type == "source_deleted")
        )).scalar_one()
    assert evt.payload["purge_entries"] is True
    assert evt.payload["affected_entry_count"] == 3
    assert evt.payload["deleted_source_id"] == str(src_id)


@pytest.mark.asyncio
async def test_meili_sync_invoked_correctly(setup_db, admin_user, monkeypatch):
    """Preserve flavour calls update_files_partial with source_id=None;
    purge flavour calls delete_files_batch."""
    update_calls: list = []
    delete_calls: list = []

    async def _capture_update(docs):
        update_calls.append(list(docs))

    async def _capture_delete(ids):
        delete_calls.append(list(ids))

    from akashic.services import search
    monkeypatch.setattr(search, "update_files_partial", _capture_update)
    monkeypatch.setattr(search, "delete_files_batch", _capture_delete)

    # Preserve flavour
    src_a = await _seed_source_with_entries(setup_db, n_entries=2)
    async with _admin_client(setup_db, admin_user) as ac:
        await ac.delete(f"/api/sources/{src_a}")
    assert len(update_calls) == 1
    docs = update_calls[0]
    assert len(docs) == 2
    assert all(d["source_id"] is None for d in docs)
    assert all("id" in d for d in docs)
    assert delete_calls == []

    # Purge flavour
    update_calls.clear()
    src_b = await _seed_source_with_entries(setup_db, n_entries=2)
    async with _admin_client(setup_db, admin_user) as ac:
        await ac.delete(f"/api/sources/{src_b}?purge_entries=true")
    assert update_calls == []
    assert len(delete_calls) == 1
    assert len(delete_calls[0]) == 2
