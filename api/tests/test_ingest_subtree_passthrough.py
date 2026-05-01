"""Phase B — ingest writes scanner-supplied subtree totals onto the Entry row.

The Local connector now emits per-directory SubtreeSizeBytes /
SubtreeFileCount / SubtreeDirCount as part of its post-order walk. The
ingest router's job is to copy those values straight onto the row at
insert time so the API doesn't have to recompute them.

This test ingests a directory record with the new fields populated and
asserts the row reflects them.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import create_access_token
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User


@pytest.mark.asyncio
async def test_ingest_passes_through_subtree_totals(
    client: AsyncClient, db_session: AsyncSession,
):
    user = User(id=uuid.uuid4(), username="ingestor", email="i@e", password_hash="x", role="admin")
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add_all([user, source])
    await db_session.commit()
    token = create_access_token({"sub": str(user.id)})

    payload = {
        "source_id": str(source.id),
        "scan_id": str(uuid.uuid4()),
        "is_final": False,
        "entries": [{
            "path": "/Reports", "name": "Reports", "kind": "directory",
            "subtree_size_bytes": 5_000_000,
            "subtree_file_count": 42,
            "subtree_dir_count": 7,
        }],
    }
    resp = await client.post(
        "/api/ingest/batch", json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    row = (await db_session.execute(
        select(Entry).where(Entry.source_id == source.id)
    )).scalar_one()
    assert row.subtree_size_bytes == 5_000_000
    assert row.subtree_file_count == 42
    assert row.subtree_dir_count == 7


@pytest.mark.asyncio
async def test_ingest_subtree_zero_is_preserved(
    client: AsyncClient, db_session: AsyncSession,
):
    """An empty directory has zero descendants — `is not None`, not
    truthiness, decides whether to write the field. The trap is the
    walrus / `if x:` short-circuit on a zero int."""
    user = User(id=uuid.uuid4(), username="z", email="z@e", password_hash="x", role="admin")
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add_all([user, source])
    await db_session.commit()
    token = create_access_token({"sub": str(user.id)})

    resp = await client.post(
        "/api/ingest/batch",
        json={
            "source_id": str(source.id),
            "scan_id": str(uuid.uuid4()),
            "is_final": False,
            "entries": [{
                "path": "/empty", "name": "empty", "kind": "directory",
                "subtree_size_bytes": 0,
                "subtree_file_count": 0,
                "subtree_dir_count": 0,
            }],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    row = (await db_session.execute(
        select(Entry).where(Entry.source_id == source.id)
    )).scalar_one()
    assert row.subtree_size_bytes == 0
    assert row.subtree_file_count == 0
    assert row.subtree_dir_count == 0


@pytest.mark.asyncio
async def test_ingest_omits_subtree_when_not_supplied(
    client: AsyncClient, db_session: AsyncSession,
):
    """Connectors that don't compute subtree totals omit the fields.
    The row goes in with NULL columns — the post-scan rollup CTE
    backfills (with null_only=True so it doesn't clobber scanner
    values when those exist)."""
    user = User(id=uuid.uuid4(), username="o", email="o@e", password_hash="x", role="admin")
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add_all([user, source])
    await db_session.commit()
    token = create_access_token({"sub": str(user.id)})

    resp = await client.post(
        "/api/ingest/batch",
        json={
            "source_id": str(source.id),
            "scan_id": str(uuid.uuid4()),
            "is_final": False,
            "entries": [{
                "path": "/legacy", "name": "legacy", "kind": "directory",
            }],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    row = (await db_session.execute(
        select(Entry).where(Entry.source_id == source.id)
    )).scalar_one()
    assert row.subtree_size_bytes is None
    assert row.subtree_file_count is None
    assert row.subtree_dir_count is None
