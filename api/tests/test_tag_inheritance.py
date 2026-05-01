"""Phase C — directory-applied tag inheritance.

Materialised inheritance: applying a tag to a directory writes one
`entry_tags` row on every descendant with `inherited_from_entry_id`
pointing at the source. The same-shaped row is materialised on ingest
when a new entry appears under a tagged ancestor. Removing the source
direct-row cascades the inherited copies; descendants that were also
*directly* tagged keep their direct rows.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.tag import EntryTag
from akashic.models.user import User
from akashic.services.tag_inheritance import (
    apply_tag,
    get_tags_for_entry,
    propagate_to_new_entry,
    rebalance_on_move,
    remove_tag,
)


async def _add_entry(
    db: AsyncSession,
    *,
    source_id: uuid.UUID,
    kind: str,
    parent_path: str,
    path: str,
) -> Entry:
    entry = Entry(
        id=uuid.uuid4(),
        source_id=source_id,
        kind=kind,
        parent_path=parent_path,
        path=path,
        name=path.rsplit("/", 1)[-1] or "/",
        first_seen_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def _bootstrap(db: AsyncSession) -> tuple[User, Source]:
    user = User(
        id=uuid.uuid4(), username=f"u-{uuid.uuid4().hex[:6]}",
        email="u@e", password_hash="x", role="admin",
    )
    src = Source(
        id=uuid.uuid4(), name=f"s-{uuid.uuid4().hex[:6]}",
        type="local", connection_config={},
    )
    db.add(user)
    db.add(src)
    await db.commit()
    return user, src


async def _tag_rows(db: AsyncSession, entry_id: uuid.UUID) -> list[EntryTag]:
    res = await db.execute(
        select(EntryTag).where(EntryTag.entry_id == entry_id)
        .order_by(EntryTag.tag, EntryTag.inherited_from_entry_id)
    )
    return list(res.scalars().all())


@pytest.mark.asyncio
async def test_apply_to_directory_materialises_inheritance(db_session: AsyncSession):
    """Tag on /a propagates to /a/x.txt and /a/sub/y.txt."""
    user, src = await _bootstrap(db_session)
    a = await _add_entry(db_session, source_id=src.id, kind="directory",
                         parent_path="/", path="/a")
    sub = await _add_entry(db_session, source_id=src.id, kind="directory",
                           parent_path="/a", path="/a/sub")
    x = await _add_entry(db_session, source_id=src.id, kind="file",
                         parent_path="/a", path="/a/x.txt")
    y = await _add_entry(db_session, source_id=src.id, kind="file",
                         parent_path="/a/sub", path="/a/sub/y.txt")

    affected = await apply_tag(
        db_session, entry_id=a.id, tag="quarterly", user_id=user.id,
    )
    await db_session.commit()

    assert a.id in affected
    assert x.id in affected
    assert y.id in affected
    assert sub.id in affected

    # Directory: one direct row.
    rows = await _tag_rows(db_session, a.id)
    assert len(rows) == 1
    assert rows[0].inherited_from_entry_id is None

    # Descendants: one inherited row each, pointing at /a.
    for desc in (x, y, sub):
        rows = await _tag_rows(db_session, desc.id)
        assert len(rows) == 1
        assert rows[0].inherited_from_entry_id == a.id


@pytest.mark.asyncio
async def test_propagate_to_new_entry_picks_up_ancestor_tags(db_session: AsyncSession):
    """A new entry under a tagged ancestor inherits the tag at ingest time."""
    user, src = await _bootstrap(db_session)
    a = await _add_entry(db_session, source_id=src.id, kind="directory",
                         parent_path="/", path="/a")
    await apply_tag(db_session, entry_id=a.id, tag="quarterly", user_id=user.id)
    await db_session.commit()

    # Simulate a new file landing inside /a after the tag was applied.
    z = await _add_entry(db_session, source_id=src.id, kind="file",
                         parent_path="/a", path="/a/z.txt")
    await propagate_to_new_entry(
        db_session, entry_id=z.id, source_id=src.id, path=z.path,
    )
    await db_session.commit()

    rows = await _tag_rows(db_session, z.id)
    assert len(rows) == 1
    assert rows[0].tag == "quarterly"
    assert rows[0].inherited_from_entry_id == a.id


@pytest.mark.asyncio
async def test_direct_and_inherited_coexist_for_same_tag(db_session: AsyncSession):
    """A descendant directly tagged with T (already inheriting T) keeps
    both rows; remove of inherited origin only drops the inherited one."""
    user, src = await _bootstrap(db_session)
    a = await _add_entry(db_session, source_id=src.id, kind="directory",
                         parent_path="/", path="/a")
    x = await _add_entry(db_session, source_id=src.id, kind="file",
                         parent_path="/a", path="/a/x.txt")

    await apply_tag(db_session, entry_id=a.id, tag="T", user_id=user.id)
    await apply_tag(db_session, entry_id=x.id, tag="T", user_id=user.id)
    await db_session.commit()

    rows = await _tag_rows(db_session, x.id)
    assert len(rows) == 2
    origins = sorted(
        ((r.inherited_from_entry_id is None) for r in rows), reverse=True,
    )
    assert origins == [True, False]  # one direct, one inherited

    # Remove T from /a → inherited row disappears, direct row stays.
    await remove_tag(db_session, entry_id=a.id, tag="T")
    await db_session.commit()

    rows = await _tag_rows(db_session, x.id)
    assert len(rows) == 1
    assert rows[0].inherited_from_entry_id is None


@pytest.mark.asyncio
async def test_remove_tag_cascades_inherited_from_directory(db_session: AsyncSession):
    """Removing T from /a clears inherited-T from every descendant."""
    user, src = await _bootstrap(db_session)
    a = await _add_entry(db_session, source_id=src.id, kind="directory",
                         parent_path="/", path="/a")
    x = await _add_entry(db_session, source_id=src.id, kind="file",
                         parent_path="/a", path="/a/x.txt")
    y = await _add_entry(db_session, source_id=src.id, kind="file",
                         parent_path="/a", path="/a/y.txt")

    await apply_tag(db_session, entry_id=a.id, tag="T", user_id=user.id)
    await db_session.commit()

    await remove_tag(db_session, entry_id=a.id, tag="T")
    await db_session.commit()

    for entry in (a, x, y):
        rows = await _tag_rows(db_session, entry.id)
        assert rows == []


@pytest.mark.asyncio
async def test_apply_does_not_cross_sources(db_session: AsyncSession):
    """A directory in source-A shouldn't inherit-tag entries that
    happen to share its path in source-B (the WHERE on
    `c.source_id = anc.source_id` is what blocks this)."""
    user = User(
        id=uuid.uuid4(), username="x", email="x@e", password_hash="x", role="admin",
    )
    sa = Source(id=uuid.uuid4(), name="A", type="local", connection_config={})
    sb = Source(id=uuid.uuid4(), name="B", type="local", connection_config={})
    db_session.add_all([user, sa, sb])
    await db_session.commit()

    a = await _add_entry(db_session, source_id=sa.id, kind="directory",
                         parent_path="/", path="/a")
    a_x = await _add_entry(db_session, source_id=sa.id, kind="file",
                           parent_path="/a", path="/a/x.txt")
    b_x = await _add_entry(db_session, source_id=sb.id, kind="file",
                           parent_path="/a", path="/a/x.txt")

    await apply_tag(db_session, entry_id=a.id, tag="T", user_id=user.id)
    await db_session.commit()

    assert len(await _tag_rows(db_session, a_x.id)) == 1   # in source A
    assert await _tag_rows(db_session, b_x.id) == []       # in source B


@pytest.mark.asyncio
async def test_apply_skips_deleted_descendants(db_session: AsyncSession):
    """Tombstoned entries don't pick up inherited tags."""
    user, src = await _bootstrap(db_session)
    a = await _add_entry(db_session, source_id=src.id, kind="directory",
                         parent_path="/", path="/a")
    keep = await _add_entry(db_session, source_id=src.id, kind="file",
                            parent_path="/a", path="/a/keep.txt")
    gone = await _add_entry(db_session, source_id=src.id, kind="file",
                            parent_path="/a", path="/a/gone.txt")
    gone.is_deleted = True
    await db_session.commit()

    await apply_tag(db_session, entry_id=a.id, tag="T", user_id=user.id)
    await db_session.commit()

    assert len(await _tag_rows(db_session, keep.id)) == 1
    assert await _tag_rows(db_session, gone.id) == []


@pytest.mark.asyncio
async def test_get_tags_for_entry_returns_origin_metadata(db_session: AsyncSession):
    """Entry-detail drawer needs the source path for the tooltip."""
    user, src = await _bootstrap(db_session)
    a = await _add_entry(db_session, source_id=src.id, kind="directory",
                         parent_path="/", path="/Reports")
    x = await _add_entry(db_session, source_id=src.id, kind="file",
                         parent_path="/Reports", path="/Reports/q3.pdf")
    await apply_tag(db_session, entry_id=a.id, tag="quarterly", user_id=user.id)
    await db_session.commit()

    tags = await get_tags_for_entry(db_session, entry_id=x.id)
    assert len(tags) == 1
    assert tags[0]["tag"] == "quarterly"
    assert tags[0]["inherited"] is True
    assert tags[0]["inherited_from_path"] == "/Reports"


@pytest.mark.asyncio
async def test_rebalance_on_move_drops_and_adds_inherited(db_session: AsyncSession):
    """Move /a/x.txt → /b/x.txt. /a is tagged, /b is untagged → drop.
    Then mark /b tagged and re-rebalance → pickup."""
    user, src = await _bootstrap(db_session)
    a = await _add_entry(db_session, source_id=src.id, kind="directory",
                         parent_path="/", path="/a")
    b = await _add_entry(db_session, source_id=src.id, kind="directory",
                         parent_path="/", path="/b")
    x = await _add_entry(db_session, source_id=src.id, kind="file",
                         parent_path="/a", path="/a/x.txt")

    await apply_tag(db_session, entry_id=a.id, tag="T", user_id=user.id)
    await db_session.commit()
    assert len(await _tag_rows(db_session, x.id)) == 1

    # Pretend the entry moved out of /a's umbrella by rewriting its path
    # in place (the helper takes the new path; the row's actual path
    # stays for this test since we only care about the inherited rows).
    await rebalance_on_move(
        db_session, entry_id=x.id, new_source_id=src.id, new_path="/b/x.txt",
    )
    await db_session.commit()
    assert await _tag_rows(db_session, x.id) == []

    # Now tag /b and rebalance again — picks up.
    await apply_tag(db_session, entry_id=b.id, tag="T2", user_id=user.id)
    await db_session.commit()
    await rebalance_on_move(
        db_session, entry_id=x.id, new_source_id=src.id, new_path="/b/x.txt",
    )
    await db_session.commit()
    rows = await _tag_rows(db_session, x.id)
    assert len(rows) == 1
    assert rows[0].tag == "T2"
    assert rows[0].inherited_from_entry_id == b.id
