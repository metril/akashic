import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.services.mirror_links import (
    backing_sources_for,
    logical_view_source_ids,
    relink_after_scan,
    relink_backing,
)

IMM_ID = "0b6c1f2e-3a4b-4c5d-8e6f-7a8b9c0d1e2f"
BASE = "/host/mnt/immich/data"


def _u() -> str:
    return uuid.uuid4().hex[:8]


async def _src(db, type_, name, cfg=None):
    s = Source(name=f"{name}-{_u()}", type=type_, connection_config=cfg or {})
    db.add(s)
    await db.flush()
    return s


async def _seed(db, source, path, **cols):
    e = Entry(
        source_id=source.id, kind="file", path=path,
        parent_path=path.rsplit("/", 1)[0], name=path.rsplit("/", 1)[1], **cols,
    )
    db.add(e)
    await db.flush()
    return e


def _map(app, remote="/data", local=BASE):
    return {"source_id": str(app.id), "remote_prefix": remote, "local_prefix": local}


async def _backing(db, mappings, type_="local"):
    return await _src(db, type_, "back", {"mirror_of": mappings})


async def _immich(db, original="/data/upload/u1/ab/cd/IMG_1.jpg"):
    app = await _src(db, "immich", "Iris")
    asset = await _seed(
        db, app, f"/asset/{_u()}", viewable_by_read=["sid:S-1"],
        domain_metadata={"immich_id": IMM_ID, "original_path": original},
    )
    return app, asset


async def _roles(db, backing):
    bid = backing.id
    await db.commit()
    rows = (await db.execute(select(Entry).where(Entry.source_id == bid))).scalars().all()
    return {e.path: e for e in rows}


@pytest.mark.asyncio
async def test_immich_original_linked(db_session):
    db = db_session
    app, asset = await _immich(db)
    b = await _backing(db, [_map(app)])
    await _seed(db, b, f"{BASE}/upload/u1/ab/cd/IMG_1.jpg")
    await relink_backing(db, b.id)
    e = (await _roles(db, b))[f"{BASE}/upload/u1/ab/cd/IMG_1.jpg"]
    assert e.link_role == "original"
    assert e.linked_entry_id == asset.id
    assert e.link_target_viewable == ["sid:S-1"]
    assert e.link_target_source_id == app.id


@pytest.mark.asyncio
async def test_immich_derivatives_linked(db_session):
    db = db_session
    app, asset = await _immich(db)
    b = await _backing(db, [_map(app)])
    paths = {
        f"{BASE}/thumbs/u1/0b/6c/{IMM_ID}-preview.jpeg": "thumbnail",
        f"{BASE}/thumbs/u1/0b/6c/{IMM_ID}-thumbnail.webp": "thumbnail",
        f"{BASE}/thumbs/u1/0b/6c/{IMM_ID}-fullsize.jpeg": "thumbnail",
        f"{BASE}/encoded-video/u1/0b/6c/{IMM_ID}.mp4": "encoded_video",
        f"{BASE}/thumbs/u1/0b/6c/{IMM_ID}-other.jpeg": None,
    }
    for p in paths:
        await _seed(db, b, p)
    await relink_backing(db, b.id)
    got = await _roles(db, b)
    for p, role in paths.items():
        assert got[p].link_role == role, p


@pytest.mark.asyncio
async def test_immich_sidecar_linked(db_session):
    db = db_session
    app, _ = await _immich(db)
    b = await _backing(db, [_map(app)])
    p = f"{BASE}/upload/u1/ab/cd/IMG_1.jpg.xmp"
    await _seed(db, b, p)
    await relink_backing(db, b.id)
    assert (await _roles(db, b))[p].link_role == "sidecar"


@pytest.mark.asyncio
async def test_original_outside_remote_prefix_stays_unlinked(db_session):
    db = db_session
    app, _ = await _immich(db, original="/mnt/external/x.jpg")
    b = await _backing(db, [_map(app)])
    p = f"{BASE}/external/x.jpg"
    await _seed(db, b, p)
    await relink_backing(db, b.id)
    assert (await _roles(db, b))[p].link_role is None


@pytest.mark.asyncio
async def test_s3_backing_key_prefix(db_session):
    db = db_session
    app, asset = await _immich(db)
    b = await _backing(db, [_map(app, local="immich")], type_="s3")
    p = "immich/upload/u1/ab/cd/IMG_1.jpg"
    await _seed(db, b, p)
    await relink_backing(db, b.id)
    e = (await _roles(db, b))[p]
    assert e.link_role == "original" and e.linked_entry_id == asset.id


async def _paperless(db):
    app = await _src(db, "paperless", "Paper")
    doc = await _seed(
        db, app, f"/doc/{_u()}", viewable_by_read=["sid:S-2"],
        domain_metadata={
            "paperless_id": 219, "media_filename": "0000219.eml",
            "archive_media_filename": "0000219.pdf",
            "versions": [{"id": 300, "media_filename": "0000300.pdf",
                          "archive_media_filename": None}],
        },
    )
    return app, doc


@pytest.mark.asyncio
async def test_paperless_rules(db_session):
    db = db_session
    app, doc = await _paperless(db)
    root = "/host/mnt/paperless/media/documents"
    b = await _backing(db, [_map(app, remote="", local=root)])
    exp = {
        f"{root}/originals/0000219.eml": "original",
        f"{root}/archive/0000219.pdf": "archive",
        f"{root}/thumbnails/0000219.webp": "thumbnail",
        f"{root}/originals/0000300.pdf": "version",
    }
    for p in exp:
        await _seed(db, b, p)
    await relink_backing(db, b.id)
    got = await _roles(db, b)
    for p, role in exp.items():
        assert got[p].link_role == role, p
        assert got[p].linked_entry_id == doc.id


@pytest.mark.asyncio
async def test_deleted_target_unlinks(db_session):
    db = db_session
    app, asset = await _immich(db)
    b = await _backing(db, [_map(app)])
    p = f"{BASE}/upload/u1/ab/cd/IMG_1.jpg"
    await _seed(db, b, p)
    await relink_backing(db, b.id)
    asset.is_deleted = True
    await db.flush()
    await relink_backing(db, b.id)
    e = (await _roles(db, b))[p]
    assert e.link_role is None and e.linked_entry_id is None
    assert e.link_target_viewable is None and e.link_target_source_id is None


@pytest.mark.asyncio
async def test_mapping_removed_clears_links(db_session):
    db = db_session
    app, _ = await _immich(db)
    b = await _backing(db, [_map(app)])
    p = f"{BASE}/upload/u1/ab/cd/IMG_1.jpg"
    await _seed(db, b, p)
    await relink_backing(db, b.id)
    b.connection_config = {"mirror_of": []}
    await db.flush()
    r = await relink_backing(db, b.id)
    assert len(r.changed_ids) == 1
    assert (await _roles(db, b))[p].link_role is None


@pytest.mark.asyncio
async def test_link_pass_idempotent(db_session):
    db = db_session
    app, _ = await _immich(db)
    b = await _backing(db, [_map(app)])
    await _seed(db, b, f"{BASE}/upload/u1/ab/cd/IMG_1.jpg")
    r1 = await relink_backing(db, b.id)
    r2 = await relink_backing(db, b.id)
    assert r1.linked == 1
    assert len(r1.changed_ids) == 1
    assert r2.linked == 1 and r2.changed_ids == []


@pytest.mark.asyncio
async def test_second_pass_returns_no_changed_ids(db_session):
    db = db_session
    app, _ = await _immich(db)
    b = await _backing(db, [_map(app)])
    await _seed(db, b, f"{BASE}/upload/u1/ab/cd/IMG_1.jpg")
    await relink_backing(db, b.id)
    r = await relink_backing(db, b.id)
    assert r.changed_ids == [] and r.linked == 1


@pytest.mark.asyncio
async def test_first_mapping_wins(db_session):
    db = db_session
    app1, a1 = await _immich(db)
    app2, a2 = await _immich(db)
    b = await _backing(db, [_map(app1), _map(app2)])
    p = f"{BASE}/upload/u1/ab/cd/IMG_1.jpg"
    await _seed(db, b, p)
    await relink_backing(db, b.id)
    assert (await _roles(db, b))[p].linked_entry_id == a1.id


@pytest.mark.asyncio
async def test_backing_sources_for_app_and_backing(db_session):
    db = db_session
    app, _ = await _immich(db)
    b = await _backing(db, [_map(app)])
    other = await _src(db, "local", "plain")
    assert await backing_sources_for(db, app.id) == [b.id]
    assert await backing_sources_for(db, b.id) == [b.id]
    assert await backing_sources_for(db, other.id) == []


@pytest.mark.asyncio
async def test_logical_view_source_ids(db_session):
    db = db_session
    app, _ = await _immich(db)
    await _backing(db, [_map(app)])
    assert app.id in await logical_view_source_ids(db)


@pytest.mark.asyncio
async def test_paperless_thumbnail_long_id(db_session):
    db = db_session
    app = await _src(db, "paperless", "Paper")
    doc = await _seed(db, app, f"/doc/{_u()}", domain_metadata={
        "paperless_id": 12345678, "media_filename": "12345678.pdf"})
    root = "/host/mnt/paperless/media/documents"
    b = await _backing(db, [_map(app, remote="", local=root)])
    p = f"{root}/thumbnails/12345678.webp"
    await _seed(db, b, p)
    await relink_backing(db, b.id)
    e = (await _roles(db, b))[p]
    assert e.link_role == "thumbnail" and e.linked_entry_id == doc.id


@pytest.mark.asyncio
async def test_immich_sidecar_without_original_extension(db_session):
    db = db_session
    app, _ = await _immich(db)
    b = await _backing(db, [_map(app)])
    p = f"{BASE}/upload/u1/ab/cd/IMG_1.xmp"
    await _seed(db, b, p)
    await relink_backing(db, b.id)
    assert (await _roles(db, b))[p].link_role == "sidecar"


@pytest.mark.asyncio
async def test_immich_sidecar_ext_form_wins_over_stem_form(db_session):
    db = db_session
    app, _ = await _immich(db)
    b = await _backing(db, [_map(app)])
    both = f"{BASE}/upload/u1/ab/cd/IMG_1.jpg.xmp"
    stem = f"{BASE}/upload/u1/ab/cd/IMG_1.xmp"
    await _seed(db, b, both)
    await _seed(db, b, stem)
    await relink_backing(db, b.id)
    got = await _roles(db, b)
    assert got[both].link_role == "sidecar"
    assert got[stem].link_role is None


@pytest.mark.asyncio
async def test_immich_deleted_ext_sidecar_does_not_block_stem_form(db_session):
    db = db_session
    app, _ = await _immich(db)
    b = await _backing(db, [_map(app)])
    gone = f"{BASE}/upload/u1/ab/cd/IMG_1.jpg.xmp"
    stem = f"{BASE}/upload/u1/ab/cd/IMG_1.xmp"
    await _seed(db, b, gone, is_deleted=True)
    await _seed(db, b, stem)
    await relink_backing(db, b.id)
    assert (await _roles(db, b))[stem].link_role == "sidecar"


@pytest.mark.asyncio
async def test_relink_and_flush_chunks_mark_dirty(db_session, monkeypatch):
    from akashic.services import mirror_links as ml

    db = db_session
    ids = [uuid.uuid4() for _ in range(25)]
    async def fake_relink(_db, _bid):
        return ml.LinkPassResult(ids, 25)
    monkeypatch.setattr(ml, "relink_backing", fake_relink)
    monkeypatch.setattr(ml, "_MARK_DIRTY_CHUNK", 10)
    seen = []
    async def fake_mark(_sid, chunk):
        seen.append(list(chunk))
    flush = AsyncMock(return_value=0)
    monkeypatch.setattr(ml.meili_indexer, "mark_dirty", fake_mark)
    monkeypatch.setattr(ml.meili_indexer, "flush", flush)
    assert await ml.relink_and_flush(db, uuid.uuid4()) is True
    assert [len(c) for c in seen] == [10, 10, 5]
    assert [i for c in seen for i in c] == ids
    assert flush.await_count == 3


@pytest.mark.asyncio
async def test_relink_after_scan_marks_and_flushes(db_session):
    db = db_session
    app, _ = await _immich(db)
    b = await _backing(db, [_map(app)])
    e = await _seed(db, b, f"{BASE}/upload/u1/ab/cd/IMG_1.jpg")
    eid, bid, aid = e.id, b.id, app.id
    await db.commit()
    with patch("akashic.services.mirror_links.meili_indexer.mark_dirty", new=AsyncMock()) as md, \
         patch("akashic.services.mirror_links.meili_indexer.flush",
               new=AsyncMock(side_effect=[1, 1, 0])) as fl:
        await relink_after_scan(db, aid)
    md.assert_awaited_once_with(bid, [eid])
    assert fl.await_count == 3


@pytest.mark.asyncio
async def test_relink_after_scan_isolates_bad_backing(db_session):
    db = db_session
    app, _ = await _immich(db)
    good = await _backing(db, [_map(app)])
    bad = await _backing(db, [_map(app), {
        "source_id": str(uuid.uuid4()), "remote_prefix": "", "local_prefix": "/x"}])
    e = await _seed(db, good, f"{BASE}/upload/u1/ab/cd/IMG_1.jpg")
    eid, gid, aid = e.id, good.id, app.id
    await db.commit()
    with patch("akashic.services.mirror_links.meili_indexer.mark_dirty", new=AsyncMock()) as md, \
         patch("akashic.services.mirror_links.meili_indexer.flush", new=AsyncMock(return_value=0)):
        await relink_after_scan(db, aid)
    md.assert_awaited_once_with(gid, [eid])


@pytest.mark.asyncio
async def test_relink_after_scan_survives_generic_error(db_session, monkeypatch):
    from akashic.services import mirror_links as ml

    db = db_session
    app, _ = await _immich(db)
    first = await _backing(db, [_map(app)])
    second = await _backing(db, [_map(app)])
    await db.commit()
    done = []
    async def fake(_db, bid):
        if bid == first.id:
            raise RuntimeError("boom")
        done.append(bid)
        return True
    monkeypatch.setattr(ml, "backing_sources_for", AsyncMock(return_value=[first.id, second.id]))
    monkeypatch.setattr(ml, "relink_and_flush", fake)
    await ml.relink_after_scan(db, app.id)
    assert done == [second.id]
