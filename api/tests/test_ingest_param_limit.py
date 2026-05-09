"""Regression: bulk INSERT chunks under postgres's 32767-parameter cap.

v0.27.1 — Pre-fix the bulk INSERT in ingest_batch passed every row
in a single `pg_insert(Entry).values(rows)` call. Entry has ~34
columns, so a batch of >~990 rows blew through asyncpg's hard
postgres limit:

    asyncpg.exceptions._base.InterfaceError:
        the number of query arguments cannot exceed 32767

Live failure mode: the lease pipeline started working (v0.27.1
JWT-cache fix), the agent ran a real SMB scan, sent its first batch
(~5000 entries), and the API 500'd on every batch. The scan failed
end-to-end without ever ingesting a single row.

This test submits a batch larger than one chunk's worth of rows and
asserts (a) the request succeeds, and (b) the API emits more than
one bulk INSERT statement (proving the chunk loop ran).
"""
import uuid

import pytest
from sqlalchemy import event

from akashic.auth.jwt import create_ingest_token
from akashic.models.source import Source
from akashic.models.user import User
from tests.conftest import seed_scan


@pytest.mark.asyncio
async def test_ingest_chunks_large_batch_under_param_cap(client, db_session):
    """A 1500-entry batch must succeed and emit ≥2 bulk INSERTs.

    With ~34 columns per Entry and a 30k param cap, chunk size is
    ~882 rows. 1500 entries → 2 chunks → 2 INSERT statements.
    """
    user = User(
        id=uuid.uuid4(), username="chunky", email="c@e",
        password_hash="x", role="admin",
    )
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add_all([user, source])
    await db_session.commit()
    token = create_ingest_token(str(user.id))
    scan_id = await seed_scan(db_session, source.id)

    bind = db_session.get_bind()
    sync_engine = bind.sync_engine if hasattr(bind, "sync_engine") else bind

    insert_with_conflict_count = [0]

    def _on_exec(conn, cursor, statement, parameters, context, executemany):
        s = " ".join(statement.split()).upper()
        if "INSERT INTO ENTRIES" in s and "ON CONFLICT" in s:
            insert_with_conflict_count[0] += 1

    event.listen(sync_engine, "before_cursor_execute", _on_exec)
    try:
        payload = {
            "source_id": str(source.id),
            "scan_id": str(scan_id),
            "is_final": False,
            "entries": [
                {
                    "path": f"/file-{i}", "name": f"f{i}", "kind": "file",
                    "size_bytes": i,
                }
                for i in range(1500)
            ],
        }
        resp = await client.post(
            "/api/ingest/batch", json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        event.remove(sync_engine, "before_cursor_execute", _on_exec)

    # 1500 rows × 34 cols = 51000 params total — has to span at least
    # two chunks. Pre-fix this was 1 statement and 51000 params,
    # which postgres rejected.
    assert insert_with_conflict_count[0] >= 2, (
        f"expected ≥2 bulk INSERTs (chunked under 32767 params), "
        f"got {insert_with_conflict_count[0]}"
    )
