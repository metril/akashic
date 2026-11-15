"""/duplicates/{hash}/files and /entries/by-hash apply the same ACL filter."""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import create_access_token
from akashic.models.entry import Entry
from akashic.models.fs_person import FsBinding, FsPerson
from akashic.models.source import Source
from akashic.models.user import User

HASH = "d" * 64


async def _seed(db: AsyncSession, role: str, name: str) -> tuple[dict, dict[str, str]]:
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db.add(source)
    await db.commit()
    ids = {}
    for path, viewers in (("/a", ["posix:uid:1000"]), ("/b", ["posix:uid:2000"]), ("/c", ["*"])):
        e = Entry(
            id=uuid.uuid4(), source_id=source.id, kind="file", parent_path="/", path=path,
            name=path[1:], content_hash=HASH, size_bytes=10,
            viewable_by_read=viewers, viewable_by_write=[], viewable_by_delete=[],
        )
        db.add(e)
        ids[path] = str(e.id)
    user = User(id=uuid.uuid4(), username=name, email=f"{name}@e", password_hash="x", role=role)
    db.add(user)
    await db.commit()
    person = FsPerson(id=uuid.uuid4(), user_id=user.id, label="t")
    db.add(person)
    await db.commit()
    db.add(FsBinding(
        id=uuid.uuid4(), fs_person_id=person.id, source_id=source.id, identity_type="posix_uid",
        identifier="1000", groups=[], groups_source="manual",
    ))
    await db.commit()
    return {"Authorization": f"Bearer {create_access_token({'sub': str(user.id)})}"}, ids


async def _both(client: AsyncClient, headers: dict) -> tuple[set[str], set[str]]:
    dup = await client.get(f"/api/v1/duplicates/{HASH}/files", headers=headers)
    ent = await client.get(f"/api/v1/entries/by-hash/{HASH}", headers=headers)
    assert dup.status_code == 200 and ent.status_code == 200, (dup.text, ent.text)
    return {f["id"] for f in dup.json()["items"]}, {e["id"] for e in ent.json()}


@pytest.mark.asyncio
async def test_acl_bypass_principal_sees_everything_on_both(rt_set, client: AsyncClient, db_session: AsyncSession):
    rt_set(browse_enforce_perms=True)
    headers, ids = await _seed(db_session, "admin", "root")
    dup, ent = await _both(client, headers)
    assert dup == ent == set(ids.values())
    groups = await client.get("/api/v1/duplicates", headers=headers)
    assert groups.json()["items"][0]["count"] == 3


@pytest.mark.asyncio
async def test_non_bypass_principal_is_filtered_on_both(rt_set, client: AsyncClient, db_session: AsyncSession):
    rt_set(browse_enforce_perms=True)
    headers, ids = await _seed(db_session, "viewer", "alice")
    dup, ent = await _both(client, headers)
    assert dup == ent == {ids["/a"], ids["/c"]}
    groups = await client.get("/api/v1/duplicates", headers=headers)
    assert groups.json()["items"][0]["count"] == 2
