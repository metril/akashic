"""Bulk INSERT ON CONFLICT replaces per-row SAVEPOINT (review C3 — proper).

Pre-fix the new-entry path wrapped each INSERT in db.begin_nested()
(SAVEPOINT + flush + RELEASE per row). On a 1k-new-entry batch that
emitted 3000 round-trips for the savepoint dance alone before the
INSERTs even ran. The proper fix is one bulk INSERT with
`ON CONFLICT DO NOTHING RETURNING id, path` — this test guards
against a regression by counting SAVEPOINT statements.
"""
import uuid

import pytest
from sqlalchemy import event

from akashic.auth.jwt import create_ingest_token
from akashic.models.source import Source
from akashic.models.user import User
from tests.conftest import seed_scan


@pytest.mark.asyncio
async def test_ingest_emits_no_per_row_savepoints(client, db_session):
    user = User(
        id=uuid.uuid4(), username="nosp", email="n@e",
        password_hash="x", role="admin",
    )
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add_all([user, source])
    await db_session.commit()
    token = create_ingest_token(str(user.id))
    scan_id = await seed_scan(db_session, source.id)

    bind = db_session.get_bind()
    sync_engine = bind.sync_engine if hasattr(bind, "sync_engine") else bind

    savepoint_count = [0]
    insert_with_conflict_count = [0]

    def _on_exec(conn, cursor, statement, parameters, context, executemany):
        s = " ".join(statement.split()).upper()
        if s.startswith("SAVEPOINT "):
            savepoint_count[0] += 1
        if "INSERT INTO ENTRIES" in s and "ON CONFLICT" in s:
            insert_with_conflict_count[0] += 1

    event.listen(sync_engine, "before_cursor_execute", _on_exec)
    try:
        # 50 fresh paths — every entry is a NEW insert (no
        # pre-existing rows to deduplicate against). Pre-fix this
        # would emit ~50 SAVEPOINTs; post-fix exactly 0.
        payload = {
            "source_id": str(source.id),
            "scan_id": str(scan_id),
            "is_final": False,
            "entries": [
                {
                    "path": f"/file-{i}", "name": f"f{i}", "kind": "file",
                    "size_bytes": i * 10,
                }
                for i in range(50)
            ],
        }
        resp = await client.post(
            "/api/ingest/batch", json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        event.remove(sync_engine, "before_cursor_execute", _on_exec)

    assert savepoint_count[0] == 0, (
        f"new-entry path emitted {savepoint_count[0]} SAVEPOINT(s); "
        f"expected 0 — bulk INSERT … ON CONFLICT path regressed?"
    )
    assert insert_with_conflict_count[0] == 1, (
        f"expected exactly one bulk INSERT … ON CONFLICT statement, "
        f"got {insert_with_conflict_count[0]}"
    )
