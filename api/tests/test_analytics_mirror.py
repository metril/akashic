"""Analytics over mirror-link logical views and the link-role breakdown."""
import uuid

import pytest

from akashic.auth.jwt import create_access_token
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User

IMM = "/data/upload/u1/ab/cd"


def _u() -> str:
    return uuid.uuid4().hex[:8]


async def _admin(db):
    user = User(id=uuid.uuid4(), username=f"adm{_u()}", email=f"{_u()}@e", password_hash="x", role="admin")
    db.add(user)
    await db.commit()
    return {"Authorization": f"Bearer {create_access_token({'sub': str(user.id)})}"}


async def _src(db, type_, name, cfg=None):
    s = Source(id=uuid.uuid4(), name=f"{name}-{_u()}", type=type_, connection_config=cfg or {})
    db.add(s)
    await db.flush()
    return s


async def _file(db, source, name, size, **cols):
    e = Entry(
        id=uuid.uuid4(), source_id=source.id, kind="file", parent_path="/d", path=f"/d/{name}",
        name=name, extension=name.rsplit(".", 1)[-1], size_bytes=size, **cols,
    )
    db.add(e)
    await db.flush()
    return e


async def _mirrored(db):
    app = await _src(db, "immich", "Iris")
    back = await _src(db, "local", "back", {"mirror_of": [
        {"source_id": str(app.id), "remote_prefix": "/data", "local_prefix": "/host/immich"},
    ]})
    return app, back


@pytest.mark.asyncio
async def test_storage_by_source_logical_view_zero_bytes(client, db_session):
    h = await _admin(db_session)
    app, back = await _mirrored(db_session)
    plain = await _src(db_session, "immich", "Plain")
    await _file(db_session, app, "a.jpg", 100)
    await _file(db_session, app, "b.jpg", 200)
    await _file(db_session, plain, "c.jpg", 50)
    await _file(db_session, back, "a.jpg", 100)
    await db_session.commit()
    rows = {r["source_id"]: r for r in (await client.get("/api/v1/analytics/storage-by-source", headers=h)).json()}
    assert rows[str(app.id)]["total_size"] == 0
    assert rows[str(app.id)]["logical_view"] is True
    assert rows[str(app.id)]["count"] == 2
    assert rows[str(plain.id)]["total_size"] == 50
    assert rows[str(plain.id)]["logical_view"] is False
    assert rows[str(back.id)]["total_size"] == 100
    # logical view is zeroed before ordering, so it sorts last despite 300 raw bytes
    order = [r["source_id"] for r in (await client.get("/api/v1/analytics/storage-by-source", headers=h)).json()]
    assert order.index(str(app.id)) > order.index(str(plain.id))
    assert order.index(str(app.id)) > order.index(str(back.id))


@pytest.mark.asyncio
async def test_storage_by_type_excludes_logical_view(client, db_session):
    h = await _admin(db_session)
    app, back = await _mirrored(db_session)
    await _file(db_session, app, "a.zzmirror", 100)
    await _file(db_session, back, "a.yymirror", 10)
    await db_session.commit()
    rows = {r["extension"]: r for r in (await client.get("/api/v1/analytics/storage-by-type", headers=h)).json()}
    assert "zzmirror" not in rows
    assert rows["yymirror"]["total_size"] == 10


@pytest.mark.asyncio
async def test_largest_files_excludes_logical_view(client, db_session):
    h = await _admin(db_session)
    app, back = await _mirrored(db_session)
    big = await _file(db_session, app, "huge.jpg", 10**12)
    small = await _file(db_session, back, "small.jpg", 10)
    await db_session.commit()
    ids = {r["id"] for r in (await client.get("/api/v1/analytics/largest-files?n=100", headers=h)).json()}
    assert str(big.id) not in ids
    assert str(small.id) in ids


@pytest.mark.asyncio
async def test_storage_by_link_role_buckets(client, db_session):
    h = await _admin(db_session)
    app, back = await _mirrored(db_session)
    target = await _file(db_session, app, "t.jpg", 1)
    await _file(db_session, back, "o.jpg", 100, linked_entry_id=target.id, link_role="original")
    await _file(db_session, back, "o2.jpg", 50, linked_entry_id=target.id, link_role="original")
    await _file(db_session, back, "t.webp", 7, linked_entry_id=target.id, link_role="thumbnail")
    await _file(db_session, back, "x.xmp", 3, linked_entry_id=target.id, link_role="sidecar")
    await _file(db_session, back, "free.txt", 11)
    await _file(db_session, back, "gone.jpg", 999, linked_entry_id=target.id, link_role="original", is_deleted=True)
    await db_session.commit()
    r = await client.get(f"/api/v1/analytics/storage-by-link-role?source_id={back.id}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"link_role": "original", "count": 2, "total_size": 150},
        {"link_role": "thumbnail", "count": 1, "total_size": 7},
        {"link_role": "encoded_video", "count": 0, "total_size": 0},
        {"link_role": "sidecar", "count": 1, "total_size": 3},
        {"link_role": "archive", "count": 0, "total_size": 0},
        {"link_role": "version", "count": 0, "total_size": 0},
        {"link_role": "unlinked", "count": 1, "total_size": 11},
    ]


@pytest.mark.asyncio
async def test_storage_by_link_role_404_outside_token(client, db_session):
    admin = await _admin(db_session)
    _, back = await _mirrored(db_session)
    other = await _src(db_session, "local", "other")
    await db_session.commit()
    t = await client.post(
        "/api/v1/me/tokens", headers=admin,
        json={"name": "t", "scopes": ["analytics:read"], "source_ids": [str(other.id)]},
    )
    assert t.status_code == 201, t.text
    h = {"Authorization": f"Bearer {t.json()['token']}"}
    r = await client.get(f"/api/v1/analytics/storage-by-link-role?source_id={back.id}", headers=h)
    assert r.status_code == 404
    r = await client.get(f"/api/v1/analytics/storage-by-link-role?source_id={other.id}", headers=h)
    assert r.status_code == 200
