"""End-of-scan move detection runs one bulk SELECT, not N+1 (review I13).

Pre-fix: per stale file with a content_hash, one SELECT to find a
"moved-to" candidate. With 1000 mass-deleted hashed files, that
meant 1000 round-trips after the rest of the batch was already
queued. Post-fix: one SELECT per batch with `content_hash IN (...)`
and an in-memory `first_by_hash` dict.
"""
import uuid

import pytest
from sqlalchemy import event, select

from akashic.auth.jwt import create_access_token
from akashic.models.entry import Entry, EntryEvent
from akashic.models.source import Source
from akashic.models.user import User


@pytest.mark.asyncio
async def test_stale_sweep_uses_one_bulk_query(client, db_session):
    user = User(
        id=uuid.uuid4(), username="stale", email="s@e",
        password_hash="x", role="admin",
    )
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add_all([user, source])
    await db_session.commit()

    # Pre-seed five files with hashes; the next scan will not see them
    # (they become stale) but will see five new files at different
    # paths with the same hashes (so each stale row has a move target).
    seeded = []
    for i in range(5):
        e = Entry(
            id=uuid.uuid4(), source_id=source.id, kind="file",
            path=f"/old/{i}", parent_path="/old", name=str(i),
            size_bytes=10, content_hash=f"hash-{i}",
        )
        seeded.append(e)
    db_session.add_all(seeded)
    await db_session.commit()

    token = create_access_token({"sub": str(user.id)})

    bind = db_session.get_bind()
    sync_engine = bind.sync_engine if hasattr(bind, "sync_engine") else bind

    # Count move-candidate-shape queries: SELECT FROM entries WHERE
    # content_hash ... AND last_seen_at >=. Pre-fix this fired N
    # times (once per stale row); post-fix it should fire exactly 1.
    n_move_queries = [0]
    has_in_clause = [False]
    captured: list[str] = []

    def _on_exec(conn, cursor, statement, parameters, context, executemany):
        s = " ".join(statement.split())
        if "FROM entries" not in s:
            return
        low = s.lower()
        # Look at the WHERE clause only — the column list always
        # includes both content_hash and last_seen_at, so checking
        # the full statement matches every entries SELECT.
        where_split = low.split(" where ", 1)
        if len(where_split) != 2:
            return
        where = where_split[1]
        if "content_hash" in where and "last_seen_at" in where:
            n_move_queries[0] += 1
            captured.append(s[:400])
            if "content_hash in (" in where:
                has_in_clause[0] = True

    event.listen(sync_engine, "before_cursor_execute", _on_exec)
    try:
        # Send the new locations (different paths, same hashes), with
        # is_final=True so the stale-sweep + move-detection runs.
        payload = {
            "source_id": str(source.id),
            "scan_id": str(uuid.uuid4()),
            "is_final": True,
            "entries": [
                {
                    "path": f"/new/{i}", "name": str(i), "kind": "file",
                    "size_bytes": 10, "content_hash": f"hash-{i}",
                }
                for i in range(5)
            ],
        }
        resp = await client.post(
            "/api/ingest/batch", json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        event.remove(sync_engine, "before_cursor_execute", _on_exec)

    assert n_move_queries[0] == 1, (
        f"expected 1 move-detection query, got {n_move_queries[0]}\n"
        + "\n---\n".join(captured)
    )
    assert has_in_clause[0], "move-detection query should use IN-clause shape"

    # Verify each stale row got an EntryEvent recording the move.
    events = (await db_session.execute(
        select(EntryEvent).where(EntryEvent.event_type == "moved")
    )).scalars().all()
    assert len(events) == 5, f"expected 5 move events, got {len(events)}"
    moved_hashes = {e.content_hash for e in events}
    assert moved_hashes == {f"hash-{i}" for i in range(5)}
