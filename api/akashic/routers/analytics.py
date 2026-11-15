import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.source_scope import restrict_sources
from akashic.auth.dependencies import Principal, check_source_access, require_scope
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.routers.browse import _should_enforce_perms
from akashic.services.access_query import user_principal_tokens, viewable_clause
from akashic.models.scan_snapshot import ScanSnapshot
from akashic.models.source import Source
from akashic.models.user import User

router = APIRouter(prefix="/analytics", tags=["analytics"])


_FILE_FILTERS = (Entry.kind == "file", Entry.is_deleted == False)  # noqa: E712


@router.get("/storage-by-type")
async def get_storage_by_type(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("analytics:read", source_scoped=True),
):
    filters = list(_FILE_FILTERS)

    stmt = restrict_sources(
        select(
            Entry.extension,
            func.count(Entry.id).label("count"),
            func.sum(Entry.size_bytes).label("total_size"),
        )
        .where(*filters)
        .group_by(Entry.extension)
        .order_by(func.sum(Entry.size_bytes).desc()),
        principal,
    )
    result = await db.execute(stmt)
    return [
        {"extension": r.extension, "count": r.count, "total_size": r.total_size}
        for r in result.all()
    ]


@router.get("/storage-by-source")
async def get_storage_by_source(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("analytics:read", source_scoped=True),
):
    filters = list(_FILE_FILTERS)

    stmt = restrict_sources(
        select(
            Entry.source_id,
            Source.name.label("source_name"),
            func.count(Entry.id).label("count"),
            func.sum(Entry.size_bytes).label("total_size"),
        )
        .join(Source, Source.id == Entry.source_id)
        .where(*filters)
        .group_by(Entry.source_id, Source.name)
        .order_by(func.sum(Entry.size_bytes).desc()),
        principal,
    )
    result = await db.execute(stmt)
    return [
        {
            "source_id": str(r.source_id),
            "source_name": r.source_name,
            "count": r.count,
            "total_size": r.total_size,
        }
        for r in result.all()
    ]


@router.get("/largest-files")
async def get_largest_files(
    n: int = Query(default=10, le=100),
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("analytics:read", source_scoped=True),
):
    user = principal.user
    filters = [*_FILE_FILTERS, Entry.size_bytes.isnot(None)]
    if await _should_enforce_perms(principal, True, db):
        filters.append(viewable_clause(await user_principal_tokens(user, db), "read"))

    stmt = (
        restrict_sources(select(Entry).where(*filters), principal)
        .order_by(Entry.size_bytes.desc())
        .limit(n)
    )
    result = await db.execute(stmt)
    entries = result.scalars().all()
    return [
        {
            "id": str(e.id),
            "source_id": str(e.source_id),
            "path": e.path,
            "filename": e.name,
            "size_bytes": e.size_bytes,
            "mime_type": e.mime_type,
        }
        for e in entries
    ]


# ── Time-series endpoints (driven by scan_snapshots) ──────────────────────


@router.get("/timeseries")
async def get_timeseries(
    source_id: uuid.UUID,
    metric: str = Query(default="size", pattern="^(size|count)$"),
    days: int = Query(default=90, ge=1, le=730),
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("analytics:read", source_scoped=True),
):
    """Return [{taken_at, value}] points for the given source's metric.

    `size` reads `total_size_bytes`; `count` reads `file_count`. Caller
    can plot directly in recharts. The response is ordered oldest-to-newest
    so the X axis is monotonic without a client-side sort."""
    await check_source_access(source_id, principal, db)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    col = ScanSnapshot.total_size_bytes if metric == "size" else ScanSnapshot.file_count

    rows = (
        await db.execute(
            select(ScanSnapshot.taken_at, col.label("value"))
            .where(
                ScanSnapshot.source_id == source_id,
                ScanSnapshot.taken_at >= cutoff,
            )
            .order_by(ScanSnapshot.taken_at.asc())
        )
    ).all()
    return [{"taken_at": r.taken_at.isoformat(), "value": int(r.value)} for r in rows]


@router.get("/forecast")
async def get_forecast(
    source_id: uuid.UUID,
    horizon_days: int = Query(default=30, ge=1, le=365),
    lookback_days: int = Query(default=90, ge=7, le=730),
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("analytics:read", source_scoped=True),
):
    """Linear extrapolation of total_size_bytes over `lookback_days`.

    No statsmodels/scipy dependency — slope from least-squares against
    the last N snapshots, residual stddev for a 95% interval band. If
    fewer than 3 snapshots exist, returns `null` for the projection so
    the UI can render a "not enough history yet" empty state."""
    await check_source_access(source_id, principal, db)

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = (
        await db.execute(
            select(ScanSnapshot.taken_at, ScanSnapshot.total_size_bytes)
            .where(
                ScanSnapshot.source_id == source_id,
                ScanSnapshot.taken_at >= cutoff,
            )
            .order_by(ScanSnapshot.taken_at.asc())
        )
    ).all()

    if len(rows) < 3:
        return {
            "history": [
                {"taken_at": r.taken_at.isoformat(), "value": int(r.total_size_bytes)}
                for r in rows
            ],
            "forecast": None,
            "reason": "insufficient_history",
        }

    # Least-squares fit on (seconds-since-epoch, bytes). Residual stddev
    # gives the 95% interval (≈ 1.96σ) widening with the horizon.
    xs = [r.taken_at.timestamp() for r in rows]
    ys = [float(r.total_size_bytes) for r in rows]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    slope = num / den if den > 0 else 0.0
    intercept = my - slope * mx
    residuals = [(y - (slope * x + intercept)) for x, y in zip(xs, ys)]
    sigma = (sum(r * r for r in residuals) / max(1, n - 2)) ** 0.5

    last_t = xs[-1]
    horizon_t = last_t + horizon_days * 86400
    points: list[dict] = []
    for i in range(0, horizon_days + 1, max(1, horizon_days // 30)):
        t = last_t + i * 86400
        value = max(0.0, slope * t + intercept)
        # Interval grows √h with horizon — classic forecast-band shape.
        band = 1.96 * sigma * (1 + (i / max(1, horizon_days)))
        points.append({
            "taken_at": datetime.fromtimestamp(t, tz=timezone.utc).isoformat(),
            "value": int(value),
            "low": int(max(0, value - band)),
            "high": int(value + band),
        })

    return {
        "history": [
            {"taken_at": r.taken_at.isoformat(), "value": int(r.total_size_bytes)}
            for r in rows
        ],
        "forecast": {
            "points": points,
            "slope_bytes_per_day": int(slope * 86400),
            "horizon_days": horizon_days,
        },
        "reason": "ok",
    }


@router.get("/extension-trend")
async def get_extension_trend(
    source_id: uuid.UUID,
    extensions: str = Query(..., description="comma-separated, e.g. pdf,mp4,docx"),
    days: int = Query(default=90, ge=1, le=730),
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("analytics:read", source_scoped=True),
):
    """Per-extension series extracted from each snapshot's by_extension JSONB.

    Returns `{extension: [{taken_at, n, bytes}]}`. Missing extensions in
    a given snapshot return zeros so the chart's lines remain monotonic
    in time."""
    await check_source_access(source_id, principal, db)
    requested = [e.strip().lower() for e in extensions.split(",") if e.strip()]
    if not requested:
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        await db.execute(
            select(ScanSnapshot.taken_at, ScanSnapshot.by_extension)
            .where(
                ScanSnapshot.source_id == source_id,
                ScanSnapshot.taken_at >= cutoff,
            )
            .order_by(ScanSnapshot.taken_at.asc())
        )
    ).all()

    out: dict[str, list[dict]] = {ext: [] for ext in requested}
    for r in rows:
        bx = r.by_extension or {}
        for ext in requested:
            entry = bx.get(ext) or {"n": 0, "bytes": 0}
            out[ext].append({
                "taken_at": r.taken_at.isoformat(),
                "n": int(entry.get("n", 0)),
                "bytes": int(entry.get("bytes", 0)),
            })
    return out


@router.get("/owner-distribution")
async def get_owner_distribution(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("analytics:read", source_scoped=True),
):
    """Latest snapshot's by_owner — one row per owner_name, ordered by bytes.

    Names are hydrated via principals_cache when the owner_name looks
    like a SID; raw POSIX names pass through. Used by the Dashboard's
    'Top owners by size' tile."""
    await check_source_access(source_id, principal, db)

    row = (
        await db.execute(
            select(ScanSnapshot.taken_at, ScanSnapshot.by_owner)
            .where(ScanSnapshot.source_id == source_id)
            .order_by(ScanSnapshot.taken_at.desc())
            .limit(1)
        )
    ).first()

    if not row:
        return {"taken_at": None, "owners": []}

    by_owner = row.by_owner or {}
    owners = [
        {"owner": k, "n": int(v.get("n", 0)), "bytes": int(v.get("bytes", 0))}
        for k, v in by_owner.items()
    ]
    owners.sort(key=lambda o: o["bytes"], reverse=True)
    return {"taken_at": row.taken_at.isoformat(), "owners": owners}
