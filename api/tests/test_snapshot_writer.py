"""Snapshot writer aggregates correctness.

These tests seed Entry rows with deterministic sizes/extensions/owners/
mtimes and assert the resulting ScanSnapshot row matches. Hot/warm/cold
boundaries are pinned via the optional `now` parameter so the test
isn't time-of-day-flaky.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from akashic.models.entry import Entry
from akashic.models.scan_snapshot import ScanSnapshot
from akashic.models.source import Source
from akashic.services.snapshot_writer import HOT_DAYS, TOP_N, WARM_DAYS, write_snapshot


async def _make_source(db, name="src"):
    src = Source(name=name, type="local", connection_config={"path": "/tmp"}, status="online")
    db.add(src)
    await db.flush()
    await db.refresh(src)
    return src


def _file(source_id, **kw):
    base = dict(
        source_id=source_id,
        kind="file",
        parent_path="/",
        path=f"/{kw.get('name', 'f.bin')}",
        name=kw.get("name", "f.bin"),
        extension=kw.get("extension", "bin"),
        size_bytes=kw.get("size_bytes", 100),
        owner_name=kw.get("owner_name"),
        fs_modified_at=kw.get("fs_modified_at"),
    )
    return Entry(**base)


@pytest.mark.asyncio
async def test_totals_match_seeded_entries(db_session):
    src = await _make_source(db_session)
    db_session.add_all([
        _file(src.id, name="a.txt", extension="txt", size_bytes=100),
        _file(src.id, name="b.txt", extension="txt", size_bytes=200),
        _file(src.id, name="c.pdf", extension="pdf", size_bytes=1000),
    ])
    db_session.add(Entry(
        source_id=src.id, kind="directory", parent_path="/", path="/sub",
        name="sub", extension=None, size_bytes=None,
    ))
    await db_session.commit()

    snap = await write_snapshot(db_session, src.id)
    await db_session.commit()

    assert snap.file_count == 3
    assert snap.directory_count == 1
    assert snap.total_size_bytes == 1300


@pytest.mark.asyncio
async def test_by_extension_buckets_lowercased(db_session):
    src = await _make_source(db_session)
    db_session.add_all([
        _file(src.id, name="a.PDF", extension="PDF", size_bytes=1000),
        _file(src.id, name="b.pdf", extension="pdf", size_bytes=500),
        _file(src.id, name="c.txt", extension="txt", size_bytes=10),
    ])
    await db_session.commit()

    snap = await write_snapshot(db_session, src.id)
    assert snap.by_extension["pdf"] == {"n": 2, "bytes": 1500}
    assert snap.by_extension["txt"] == {"n": 1, "bytes": 10}


@pytest.mark.asyncio
async def test_by_extension_caps_at_top_n_with_other_bucket(db_session):
    src = await _make_source(db_session)
    # TOP_N+5 distinct extensions, sized so the smallest 5 get folded
    # into _other. Extension i has size 1000 - i so smaller-i wins the
    # top slots.
    for i in range(TOP_N + 5):
        ext = f"ext{i:03d}"
        db_session.add(_file(
            src.id, name=f"f{i}.{ext}", extension=ext, size_bytes=1000 - i,
        ))
    await db_session.commit()

    snap = await write_snapshot(db_session, src.id)
    # Top-N extensions plus one "_other" rollup row.
    assert len(snap.by_extension) == TOP_N + 1
    assert "_other" in snap.by_extension
    assert snap.by_extension["_other"]["n"] == 5


@pytest.mark.asyncio
async def test_by_owner_unknown_for_null(db_session):
    src = await _make_source(db_session)
    db_session.add_all([
        _file(src.id, name="a", owner_name="alice", size_bytes=100),
        _file(src.id, name="b", owner_name=None, size_bytes=200),
    ])
    await db_session.commit()

    snap = await write_snapshot(db_session, src.id)
    assert snap.by_owner["alice"] == {"n": 1, "bytes": 100}
    assert snap.by_owner["_unknown"] == {"n": 1, "bytes": 200}


@pytest.mark.asyncio
async def test_age_buckets_hot_warm_cold(db_session):
    src = await _make_source(db_session)
    now = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)
    # hot: 10d ago; warm: 100d ago; cold: 400d ago; unknown: NULL.
    db_session.add_all([
        _file(src.id, name="hot", size_bytes=1, fs_modified_at=now - timedelta(days=10)),
        _file(src.id, name="warm", size_bytes=10, fs_modified_at=now - timedelta(days=100)),
        _file(src.id, name="cold", size_bytes=100, fs_modified_at=now - timedelta(days=400)),
        _file(src.id, name="unknown", size_bytes=1000, fs_modified_at=None),
    ])
    await db_session.commit()

    snap = await write_snapshot(db_session, src.id, now=now)
    assert snap.by_kind_and_age["hot"] == {"n": 1, "bytes": 1}
    assert snap.by_kind_and_age["warm"] == {"n": 1, "bytes": 10}
    assert snap.by_kind_and_age["cold"] == {"n": 1, "bytes": 100}
    assert snap.by_kind_and_age["_unknown"] == {"n": 1, "bytes": 1000}


@pytest.mark.asyncio
async def test_age_boundary_inclusive_at_hot_cutoff(db_session):
    """At exactly HOT_DAYS old, file is still hot (>= cutoff)."""
    src = await _make_source(db_session)
    now = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)
    db_session.add(_file(
        src.id, name="boundary", size_bytes=1,
        fs_modified_at=now - timedelta(days=HOT_DAYS),
    ))
    await db_session.commit()
    snap = await write_snapshot(db_session, src.id, now=now)
    assert snap.by_kind_and_age["hot"]["n"] == 1
    assert snap.by_kind_and_age["warm"]["n"] == 0


@pytest.mark.asyncio
async def test_writer_excludes_deleted_entries(db_session):
    src = await _make_source(db_session)
    e1 = _file(src.id, name="kept", size_bytes=100)
    e2 = _file(src.id, name="gone", size_bytes=999)
    e2.is_deleted = True
    db_session.add_all([e1, e2])
    await db_session.commit()

    snap = await write_snapshot(db_session, src.id)
    assert snap.file_count == 1
    assert snap.total_size_bytes == 100


@pytest.mark.asyncio
async def test_snapshot_persisted_with_relationships(db_session):
    src = await _make_source(db_session)
    db_session.add(_file(src.id, name="a", size_bytes=42))
    await db_session.commit()

    snap = await write_snapshot(db_session, src.id)
    await db_session.commit()

    rows = (await db_session.execute(select(ScanSnapshot))).scalars().all()
    assert len(rows) == 1
    assert rows[0].source_id == src.id
    assert rows[0].scan_id is None
    assert rows[0].total_size_bytes == 42
