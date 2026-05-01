"""Phase A — `/api/storage/tree` recursive subtree expansion.

The endpoint feeds the WinDirStat-style nested treemap. Tests cover:
- nested-children shape matches the seeded hierarchy
- node-budget caps the response and `truncated` flips
- per-parent <other> rectangles account for budget-pruned size
- min_bytes filters tiny leaves out
- perm-trim drops hidden entries when BROWSE_ENFORCE_PERMS is on
- empty / missing-root paths return null root, not 500
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import create_access_token
from akashic.config import settings
from akashic.models.entry import Entry
from akashic.models.fs_person import FsBinding, FsPerson
from akashic.models.source import Source
from akashic.models.user import SourcePermission, User
from akashic.services.subtree_rollup import rollup_source


async def _admin_token(db_session: AsyncSession) -> tuple[User, str]:
    user = User(id=uuid.uuid4(), username="adm", email="a@e", password_hash="x", role="admin")
    db_session.add(user)
    await db_session.commit()
    return user, create_access_token({"sub": str(user.id)})


async def _add_entry(
    db: AsyncSession, *, source_id, kind, parent_path, path,
    size=None, name=None, viewable=None,
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    db.add(Entry(
        id=entry_id, source_id=source_id, kind=kind,
        parent_path=parent_path, path=path,
        name=name or (path.rsplit("/", 1)[-1] or "/"),
        size_bytes=size,
        viewable_by_read=viewable,
    ))
    await db.commit()
    return entry_id


@pytest.mark.asyncio
async def test_tree_returns_nested_hierarchy(
    client: AsyncClient, db_session: AsyncSession,
):
    """Seed a small tree, fetch /tree, assert the JSON nests properly."""
    _, token = await _admin_token(db_session)
    src = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add(src)
    await db_session.commit()

    # /
    # ├── a/
    # │   ├── x.txt   (100)
    # │   └── y.txt   (200)
    # └── b/
    #     └── c/
    #         └── z.bin (1000)
    await _add_entry(db_session, source_id=src.id, kind="directory", parent_path="", path="/")
    await _add_entry(db_session, source_id=src.id, kind="directory", parent_path="/", path="/a")
    await _add_entry(db_session, source_id=src.id, kind="directory", parent_path="/", path="/b")
    await _add_entry(db_session, source_id=src.id, kind="directory", parent_path="/b", path="/b/c")
    await _add_entry(db_session, source_id=src.id, kind="file", parent_path="/a", path="/a/x.txt", size=100)
    await _add_entry(db_session, source_id=src.id, kind="file", parent_path="/a", path="/a/y.txt", size=200)
    await _add_entry(db_session, source_id=src.id, kind="file", parent_path="/b/c", path="/b/c/z.bin", size=1000)
    await rollup_source(db_session, src.id)
    await db_session.commit()

    body = (await client.get(
        f"/api/storage/tree?source_id={src.id}&path=/&max_nodes=100",
        headers={"Authorization": f"Bearer {token}"},
    )).json()

    assert body["source_id"] == str(src.id)
    assert body["truncated"] is False
    root = body["root"]
    assert root is not None
    assert root["path"] == "/"

    # Top-level kids are /b (1000) + /a (300), sorted desc.
    top_names = [c["name"] for c in root["children"]]
    assert top_names[0] == "b"
    assert top_names[1] == "a"

    # /b/c/z.bin must surface as a leaf two levels deep.
    b = next(c for c in root["children"] if c["name"] == "b")
    c_dir = next(c for c in b["children"] if c["name"] == "c")
    leaves = [c for c in c_dir["children"] if c["kind"] == "file"]
    assert len(leaves) == 1 and leaves[0]["name"] == "z.bin"
    assert leaves[0]["size_bytes"] == 1000


@pytest.mark.asyncio
async def test_tree_node_budget_truncates_and_synthesises_other(
    client: AsyncClient, db_session: AsyncSession,
):
    """When max_nodes < total nodes, the response drops smallest leaves
    and the remaining directories show <other> rectangles standing in
    for the missing size — so the visual scale stays honest."""
    _, token = await _admin_token(db_session)
    src = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add(src)
    await db_session.commit()

    await _add_entry(db_session, source_id=src.id, kind="directory", parent_path="", path="/")
    await _add_entry(db_session, source_id=src.id, kind="directory", parent_path="/", path="/d")
    # 10 files under /d of decreasing size.
    for i in range(10):
        await _add_entry(
            db_session, source_id=src.id, kind="file",
            parent_path="/d", path=f"/d/f{i}", size=1000 - i * 50,
        )
    await rollup_source(db_session, src.id)
    await db_session.commit()

    # Budget too small to hold every leaf.
    body = (await client.get(
        f"/api/storage/tree?source_id={src.id}&path=/&max_nodes=5",
        headers={"Authorization": f"Bearer {token}"},
    )).json()

    assert body["truncated"] is True
    root = body["root"]
    d = next(c for c in root["children"] if c["name"] == "d")
    # /d's children include real leaves + a synthetic <other> covering
    # the dropped tail.
    other = [c for c in d["children"] if c["kind"] == "other"]
    assert len(other) == 1
    # The total of d's children's sizes equals d's recorded subtree.
    kids_sum = sum(c["size_bytes"] for c in d["children"])
    assert kids_sum == d["size_bytes"]


@pytest.mark.asyncio
async def test_tree_min_bytes_filters_small_leaves(
    client: AsyncClient, db_session: AsyncSession,
):
    """Files smaller than min_bytes are filtered at SQL level and
    surface as missing size, accounted for via <other>."""
    _, token = await _admin_token(db_session)
    src = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add(src)
    await db_session.commit()

    await _add_entry(db_session, source_id=src.id, kind="directory", parent_path="", path="/")
    await _add_entry(db_session, source_id=src.id, kind="file", parent_path="/", path="/big", size=10_000)
    await _add_entry(db_session, source_id=src.id, kind="file", parent_path="/", path="/tiny", size=1)
    await rollup_source(db_session, src.id)
    await db_session.commit()

    body = (await client.get(
        f"/api/storage/tree?source_id={src.id}&path=/&min_bytes=100",
        headers={"Authorization": f"Bearer {token}"},
    )).json()

    root = body["root"]
    file_names = [c["name"] for c in root["children"] if c["kind"] == "file"]
    assert "big" in file_names
    assert "tiny" not in file_names


@pytest.mark.asyncio
async def test_tree_perm_filter_hides_unviewable(
    client: AsyncClient, db_session: AsyncSession, monkeypatch,
):
    """When BROWSE_ENFORCE_PERMS is on and the user has bindings, the
    recursive CTE filters hidden entries out at SQL — the response
    never sees them."""
    monkeypatch.setattr(settings, "browse_enforce_perms", True)

    src = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    user = User(id=uuid.uuid4(), username="alice", email="a@e", password_hash="x", role="user")
    db_session.add_all([src, user])
    await db_session.commit()

    # Source-level grant + a binding that gives uid 1000 only.
    db_session.add(SourcePermission(user_id=user.id, source_id=src.id, access_level="read"))
    person = FsPerson(id=uuid.uuid4(), user_id=user.id, label="t")
    db_session.add(person)
    await db_session.commit()
    db_session.add(FsBinding(
        id=uuid.uuid4(), fs_person_id=person.id, source_id=src.id,
        identity_type="posix_uid", identifier="1000", groups=[],
        groups_source="manual",
    ))
    await db_session.commit()

    await _add_entry(db_session, source_id=src.id, kind="directory", parent_path="", path="/", viewable=["*"])
    await _add_entry(
        db_session, source_id=src.id, kind="file", parent_path="/", path="/visible",
        size=10, viewable=["posix:uid:1000"],
    )
    await _add_entry(
        db_session, source_id=src.id, kind="file", parent_path="/", path="/hidden",
        size=10, viewable=["posix:uid:9999"],
    )
    await rollup_source(db_session, src.id)
    await db_session.commit()

    token = create_access_token({"sub": str(user.id)})
    body = (await client.get(
        f"/api/storage/tree?source_id={src.id}&path=/",
        headers={"Authorization": f"Bearer {token}"},
    )).json()

    assert body["enforced"] is True
    file_names = [c["name"] for c in body["root"]["children"] if c["kind"] == "file"]
    assert "visible" in file_names
    assert "hidden" not in file_names


@pytest.mark.asyncio
async def test_tree_missing_root_returns_null(
    client: AsyncClient, db_session: AsyncSession,
):
    _, token = await _admin_token(db_session)
    src = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add(src)
    await db_session.commit()

    body = (await client.get(
        f"/api/storage/tree?source_id={src.id}&path=/nope",
        headers={"Authorization": f"Bearer {token}"},
    )).json()
    assert body["root"] is None
    assert body["node_count"] == 0


@pytest.mark.asyncio
async def test_tree_synthesises_root_when_connector_has_no_literal_slash(
    client: AsyncClient, db_session: AsyncSession,
):
    """SMB / SSH / S3 connectors emit top-level entries with
    `parent_path = '/'` but no literal `path = '/'` row. The recursive
    CTE used to anchor on `path = '/'` and return zero rows for these
    sources — silently empty treemap.

    With the secondary anchor in place, the CTE picks up the
    de-facto first-level entries, the response synthesises a root
    node, and the children attach to it.
    """
    _, token = await _admin_token(db_session)
    src = Source(id=uuid.uuid4(), name="smb", type="smb", connection_config={})
    db_session.add(src)
    await db_session.commit()

    # SMB-style layout: no path='/' row, top-level dirs are bare names
    # with parent_path='/'.
    await _add_entry(
        db_session, source_id=src.id, kind="directory",
        parent_path="/", path="Movies",
    )
    await _add_entry(
        db_session, source_id=src.id, kind="directory",
        parent_path="/", path="Music",
    )
    await _add_entry(
        db_session, source_id=src.id, kind="file",
        parent_path="Movies", path="Movies/big.mkv", size=4000,
    )
    await _add_entry(
        db_session, source_id=src.id, kind="file",
        parent_path="Music", path="Music/song.mp3", size=200,
    )
    await rollup_source(db_session, src.id)
    await db_session.commit()

    body = (await client.get(
        f"/api/storage/tree?source_id={src.id}&path=/&max_nodes=100",
        headers={"Authorization": f"Bearer {token}"},
    )).json()

    root = body["root"]
    assert root is not None, "synthetic root should appear when no path='/' exists"
    assert root["path"] == "/"
    assert root["kind"] == "directory"

    # Top-level kids attached to the synthetic root, sorted by size.
    names = [c["name"] for c in root["children"]]
    assert names == ["Movies", "Music"]

    # Synthetic root's size_bytes is the sum of its children.
    assert root["size_bytes"] == 4200

    # Recursion still descends — Movies' file is in there.
    movies = root["children"][0]
    assert any(c["name"] == "big.mkv" for c in movies["children"])
