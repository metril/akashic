"""Two concurrent ingest batches with overlapping paths must both
succeed (review C3).

Pre-fix: both batches pre-loaded existing_by_path, both found no row,
both tried to INSERT — the loser's commit raised IntegrityError on
uq_entries_source_path and the entire batch (including non-conflicting
entries) was rolled back. Post-fix: per-entry SAVEPOINT swallows the
unique-violation, re-fetches the winner's row, and applies the
incoming data as if it were an update."""
import asyncio
import uuid

import pytest
from sqlalchemy import select

from akashic.auth.jwt import create_ingest_token
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User


@pytest.mark.asyncio
async def test_concurrent_ingest_with_overlapping_paths(client, db_session):
    user = User(
        id=uuid.uuid4(), username="race", email="r@e",
        password_hash="x", role="admin",
    )
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add_all([user, source])
    await db_session.commit()
    token = create_ingest_token(str(user.id))

    # Two batches, overlapping at /shared. Without the savepoint,
    # whichever loses the unique-constraint race fails its entire
    # batch (and its non-overlapping rows would be lost too).
    overlap_path = "/shared"
    batch_a = {
        "source_id": str(source.id),
        "scan_id": str(uuid.uuid4()),
        "is_final": False,
        "entries": [
            {"path": "/only-a", "name": "only-a", "kind": "file", "size_bytes": 11},
            {"path": overlap_path, "name": "shared", "kind": "file", "size_bytes": 100},
        ],
    }
    batch_b = {
        "source_id": str(source.id),
        "scan_id": str(uuid.uuid4()),
        "is_final": False,
        "entries": [
            {"path": "/only-b", "name": "only-b", "kind": "file", "size_bytes": 22},
            {"path": overlap_path, "name": "shared", "kind": "file", "size_bytes": 200},
        ],
    }

    headers = {"Authorization": f"Bearer {token}"}
    res_a, res_b = await asyncio.gather(
        client.post("/api/ingest/batch", json=batch_a, headers=headers),
        client.post("/api/ingest/batch", json=batch_b, headers=headers),
    )

    # Both must succeed — neither batch lost.
    assert res_a.status_code == 200, res_a.text
    assert res_b.status_code == 200, res_b.text

    rows = (await db_session.execute(
        select(Entry).where(Entry.source_id == source.id).order_by(Entry.path)
    )).scalars().all()
    paths = [r.path for r in rows]

    # Non-overlapping rows from both batches present.
    assert "/only-a" in paths
    assert "/only-b" in paths
    # Overlapping row exists exactly once (unique constraint held).
    assert paths.count(overlap_path) == 1
    # Whichever batch won, the surviving size_bytes is one of the two
    # incoming values — not, e.g., a corrupt zero or the absence of the
    # column.
    shared_row = next(r for r in rows if r.path == overlap_path)
    assert shared_row.size_bytes in (100, 200)
