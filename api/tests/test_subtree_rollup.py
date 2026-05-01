"""Phase 9 — bottom-up subtree aggregate rollup.

Synthesises a small directory tree, runs `rollup_source`, and asserts
each directory's aggregates match a hand-computed sum of its
descendants.
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.services.subtree_rollup import rollup_source


async def _add_entry(
    db: AsyncSession, *, source_id, kind, parent_path, path, size=None,
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    db.add(Entry(
        id=entry_id, source_id=source_id, kind=kind,
        parent_path=parent_path, path=path,
        name=path.rsplit("/", 1)[-1] or "/",
        size_bytes=size,
    ))
    await db.commit()
    return entry_id


@pytest.mark.asyncio
async def test_rollup_computes_subtree_aggregates(db_session: AsyncSession):
    """Tree:
        /
        ├── a (dir)
        │   ├── x.txt   (100)
        │   └── y.txt   (200)
        └── b (dir)
            └── c (dir)
                └── z.txt (1000)
    """
    src = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add(src)
    await db_session.commit()

    a = await _add_entry(db_session, source_id=src.id, kind="directory", parent_path="/", path="/a")
    b = await _add_entry(db_session, source_id=src.id, kind="directory", parent_path="/", path="/b")
    c = await _add_entry(db_session, source_id=src.id, kind="directory", parent_path="/b", path="/b/c")
    await _add_entry(db_session, source_id=src.id, kind="file", parent_path="/a", path="/a/x.txt", size=100)
    await _add_entry(db_session, source_id=src.id, kind="file", parent_path="/a", path="/a/y.txt", size=200)
    await _add_entry(db_session, source_id=src.id, kind="file", parent_path="/b/c", path="/b/c/z.txt", size=1000)

    updated = await rollup_source(db_session, src.id)
    await db_session.commit()
    assert updated == 3  # 3 directories

    # Need fresh reads — the rollup updated rows directly.
    a_row = (await db_session.execute(select(Entry).where(Entry.id == a))).scalar_one()
    b_row = (await db_session.execute(select(Entry).where(Entry.id == b))).scalar_one()
    c_row = (await db_session.execute(select(Entry).where(Entry.id == c))).scalar_one()

    assert a_row.subtree_size_bytes == 300
    assert a_row.subtree_file_count == 2
    assert a_row.subtree_dir_count == 0

    assert c_row.subtree_size_bytes == 1000
    assert c_row.subtree_file_count == 1
    assert c_row.subtree_dir_count == 0

    # b rolls up c's already-rolled-up totals.
    assert b_row.subtree_size_bytes == 1000
    assert b_row.subtree_file_count == 1
    assert b_row.subtree_dir_count == 1


@pytest.mark.asyncio
async def test_rollup_handles_empty_directory(db_session: AsyncSession):
    """An empty dir has all-zero aggregates, not NULL."""
    src = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add(src)
    await db_session.commit()

    empty = await _add_entry(
        db_session, source_id=src.id, kind="directory",
        parent_path="/", path="/empty",
    )
    await rollup_source(db_session, src.id)
    await db_session.commit()

    row = (await db_session.execute(select(Entry).where(Entry.id == empty))).scalar_one()
    assert row.subtree_size_bytes == 0
    assert row.subtree_file_count == 0
    assert row.subtree_dir_count == 0


@pytest.mark.asyncio
async def test_rollup_ignores_deleted(db_session: AsyncSession):
    """Tombstoned entries don't contribute to subtree aggregates."""
    src = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add(src)
    await db_session.commit()

    parent = await _add_entry(
        db_session, source_id=src.id, kind="directory", parent_path="/", path="/p",
    )
    await _add_entry(
        db_session, source_id=src.id, kind="file",
        parent_path="/p", path="/p/keep.txt", size=500,
    )
    deleted_id = await _add_entry(
        db_session, source_id=src.id, kind="file",
        parent_path="/p", path="/p/gone.txt", size=99999,
    )
    # Tombstone the second file.
    deleted_row = (await db_session.execute(select(Entry).where(Entry.id == deleted_id))).scalar_one()
    deleted_row.is_deleted = True
    await db_session.commit()

    await rollup_source(db_session, src.id)
    await db_session.commit()

    parent_row = (await db_session.execute(select(Entry).where(Entry.id == parent))).scalar_one()
    assert parent_row.subtree_size_bytes == 500  # 99999 ignored
    assert parent_row.subtree_file_count == 1
