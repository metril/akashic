"""Phase 7 — `/api/dashboard/summary` aggregator.

The endpoint is the seam Phase 7's homepage rebuild relies on. Each
test asserts one tile's data, so a regression in any of them surfaces
as a single named failure rather than "the dashboard is broken".
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import create_access_token
from akashic.models.entry import Entry
from akashic.models.fs_unbound_identity import FsUnboundIdentity
from akashic.models.principals_cache import PrincipalsCache
from akashic.models.scan import Scan
from akashic.models.scan_snapshot import ScanSnapshot
from akashic.models.source import Source
from akashic.models.user import User


async def _admin_token(db_session: AsyncSession) -> tuple[User, str]:
    user = User(id=uuid.uuid4(), username="adm", email="a@e", password_hash="x", role="admin")
    db_session.add(user)
    await db_session.commit()
    return user, create_access_token({"sub": str(user.id)})


@pytest.mark.asyncio
async def test_summary_basic_shape(client: AsyncClient, db_session: AsyncSession):
    """A clean DB still returns the expected JSON shape — every tile
    keys exist so the web side never has to defensively check for
    missing fields after a fresh deploy."""
    _, token = await _admin_token(db_session)

    resp = await client.get(
        "/api/dashboard/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) >= {
        "storage", "scans", "forecast_hints", "top_owners",
        "top_extensions_growth_30d", "recent_scans",
        "access_risks", "identity_health",
    }
    assert body["storage"]["total_bytes"] == 0
    assert body["storage"]["total_files"] == 0
    # Empty DB → 30d delta is unknown (None), not 0; the UI distinguishes
    # them ("no history" vs "no change").
    assert body["storage"]["delta_30d_bytes"] is None
    assert body["scans"]["active"] == 0
    assert body["top_owners"] == []
    assert body["recent_scans"] == []
    # v0.4.4: access_risks split off into GET /dashboard/access-risks.
    # Summary always returns null here so the heavy COUNT doesn't
    # bottleneck storage / scans / owner tiles during active scans.
    assert body["access_risks"] is None


@pytest.mark.asyncio
async def test_summary_aggregates_top_owners_across_sources(
    client: AsyncClient, db_session: AsyncSession,
):
    _, token = await _admin_token(db_session)

    src_a = Source(id=uuid.uuid4(), name="A", type="local", connection_config={})
    src_b = Source(id=uuid.uuid4(), name="B", type="local", connection_config={})
    db_session.add_all([src_a, src_b])
    await db_session.commit()

    now = datetime.now(timezone.utc)
    db_session.add_all([
        ScanSnapshot(
            id=uuid.uuid4(), source_id=src_a.id,
            taken_at=now, file_count=10, directory_count=0, total_size_bytes=1000,
            by_owner={"alice": {"n": 8, "bytes": 800}, "bob": {"n": 2, "bytes": 200}},
            by_extension={}, by_kind_and_age={},
        ),
        ScanSnapshot(
            id=uuid.uuid4(), source_id=src_b.id,
            taken_at=now, file_count=5, directory_count=0, total_size_bytes=500,
            by_owner={"alice": {"n": 1, "bytes": 100}, "carol": {"n": 4, "bytes": 400}},
            by_extension={}, by_kind_and_age={},
        ),
    ])
    await db_session.commit()

    body = (await client.get(
        "/api/dashboard/summary",
        headers={"Authorization": f"Bearer {token}"},
    )).json()

    # Owners aggregated across sources, sorted desc by bytes.
    owners = body["top_owners"]
    by_name = {o["owner"]: o for o in owners}
    assert by_name["alice"]["bytes"] == 900     # 800 + 100
    assert by_name["alice"]["n"] == 9           # 8 + 1
    # Sort order — alice (900) > carol (400) > bob (200).
    assert [o["owner"] for o in owners] == ["alice", "carol", "bob"]
    # Storage totals are sum of latest per-source.
    assert body["storage"]["total_bytes"] == 1500
    assert body["storage"]["total_files"] == 15


@pytest.mark.asyncio
async def test_summary_30d_delta_and_extension_growth(
    client: AsyncClient, db_session: AsyncSession,
):
    """A 31-day-old snapshot + a current snapshot → delta is the
    arithmetic difference. Extensions growing more than they shrunk
    appear in top_extensions_growth_30d, sorted desc by delta."""
    _, token = await _admin_token(db_session)

    src = Source(id=uuid.uuid4(), name="S", type="local", connection_config={})
    db_session.add(src)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    long_ago = now - timedelta(days=45)
    db_session.add_all([
        ScanSnapshot(
            id=uuid.uuid4(), source_id=src.id,
            taken_at=long_ago, file_count=10, directory_count=0, total_size_bytes=500,
            by_owner={}, by_extension={
                "pdf": {"n": 5, "bytes": 500},
                "mp4": {"n": 2, "bytes": 200},
            },
            by_kind_and_age={},
        ),
        ScanSnapshot(
            id=uuid.uuid4(), source_id=src.id,
            taken_at=now, file_count=20, directory_count=0, total_size_bytes=1500,
            by_owner={}, by_extension={
                "pdf": {"n": 5, "bytes": 500},     # unchanged
                "mp4": {"n": 4, "bytes": 1000},   # +800 bytes
            },
            by_kind_and_age={},
        ),
    ])
    await db_session.commit()

    body = (await client.get(
        "/api/dashboard/summary",
        headers={"Authorization": f"Bearer {token}"},
    )).json()

    assert body["storage"]["delta_30d_bytes"] == 1000   # 1500 - 500
    assert body["storage"]["delta_30d_files"] == 10     # 20 - 10

    growth = body["top_extensions_growth_30d"]
    assert len(growth) == 1
    assert growth[0]["extension"] == "mp4"
    assert growth[0]["delta_bytes"] == 800
    # pdf had no growth, so it's not included.
    assert all(g["extension"] != "pdf" for g in growth)


@pytest.mark.asyncio
async def test_access_risks_endpoint_admin_only(
    client: AsyncClient, db_session: AsyncSession,
):
    """A file with `*` in viewable_by_read counts as world-readable.
    Non-admin callers get a 200 with `access_risks: null` so the
    Dashboard tile can render a placeholder without a 403 round-trip
    on every mount.

    v0.4.4: split off from the summary endpoint into its own lazy
    fetch so a slow COUNT doesn't bottleneck the rest of the page.
    """
    # Bust the in-process server-side cache so neither order of
    # admin/viewer assertions reads a previous test's value.
    from akashic.routers import dashboard as dashboard_router
    dashboard_router._risk_cache.clear()

    src = Source(id=uuid.uuid4(), name="S", type="local", connection_config={})
    admin = User(id=uuid.uuid4(), username="adm2", email="a2@e", password_hash="x", role="admin")
    viewer = User(id=uuid.uuid4(), username="viewer", email="v@e", password_hash="x", role="user")
    db_session.add_all([src, admin, viewer])
    await db_session.commit()

    db_session.add_all([
        Entry(
            id=uuid.uuid4(), source_id=src.id, kind="file",
            parent_path="/", path="/public", name="public",
            viewable_by_read=["*", "posix:uid:1000"],
            viewable_by_write=[], viewable_by_delete=[],
        ),
        Entry(
            id=uuid.uuid4(), source_id=src.id, kind="file",
            parent_path="/", path="/private", name="private",
            viewable_by_read=["posix:uid:1000"],
            viewable_by_write=[], viewable_by_delete=[],
        ),
    ])
    await db_session.commit()

    admin_token = create_access_token({"sub": str(admin.id)})
    viewer_token = create_access_token({"sub": str(viewer.id)})

    admin_body = (await client.get(
        "/api/dashboard/access-risks",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    assert admin_body["access_risks"] == {"public_read_count": 1}
    # First call is a fresh compute; cache_age starts at 0.
    assert admin_body["cache_age_seconds"] == 0

    viewer_body = (await client.get(
        "/api/dashboard/access-risks",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )).json()
    assert viewer_body == {"access_risks": None}


@pytest.mark.asyncio
async def test_access_risks_endpoint_caches(
    client: AsyncClient, db_session: AsyncSession,
):
    """Second call within the cache window serves from the in-process
    cache (cache_age_seconds > 0); avoids hammering the COUNT during
    a busy scan. v0.4.4."""
    from akashic.routers import dashboard as dashboard_router
    dashboard_router._risk_cache.clear()

    admin = User(
        id=uuid.uuid4(), username="adm-cache", email="ac@e",
        password_hash="x", role="admin",
    )
    db_session.add(admin)
    await db_session.commit()
    token = create_access_token({"sub": str(admin.id)})

    first = (await client.get(
        "/api/dashboard/access-risks",
        headers={"Authorization": f"Bearer {token}"},
    )).json()
    assert first["cache_age_seconds"] == 0

    second = (await client.get(
        "/api/dashboard/access-risks",
        headers={"Authorization": f"Bearer {token}"},
    )).json()
    assert second["access_risks"] == first["access_risks"]
    # Some non-zero age — the exact value depends on monotonic clock,
    # but 60s TTL means it's at most 60.
    assert 0 <= second["cache_age_seconds"] <= 60


@pytest.mark.asyncio
async def test_summary_identity_health_counts(
    client: AsyncClient, db_session: AsyncSession,
):
    admin, token = await _admin_token(db_session)

    src = Source(id=uuid.uuid4(), name="S", type="local", connection_config={})
    db_session.add(src)
    await db_session.commit()

    db_session.add_all([
        FsUnboundIdentity(
            id=uuid.uuid4(), user_id=admin.id,
            identity_type="sid", identifier="S-1-2-3", confidence="claim",
            groups=[],
        ),
        PrincipalsCache(source_id=src.id, sid="S-1-5-21-NULLNAME", name=None),
        PrincipalsCache(source_id=src.id, sid="S-1-5-21-RESOLVED", name="DOMAIN\\alice"),
    ])
    await db_session.commit()

    body = (await client.get(
        "/api/dashboard/summary",
        headers={"Authorization": f"Bearer {token}"},
    )).json()

    assert body["identity_health"]["unbound_count"] == 1
    # Only the NULL-name row counts as "unresolved".
    assert body["identity_health"]["unresolved_sid_count"] == 1


@pytest.mark.asyncio
async def test_summary_recent_scans_includes_source_name(
    client: AsyncClient, db_session: AsyncSession,
):
    _, token = await _admin_token(db_session)
    src = Source(id=uuid.uuid4(), name="MyShare", type="local", connection_config={})
    db_session.add(src)
    await db_session.commit()

    db_session.add(Scan(
        id=uuid.uuid4(), source_id=src.id, scan_type="incremental",
        status="completed", started_at=datetime.now(timezone.utc),
        files_found=42, files_new=10, files_changed=2, files_deleted=0,
    ))
    await db_session.commit()

    body = (await client.get(
        "/api/dashboard/summary",
        headers={"Authorization": f"Bearer {token}"},
    )).json()

    assert len(body["recent_scans"]) == 1
    s = body["recent_scans"][0]
    assert s["source_name"] == "MyShare"
    assert s["files_new"] == 10
