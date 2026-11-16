"""Directory sync service: upsert/delete, lock, reconcile, due check, refresh."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from akashic.models.directory import DirectoryEntry, DirectorySyncRun
from akashic.services.directory import sync
from akashic.services.directory.records import DirectoryError, DirRecord

AD_CFG = {"ldap_server": "ldap://x", "ldap_user_base": "dc=x"}
AK_CFG = {"authentik_url": "https://a", "authentik_api_token": "t"}


def rec(ref, backend="ad", kind="person", name=None, groups=(), status="none", **kw):
    return DirRecord(
        backend=backend, ref=ref, kind=kind, display_name=name or ref,
        username=ref, email=None, sid=kw.get("sid"), dn=kw.get("dn"),
        group_sids=tuple(groups), groups_status=status,
    )


def entry(ref, backend="ad", **kw):
    return DirectoryEntry(
        backend=backend, ref=ref, kind=kw.pop("kind", "person"), display_name=kw.pop("display_name", ref),
        groups_status=kw.pop("groups_status", "none"),
        synced_at=kw.pop("synced_at", datetime(2020, 1, 1, tzinfo=timezone.utc)), **kw,
    )


@pytest.fixture
def svc(setup_db, monkeypatch):
    monkeypatch.setattr(sync, "async_session", setup_db)
    return setup_db


async def _run(setup_db, backend="ad", status="running", **kw):
    async with setup_db() as db:
        r = DirectorySyncRun(backend=backend, status=status, trigger="manual", **kw)
        db.add(r)
        await db.commit()
        await db.refresh(r)
        return r


async def _get(setup_db, model, **where):
    async with setup_db() as db:
        q = select(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return (await db.execute(q)).scalars().all()


@pytest.mark.asyncio
async def test_sync_upserts_and_removes_missing(svc, monkeypatch):
    async with svc() as db:
        db.add_all([entry("A", display_name="old"), entry("B")])
        await db.commit()
    monkeypatch.setattr(sync, "fetch_all_ad", lambda timeout: [
        rec("A", name="new"), rec("C"), rec("G", kind="group", groups=["S-1"], status="ok"),
    ])
    run = await _run(svc)
    await sync.run_backend(run.id, "ad")

    rows = {e.ref: e for e in await _get(svc, DirectoryEntry)}
    assert set(rows) == {"A", "C", "G"}
    assert rows["A"].display_name == "new"
    assert rows["G"].group_sids == ["S-1"]
    r = (await _get(svc, DirectorySyncRun, id=run.id))[0]
    assert r.status == "succeeded" and r.finished_at is not None and r.error is None
    assert r.counts == {"users": 2, "groups": 1, "removed": 1, "groups_failed": 0}


@pytest.mark.asyncio
async def test_failed_sync_deletes_nothing(svc, monkeypatch):
    async with svc() as db:
        db.add_all([entry("A"), entry("B")])
        await db.commit()

    def boom(timeout):
        raise DirectoryError("AD: SERVER_DOWN")
    monkeypatch.setattr(sync, "fetch_all_ad", boom)
    run = await _run(svc)
    await sync.run_backend(run.id, "ad")

    assert {e.ref for e in await _get(svc, DirectoryEntry)} == {"A", "B"}
    r = (await _get(svc, DirectorySyncRun, id=run.id))[0]
    assert r.status == "failed" and r.error == "AD: SERVER_DOWN" and r.finished_at is not None


@pytest.mark.asyncio
async def test_unexpected_error_records_only_type_name(svc, monkeypatch):
    def boom(timeout):
        raise RuntimeError("password=hunter2")
    monkeypatch.setattr(sync, "fetch_all_ad", boom)
    run = await _run(svc)
    await sync.run_backend(run.id, "ad")
    r = (await _get(svc, DirectorySyncRun, id=run.id))[0]
    assert r.status == "failed" and r.error == "RuntimeError"


@pytest.mark.asyncio
async def test_sync_only_touches_own_backend(svc, monkeypatch):
    async with svc() as db:
        db.add_all([entry("A"), entry("K", backend="authentik")])
        await db.commit()

    async def ak(timeout):
        return [rec("K2", backend="authentik")]
    monkeypatch.setattr(sync, "fetch_all_authentik", ak)
    run = await _run(svc, backend="authentik")
    await sync.run_backend(run.id, "authentik")

    rows = {(e.backend, e.ref) for e in await _get(svc, DirectoryEntry)}
    assert rows == {("ad", "A"), ("authentik", "K2")}


@pytest.fixture
def noop_runner(monkeypatch):
    calls = []

    async def fake(run_id, backend):
        calls.append(backend)
    monkeypatch.setattr(sync, "run_backend", fake)
    return calls


@pytest.mark.asyncio
async def test_start_sync_conflict(svc, rt_set, noop_runner):
    rt_set(**AD_CFG)
    runs = await sync.start_sync(trigger="manual", triggered_by=None)
    await asyncio.gather(*sync._tasks)
    assert [r.backend for r in runs] == ["ad"] and noop_runner == ["ad"]
    with pytest.raises(sync.SyncAlreadyRunning):
        await sync.start_sync(trigger="scheduled", triggered_by=None)
    assert len(await _get(svc, DirectorySyncRun)) == 1


@pytest.mark.asyncio
async def test_start_sync_conflict_is_atomic_across_backends(svc, rt_set, noop_runner):
    rt_set(**AD_CFG, **AK_CFG)
    await _run(svc, backend="authentik")
    with pytest.raises(sync.SyncAlreadyRunning):
        await sync.start_sync(trigger="manual", triggered_by=None)
    assert [r.backend for r in await _get(svc, DirectorySyncRun)] == ["authentik"]
    assert noop_runner == []


@pytest.mark.asyncio
async def test_start_sync_no_backend(svc, rt_set):
    rt_set()
    with pytest.raises(sync.NoBackendConfigured):
        await sync.start_sync(trigger="manual", triggered_by=None)


@pytest.mark.asyncio
async def test_failed_group_read_keeps_cached_groups(svc, monkeypatch):
    t = datetime(2024, 5, 1, tzinfo=timezone.utc)
    async with svc() as db:
        db.add(entry("A", group_sids=["G1"], groups_status="ok", groups_synced_at=t))
        await db.commit()
    monkeypatch.setattr(sync, "fetch_all_ad", lambda timeout: [rec("A", status="failed")])
    run = await _run(svc)
    await sync.run_backend(run.id, "ad")
    e = (await _get(svc, DirectoryEntry, ref="A"))[0]
    assert e.group_sids == ["G1"] and e.groups_synced_at == t and e.groups_status == "failed"


@pytest.mark.asyncio
async def test_ok_group_read_sets_groups_synced_at(svc, monkeypatch):
    monkeypatch.setattr(sync, "fetch_all_ad", lambda timeout: [rec("A", groups=["G2"], status="ok")])
    run = await _run(svc)
    await sync.run_backend(run.id, "ad")
    e = (await _get(svc, DirectoryEntry, ref="A"))[0]
    started = (await _get(svc, DirectorySyncRun, id=run.id))[0].started_at
    assert e.group_sids == ["G2"] and e.groups_synced_at == started and e.synced_at == started


@pytest.mark.asyncio
async def test_reconcile_marks_running_failed(svc):
    run = await _run(svc)
    done = await _run(svc, status="succeeded")
    assert await sync.reconcile_directory_runs() == 1
    r = (await _get(svc, DirectorySyncRun, id=run.id))[0]
    assert r.status == "failed" and r.error == "interrupted by API restart"
    assert r.finished_at is not None
    assert (await _get(svc, DirectorySyncRun, id=done.id))[0].status == "succeeded"


@pytest.mark.asyncio
async def test_sync_due(svc, rt_set):
    now = datetime.now(timezone.utc)
    rt_set(directory_sync_interval_minutes=0)
    async with svc() as db:
        assert await sync.sync_due(db, now) is False
    rt_set(directory_sync_interval_minutes=60)
    async with svc() as db:
        assert await sync.sync_due(db, now) is True  # never synced
    await _run(svc, status="succeeded", finished_at=now - timedelta(minutes=30))
    async with svc() as db:
        assert await sync.sync_due(db, now) is False  # within interval
    async with svc() as db:
        assert await sync.sync_due(db, now + timedelta(minutes=31)) is True
    await _run(svc, backend="authentik")
    async with svc() as db:
        assert await sync.sync_due(db, now + timedelta(hours=5)) is False  # running


@pytest.mark.asyncio
async def test_refresh_records_updates_and_deletes(svc, rt_set, monkeypatch):
    rt_set(**AD_CFG)
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    async with svc() as db:
        db.add_all([
            entry("U1", sid="S-1-1", group_sids=["OLD"], groups_status="ok", groups_synced_at=old),
            entry("U2", sid="S-1-2"),
            entry("U3", sid="S-1-3"),
        ])
        await db.commit()

    def live(*, sid, dn, timeout):
        if sid == "S-1-1":
            return rec("U1", sid="S-1-1", groups=["NEW"], status="ok")
        if sid == "S-1-2":
            return None
        raise DirectoryError("AD: SERVER_DOWN")
    monkeypatch.setattr(sync, "fetch_ad_person", live)

    async with svc() as db:
        stored = [sync._to_record(e) for e in (await db.execute(select(DirectoryEntry))).scalars()]
        out = await sync.refresh_records(db, stored)

    assert {r.ref for r in out} == {"U1", "U3"}
    u1 = next(r for r in out if r.ref == "U1")
    assert u1.group_sids == ("NEW",)
    rows = {e.ref: e for e in await _get(svc, DirectoryEntry)}
    assert set(rows) == {"U1", "U3"}
    assert rows["U1"].group_sids == ["NEW"] and rows["U1"].synced_at > old
    assert rows["U1"].groups_synced_at > old
    assert rows["U3"].synced_at == old  # unreachable backend: untouched
