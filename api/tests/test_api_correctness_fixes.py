"""Regression tests for the ingest/search/browse/purge correctness sweep."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import create_access_token, create_ingest_token
from akashic.models.entry import Entry
from akashic.models.maintenance_job import MaintenanceJob
from akashic.models.scan import Scan
from akashic.models.source import Source
from akashic.models.user import User
from tests.conftest import seed_scan


async def _admin_and_source(db: AsyncSession):
    user = User(
        id=uuid.uuid4(), username=f"a{uuid.uuid4().hex[:6]}", email="a@e",
        password_hash="x", role="admin",
    )
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db.add_all([user, source])
    await db.commit()
    return user, source


# 1 — sweep returns ids and they are removed from the index
@pytest.mark.asyncio
async def test_sweep_returns_deleted_file_ids_and_index_delete(
    db_session: AsyncSession, monkeypatch,
):
    from akashic.routers.ingest import _delete_swept_from_index, _sweep_stale_entries

    _, source = await _admin_and_source(db_session)
    now = datetime.now(timezone.utc)
    old = Entry(
        id=uuid.uuid4(), source_id=source.id, kind="file", path="/old",
        name="old", parent_path="/", last_seen_at=now - timedelta(days=1),
    )
    keep = Entry(
        id=uuid.uuid4(), source_id=source.id, kind="file", path="/keep",
        name="keep", parent_path="/", last_seen_at=now,
    )
    db_session.add_all([old, keep])
    scan = Scan(
        id=uuid.uuid4(), source_id=source.id, scan_type="incremental",
        status="running", started_at=now - timedelta(hours=1),
    )
    db_session.add(scan)
    await db_session.commit()

    ids = await _sweep_stale_entries(db_session, scan, now)
    await db_session.commit()
    assert ids == [str(old.id)]

    called: list = []

    async def _fake(file_ids):
        called.append(file_ids)

    monkeypatch.setattr("akashic.services.search.delete_files_batch", _fake)
    await _delete_swept_from_index(ids)
    assert called == [[str(old.id)]]


# 2 — unit finalize guards
@pytest.mark.asyncio
async def test_complete_unit_on_cancelled_scan_is_409(setup_db):
    from tests.test_scan_work_units import (
        _auth, _bearer_client, _mint_scanner, _seed_scan, admin_user,  # noqa: F401
    )
    from akashic.models.scan_work_unit import ScanWorkUnit

    async with setup_db() as s:
        user = User(
            id=uuid.uuid4(), username="adm", email="a@b.c",
            password_hash="x", role="admin",
        )
        s.add(user)
        await s.commit()
    scan_id, _ = await _seed_scan(setup_db)
    scn = await _mint_scanner(setup_db, user)
    async with _bearer_client(setup_db) as ac:
        await ac.post(
            f"/api/scans/{scan_id}/work/split",
            json={"child_paths": ["only"]}, headers=_auth(scn),
        )
        leased = (await ac.post(
            f"/api/scans/{scan_id}/work/lease", headers=_auth(scn)
        )).json()
        async with setup_db() as s:
            scan = (await s.execute(select(Scan).where(Scan.id == scan_id))).scalar_one()
            scan.status = "cancelled"
            await s.commit()
        r = await ac.post(
            f"/api/scans/{scan_id}/work/{leased['id']}/complete", headers=_auth(scn),
        )
        assert r.status_code == 409
        r = await ac.post(
            f"/api/scans/{scan_id}/work/{leased['id']}/fail",
            json={}, headers=_auth(scn),
        )
        assert r.status_code == 409
    async with setup_db() as s:
        scan = (await s.execute(select(Scan).where(Scan.id == scan_id))).scalar_one()
        unit = (await s.execute(select(ScanWorkUnit))).scalars().first()
    assert scan.status == "cancelled"
    assert unit.status == "running"


# 4 — duplicate paths in one batch
@pytest.mark.asyncio
async def test_ingest_batch_dedupes_paths_last_wins(
    client: AsyncClient, db_session: AsyncSession,
):
    user, source = await _admin_and_source(db_session)
    scan_id = await seed_scan(db_session, source.id)
    token = create_ingest_token(str(user.id))
    base = {"kind": "file", "name": "x", "path": "/x"}
    r = await client.post(
        "/api/ingest/batch",
        json={
            "source_id": str(source.id), "scan_id": str(scan_id),
            "entries": [{**base, "size_bytes": 1}, {**base, "size_bytes": 2}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    rows = (await db_session.execute(
        select(Entry).where(Entry.source_id == source.id)
    )).scalars().all()
    assert len(rows) == 1 and rows[0].size_bytes == 2
    assert r.json()["files_processed"] == 1


# 5 — meili flush keeps the timestamp when a remainder is left
@pytest.mark.asyncio
async def test_meili_flush_rearms_timestamp_when_remainder(setup_db, monkeypatch):
    from akashic.services import meili_indexer
    from akashic.services.scan_pubsub import _client as redis_client

    sid = uuid.uuid4()
    monkeypatch.setattr(meili_indexer, "_bg_session", lambda: setup_db)
    monkeypatch.setattr(meili_indexer, "_FLUSH_SIZE_THRESHOLD", 2)

    async def _noop(_):
        return None

    monkeypatch.setattr("akashic.services.search.update_files_partial", _noop)
    redis = redis_client()
    try:
        await meili_indexer.mark_dirty(sid, [uuid.uuid4() for _ in range(5)])
        await meili_indexer.flush(sid)
        assert await redis.scard(meili_indexer._set_key(sid)) == 3
        assert await redis.exists(meili_indexer._ts_key(sid)) == 1
    finally:
        await redis.delete(meili_indexer._set_key(sid), meili_indexer._ts_key(sid))


# 6 — top_children drain: SPOP, rollback on failure, paths restored
@pytest.mark.asyncio
async def test_top_children_drain_restores_paths_on_failure(setup_db, monkeypatch):
    from akashic.services import top_children_worker as w

    sid = uuid.uuid4()
    monkeypatch.setattr(w, "_redis", None)
    r = w._client()
    key = w._key(str(sid))
    await r.delete(key)
    await r.sadd(key, "/a", "/b")

    async def _boom(db, source_id, paths):
        raise RuntimeError("x")

    monkeypatch.setattr(w, "rollup_top_children_for_paths", _boom)
    async with setup_db() as db:
        with pytest.raises(RuntimeError):
            await w._drain_one_source(str(sid), db)
    assert await r.smembers(key) == {"/a", "/b"}

    seen: list = []

    async def _ok(db, source_id, paths):
        seen.extend(paths)
        return len(paths)

    monkeypatch.setattr(w, "rollup_top_children_for_paths", _ok)
    async with setup_db() as db:
        assert await w._drain_one_source(str(sid), db) == 2
    assert sorted(seen) == ["/a", "/b"]
    assert await r.exists(key) == 0
    await r.aclose()
    monkeypatch.setattr(w, "_redis", None)


# 7 — browse sort=modified page 2 + accurate hidden count
@pytest.mark.asyncio
async def test_browse_modified_cursor_second_page(
    client: AsyncClient, db_session: AsyncSession,
):
    user, source = await _admin_and_source(db_session)
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    db_session.add_all([
        Entry(
            id=uuid.uuid4(), source_id=source.id, kind="file", path=f"/f{i}",
            name=f"f{i}", parent_path="/", fs_modified_at=base + timedelta(days=i),
        )
        for i in range(4)
    ])
    await db_session.commit()
    h = {"Authorization": f"Bearer {create_access_token({'sub': str(user.id)})}"}
    url = f"/api/browse?source_id={source.id}&sort=modified&order=desc&limit=2"
    p1 = (await client.get(url, headers=h)).json()
    assert p1["next_cursor"]
    r2 = await client.get(url + f"&cursor={p1['next_cursor']}", headers=h)
    assert r2.status_code == 200, r2.text
    got = [e["path"] for e in p1["entries"]] + [e["path"] for e in r2.json()["entries"]]
    assert got == ["/f3", "/f2", "/f1", "/f0"]


@pytest.mark.asyncio
async def test_audit_hidden_uses_filtered_total(db_session: AsyncSession):
    from sqlalchemy import and_
    from akashic.models.audit_event import AuditEvent  # noqa: F401
    from akashic.routers import browse

    user, source = await _admin_and_source(db_session)
    db_session.add_all([
        Entry(
            id=uuid.uuid4(), source_id=source.id, kind="file", path=f"/f{i}",
            name=f"f{i}", parent_path="/",
        )
        for i in range(5)
    ])
    await db_session.commit()
    events: list = []

    async def _rec(**kw):
        events.append(kw["payload"])

    orig = browse.record_event
    browse.record_event = _rec
    try:
        nopermfilter = [Entry.source_id == source.id]
        withperm = nopermfilter + [Entry.path.in_(["/f0", "/f1", "/f2", "/f3"])]
        # page shows 2 rows, but 4 of 5 are visible in total => hidden == 1
        await browse._maybe_audit_filter(
            db=db_session, user=user, request=None, source_id=source.id,
            path="/", visible_count=2, base_filter=withperm,
            base_filter_no_perm=nopermfilter,
        )
    finally:
        browse.record_event = orig
    assert events and events[0]["hidden"] == 1


# 8 — only one running job per kind (DB-enforced)
@pytest.mark.asyncio
async def test_maintenance_running_unique_per_kind(db_session: AsyncSession):
    db_session.add(MaintenanceJob(kind="reindex_search", status="running", params={}))
    await db_session.commit()
    db_session.add(MaintenanceJob(kind="reindex_search", status="running", params={}))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
    db_session.add(MaintenanceJob(kind="warm_groups", status="running", params={}))
    await db_session.commit()


@pytest.mark.asyncio
async def test_reindex_uses_partial_update_with_keyset(setup_db, monkeypatch):
    from akashic.tools import reindex_search

    async with setup_db() as db:
        _, source = await _admin_and_source(db)
        db.add_all([
            Entry(
                id=uuid.uuid4(), source_id=source.id, kind="file",
                path=f"/f{i}", name=f"f{i}", parent_path="/",
            )
            for i in range(5)
        ])
        await db.commit()

    pushed: list[list[dict]] = []

    async def _partial(docs):
        pushed.append(docs)

    async def _noop():
        return None

    monkeypatch.setattr(reindex_search, "update_files_partial", _partial)
    monkeypatch.setattr(reindex_search, "ensure_index", _noop)
    from akashic.config import settings
    from tests.conftest import TEST_DB_URL
    monkeypatch.setattr(settings, "database_url", TEST_DB_URL)
    assert await reindex_search._reindex(2) == 5
    assert [len(p) for p in pushed] == [2, 2, 1]
    ids = [d["id"] for p in pushed for d in p]
    assert len(set(ids)) == 5


# 9 — duplicate files pagination
@pytest.mark.asyncio
async def test_duplicate_files_limit_offset(
    client: AsyncClient, db_session: AsyncSession,
):
    user, source = await _admin_and_source(db_session)
    db_session.add_all([
        Entry(
            id=uuid.uuid4(), source_id=source.id, kind="file", path=f"/d{i}",
            name=f"d{i}", parent_path="/", content_hash="h1", size_bytes=1,
        )
        for i in range(5)
    ])
    await db_session.commit()
    h = {"Authorization": f"Bearer {create_access_token({'sub': str(user.id)})}"}
    r = await client.get("/api/duplicates/h1/files?limit=2&offset=4", headers=h)
    assert r.status_code == 200 and len(r.json()) == 1
    r = await client.get("/api/duplicates/h1/files?limit=999999", headers=h)
    assert r.status_code == 422


# 3 — conflicting /complete still releases the lease, keeps terminal status
@pytest.mark.asyncio
async def test_complete_scan_conflict_releases_lease(setup_db):
    from tests.test_scan_work_units import (
        _auth, _bearer_client, _mint_scanner, _seed_scan,
    )

    async with setup_db() as s:
        user = User(
            id=uuid.uuid4(), username="adm2", email="a@b.c",
            password_hash="x", role="admin",
        )
        s.add(user)
        await s.commit()
    scan_id, _ = await _seed_scan(setup_db)
    scn = await _mint_scanner(setup_db, user)
    async with setup_db() as s:
        scan = (await s.execute(select(Scan).where(Scan.id == scan_id))).scalar_one()
        scan.status = "cancelled"
        scan.assigned_scanner_id = uuid.UUID(scn["id"])
        scan.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        await s.commit()
    async with _bearer_client(setup_db) as ac:
        r = await ac.post(
            f"/api/scans/{scan_id}/complete",
            json={"status": "completed"}, headers=_auth(scn),
        )
    assert r.status_code == 409
    async with setup_db() as s:
        scan = (await s.execute(select(Scan).where(Scan.id == scan_id))).scalar_one()
    assert scan.status == "cancelled"
    assert scan.lease_expires_at is None


# 9 — purge deleted counts all kinds, chunked
@pytest.mark.asyncio
async def test_purge_deleted_counts_all_kinds(
    client: AsyncClient, db_session: AsyncSession, monkeypatch,
):
    from akashic.routers import purge

    monkeypatch.setattr(purge, "_PURGE_CHUNK", 2)
    removed: list[str] = []

    async def _fake(ids):
        removed.extend(ids)

    monkeypatch.setattr("akashic.services.search.delete_files_batch", _fake)
    user, source = await _admin_and_source(db_session)
    old = datetime.now(timezone.utc) - timedelta(days=60)
    db_session.add_all([
        Entry(
            id=uuid.uuid4(), source_id=source.id,
            kind="file" if i < 3 else "directory", path=f"/p{i}", name=f"p{i}",
            parent_path="/", is_deleted=True, deleted_at=old,
        )
        for i in range(5)
    ])
    await db_session.commit()
    h = {"Authorization": f"Bearer {create_access_token({'sub': str(user.id)})}"}
    r = await client.post("/api/purge/deleted?older_than_days=30", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["files_removed"] == 3 and r.json()["total_removed"] == 5
    assert len(removed) == 3
    left = (await db_session.execute(select(Entry))).scalars().all()
    assert left == []
