"""Viewers read every source (no per-source grants); admin writes stay admin-only."""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import create_access_token
from akashic.models.entry import Entry
from akashic.models.fs_person import FsBinding, FsPerson
from akashic.models.source import Source
from akashic.models.user import User


async def _seed(db: AsyncSession):
    viewer = User(id=uuid.uuid4(), username="vw", email="vw@e", password_hash="x", role="viewer")
    srcs = [Source(id=uuid.uuid4(), name=n, type="local", connection_config={}) for n in ("s1", "s2")]
    db.add_all([viewer, *srcs])
    await db.commit()
    for s in srcs:
        db.add(Entry(
            id=uuid.uuid4(), source_id=s.id, kind="file", parent_path="/",
            path="/f.txt", name="f.txt", extension="txt",
            viewable_by_read=["posix:uid:1000"], viewable_by_write=[], viewable_by_delete=[],
        ))
    await db.commit()
    return viewer, srcs, {"Authorization": f"Bearer {create_access_token({'sub': str(viewer.id)})}"}


@pytest.mark.asyncio
async def test_viewer_sees_all_sources_everywhere(client: AsyncClient, db_session: AsyncSession):
    _, srcs, h = await _seed(db_session)
    r = await client.get("/api/v1/sources", headers=h)
    assert r.status_code == 200 and {s["id"] for s in r.json()} == {str(s.id) for s in srcs}
    for s in srcs:
        assert (await client.get(f"/api/v1/sources/{s.id}", headers=h)).status_code == 200
        r = await client.get(f"/api/v1/browse?source_id={s.id}", headers=h)
        assert r.status_code == 200 and len(r.json()["entries"]) == 1
    assert (await client.get("/api/v1/dashboard/summary", headers=h)).status_code == 200
    r = await client.get("/api/v1/entries", headers=h)
    assert r.status_code == 200 and len(r.json()) == 2


@pytest.mark.asyncio
async def test_viewer_search_sql_all_sources(client: AsyncClient, db_session: AsyncSession):
    _, _, h = await _seed(db_session)
    r = await client.get("/api/v1/search", params={"q": "f.txt", "mode": "glob"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2


@pytest.mark.asyncio
async def test_viewer_403_on_admin_writes(client: AsyncClient, db_session: AsyncSession):
    _, srcs, h = await _seed(db_session)
    assert (await client.post(
        "/api/v1/sources", headers=h,
        json={"name": "x", "type": "local", "connection_config": {"path": "/tmp"}},
    )).status_code == 403
    assert (await client.delete(f"/api/v1/sources/{srcs[0].id}", headers=h)).status_code == 403
    assert (await client.post(f"/api/v1/scans?source_id={srcs[0].id}", headers=h)).status_code in (403, 405, 422)


@pytest.mark.asyncio
async def test_viewer_acl_filtered_when_enforced(client: AsyncClient, db_session: AsyncSession, rt_set):
    rt_set(browse_enforce_perms=True)
    viewer, srcs, h = await _seed(db_session)
    person = FsPerson(id=uuid.uuid4(), user_id=viewer.id, label="t")
    db_session.add(person)
    await db_session.commit()
    db_session.add(FsBinding(
        id=uuid.uuid4(), fs_person_id=person.id, source_id=srcs[0].id,
        identity_type="posix_uid", identifier="9999", groups=[], groups_source="manual",
    ))
    await db_session.commit()
    # Sources still all visible, but files are ACL-trimmed.
    r = await client.get("/api/v1/sources", headers=h)
    assert len(r.json()) == 2
    r = await client.get(f"/api/v1/browse?source_id={srcs[0].id}", headers=h)
    assert r.json()["entries"] == []
    r = await client.get("/api/v1/duplicates", headers=h)
    assert r.status_code == 200
