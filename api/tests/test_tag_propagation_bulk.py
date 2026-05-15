"""Bulk tag-inheritance propagation (v0.29.2 Part F).

Pre-fix the ingest hot path called ``propagate_to_new_entry`` once
per new entry — N+1 SELECTs per batch. The bulk version computes
the entire fan-out in 1 SELECT + 1 INSERT per source via PostgreSQL
``unnest(uuid[], text[])``.

Covers:

  * Bulk call against an empty list is a no-op (no SQL emitted).
  * Multiple new entries under one tagged ancestor each pick up the
    ancestor's tags.
  * Entries under DIFFERENT tagged ancestors get only their own
    ancestor's tags (no cross-contamination).
  * Mixed-source input is grouped into one query per source.
  * Singular ``propagate_to_new_entry`` still works as a thin wrapper.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.tag import EntryTag
from akashic.models.user import User
from akashic.services.tag_inheritance import (
    apply_tag,
    propagate_to_new_entries,
    propagate_to_new_entry,
)


async def _seed_user(db) -> User:
    u = User(
        id=uuid.uuid4(), username=f"u-{uuid.uuid4().hex[:6]}",
        email="x@e", password_hash="x", role="admin",
    )
    db.add(u)
    await db.flush()
    return u


async def _seed_source(db) -> Source:
    s = Source(
        id=uuid.uuid4(), name=f"s-{uuid.uuid4().hex[:6]}",
        type="local", connection_config={"path": "/tmp"},
    )
    db.add(s)
    await db.flush()
    return s


def _split_path(path: str) -> tuple[str, str]:
    """Return (parent_path, name) from a posix-style absolute path."""
    if path == "/" or "/" not in path:
        return "", path or "/"
    parent, _, name = path.rpartition("/")
    return parent or "/", name


async def _seed_directory(db, src, path) -> Entry:
    parent, name = _split_path(path)
    e = Entry(
        id=uuid.uuid4(), source_id=src.id, path=path,
        kind="directory", parent_path=parent, name=name,
    )
    db.add(e)
    await db.flush()
    return e


async def _seed_file(db, src, path) -> Entry:
    parent, name = _split_path(path)
    e = Entry(
        id=uuid.uuid4(), source_id=src.id, path=path,
        kind="file", parent_path=parent, name=name,
    )
    db.add(e)
    await db.flush()
    return e


@pytest.mark.asyncio
async def test_propagate_empty_list_is_noop(db_session):
    # Should not error and not touch the DB.
    await propagate_to_new_entries(db_session, new_entries=[])


@pytest.mark.asyncio
async def test_bulk_propagation_to_many_children_under_one_ancestor(
    db_session,
):
    user = await _seed_user(db_session)
    src = await _seed_source(db_session)
    parent = await _seed_directory(db_session, src, "/data/projects")
    await db_session.commit()
    # Tag the parent directory with two tags.
    await apply_tag(db_session, entry_id=parent.id, tag="confidential", user_id=user.id)
    await apply_tag(db_session, entry_id=parent.id, tag="reviewed", user_id=user.id)
    await db_session.commit()

    # Three new files arrive via ingest under the tagged ancestor.
    new_a = await _seed_file(db_session, src, "/data/projects/a.txt")
    new_b = await _seed_file(db_session, src, "/data/projects/b.txt")
    new_c = await _seed_file(db_session, src, "/data/projects/sub/c.txt")
    await db_session.commit()

    await propagate_to_new_entries(
        db_session,
        new_entries=[
            (new_a.id, src.id, new_a.path),
            (new_b.id, src.id, new_b.path),
            (new_c.id, src.id, new_c.path),
        ],
    )
    await db_session.commit()

    # Each new entry should now carry both inherited tags.
    for entry_id in (new_a.id, new_b.id, new_c.id):
        rows = (await db_session.execute(
            select(EntryTag.tag).where(EntryTag.entry_id == entry_id)
        )).scalars().all()
        assert sorted(rows) == ["confidential", "reviewed"], \
            f"entry {entry_id} got tags {rows}"


@pytest.mark.asyncio
async def test_bulk_propagation_does_not_cross_contaminate_siblings(
    db_session,
):
    """Two tagged dirs at different paths; new entries under each must
    only inherit their own ancestor's tags."""
    user = await _seed_user(db_session)
    src = await _seed_source(db_session)
    dir_x = await _seed_directory(db_session, src, "/x")
    dir_y = await _seed_directory(db_session, src, "/y")
    await db_session.commit()
    await apply_tag(db_session, entry_id=dir_x.id, tag="x-only", user_id=user.id)
    await apply_tag(db_session, entry_id=dir_y.id, tag="y-only", user_id=user.id)
    await db_session.commit()

    file_x = await _seed_file(db_session, src, "/x/file.txt")
    file_y = await _seed_file(db_session, src, "/y/file.txt")
    await db_session.commit()

    await propagate_to_new_entries(
        db_session,
        new_entries=[
            (file_x.id, src.id, file_x.path),
            (file_y.id, src.id, file_y.path),
        ],
    )
    await db_session.commit()

    tags_x = (await db_session.execute(
        select(EntryTag.tag).where(EntryTag.entry_id == file_x.id)
    )).scalars().all()
    tags_y = (await db_session.execute(
        select(EntryTag.tag).where(EntryTag.entry_id == file_y.id)
    )).scalars().all()
    assert tags_x == ["x-only"]
    assert tags_y == ["y-only"]


@pytest.mark.asyncio
async def test_singular_wrapper_still_works(db_session):
    """The kept ``propagate_to_new_entry`` shim must still inherit
    one entry's tags — non-ingest callers (admin tag-apply, move-
    rebalancer) depend on it."""
    user = await _seed_user(db_session)
    src = await _seed_source(db_session)
    parent = await _seed_directory(db_session, src, "/wrap")
    await db_session.commit()
    await apply_tag(db_session, entry_id=parent.id, tag="t1", user_id=user.id)
    await db_session.commit()

    f = await _seed_file(db_session, src, "/wrap/x.txt")
    await db_session.commit()

    await propagate_to_new_entry(
        db_session, entry_id=f.id, source_id=src.id, path=f.path,
    )
    await db_session.commit()

    tags = (await db_session.execute(
        select(EntryTag.tag).where(EntryTag.entry_id == f.id)
    )).scalars().all()
    assert tags == ["t1"]


@pytest.mark.asyncio
async def test_mixed_source_input_groups_per_source(db_session):
    """A bulk call spanning two sources must propagate each source's
    ancestor tags independently. No cross-source ancestor-matching."""
    user = await _seed_user(db_session)
    src_a = await _seed_source(db_session)
    src_b = await _seed_source(db_session)
    dir_a = await _seed_directory(db_session, src_a, "/aa")
    dir_b = await _seed_directory(db_session, src_b, "/bb")
    await db_session.commit()
    await apply_tag(db_session, entry_id=dir_a.id, tag="a-tag", user_id=user.id)
    await apply_tag(db_session, entry_id=dir_b.id, tag="b-tag", user_id=user.id)
    await db_session.commit()

    fa = await _seed_file(db_session, src_a, "/aa/x.txt")
    fb = await _seed_file(db_session, src_b, "/bb/y.txt")
    await db_session.commit()

    await propagate_to_new_entries(
        db_session,
        new_entries=[
            (fa.id, src_a.id, fa.path),
            (fb.id, src_b.id, fb.path),
        ],
    )
    await db_session.commit()

    tags_a = (await db_session.execute(
        select(EntryTag.tag).where(EntryTag.entry_id == fa.id)
    )).scalars().all()
    tags_b = (await db_session.execute(
        select(EntryTag.tag).where(EntryTag.entry_id == fb.id)
    )).scalars().all()
    assert tags_a == ["a-tag"]
    assert tags_b == ["b-tag"]
