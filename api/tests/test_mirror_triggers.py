"""Mirror-link triggers: source create/update/delete and post-scan ordering."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from akashic.auth.dependencies import get_principal
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services import post_scan
from akashic.services.mirror_links import relink_backing
from tests.principals import as_principal

IMM_ID = "0b6c1f2e-3a4b-4c5d-8e6f-7a8b9c0d1e2f"
BASE = "/host/mnt/immich/data"


def _u() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def admin_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(), username=f"admin-{_u()}", email=f"{_u()}@b.c",
            password_hash="x", role="admin",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def client(setup_db, admin_user):
    async def _db():
        async with setup_db() as session:
            yield session

    async def _user():
        return admin_user

    app = create_app()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_principal] = as_principal(_user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _stub_side_effects(monkeypatch):
    from akashic.services import meili_indexer, search

    monkeypatch.setattr(search, "delete_files_batch", AsyncMock())
    monkeypatch.setattr(search, "update_files_partial", AsyncMock())
    monkeypatch.setattr(meili_indexer, "mark_dirty", AsyncMock())
    monkeypatch.setattr(meili_indexer, "flush", AsyncMock(return_value=0))
    monkeypatch.setattr(post_scan, "relink_source_bg", AsyncMock())


async def _mk(setup_db, type_, cfg=None) -> uuid.UUID:
    async with setup_db() as db:
        s = Source(name=f"{type_}-{_u()}", type=type_, connection_config=cfg or {})
        db.add(s)
        await db.commit()
        return s.id


def _body(target, local=f"{BASE}/"):
    return {
        "name": f"b-{_u()}", "type": "local",
        "connection_config": {
            "path": "/tmp",
            "mirror_of": [{"source_id": str(target), "remote_prefix": "/data", "local_prefix": local}],
        },
    }


@pytest.mark.asyncio
async def test_create_source_normalizes_mirror_of(client, setup_db):
    app = await _mk(setup_db, "immich")
    r = await client.post("/api/v1/sources", json=_body(app))
    assert r.status_code == 201, r.text
    assert r.json()["connection_config"]["mirror_of"][0]["local_prefix"] == BASE
    assert post_scan.relink_source_bg.call_count == 1


@pytest.mark.asyncio
async def test_create_source_rejects_bad_mirror_target(client, setup_db):
    local = await _mk(setup_db, "local")
    r = await client.post("/api/v1/sources", json=_body(local))
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_source_unknown_mirror_target_404(client, setup_db):
    b = await _mk(setup_db, "local", {"path": "/tmp"})
    r = await client.patch(
        f"/api/v1/sources/{b}",
        json={"connection_config": _body(uuid.uuid4())["connection_config"]},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_mirror_of_schedules_relink(client, setup_db):
    app = await _mk(setup_db, "immich")
    b = await _mk(setup_db, "local", {"path": "/tmp"})
    cfg = {"connection_config": _body(app)["connection_config"]}
    r = await client.patch(f"/api/v1/sources/{b}", json=cfg)
    assert r.status_code == 200, r.text
    assert post_scan.relink_source_bg.call_count == 1
    assert post_scan.relink_source_bg.call_args.args[0] == str(b)
    r = await client.patch(f"/api/v1/sources/{b}", json=cfg)
    assert r.status_code == 200
    assert post_scan.relink_source_bg.call_count == 1


@pytest.mark.asyncio
async def test_clearing_last_mapping_unlinks(client, setup_db):
    app = await _mk(setup_db, "immich")
    async with setup_db() as db:
        asset = Entry(
            source_id=app, kind="file", path=f"/asset/{_u()}", parent_path="/asset", name="a",
            domain_metadata={"immich_id": IMM_ID, "original_path": "/data/x/IMG.jpg"},
        )
        b = Source(name=f"b-{_u()}", type="local", connection_config={"path": "/tmp", "mirror_of": [
            {"source_id": str(app), "remote_prefix": "/data", "local_prefix": BASE}]})
        db.add_all([asset, b])
        await db.flush()
        db.add(Entry(source_id=b.id, kind="file", path=f"{BASE}/x/IMG.jpg",
                     parent_path=f"{BASE}/x", name="IMG.jpg"))
        await db.flush()
        await relink_backing(db, b.id)
        await db.commit()
        bid = b.id
    async with setup_db() as db:
        e = (await db.execute(select(Entry).where(Entry.source_id == bid))).scalar_one()
        assert e.linked_entry_id is not None
    r = await client.patch(f"/api/v1/sources/{bid}", json={"connection_config": {"mirror_of": []}})
    assert r.status_code == 200, r.text
    # real background relink, run against the test DB
    from akashic.services import mirror_links
    async with setup_db() as db:
        await mirror_links.relink_and_flush(db, bid)
    async with setup_db() as db:
        e = (await db.execute(select(Entry).where(Entry.source_id == bid))).scalar_one()
        assert e.linked_entry_id is None and e.link_role is None


@pytest.mark.asyncio
async def test_run_post_scan_tasks_order(monkeypatch):
    calls: list[str] = []

    def rec(name):
        async def _f(*a, **k):
            calls.append(name)
        return _f

    for attr, name in [("_rollup_subtree_aggregates", "rollup"), ("_relink_mirror_links", "relink"),
                       ("_write_scan_snapshot", "snapshot"), ("_dispatch_scan_webhooks", "webhooks")]:
        monkeypatch.setattr(post_scan, attr, rec(name))
    await post_scan.run_post_scan_tasks("s", "src", "completed", "db")
    assert calls == ["rollup", "relink", "snapshot", "webhooks"]
    calls.clear()
    await post_scan.run_post_scan_tasks("s", "src", "failed", "db")
    assert calls == ["rollup", "snapshot", "webhooks"]


async def _backing_with_link(setup_db, app):
    async with setup_db() as db:
        asset = Entry(
            source_id=app, kind="file", path=f"/asset/{_u()}", parent_path="/asset", name="a",
            domain_metadata={"immich_id": IMM_ID, "original_path": "/data/x/IMG.jpg"},
        )
        b = Source(name=f"b-{_u()}", type="local", connection_config={"path": "/tmp", "mirror_of": [
            {"source_id": str(app), "remote_prefix": "/data", "local_prefix": BASE}]})
        db.add_all([asset, b])
        await db.flush()
        db.add(Entry(source_id=b.id, kind="file", path=f"{BASE}/x/IMG.jpg",
                     parent_path=f"{BASE}/x", name="IMG.jpg"))
        await db.flush()
        await relink_backing(db, b.id)
        await db.commit()
        return b.id


@pytest.mark.asyncio
async def test_delete_app_source_strips_mapping_and_unlinks(client, setup_db):
    app = await _mk(setup_db, "immich")
    bid = await _backing_with_link(setup_db, app)
    r = await client.delete(f"/api/v1/sources/{app}")
    assert r.status_code == 204, r.text
    async with setup_db() as db:
        b = await db.get(Source, bid)
        assert b.connection_config["mirror_of"] == []
        e = (await db.execute(select(Entry).where(Entry.source_id == bid))).scalar_one()
        assert e.linked_entry_id is None and e.link_role is None
        assert e.link_target_viewable is None and e.link_target_source_id is None


@pytest.mark.asyncio
async def test_delete_backing_source_preserve_clears_links(client, setup_db):
    app = await _mk(setup_db, "immich")
    bid = await _backing_with_link(setup_db, app)
    r = await client.delete(f"/api/v1/sources/{bid}")
    assert r.status_code == 204, r.text
    async with setup_db() as db:
        e = (await db.execute(select(Entry).where(Entry.path == f"{BASE}/x/IMG.jpg"))).scalars().all()
        assert e and all(
            x.linked_entry_id is None and x.link_role is None
            and x.link_target_viewable is None and x.link_target_source_id is None
            for x in e
        )
