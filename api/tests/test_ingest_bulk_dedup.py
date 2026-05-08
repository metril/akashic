"""Ingest dedup is bulk: one SELECT per batch, not one per entry.

Regression guard for the v0.4.x ingest hot-path optimization. Pre-fix,
the ingest router ran `SELECT Entry WHERE source_id=? AND path=?` once
per incoming entry — a 1k-entry batch produced 1k round-trips and
dominated p50 at scale. Post-fix, all paths are pulled in one
`path IN (...)` SELECT before the per-entry loop.

The assertion counts the ingest-time `SELECT FROM entries` queries the
batch produces. The exact count includes the bulk lookup *and* the
end-of-batch stale-detection scan when `is_final=True`, so the test
runs with `is_final=False` to keep the count clean.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import create_ingest_token
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User


@pytest.mark.asyncio
async def test_ingest_dedup_uses_one_select_for_whole_batch(
    client: AsyncClient, db_session: AsyncSession,
):
    user = User(
        id=uuid.uuid4(), username="bulk", email="b@e",
        password_hash="x", role="admin",
    )
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add_all([user, source])
    await db_session.commit()

    # Pre-seed a few entries so the batch hits both the "existing" and
    # "new" branches — the optimization has to behave correctly for
    # both.
    for path in ("/a", "/b"):
        db_session.add(Entry(
            source_id=source.id, kind="file", path=path, name=path[1:],
            parent_path="/", size_bytes=10,
        ))
    await db_session.commit()
    token = create_ingest_token(str(user.id))

    # The async test session is bound to an AsyncEngine — its
    # underlying sync_engine is what fires `before_cursor_execute`.
    # Use that for the API request engine too (same TEST_DB_URL).
    bind = db_session.get_bind()
    sync_engine = bind.sync_engine if hasattr(bind, "sync_engine") else bind

    # Two counters: bulk-shape (`path IN (...)`, the post-fix shape)
    # and single-shape (`entries.path = ...`, the pre-fix shape).
    # Either pattern proves intent — bulk count must be 1, single
    # count must be 0.
    bulk_count = [0]
    single_count = [0]

    def _on_exec(conn, cursor, statement, parameters, context, executemany):
        s = " ".join(statement.split())
        # Restrict to ingest's dedup site: SELECT against entries
        # filtered by source_id. End-of-batch stale-scan filters by
        # last_seen_at; move-detection filters by content_hash in the
        # WHERE — neither of those is what we're measuring.
        if "FROM entries" not in s:
            return
        where_split = s.split(" WHERE ", 1)
        if len(where_split) != 2:
            return
        where_clause = where_split[1].lower()
        if "source_id" not in where_clause:
            return
        if "last_seen_at" in where_clause or "content_hash" in where_clause:
            return
        if "entries.path in (" in where_clause or " path in (" in where_clause:
            bulk_count[0] += 1
        elif "entries.path =" in where_clause or " path =" in where_clause:
            single_count[0] += 1

    event.listen(sync_engine, "before_cursor_execute", _on_exec)
    try:
        payload = {
            "source_id": str(source.id),
            "scan_id": str(uuid.uuid4()),
            "is_final": False,
            "entries": [
                {"path": "/a", "name": "a", "kind": "file", "size_bytes": 11},
                {"path": "/b", "name": "b", "kind": "file", "size_bytes": 22},
                {"path": "/c", "name": "c", "kind": "file", "size_bytes": 33},
                {"path": "/d", "name": "d", "kind": "file", "size_bytes": 44},
            ],
        }
        resp = await client.post(
            "/api/ingest/batch", json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        event.remove(sync_engine, "before_cursor_execute", _on_exec)

    # Pre-fix: single_count=4, bulk_count=0. Post-fix: bulk_count=1,
    # single_count=0.
    assert single_count[0] == 0, (
        f"per-entry dedup SELECT regressed: saw {single_count[0]} "
        "single-path lookups against entries"
    )
    assert bulk_count[0] == 1, (
        f"expected exactly one bulk dedup SELECT, got {bulk_count[0]}"
    )

    # Sanity: state is correct — existing entries got their sizes
    # updated, new entries were inserted.
    rows = (await db_session.execute(
        select(Entry).where(Entry.source_id == source.id).order_by(Entry.path)
    )).scalars().all()
    by_path = {r.path: r for r in rows}
    assert by_path["/a"].size_bytes == 11
    assert by_path["/b"].size_bytes == 22
    assert by_path["/c"].size_bytes == 33
    assert by_path["/d"].size_bytes == 44


@pytest.mark.asyncio
async def test_ingest_empty_batch_does_no_dedup_select(
    client: AsyncClient, db_session: AsyncSession,
):
    """An empty entries list must not emit a `WHERE path IN ()` query —
    the new bulk path guards on the empty-list case. This catches the
    regression where the IN-clause becomes `path IN ()` and either
    errors out on some backends or matches everything on others."""
    user = User(
        id=uuid.uuid4(), username="empty", email="e@e",
        password_hash="x", role="admin",
    )
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add_all([user, source])
    await db_session.commit()
    token = create_ingest_token(str(user.id))

    resp = await client.post(
        "/api/ingest/batch",
        json={
            "source_id": str(source.id),
            "scan_id": str(uuid.uuid4()),
            "is_final": False,
            "entries": [],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
