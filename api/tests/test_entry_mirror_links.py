"""Mirror links: browse badge fields and entry-detail linked_entry / backing_files."""
import uuid

import pytest

from akashic.auth.jwt import create_access_token
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User


def _h(user):
    return {"Authorization": f"Bearer {create_access_token({'sub': str(user.id)})}"}


async def _seed(db):
    admin = User(id=uuid.uuid4(), username=f"a{uuid.uuid4().hex[:6]}", email="a@e", password_hash="x", role="admin")
    app = Source(id=uuid.uuid4(), name=f"app{uuid.uuid4().hex[:6]}", type="immich", connection_config={})
    disk = Source(id=uuid.uuid4(), name=f"disk{uuid.uuid4().hex[:6]}", type="local", connection_config={})
    db.add_all([admin, app, disk])
    await db.commit()

    def mk(src, name, **kw):
        return Entry(
            id=uuid.uuid4(), source_id=src.id, kind="file", parent_path="/",
            path=f"/{name}", name=name,
            viewable_by_read=["*"], viewable_by_write=[], viewable_by_delete=[], **kw,
        )
    asset = mk(app, "asset.jpg")
    link = dict(linked_entry_id=asset.id, link_target_viewable=["*"], link_target_source_id=app.id)
    thumb = mk(disk, "a-thumb.jpg", size_bytes=40_000, link_role="thumbnail", **link)
    orig = mk(disk, "z-orig.jpg", size_bytes=3_000_000, link_role="original", **link)
    plain = mk(disk, "plain.txt")
    db.add(asset)
    await db.commit()
    db.add_all([thumb, orig, plain])
    await db.commit()
    return admin, app, disk, asset, orig, thumb, plain


@pytest.mark.asyncio
async def test_browse_returns_link_badge_fields(client, db_session):
    admin, app, disk, asset, orig, thumb, plain = await _seed(db_session)
    r = await client.get(f"/api/v1/browse?source_id={disk.id}", headers=_h(admin))
    assert r.status_code == 200, r.text
    by = {i["name"]: i for i in r.json()["items"]}
    assert by["z-orig.jpg"]["link_role"] == "original"
    assert by["z-orig.jpg"]["linked_entry_id"] == str(asset.id)
    assert by["z-orig.jpg"]["linked_entry_name"] == "asset.jpg"
    assert by["plain.txt"]["link_role"] is None
    assert by["plain.txt"]["linked_entry_id"] is None
    assert by["plain.txt"]["linked_entry_name"] is None


@pytest.mark.asyncio
async def test_entry_detail_app_lists_backing_files(client, db_session):
    admin, app, disk, asset, *_ = await _seed(db_session)
    r = await client.get(f"/api/v1/entries/{asset.id}", headers=_h(admin))
    assert r.status_code == 200, r.text
    j = r.json()
    assert [b["link_role"] for b in j["backing_files"]] == ["original", "thumbnail"]
    assert j["backing_total_bytes"] == 3_040_000
    assert j["backing_files"][0]["source_name"] == disk.name
    assert j["linked_entry"] is None


@pytest.mark.asyncio
async def test_entry_detail_backing_shows_linked_entry(client, db_session):
    admin, app, disk, asset, orig, *_ = await _seed(db_session)
    r = await client.get(f"/api/v1/entries/{orig.id}", headers=_h(admin))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["link_role"] == "original"
    assert j["linked_entry_id"] == str(asset.id)
    le = j["linked_entry"]
    assert le["id"] == str(asset.id) and le["name"] == "asset.jpg"
    assert le["source_name"] == app.name and le["link_role"] == "original"
    assert j["backing_files"] == [] and j["backing_total_bytes"] is None


@pytest.mark.asyncio
async def test_entry_detail_backing_files_respect_source_limit(client, db_session):
    admin, app, disk, asset, *_ = await _seed(db_session)
    r = await client.post(
        "/api/v1/me/tokens", headers=_h(admin),
        json={"name": "t", "scopes": ["files:read"], "source_ids": [str(app.id)]},
    )
    assert r.status_code == 201, r.text
    tok = {"Authorization": f"Bearer {r.json()['token']}"}
    r = await client.get(f"/api/v1/entries/{asset.id}", headers=tok)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["backing_files"] == [] and j["backing_total_bytes"] is None


@pytest.mark.asyncio
async def test_entry_detail_linked_entry_null_for_limited_token(client, db_session):
    admin, app, disk, asset, orig, *_ = await _seed(db_session)
    r = await client.post(
        "/api/v1/me/tokens", headers=_h(admin),
        json={"name": "t", "scopes": ["files:read"], "source_ids": [str(disk.id)]},
    )
    tok = {"Authorization": f"Bearer {r.json()['token']}"}
    j = (await client.get(f"/api/v1/entries/{orig.id}", headers=tok)).json()
    assert j["linked_entry"] is None
