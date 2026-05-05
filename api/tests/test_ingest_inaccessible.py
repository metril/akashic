"""v0.5.11 — ingest persists inaccessible_dirs/files counts on the
Scan row, accumulating across IsFinal=true batches so the parallel
agent path (one final per work unit) sums correctly across units.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.scan import Scan
from akashic.models.source import Source
from akashic.models.user import User
from akashic.auth.jwt import create_access_token


async def _seed(db_session, source_id):
    user = User(
        id=uuid.uuid4(), username="admin", email="a@b.c",
        password_hash="x", role="admin",
    )
    source = Source(
        id=source_id, name="t", type="local",
        connection_config={"path": "/tmp"},
    )
    db_session.add_all([user, source])
    await db_session.flush()
    scan = Scan(
        id=uuid.uuid4(), source_id=source_id,
        scan_type="full", status="scanning",
    )
    db_session.add(scan)
    await db_session.commit()
    return user, scan


@pytest.mark.asyncio
async def test_inaccessible_counts_persisted_on_final_batch(
    client: AsyncClient, db_session: AsyncSession
):
    sid = uuid.uuid4()
    user, scan = await _seed(db_session, sid)
    token = create_access_token({"sub": str(user.id)})

    payload = {
        "source_id": str(sid),
        "scan_id": str(scan.id),
        "entries": [],
        "is_final": True,
        "inaccessible_dirs": 3,
        "inaccessible_files": 7,
    }
    resp = await client.post(
        "/api/ingest/batch", json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    fetched = (await db_session.execute(
        select(Scan).where(Scan.id == scan.id)
    )).scalar_one()
    await db_session.refresh(fetched)
    assert fetched.inaccessible_dirs == 3
    assert fetched.inaccessible_files == 7


@pytest.mark.asyncio
async def test_inaccessible_counts_accumulate_across_batches(
    client: AsyncClient, db_session: AsyncSession
):
    """Parallel agent path: each work unit ships its own IsFinal=true
    batch with per-unit counts. The api adds (doesn't assign) so the
    Scan row ends up with the per-scan total."""
    sid = uuid.uuid4()
    user, scan = await _seed(db_session, sid)
    token = create_access_token({"sub": str(user.id)})

    for d, f in [(2, 5), (4, 1), (1, 3)]:
        payload = {
            "source_id": str(sid),
            "scan_id": str(scan.id),
            "entries": [],
            "is_final": True,
            "inaccessible_dirs": d,
            "inaccessible_files": f,
        }
        resp = await client.post(
            "/api/ingest/batch", json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    fetched = (await db_session.execute(
        select(Scan).where(Scan.id == scan.id)
    )).scalar_one()
    await db_session.refresh(fetched)
    assert fetched.inaccessible_dirs == 7  # 2 + 4 + 1
    assert fetched.inaccessible_files == 9  # 5 + 1 + 3


@pytest.mark.asyncio
async def test_legacy_batch_without_counts_defaults_to_zero(
    client: AsyncClient, db_session: AsyncSession
):
    """Old scanners that don't send the new fields must continue to
    work — schema defaults missing fields to 0, and the accumulator
    skips when both are 0 so the Scan row stays at 0."""
    sid = uuid.uuid4()
    user, scan = await _seed(db_session, sid)
    token = create_access_token({"sub": str(user.id)})

    payload = {
        "source_id": str(sid),
        "scan_id": str(scan.id),
        "entries": [],
        "is_final": True,
    }
    resp = await client.post(
        "/api/ingest/batch", json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    fetched = (await db_session.execute(
        select(Scan).where(Scan.id == scan.id)
    )).scalar_one()
    await db_session.refresh(fetched)
    assert fetched.inaccessible_dirs == 0
    assert fetched.inaccessible_files == 0
