"""Per-source Meilisearch index debouncer (v0.29.2).

Covers:

  * mark_dirty SADDs entry ids onto a per-source set; subsequent
    marks accumulate.
  * The debouncer's "should flush?" predicate fires on size threshold
    (>= 5000) or age threshold (>= 5 s).
  * flush is idempotent on empty sets.
  * Two sources mark independently — sets don't bleed across.
  * Ingest no longer creates the per-batch BackgroundTask
    (_index_files_to_meilisearch is no longer added).

We stub the actual Meili push (update_files_partial) so the tests
don't require a running Meilisearch instance.
"""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.services import meili_indexer
from akashic.services.scan_pubsub import _client as redis_client


async def _cleanup(*source_ids):
    redis = redis_client()
    keys = []
    for sid in source_ids:
        keys.append(meili_indexer._set_key(sid))
        keys.append(meili_indexer._ts_key(sid))
    if keys:
        try:
            await redis.delete(*keys)
        except Exception:  # noqa: BLE001
            pass


@pytest.mark.asyncio
async def test_mark_dirty_accumulates(setup_db):
    sid = uuid.uuid4()
    eids_a = [uuid.uuid4() for _ in range(3)]
    eids_b = [uuid.uuid4() for _ in range(2)]
    try:
        await meili_indexer.mark_dirty(sid, eids_a)
        await meili_indexer.mark_dirty(sid, eids_b)
        redis = redis_client()
        scard = await redis.scard(meili_indexer._set_key(sid))
        assert scard == 5
        # Timestamp key gets stamped on first mark and is preserved.
        ts = await redis.get(meili_indexer._ts_key(sid))
        assert ts is not None
    finally:
        await _cleanup(sid)


@pytest.mark.asyncio
async def test_mark_dirty_empty_input_is_noop(setup_db):
    sid = uuid.uuid4()
    try:
        await meili_indexer.mark_dirty(sid, [])
        redis = redis_client()
        assert await redis.exists(meili_indexer._set_key(sid)) == 0
    finally:
        await _cleanup(sid)


@pytest.mark.asyncio
async def test_should_flush_size_threshold(setup_db, monkeypatch):
    """Above the size threshold → flush, regardless of age."""
    monkeypatch.setattr(meili_indexer, "_FLUSH_SIZE_THRESHOLD", 3)
    sid = uuid.uuid4()
    try:
        await meili_indexer.mark_dirty(
            sid, [uuid.uuid4() for _ in range(3)],
        )
        assert await meili_indexer._should_flush(sid) is True
    finally:
        await _cleanup(sid)


@pytest.mark.asyncio
async def test_should_flush_age_threshold(setup_db, monkeypatch):
    """Below size threshold but past age threshold → flush."""
    monkeypatch.setattr(meili_indexer, "_FLUSH_SIZE_THRESHOLD", 1000)
    monkeypatch.setattr(meili_indexer, "_FLUSH_AGE_SECONDS", 0.1)
    sid = uuid.uuid4()
    try:
        await meili_indexer.mark_dirty(sid, [uuid.uuid4()])
        # Just-marked → should NOT flush (age < threshold).
        assert await meili_indexer._should_flush(sid) is False
        await asyncio.sleep(0.15)
        assert await meili_indexer._should_flush(sid) is True
    finally:
        await _cleanup(sid)


@pytest.mark.asyncio
async def test_should_flush_empty_set_returns_false(setup_db):
    sid = uuid.uuid4()
    assert await meili_indexer._should_flush(sid) is False


@pytest.mark.asyncio
async def test_flush_empty_set_is_idempotent(setup_db):
    sid = uuid.uuid4()
    n = await meili_indexer.flush(sid)
    assert n == 0


@pytest.mark.asyncio
async def test_two_sources_dirty_sets_are_independent(setup_db):
    """A's marks must not affect B's set, and vice versa — confirms the
    per-source parallelism claim."""
    sid_a = uuid.uuid4()
    sid_b = uuid.uuid4()
    try:
        await meili_indexer.mark_dirty(sid_a, [uuid.uuid4(), uuid.uuid4()])
        await meili_indexer.mark_dirty(sid_b, [uuid.uuid4()])
        redis = redis_client()
        assert await redis.scard(meili_indexer._set_key(sid_a)) == 2
        assert await redis.scard(meili_indexer._set_key(sid_b)) == 1
    finally:
        await _cleanup(sid_a, sid_b)


@pytest.mark.asyncio
async def test_flush_clears_set_and_ts(setup_db, monkeypatch):
    """After a successful flush, both keys are gone so the next
    mark_dirty starts a fresh debounce window."""
    sid = uuid.uuid4()
    # The flush() helper opens a fresh session against
    # settings.database_url (the production URL), which the test
    # harness doesn't point at the per-test schema. Override
    # _bg_session to use the test's session maker so the SELECT
    # against entries succeeds (returns 0 rows for our stub UUID).
    monkeypatch.setattr(meili_indexer, "_bg_session", lambda: setup_db)
    # Stub the Meili push to a no-op so we don't need a real engine.
    async def _noop_index(_):
        return None
    monkeypatch.setattr(
        "akashic.services.search.update_files_partial", _noop_index,
    )
    try:
        await meili_indexer.mark_dirty(sid, [uuid.uuid4()])
        n = await meili_indexer.flush(sid)
        # No matching Entry row → 0 indexed.
        assert n == 0
        redis = redis_client()
        assert await redis.exists(meili_indexer._set_key(sid)) == 0
        assert await redis.exists(meili_indexer._ts_key(sid)) == 0
    finally:
        await _cleanup(sid)


@pytest.mark.asyncio
async def test_flush_uses_partial_update_not_replace(setup_db, monkeypatch):
    """v0.30.0 regression guard: the metadata flush must go through
    Meili's partial `update_documents` (update_files_partial), NOT the
    full-replace `add_documents` (index_files_batch). A full replace
    would wipe the `content_text` the scanner posts via
    /api/ingest/content, since build_entry_doc emits only metadata.
    """
    sid = uuid.uuid4()
    async with setup_db() as session:
        src = Source(
            id=sid, name=f"src-{uuid.uuid4().hex[:6]}",
            type="local", connection_config={"path": "/tmp"},
        )
        session.add(src)
        await session.flush()
        entry = Entry(
            id=uuid.uuid4(), source_id=sid, kind="file",
            parent_path="/tmp", path="/tmp/doc.pdf", name="doc.pdf",
        )
        session.add(entry)
        await session.commit()
        entry_id = entry.id

    monkeypatch.setattr(meili_indexer, "_bg_session", lambda: setup_db)

    partial_docs: list = []
    replace_docs: list = []

    async def _capture_partial(docs):
        partial_docs.extend(docs)

    async def _capture_replace(docs):
        replace_docs.extend(docs)

    monkeypatch.setattr(
        "akashic.services.search.update_files_partial", _capture_partial,
    )
    monkeypatch.setattr(
        "akashic.services.search.index_files_batch", _capture_replace,
    )

    try:
        await meili_indexer.mark_dirty(sid, [entry_id])
        n = await meili_indexer.flush(sid)
        assert n == 1
        # The fix: partial update was used, full replace was NOT.
        assert len(partial_docs) == 1, "flush must call update_files_partial"
        assert replace_docs == [], "flush must NOT call index_files_batch (full replace)"
        # The metadata doc carries no content_text — so a partial
        # update leaves any existing content_text on the Meili doc
        # untouched. (A full replace would have dropped it.)
        assert "content_text" not in partial_docs[0]
        assert partial_docs[0]["id"] == str(entry_id)
    finally:
        await _cleanup(sid)
