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
    assert r.status_code == 201, r.text
    tok = {"Authorization": f"Bearer {r.json()['token']}"}
    j = (await client.get(f"/api/v1/entries/{orig.id}", headers=tok)).json()
    assert j["linked_entry"] is None


@pytest.mark.asyncio
async def test_browse_hides_link_name_for_deleted_target(client, db_session):
    admin, app, disk, asset, *_ = await _seed(db_session)
    asset.is_deleted = True
    await db_session.commit()
    r = await client.get(f"/api/v1/browse?source_id={disk.id}", headers=_h(admin))
    by = {i["name"]: i for i in r.json()["items"]}
    assert by["z-orig.jpg"]["linked_entry_name"] is None


@pytest.mark.asyncio
async def test_enforced_perms_hide_inaccessible_sides(client, db_session, rt_set):
    from akashic.models.fs_person import FsBinding, FsPerson
    rt_set(browse_enforce_perms=True)
    admin, app, disk, asset, orig, thumb, plain = await _seed(db_session)
    viewer = User(id=uuid.uuid4(), username=f"v{uuid.uuid4().hex[:6]}", email="v@e", password_hash="x", role="viewer")
    db_session.add(viewer)
    await db_session.commit()
    person = FsPerson(id=uuid.uuid4(), user_id=viewer.id, label="p")
    db_session.add(person)
    await db_session.commit()
    db_session.add(FsBinding(id=uuid.uuid4(), fs_person_id=person.id, source_id=disk.id,
                             identity_type="posix_uid", identifier="1000", groups=[],
                             groups_source="manual"))
    await db_session.commit()
    me, other = ["posix:uid:1000"], ["posix:uid:999"]

    # Target not viewable: linked_entry and browse name are hidden.
    orig.viewable_by_read = me
    asset.viewable_by_read = other
    await db_session.commit()
    j = (await client.get(f"/api/v1/entries/{orig.id}", headers=_h(viewer))).json()
    assert j["linked_entry"] is None
    items = (await client.get(f"/api/v1/browse?source_id={disk.id}", headers=_h(viewer))).json()["items"]
    row = next(i for i in items if i["name"] == "z-orig.jpg")
    assert row["link_role"] == "original" and row["linked_entry_name"] is None

    # Target viewable, one backing file not: only the viewable one is listed.
    asset.viewable_by_read = me
    thumb.viewable_by_read = other
    await db_session.commit()
    j = (await client.get(f"/api/v1/entries/{asset.id}", headers=_h(viewer))).json()
    assert [b["id"] for b in j["backing_files"]] == [str(orig.id)]
    assert j["backing_total_bytes"] == 3_000_000
    j = (await client.get(f"/api/v1/entries/{orig.id}", headers=_h(viewer))).json()
    assert j["linked_entry"]["id"] == str(asset.id)
