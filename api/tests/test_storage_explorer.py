"""Phase 9 — /api/storage/sources + /api/storage/children behaviour.

The endpoints are pure read paths. The interesting cases are: the
shape of the cross-source response, the shape of the drill-down
response, the long-tail `<other>` rollup when more than `limit`
children exist, and the color modes.
"""
import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import create_access_token
from akashic.models.entry import Entry
from akashic.models.scan_snapshot import ScanSnapshot
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services.subtree_rollup import rollup_source


async def _admin_token(db_session: AsyncSession) -> tuple[User, str]:
    user = User(id=uuid.uuid4(), username="adm", email="a@e", password_hash="x", role="admin")
    db_session.add(user)
    await db_session.commit()
    return user, create_access_token({"sub": str(user.id)})


@pytest.mark.asyncio
async def test_sources_returns_one_row_per_source_sorted_by_size(
    client: AsyncClient, db_session: AsyncSession,
):
    _, token = await _admin_token(db_session)
    big = Source(id=uuid.uuid4(), name="big", type="local", connection_config={})
    small = Source(id=uuid.uuid4(), name="small", type="local", connection_config={})
    db_session.add_all([big, small])
    await db_session.commit()

    now = datetime.now(timezone.utc)
    db_session.add_all([
        ScanSnapshot(
            id=uuid.uuid4(), source_id=big.id, taken_at=now,
            file_count=100, directory_count=10, total_size_bytes=10_000_000,
            by_owner={}, by_extension={}, by_kind_and_age={},
        ),
        ScanSnapshot(
            id=uuid.uuid4(), source_id=small.id, taken_at=now,
            file_count=10, directory_count=2, total_size_bytes=1_000,
            by_owner={}, by_extension={}, by_kind_and_age={},
        ),
    ])
    await db_session.commit()

    resp = await client.get(
        "/api/storage/sources",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["sources"]
    assert [r["source_name"] for r in rows] == ["big", "small"]
    assert rows[0]["size_bytes"] == 10_000_000


@pytest.mark.asyncio
async def test_children_uses_subtree_aggregates_for_directories(
    client: AsyncClient, db_session: AsyncSession,
):
    """Drill-down sizes directories by their subtree_size_bytes (set
    by the rollup), not by the directory entry's own size_bytes."""
    _, token = await _admin_token(db_session)
    src = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add(src)
    await db_session.commit()

    db_session.add_all([
        Entry(
            id=uuid.uuid4(), source_id=src.id, kind="directory",
            parent_path="/", path="/big", name="big",
        ),
        Entry(
            id=uuid.uuid4(), source_id=src.id, kind="directory",
            parent_path="/", path="/small", name="small",
        ),
        Entry(
            id=uuid.uuid4(), source_id=src.id, kind="file",
            parent_path="/big", path="/big/a.bin", name="a.bin",
            size_bytes=1_000_000, extension="bin",
        ),
        Entry(
            id=uuid.uuid4(), source_id=src.id, kind="file",
            parent_path="/small", path="/small/a.txt", name="a.txt",
            size_bytes=100, extension="txt",
        ),
    ])
    await db_session.commit()
    await rollup_source(db_session, src.id)
    await db_session.commit()

    resp = await client.get(
        f"/api/storage/children?source_id={src.id}&path=/&color_by=type",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    children = body["children"]
    by_name = {c["name"]: c for c in children}
    assert by_name["big"]["size_bytes"] == 1_000_000
    assert by_name["small"]["size_bytes"] == 100
    # Sort order: largest first.
    assert children[0]["name"] == "big"


@pytest.mark.asyncio
async def test_children_overflow_rolls_into_other_bucket(
    client: AsyncClient, db_session: AsyncSession,
):
    """When more than `limit` children exist the long tail rolls into
    a synthetic <other> rectangle so the treemap stays bounded."""
    _, token = await _admin_token(db_session)
    src = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add(src)
    await db_session.commit()

    # 5 files of decreasing size.
    db_session.add_all([
        Entry(
            id=uuid.uuid4(), source_id=src.id, kind="file",
            parent_path="/", path=f"/f{i}", name=f"f{i}",
            size_bytes=1000 - i * 100, extension="bin",
        )
        for i in range(5)
    ])
    await db_session.commit()

    resp = await client.get(
        f"/api/storage/children?source_id={src.id}&path=/&limit=2",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["children"]) == 2
    assert body["other"] is not None
    assert body["other"]["child_count"] == 3
    # Top 2 are 1000 and 900; <other> rolls 800 + 700 + 600 = 2100.
    assert body["other"]["size_bytes"] == 2100


@pytest.mark.asyncio
async def test_children_color_by_risk_admin_only(
    client: AsyncClient, db_session: AsyncSession,
):
    """Risk coloring requires admin. Public-readable rows get
    color_key='public'; restricted rows get color_key='restricted'."""
    _, token = await _admin_token(db_session)
    src = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add(src)
    await db_session.commit()

    db_session.add_all([
        Entry(
            id=uuid.uuid4(), source_id=src.id, kind="file",
            parent_path="/", path="/public", name="public", size_bytes=10,
            viewable_by_read=["*"],
        ),
        Entry(
            id=uuid.uuid4(), source_id=src.id, kind="file",
            parent_path="/", path="/restricted", name="restricted", size_bytes=10,
            viewable_by_read=["sid:S-1-5-21-X"],
        ),
    ])
    await db_session.commit()

    body = (await client.get(
        f"/api/storage/children?source_id={src.id}&path=/&color_by=risk",
        headers={"Authorization": f"Bearer {token}"},
    )).json()
    by_name = {c["name"]: c for c in body["children"]}
    assert by_name["public"]["color_key"] == "public"
    assert by_name["restricted"]["color_key"] == "restricted"


@pytest.mark.asyncio
async def test_children_color_by_age_buckets(
    client: AsyncClient, db_session: AsyncSession,
):
    """hot < 30 days, warm < 365 days, cold >= 365 days."""
    from datetime import timedelta
    _, token = await _admin_token(db_session)
    src = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add(src)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    db_session.add_all([
        Entry(
            id=uuid.uuid4(), source_id=src.id, kind="file",
            parent_path="/", path="/h", name="h", size_bytes=10,
            fs_modified_at=now - timedelta(days=1),
        ),
        Entry(
            id=uuid.uuid4(), source_id=src.id, kind="file",
            parent_path="/", path="/w", name="w", size_bytes=10,
            fs_modified_at=now - timedelta(days=100),
        ),
        Entry(
            id=uuid.uuid4(), source_id=src.id, kind="file",
            parent_path="/", path="/c", name="c", size_bytes=10,
            fs_modified_at=now - timedelta(days=400),
        ),
    ])
    await db_session.commit()

    body = (await client.get(
        f"/api/storage/children?source_id={src.id}&path=/&color_by=age",
        headers={"Authorization": f"Bearer {token}"},
    )).json()
    keys = {c["name"]: c["color_key"] for c in body["children"]}
    assert keys == {"h": "hot", "w": "warm", "c": "cold"}
