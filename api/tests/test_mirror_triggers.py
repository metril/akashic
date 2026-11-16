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
from akashic.models.audit_event import AuditEvent
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services import mirror_links, post_scan
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


def _m(backing, local=f"{BASE}/", remote="/data"):
    return {"source_id": str(backing), "remote_prefix": remote, "local_prefix": local}


async def _app(setup_db, type_="immich", mirrors=None, name=None) -> uuid.UUID:
    cfg = {"mirrors": mirrors} if mirrors is not None else {}
    async with setup_db() as db:
        s = Source(name=name or f"{type_}-{_u()}", type=type_, connection_config=cfg)
        db.add(s)
        await db.commit()
        return s.id


def _create_body(mirrors):
    return {
        "name": f"app-{_u()}", "type": "immich",
        "connection_config": {"url": "http://x", "api_key": "k", "mirrors": mirrors},
    }


@pytest.mark.asyncio
async def test_create_app_source_normalizes_mirrors(client, setup_db):
    b = await _mk(setup_db, "local", {"path": "/tmp"})
    r = await client.post("/api/v1/sources", json=_create_body([_m(b)]))
    assert r.status_code == 201, r.text
    assert r.json()["connection_config"]["mirrors"][0]["local_prefix"] == BASE
    post_scan.relink_source_bg.assert_called_once()
    assert post_scan.relink_source_bg.call_args.args[0] == str(b)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["create_local", "update_local", "update_immich"])
async def test_mirror_of_rejected(client, setup_db, case):
    if case == "create_local":
        r = await client.post("/api/v1/sources", json={
            "name": f"l-{_u()}", "type": "local",
            "connection_config": {"path": "/tmp", "mirror_of": []},
        })
    else:
        sid = await _mk(setup_db, "local" if case == "update_local" else "immich", {"path": "/tmp"})
        r = await client.patch(
            f"/api/v1/sources/{sid}", json={"connection_config": {"mirror_of": []}}
        )
    assert r.status_code == 400
    assert "mirrors" in r.json()["message"]


@pytest.mark.asyncio
async def test_mirrors_on_backing_type_rejected(client, setup_db):
    other = await _mk(setup_db, "local", {"path": "/tmp"})
    b = await _mk(setup_db, "local", {"path": "/tmp"})
    r = await client.patch(
        f"/api/v1/sources/{b}", json={"connection_config": {"mirrors": [_m(other)]}}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_unknown_backing_404(client, setup_db):
    app = await _app(setup_db)
    r = await client.patch(
        f"/api/v1/sources/{app}",
        json={"connection_config": {"mirrors": [_m(uuid.uuid4())]}},
    )
    assert r.status_code == 404


def test_changed_backings():
    b1, b2, b3 = sorted(uuid.uuid4() for _ in range(3))
    x, y = _m(b1, "/a"), _m(b1, "/b")
    base = [x, y, _m(b2, "/c")]
    assert mirror_links.changed_backings(base, list(base)) == []
    assert mirror_links.changed_backings(base, [y, x, _m(b2, "/c")]) == [b1]
    assert mirror_links.changed_backings(base, [x, y]) == [b2]
    assert mirror_links.changed_backings(base, base + [_m(b3, "/d")]) == [b3]


@pytest.mark.asyncio
async def test_update_mirrors_relinks_changed_only(client, setup_db):
    b1 = await _mk(setup_db, "local", {"path": "/tmp"})
    b2 = await _mk(setup_db, "local", {"path": "/tmp"})
    app = await _app(setup_db, mirrors=[_m(b1, "/a"), _m(b2, "/b")])
    r = await client.patch(
        f"/api/v1/sources/{app}",
        json={"connection_config": {"mirrors": [_m(b1, "/z"), _m(b2, "/b")]}},
    )
    assert r.status_code == 200, r.text
    post_scan.relink_source_bg.assert_called_once()
    assert post_scan.relink_source_bg.call_args.args[0] == str(b1)
    post_scan.relink_source_bg.reset_mock()
    r = await client.patch(
        f"/api/v1/sources/{app}",
        json={"connection_config": {"mirrors": [_m(b1, "/z"), _m(b2, "/b")]}},
    )
    assert r.status_code == 200
    post_scan.relink_source_bg.assert_not_called()


@pytest.mark.asyncio
async def test_rename_app_source_relinks_backings(client, setup_db):
    b1 = await _mk(setup_db, "local", {"path": "/tmp"})
    b2 = await _mk(setup_db, "local", {"path": "/tmp"})
    app = await _app(setup_db, mirrors=[_m(b1, "/a"), _m(b2, "/b")])
    r = await client.patch(f"/api/v1/sources/{app}", json={"name": f"renamed-{_u()}"})
    assert r.status_code == 200, r.text
    got = {c.args[0] for c in post_scan.relink_source_bg.call_args_list}
    assert got == {str(b1), str(b2)}


@pytest.mark.asyncio
async def test_clearing_last_mapping_unlinks(client, setup_db):
    bid = await _mk(setup_db, "local", {"path": "/tmp"})
    app = await _app(setup_db, mirrors=[_m(bid, BASE)])
    async with setup_db() as db:
        asset = Entry(
            source_id=app, kind="file", path=f"/asset/{_u()}", parent_path="/asset", name="a",
            domain_metadata={"immich_id": IMM_ID, "original_path": "/data/x/IMG.jpg"},
        )
        db.add(asset)
        await db.flush()
        db.add(Entry(source_id=bid, kind="file", path=f"{BASE}/x/IMG.jpg",
                     parent_path=f"{BASE}/x", name="IMG.jpg"))
        await db.flush()
        await relink_backing(db, bid)
        await db.commit()
    async with setup_db() as db:
        e = (await db.execute(select(Entry).where(Entry.source_id == bid))).scalar_one()
        assert e.linked_entry_id is not None
    r = await client.patch(f"/api/v1/sources/{app}", json={"connection_config": {"mirrors": []}})
    assert r.status_code == 200, r.text
    assert post_scan.relink_source_bg.call_args.args[0] == str(bid)
    # real background relink, run against the test DB
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


async def _backing_with_link(setup_db, app, extra_apps=()):
    async with setup_db() as db:
        asset = Entry(
            source_id=app, kind="file", path=f"/asset/{_u()}", parent_path="/asset", name="a",
            domain_metadata={"immich_id": IMM_ID, "original_path": "/data/x/IMG.jpg"},
        )
        b = Source(name=f"b-{_u()}", type="local", connection_config={"path": "/tmp"})
        db.add_all([asset, b])
        await db.flush()
        for aid in (app, *extra_apps):
            a = await db.get(Source, aid)
            a.connection_config = {**a.connection_config, "mirrors": [_m(b.id, BASE)]}
        db.add(Entry(source_id=b.id, kind="file", path=f"{BASE}/x/IMG.jpg",
                     parent_path=f"{BASE}/x", name="IMG.jpg"))
        await db.flush()
        await relink_backing(db, b.id)
        await db.commit()
        return b.id


@pytest.mark.asyncio
async def test_delete_app_source_relinks_backings(client, setup_db):
    app = await _app(setup_db)
    bid = await _backing_with_link(setup_db, app)
    r = await client.delete(f"/api/v1/sources/{app}")
    assert r.status_code == 204, r.text
    post_scan.relink_source_bg.assert_called_once()
    assert post_scan.relink_source_bg.call_args.args[0] == str(bid)
    async with setup_db() as db:
        await mirror_links.relink_and_flush(db, bid)
    async with setup_db() as db:
        e = (await db.execute(select(Entry).where(Entry.source_id == bid))).scalar_one()
        assert e.linked_entry_id is None and e.link_role is None
        assert e.link_target_viewable is None and e.link_target_source_id is None


@pytest.mark.asyncio
async def test_delete_app_source_with_corrupt_mirrors_succeeds(client, setup_db):
    app = await _app(setup_db)
    bid = await _backing_with_link(setup_db, app)
    async with setup_db() as db:
        a = await db.get(Source, app)
        a.connection_config = {"mirrors": [{"source_id": "not-a-uuid"}]}
        await db.commit()
    r = await client.delete(f"/api/v1/sources/{app}")
    assert r.status_code == 204, r.text
    assert post_scan.relink_source_bg.call_args.args[0] == str(bid)
    async with setup_db() as db:
        assert await db.get(Source, app) is None


@pytest.mark.asyncio
async def test_delete_backing_strips_from_apps(client, setup_db):
    app1 = await _app(setup_db)
    app2 = await _app(setup_db, "paperless")
    bid = await _backing_with_link(setup_db, app1, extra_apps=(app2,))
    r = await client.delete(f"/api/v1/sources/{bid}")
    assert r.status_code == 204, r.text
    async with setup_db() as db:
        for aid in (app1, app2):
            assert (await db.get(Source, aid)).connection_config["mirrors"] == []
        n = (await db.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == "source_updated",
                AuditEvent.source_id.in_([app1, app2]),
            )
        )).scalars().all()
        assert len(n) == 2
        e = (await db.execute(select(Entry).where(Entry.path == f"{BASE}/x/IMG.jpg"))).scalars().all()
        assert e and all(
            x.linked_entry_id is None and x.link_role is None
            and x.link_target_viewable is None and x.link_target_source_id is None
            for x in e
        )
