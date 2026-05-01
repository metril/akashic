"""Time-series analytics endpoints driven by scan_snapshots.

These cover the four new endpoints (timeseries, forecast,
extension-trend, owner-distribution). Snapshot rows are seeded
directly so the tests don't depend on the snapshot writer; that's
covered separately in test_snapshot_writer.py.
"""
from datetime import datetime, timedelta, timezone

import pytest

from akashic.models.scan_snapshot import ScanSnapshot
from akashic.models.source import Source


async def _register_login(client, username="admin", password="testpass123"):
    await client.post("/api/users/register", json={"username": username, "password": password})
    login = await client.post(
        "/api/users/login",
        json={"username": username, "password": password},
    )
    return login.json()["access_token"]


async def _make_source(db, name="src"):
    s = Source(name=name, type="local", connection_config={"path": "/tmp"}, status="online")
    db.add(s)
    await db.flush()
    await db.refresh(s)
    return s


def _snap(source_id, *, taken_at, total=0, count=0, by_extension=None, by_owner=None):
    return ScanSnapshot(
        source_id=source_id,
        taken_at=taken_at,
        total_size_bytes=total,
        file_count=count,
        directory_count=0,
        by_extension=by_extension or {},
        by_owner=by_owner or {},
        by_kind_and_age={},
    )


@pytest.mark.asyncio
async def test_timeseries_returns_oldest_first(client, db_session):
    token = await _register_login(client)
    src = await _make_source(db_session)

    now = datetime.now(timezone.utc)
    db_session.add_all([
        _snap(src.id, taken_at=now - timedelta(days=2), total=100, count=10),
        _snap(src.id, taken_at=now - timedelta(days=1), total=200, count=20),
        _snap(src.id, taken_at=now,                       total=300, count=30),
    ])
    await db_session.commit()

    r = await client.get(
        f"/api/analytics/timeseries?source_id={src.id}&metric=size&days=30",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert [p["value"] for p in body] == [100, 200, 300]


@pytest.mark.asyncio
async def test_timeseries_metric_count(client, db_session):
    token = await _register_login(client)
    src = await _make_source(db_session)
    now = datetime.now(timezone.utc)
    db_session.add(_snap(src.id, taken_at=now, total=999, count=42))
    await db_session.commit()

    r = await client.get(
        f"/api/analytics/timeseries?source_id={src.id}&metric=count",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()[0]["value"] == 42


@pytest.mark.asyncio
async def test_forecast_with_insufficient_history_returns_null(client, db_session):
    token = await _register_login(client)
    src = await _make_source(db_session)
    now = datetime.now(timezone.utc)
    # Only 2 points; need at least 3 for a fit.
    db_session.add_all([
        _snap(src.id, taken_at=now - timedelta(days=1), total=100),
        _snap(src.id, taken_at=now,                       total=200),
    ])
    await db_session.commit()

    r = await client.get(
        f"/api/analytics/forecast?source_id={src.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["forecast"] is None
    assert body["reason"] == "insufficient_history"
    assert len(body["history"]) == 2


@pytest.mark.asyncio
async def test_forecast_extrapolates_growth(client, db_session):
    """A clean linear growth pattern should produce a positive slope."""
    token = await _register_login(client)
    src = await _make_source(db_session)
    now = datetime.now(timezone.utc)
    # 10 points over 9 days, ~100 GB/day with deterministic noise so the
    # least-squares fit has non-zero residual stddev (otherwise the
    # confidence band collapses to a line and width comparisons fail).
    GB = 1024 * 1024 * 1024
    noise_steps = [-7, +5, -3, +9, -2, +1, -8, +6, -4, +3]  # bytes-scale, in GB
    for i in range(10):
        db_session.add(_snap(
            src.id,
            taken_at=now - timedelta(days=9 - i),
            total=i * 100 * GB + noise_steps[i] * GB,
        ))
    await db_session.commit()

    r = await client.get(
        f"/api/analytics/forecast?source_id={src.id}&horizon_days=30",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["forecast"] is not None
    # Slope should be ~100 GB/day. Tolerance widened to 5% because of the
    # injected noise in the seed data.
    slope = body["forecast"]["slope_bytes_per_day"]
    expected = 100 * GB
    assert abs(slope - expected) / expected < 0.05
    # Forecast points exist and values monotonically grow.
    pts = body["forecast"]["points"]
    assert len(pts) > 0
    assert pts[-1]["value"] > pts[0]["value"]
    # Confidence band widens at the horizon.
    assert (pts[-1]["high"] - pts[-1]["low"]) > (pts[0]["high"] - pts[0]["low"])


@pytest.mark.asyncio
async def test_extension_trend_pads_missing_buckets_with_zeros(client, db_session):
    """A snapshot missing 'pdf' should still return zeros for that day so
    the chart's line stays continuous in time."""
    token = await _register_login(client)
    src = await _make_source(db_session)
    now = datetime.now(timezone.utc)

    db_session.add_all([
        _snap(
            src.id, taken_at=now - timedelta(days=1),
            by_extension={"pdf": {"n": 5, "bytes": 5000}, "txt": {"n": 1, "bytes": 100}},
        ),
        _snap(
            src.id, taken_at=now,
            by_extension={"txt": {"n": 2, "bytes": 200}},  # no pdf today
        ),
    ])
    await db_session.commit()

    r = await client.get(
        f"/api/analytics/extension-trend?source_id={src.id}&extensions=pdf,txt&days=30",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["pdf"]) == 2
    assert body["pdf"][0]["bytes"] == 5000
    assert body["pdf"][1]["bytes"] == 0
    assert body["txt"][1]["n"] == 2


@pytest.mark.asyncio
async def test_owner_distribution_returns_latest_snapshot_only(client, db_session):
    token = await _register_login(client)
    src = await _make_source(db_session)
    now = datetime.now(timezone.utc)

    # Old snapshot — should be ignored.
    db_session.add(_snap(
        src.id, taken_at=now - timedelta(days=3),
        by_owner={"alice": {"n": 10, "bytes": 1000}},
    ))
    # Latest snapshot — should be returned.
    db_session.add(_snap(
        src.id, taken_at=now,
        by_owner={
            "bob":   {"n": 5, "bytes": 500},
            "carol": {"n": 3, "bytes": 5000},
        },
    ))
    await db_session.commit()

    r = await client.get(
        f"/api/analytics/owner-distribution?source_id={src.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    # Sorted by bytes desc.
    assert [o["owner"] for o in body["owners"]] == ["carol", "bob"]
    assert body["owners"][0]["bytes"] == 5000


@pytest.mark.asyncio
async def test_owner_distribution_empty_when_no_snapshots(client, db_session):
    token = await _register_login(client)
    src = await _make_source(db_session)
    r = await client.get(
        f"/api/analytics/owner-distribution?source_id={src.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json() == {"taken_at": None, "owners": []}


@pytest.mark.asyncio
async def test_timeseries_403_for_inaccessible_source(client, db_session):
    """A non-admin user without source permission should get 403."""
    admin_token = await _register_login(client)
    # Make a regular user.
    await client.post(
        "/api/users/create",
        json={"username": "regular", "password": "testpass123", "role": "user"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    login = await client.post(
        "/api/users/login",
        json={"username": "regular", "password": "testpass123"},
    )
    user_token = login.json()["access_token"]

    src = await _make_source(db_session)
    r = await client.get(
        f"/api/analytics/timeseries?source_id={src.id}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert r.status_code == 403
