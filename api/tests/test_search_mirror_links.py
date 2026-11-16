"""Mirror links: default search hides linked backing files from viewers who
can also see the linked app entry; `link` filter; hit fields."""
import uuid

import pytest

from akashic.auth.jwt import create_access_token
from akashic.models.entry import Entry
from akashic.models.fs_person import FsBinding  # noqa: F401
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services.filter_grammar import LinkPred, serialize
from akashic.services.link_visibility import hide_linked_meili, hide_linked_sql


def test_hide_linked_meili_variants():
    sid = uuid.uuid4()
    assert hide_linked_meili(None, None) == "NOT (link_role IS NOT NULL)"
    assert hide_linked_meili(["a", 'b"'], None) == (
        'NOT (link_role IS NOT NULL AND link_target_viewable IN ["a","b\\""])'
    )
    assert hide_linked_meili(None, [sid]) == (
        f'NOT (link_role IS NOT NULL AND link_target_source_id IN ["{sid}"])'
    )


def test_hide_linked_empty_scope_hides_nothing():
    sid = uuid.uuid4()
    for toks, srcs in (([], None), (None, []), ([], [sid]), (["t"], [])):
        assert hide_linked_meili(toks, srcs) == ""
        assert str(hide_linked_sql(toks, srcs)) == "true"


@pytest.mark.asyncio
async def test_ensure_index_filterable_includes_link_fields(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from akashic.services import search as svc
    index = MagicMock()
    for m in ("update_searchable_attributes", "update_filterable_attributes",
              "update_sortable_attributes", "update_pagination"):
        setattr(index, m, AsyncMock())
    client = MagicMock()
    client.get_index = AsyncMock(return_value=index)
    monkeypatch.setattr(svc, "get_meili_client", AsyncMock(return_value=client))
    await svc.ensure_index()
    attrs = index.update_filterable_attributes.call_args.args[0]
    assert {"link_role", "link_target_viewable", "link_target_source_id"} <= set(attrs)


@pytest.fixture
def _no_meili(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("meili down")
    monkeypatch.setattr("akashic.services.search.search_files", boom)


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
            viewable_by_read=kw.pop("vr", ["*"]), viewable_by_write=[], viewable_by_delete=[], **kw,
        )
    asset = mk(app, "asset.jpg")
    orig = mk(disk, "orig.jpg", linked_entry_id=asset.id, link_role="original",
              link_target_viewable=["*"], link_target_source_id=app.id)
    thumb = mk(disk, "thumb.jpg", linked_entry_id=asset.id, link_role="thumbnail",
               link_target_viewable=["*"], link_target_source_id=app.id)
    plain = mk(disk, "plain.txt")
    db.add(asset)
    await db.commit()
    db.add_all([orig, thumb, plain])
    await db.commit()
    return admin, app, disk, asset, orig, thumb, plain


async def _get(client, user, qs=""):
    tok = create_access_token({"sub": str(user.id)})
    r = await client.get(f"/api/v1/search?{qs}", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_default_search_hides_linked_backing_for_admin(client, db_session, _no_meili):
    admin, *_ = await _seed(db_session)
    names = sorted(h["filename"] for h in (await _get(client, admin, "mode=glob"))["items"])
    assert names == ["asset.jpg", "plain.txt"]


@pytest.mark.asyncio
async def test_link_filter_any_returns_only_linked(client, db_session, _no_meili):
    admin, *_ = await _seed(db_session)
    enc = serialize([LinkPred(kind="link", value="any")])
    names = sorted(h["filename"] for h in (await _get(client, admin, f"filters={enc}"))["items"])
    assert names == ["orig.jpg", "thumb.jpg"]


@pytest.mark.asyncio
async def test_link_filter_role(client, db_session, _no_meili):
    admin, *_ = await _seed(db_session)
    enc = serialize([LinkPred(kind="link", value="thumbnail")])
    items = (await _get(client, admin, f"filters={enc}"))["items"]
    assert [h["filename"] for h in items] == ["thumb.jpg"]


@pytest.mark.asyncio
async def test_hit_carries_link_role(client, db_session, _no_meili):
    admin, _, _, asset, *_ = await _seed(db_session)
    enc = serialize([LinkPred(kind="link", value="original")])
    hit = (await _get(client, admin, f"filters={enc}"))["items"][0]
    assert hit["link_role"] == "original"
    assert hit["linked_entry_id"] == str(asset.id)


@pytest.mark.asyncio
async def test_link_hidden_only_when_target_visible(client, db_session, _no_meili):
    from akashic.models.fs_person import FsBinding, FsPerson
    admin, app, disk, asset, orig, thumb, plain = await _seed(db_session)
    # Target viewable only to uid 999; viewer is bound to uid 1000.
    for e in (orig, thumb):
        e.link_target_viewable = ["posix:uid:999"]
    viewer = User(id=uuid.uuid4(), username=f"v{uuid.uuid4().hex[:6]}", email="v@e", password_hash="x", role="viewer")
    db_session.add(viewer)
    await db_session.commit()
    person = FsPerson(id=uuid.uuid4(), user_id=viewer.id, label="p")
    db_session.add(person)
    await db_session.commit()
    db_session.add(FsBinding(id=uuid.uuid4(), fs_person_id=person.id, source_id=disk.id,
                             identity_type="posix_uid", identifier="1000", groups=[]))
    await db_session.commit()
    for e in (orig, thumb, plain):
        e.viewable_by_read = ["posix:uid:1000"]
    asset.viewable_by_read = ["posix:uid:999"]
    await db_session.commit()
    names = sorted(h["filename"] for h in (await _get(client, viewer))["items"])
    assert names == ["orig.jpg", "plain.txt", "thumb.jpg"]


@pytest.mark.asyncio
async def test_source_limited_token_sees_backing_when_app_source_not_allowed(client, db_session, _no_meili):
    admin, app, disk, *_ = await _seed(db_session)
    tok = create_access_token({"sub": str(admin.id)})
    r = await client.post(
        "/api/v1/me/tokens", headers={"Authorization": f"Bearer {tok}"},
        json={"name": "t", "scopes": ["files:read"], "source_ids": [str(disk.id)]},
    )
    assert r.status_code == 201, r.text
    secret = r.json()["token"]
    r = await client.get("/api/v1/search?mode=glob", headers={"Authorization": f"Bearer {secret}"})
    assert r.status_code == 200, r.text
    assert sorted(h["filename"] for h in r.json()["items"]) == ["orig.jpg", "plain.txt", "thumb.jpg"]
