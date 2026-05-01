"""Dashboard summary aggregator — single round-trip for the homepage tiles.

Phase 7 rewrites the Dashboard around 8 click-through tiles, each
landing in a filtered destination (Search/Analytics/AdminAccess/etc).
Rather than firing one query per tile (which the old Dashboard did,
producing a waterfall of N+1 requests), we fold everything into one
endpoint: the page renders skeletons once, then everything appears.

Source-visibility scoping mirrors the existing /analytics endpoints:
admins see global numbers; non-admins see only the sources their
SourcePermission rows admit. The access-risks tile is admin-only —
"how many files are world-readable?" is a security operator question
and the answer is sensitive in itself.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user, get_permitted_source_ids
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.fs_unbound_identity import FsUnboundIdentity
from akashic.models.principals_cache import PrincipalsCache
from akashic.models.scan import Scan
from akashic.models.scan_snapshot import ScanSnapshot
from akashic.models.source import Source
from akashic.models.user import User

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


_TOP_N_OWNERS = 5
_TOP_N_EXTENSIONS = 5
_RECENT_SCANS = 6


async def _allowed_source_ids(user: User, db: AsyncSession) -> list[str] | None:
    """Returns None for admins (no scoping), [] when user has zero
    sources, or the explicit ID list for everyone else. Pre-converted to
    string so it slots straight into JSONB lookups."""
    allowed = await get_permitted_source_ids(user, db)
    if allowed is None:
        return None
    return [str(s) for s in allowed]


async def _latest_snapshots(
    db: AsyncSession, allowed: list[str] | None,
) -> list[ScanSnapshot]:
    """One row per source — the most recent snapshot. Used to compute
    'current' totals and the per-source breakdowns. Skipped sources
    (no snapshot ever) just don't contribute."""
    # Lateral DISTINCT ON: cheap "latest per group" with the existing
    # (source_id, taken_at DESC) index.
    inner = (
        select(ScanSnapshot)
        .order_by(ScanSnapshot.source_id, ScanSnapshot.taken_at.desc())
        .distinct(ScanSnapshot.source_id)
    )
    if allowed is not None:
        inner = inner.where(
            ScanSnapshot.source_id.in_([s for s in allowed]) if allowed else literal(False)
        )
    return list((await db.execute(inner)).scalars().all())


async def _snapshots_at_age(
    db: AsyncSession, allowed: list[str] | None, days: int,
) -> dict[str, ScanSnapshot]:
    """Per-source latest snapshot taken at-or-before `days` ago. Used
    for the 30d delta. Returns {source_id_str: ScanSnapshot}."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(ScanSnapshot)
        .where(ScanSnapshot.taken_at <= cutoff)
        .order_by(ScanSnapshot.source_id, ScanSnapshot.taken_at.desc())
        .distinct(ScanSnapshot.source_id)
    )
    if allowed is not None:
        stmt = stmt.where(
            ScanSnapshot.source_id.in_(allowed) if allowed else literal(False)
        )
    rows = (await db.execute(stmt)).scalars().all()
    return {str(r.source_id): r for r in rows}


def _aggregate_jsonb(snapshots: list[ScanSnapshot], field: str) -> dict[str, dict]:
    """Sum a per-snapshot top-N JSONB (by_owner / by_extension) across
    sources into a single {key: {"n": ..., "bytes": ...}} dict."""
    out: dict[str, dict] = {}
    for snap in snapshots:
        bx = getattr(snap, field) or {}
        for k, v in bx.items():
            if k == "_other":
                # _other rolls non-top-N items together — including it
                # would skew "top owner" toward the catch-all bucket.
                continue
            cur = out.setdefault(k, {"n": 0, "bytes": 0})
            cur["n"] += int(v.get("n", 0))
            cur["bytes"] += int(v.get("bytes", 0))
    return out


@router.get("/summary")
async def get_summary(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    is_admin = user.role == "admin"
    allowed = await _allowed_source_ids(user, db)

    # ── Source counts ───────────────────────────────────────────────────
    src_stmt = select(Source.id, Source.status, Source.name)
    if allowed is not None:
        src_stmt = src_stmt.where(
            Source.id.in_(allowed) if allowed else literal(False)
        )
    sources = (await db.execute(src_stmt)).all()
    active_scan_count = sum(1 for s in sources if s.status in ("running", "scanning"))

    # ── Storage totals + 30d delta (snapshot-driven) ───────────────────
    latest = await _latest_snapshots(db, allowed)
    older = await _snapshots_at_age(db, allowed, days=30)

    total_bytes = sum(s.total_size_bytes or 0 for s in latest)
    total_files = sum(s.file_count or 0 for s in latest)

    delta_bytes = 0
    delta_files = 0
    delta_known = False
    for snap in latest:
        prev = older.get(str(snap.source_id))
        if prev is not None:
            delta_known = True
            delta_bytes += (snap.total_size_bytes or 0) - (prev.total_size_bytes or 0)
            delta_files += (snap.file_count or 0) - (prev.file_count or 0)

    # ── Capacity-forecast hint (per top-3 sources by current bytes) ────
    # The full forecast endpoint runs a least-squares fit per source;
    # for the dashboard we just surface a slope estimate from the last
    # 30 days so each source's tile can show "+X GB/day" without a
    # second round-trip per source.
    sources_by_size = sorted(
        latest, key=lambda s: s.total_size_bytes or 0, reverse=True,
    )[:3]
    forecast_hints: list[dict] = []
    for snap in sources_by_size:
        prev = older.get(str(snap.source_id))
        if prev is None:
            continue
        elapsed_days = max(1.0, (snap.taken_at - prev.taken_at).total_seconds() / 86400)
        slope = ((snap.total_size_bytes or 0) - (prev.total_size_bytes or 0)) / elapsed_days
        # Source name lookup — small list, in-memory is fine.
        name = next((s.name for s in sources if s.id == snap.source_id), None)
        forecast_hints.append({
            "source_id": str(snap.source_id),
            "source_name": name,
            "current_bytes": int(snap.total_size_bytes or 0),
            "slope_bytes_per_day": int(slope),
        })

    # ── Top owners across sources (latest snapshots aggregate) ─────────
    by_owner_agg = _aggregate_jsonb(latest, "by_owner")
    top_owners = sorted(
        ({"owner": k, "n": v["n"], "bytes": v["bytes"]} for k, v in by_owner_agg.items()),
        key=lambda o: o["bytes"], reverse=True,
    )[:_TOP_N_OWNERS]

    # ── Top extensions by 30d growth ───────────────────────────────────
    by_ext_now = _aggregate_jsonb(latest, "by_extension")
    by_ext_then = _aggregate_jsonb(list(older.values()), "by_extension")
    growth: list[dict] = []
    for ext, cur in by_ext_now.items():
        prev = by_ext_then.get(ext, {"n": 0, "bytes": 0})
        delta = cur["bytes"] - prev["bytes"]
        if delta > 0:
            growth.append({"extension": ext, "delta_bytes": delta, "current_bytes": cur["bytes"]})
    growth.sort(key=lambda x: x["delta_bytes"], reverse=True)
    top_extensions_growth = growth[:_TOP_N_EXTENSIONS]

    # ── Recent scans ───────────────────────────────────────────────────
    scan_stmt = (
        select(Scan)
        .order_by(Scan.started_at.desc().nulls_last())
        .limit(_RECENT_SCANS)
    )
    if allowed is not None:
        scan_stmt = scan_stmt.where(
            Scan.source_id.in_(allowed) if allowed else literal(False)
        )
    recent_scans_rows = (await db.execute(scan_stmt)).scalars().all()
    source_name_by_id = {s.id: s.name for s in sources}
    recent_scans = [
        {
            "id": str(s.id),
            "source_id": str(s.source_id),
            "source_name": source_name_by_id.get(s.source_id),
            "scan_type": s.scan_type,
            "status": s.status,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            "files_new": s.files_new,
            "files_changed": s.files_changed,
        }
        for s in recent_scans_rows
    ]

    # ── Access risks (admin only) ───────────────────────────────────────
    access_risks: dict | None = None
    if is_admin:
        # `*` is the wildcard "anyone" token from acl_denorm. Its presence
        # in viewable_by_read means the entry is world-readable. We count
        # files only (directories and stale rows are filtered out).
        public_count_stmt = (
            select(func.count(Entry.id))
            .where(
                Entry.kind == "file",
                Entry.is_deleted == False,  # noqa: E712
                Entry.viewable_by_read.op("&&")(["*"]),
            )
        )
        public_count = (await db.execute(public_count_stmt)).scalar() or 0
        access_risks = {"public_read_count": int(public_count)}

    # ── Identity health (admin or self-relevant counts) ────────────────
    if is_admin:
        unbound_count_stmt = select(func.count(FsUnboundIdentity.id))
        unresolved_sid_stmt = select(func.count()).select_from(PrincipalsCache).where(
            PrincipalsCache.name.is_(None),
        )
    else:
        unbound_count_stmt = (
            select(func.count(FsUnboundIdentity.id))
            .where(FsUnboundIdentity.user_id == user.id)
        )
        # Non-admins don't need cross-source unresolved counts.
        unresolved_sid_stmt = select(literal(0))
    unbound_count = (await db.execute(unbound_count_stmt)).scalar() or 0
    unresolved_sid = (await db.execute(unresolved_sid_stmt)).scalar() or 0
    identity_health = {
        "unbound_count": int(unbound_count),
        "unresolved_sid_count": int(unresolved_sid),
    }

    return {
        "storage": {
            "total_bytes": int(total_bytes),
            "total_files": int(total_files),
            "delta_30d_bytes": int(delta_bytes) if delta_known else None,
            "delta_30d_files": int(delta_files) if delta_known else None,
        },
        "scans": {
            "active": active_scan_count,
            "total_sources": len(sources),
        },
        "forecast_hints": forecast_hints,
        "top_owners": top_owners,
        "top_extensions_growth_30d": top_extensions_growth,
        "recent_scans": recent_scans,
        "access_risks": access_risks,
        "identity_health": identity_health,
    }


__all__ = ["router"]
