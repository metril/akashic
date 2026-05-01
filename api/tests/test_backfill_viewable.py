"""Tests for `akashic.tools.backfill_viewable`.

The tool has to: (1) populate NULL columns from acl/mode/uid/gid; (2) skip
already-populated rows so re-runs are idempotent and cheap; (3) survive a
mid-run crash via the checkpoint file.
"""
import uuid

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.tools.backfill_viewable import _backfill, _read_checkpoint, _write_checkpoint


async def _seed_legacy_entry(
    db: AsyncSession, source_id: uuid.UUID, path: str, mode: int, uid: int,
) -> Entry:
    e = Entry(
        id=uuid.uuid4(),
        source_id=source_id, kind="file",
        parent_path="/", path=path, name=path.lstrip("/"),
        mode=mode, uid=uid, gid=100,
    )
    db.add(e)
    await db.commit()
    # Force NULL columns post-commit — simulates the pre-Phase-4 state
    # where the row exists without the new projection populated.
    await db.execute(
        update(Entry)
        .where(Entry.id == e.id)
        .values(
            viewable_by_read=None,
            viewable_by_write=None,
            viewable_by_delete=None,
        )
    )
    await db.commit()
    return e


@pytest.mark.asyncio
async def test_backfill_populates_null_rows(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "akashic.tools.backfill_viewable.settings",
        type("S", (), {"database_url": _test_db_url()})(),
    )

    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add(source)
    await db_session.commit()

    e = await _seed_legacy_entry(db_session, source.id, "/r1", mode=0o644, uid=1000)
    assert e.viewable_by_read is None  # confirm seed left it NULL

    ckpt = tmp_path / "ckpt"
    total = await _backfill(batch_size=10, checkpoint_path=ckpt, source_id=None)
    assert total == 1

    await db_session.refresh(e)
    assert e.viewable_by_read is not None
    # 0o644 + uid 1000 → owner reads, world reads.
    assert "posix:uid:1000" in e.viewable_by_read
    assert "*" in e.viewable_by_read

    # Checkpoint persisted.
    assert _read_checkpoint(ckpt) == e.id


@pytest.mark.asyncio
async def test_backfill_resumes_from_checkpoint(db_session, tmp_path, monkeypatch):
    """A re-run with a checkpoint pointing past row N processes only rows
    after N — the contract that makes the tool resumable on crash."""
    monkeypatch.setattr(
        "akashic.tools.backfill_viewable.settings",
        type("S", (), {"database_url": _test_db_url()})(),
    )

    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add(source)
    await db_session.commit()

    # Two NULL rows; we'll pretend the first was already processed by
    # writing its id to the checkpoint, then run and verify only the
    # second gets touched.
    e1 = await _seed_legacy_entry(db_session, source.id, "/a", mode=0o600, uid=2000)
    e2 = await _seed_legacy_entry(db_session, source.id, "/b", mode=0o600, uid=2001)
    # Order rows by id so we know which is "first".
    first, second = sorted([e1, e2], key=lambda x: x.id)

    ckpt = tmp_path / "ckpt"
    _write_checkpoint(ckpt, first.id)

    total = await _backfill(batch_size=10, checkpoint_path=ckpt, source_id=None)
    assert total == 1

    await db_session.refresh(first)
    await db_session.refresh(second)
    # First was skipped (still NULL), second was filled.
    assert first.viewable_by_read is None
    assert second.viewable_by_read is not None


def _test_db_url() -> str:
    import os
    return os.environ.get(
        "TEST_DB_URL",
        "postgresql+asyncpg://akashic:changeme@localhost:5432/akashic_test",
    )
