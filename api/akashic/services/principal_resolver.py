"""Resolve unresolved SIDs against an SMB source's LSARPC, with caching.

Background — why this exists post-scan:

The SMB connector tries to resolve SIDs at scan time via LSARPC over
the IPC$ pipe (see `scanner/internal/connector/smb.go`). When LSARPC
is reachable that's the path. When it isn't (DC down at scan time,
network ACL blocked, etc.), the connector ships raw "S-1-5-21-…"
strings into the entry's `acl` JSON. The web app then has to render
either an opaque SID or fall back to italic-gray "unresolved" text
(see `web/src/components/acl/NtACL.tsx`).

This service offers a second chance: when the user actually opens an
entry, we batch the unresolved SIDs from its ACL, spawn the bundled
scanner with `resolve-sids`, and update the cache with whatever LSARPC
returns now.

The cache is per-source because the same SID can name different
principals across two domains. TTLs: positive resolutions stay valid
for 7 days (most renames don't propagate that fast and the user can
force a re-scan if they did); negative cache rows get re-attempted
after 1 hour to avoid hammering the DC if it's transiently down.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shlex
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.principals_cache import PrincipalsCache
from akashic.models.source import Source
from akashic.services.scanner_helpers import scanner_binary_path

logger = logging.getLogger(__name__)

# Cache freshness windows. Picked to balance "user sees a fresh name
# when something just got renamed" against "we don't slam the DC for
# every page load when LSARPC is offline".
POSITIVE_TTL = timedelta(days=7)
NEGATIVE_TTL = timedelta(hours=1)


@dataclass
class ResolvedPrincipal:
    """Per-SID result returned to the web. Status describes why a name
    might be missing — important UX signal for the unresolved-italic
    treatment vs. "we never tried"."""
    sid: str
    name: str | None
    domain: str | None
    kind: str | None
    status: str  # "resolved" | "unresolved" | "skipped" | "error"
    last_attempt_at: datetime | None


def _to_dict(p: ResolvedPrincipal) -> dict[str, Any]:
    return {
        "sid": p.sid,
        "name": p.name,
        "domain": p.domain,
        "kind": p.kind,
        "status": p.status,
        "last_attempt_at": p.last_attempt_at.isoformat() if p.last_attempt_at else None,
    }


def _split_cache_hits_misses(
    rows: list[PrincipalsCache], requested: list[str], now: datetime,
) -> tuple[dict[str, ResolvedPrincipal], list[str]]:
    """Filter cache rows into "fresh enough to return" and "stale or missing".

    Stale rows aren't returned to the caller — we want them re-resolved
    inline so the user sees the most current state when they open an
    entry. Negative cache rows that are still inside NEGATIVE_TTL ARE
    returned (we'd just fail again if we retried)."""
    fresh: dict[str, ResolvedPrincipal] = {}
    by_sid = {r.sid: r for r in rows}
    misses: list[str] = []
    for sid in requested:
        row = by_sid.get(sid)
        if row is None:
            misses.append(sid)
            continue
        if row.name is not None:
            # Positive cache row.
            if row.resolved_at and (now - row.resolved_at) <= POSITIVE_TTL:
                fresh[sid] = ResolvedPrincipal(
                    sid=sid,
                    name=row.name,
                    domain=row.domain,
                    kind=row.kind,
                    status="resolved",
                    last_attempt_at=row.last_attempt_at,
                )
                continue
        else:
            # Negative cache row.
            if (now - row.last_attempt_at) <= NEGATIVE_TTL:
                fresh[sid] = ResolvedPrincipal(
                    sid=sid,
                    name=None,
                    domain=None,
                    kind=None,
                    status="unresolved",
                    last_attempt_at=row.last_attempt_at,
                )
                continue
        misses.append(sid)
    return fresh, misses


async def _spawn_resolve_sids(source: Source, sids: list[str]) -> dict[str, Any]:
    """Run `akashic-scanner resolve-sids` for a batch of SIDs.

    Returns the parsed JSON {"resolved": {sid: {...}}, "unresolved": [...]}
    on success. Raises RuntimeError on spawn / non-zero exit / parse
    failure — the caller treats those as "couldn't resolve any of these
    SIDs" and writes negative cache rows.
    """
    cfg: dict[str, Any] = source.connection_config or {}
    if source.type != "smb":
        raise RuntimeError(f"resolve-sids only supports smb sources (got {source.type!r})")
    host = (cfg.get("host") or "").strip()
    user = (cfg.get("username") or "").strip()
    if not host or not user:
        raise RuntimeError("source missing host/username for SID resolution")
    port = int(cfg.get("port") or 445)
    password = cfg.get("password") or ""

    binary = scanner_binary_path()
    if not binary:
        raise RuntimeError("akashic-scanner binary not on PATH")

    argv = [
        binary, "resolve-sids",
        "--type=smb",
        "--host", host,
        "--port", str(port),
        "--user", user,
        "--password-stdin",
    ]
    payload = json.dumps({"password": password, "sids": sids}) + "\n"
    logger.info(
        "resolve-sids spawn: source=%s host=%s sids=%d argv=%s",
        source.id, host, len(sids), shlex.join(argv),
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Per-batch timeout: 30 s covers SMB session setup + LSARPC bind
        # + the lookup itself. A DC that's silent past 30 s should be
        # treated as down; the user can retry by reopening the entry.
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=payload.encode()), timeout=30.0,
        )
    except asyncio.TimeoutError:
        raise RuntimeError("resolve-sids: scanner timeout after 30s")
    except OSError as exc:
        raise RuntimeError(f"resolve-sids: spawn failed: {exc}")

    if proc.returncode != 0:
        msg = (stderr_b.decode("utf-8", errors="replace") or "").strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"resolve-sids: {msg}")

    try:
        return json.loads(stdout_b.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"resolve-sids: scanner output not JSON: {exc}")


async def _persist_results(
    db: AsyncSession,
    source_id: uuid.UUID,
    resolved_rows: list[dict[str, Any]],
    unresolved_sids: list[str],
    now: datetime,
) -> None:
    """Upsert resolved + negative-cache rows into principals_cache.

    Uses ON CONFLICT DO UPDATE keyed on (source_id, sid) so a stale
    negative entry gets promoted to a positive one when the DC comes
    back, and vice versa."""
    if resolved_rows:
        stmt = pg_insert(PrincipalsCache).values([
            {
                "source_id": source_id,
                "sid": r["sid"],
                "name": r["name"],
                "domain": r.get("domain"),
                "kind": r.get("kind"),
                "resolved_at": now,
                "last_attempt_at": now,
            }
            for r in resolved_rows
        ])
        await db.execute(stmt.on_conflict_do_update(
            index_elements=["source_id", "sid"],
            set_={
                "name": stmt.excluded.name,
                "domain": stmt.excluded.domain,
                "kind": stmt.excluded.kind,
                "resolved_at": stmt.excluded.resolved_at,
                "last_attempt_at": stmt.excluded.last_attempt_at,
            },
        ))
    if unresolved_sids:
        stmt = pg_insert(PrincipalsCache).values([
            {
                "source_id": source_id,
                "sid": sid,
                "name": None,
                "domain": None,
                "kind": None,
                "resolved_at": None,
                "last_attempt_at": now,
            }
            for sid in unresolved_sids
        ])
        await db.execute(stmt.on_conflict_do_update(
            index_elements=["source_id", "sid"],
            set_={"last_attempt_at": stmt.excluded.last_attempt_at},
        ))
    await db.commit()


async def resolve_principals(
    db: AsyncSession,
    source_id: uuid.UUID,
    sids: list[str],
) -> dict[str, ResolvedPrincipal]:
    """Top-level entry point. Returns a sid → ResolvedPrincipal map for
    every input SID, populating from cache where fresh and from a
    scanner spawn for cache misses.

    Errors from the scanner spawn (binary missing, timeout, SMB auth
    failure) flow through as a `status="error"` per affected SID rather
    than raising — the user's ACL view shouldn't fail entirely just
    because the DC is briefly unreachable.
    """
    if not sids:
        return {}
    sids = list({s for s in sids if s})  # dedupe; drop empties
    if not sids:
        return {}

    now = datetime.now(timezone.utc)

    # 1. Load cache rows for the requested set.
    cache_rows = (await db.execute(
        select(PrincipalsCache).where(
            PrincipalsCache.source_id == source_id,
            PrincipalsCache.sid.in_(sids),
        )
    )).scalars().all()
    fresh, to_resolve = _split_cache_hits_misses(cache_rows, sids, now)

    if not to_resolve:
        return fresh

    # 2. Verify the source exists and is an SMB source. Local/NFS/S3
    #    sources have no LSARPC concept — skip with status=skipped so
    #    the UI knows resolution wasn't possible (vs. just "we tried
    #    and failed").
    source = (await db.execute(
        select(Source).where(Source.id == source_id)
    )).scalar_one_or_none()
    if source is None:
        for sid in to_resolve:
            fresh[sid] = ResolvedPrincipal(
                sid=sid, name=None, domain=None, kind=None,
                status="error", last_attempt_at=now,
            )
        return fresh
    if source.type != "smb":
        for sid in to_resolve:
            fresh[sid] = ResolvedPrincipal(
                sid=sid, name=None, domain=None, kind=None,
                status="skipped", last_attempt_at=None,
            )
        return fresh

    # 3. Spawn scanner. Failures translate to status="error" for each
    #    requested SID — they're NOT written to the cache (an error is
    #    transient state; we want to retry next time).
    try:
        result = await _spawn_resolve_sids(source, to_resolve)
    except RuntimeError as exc:
        logger.warning("resolve_principals: scanner spawn failed for source=%s: %s", source_id, exc)
        for sid in to_resolve:
            fresh[sid] = ResolvedPrincipal(
                sid=sid, name=None, domain=None, kind=None,
                status="error", last_attempt_at=now,
            )
        return fresh

    # 4. Build response + persist cache rows (positive + negative).
    resolved_payload: dict[str, dict[str, Any]] = result.get("resolved") or {}
    unresolved_payload: list[str] = result.get("unresolved") or []

    resolved_rows: list[dict[str, Any]] = []
    for sid, body in resolved_payload.items():
        name = body.get("name") or None
        if not name:
            unresolved_payload.append(sid)
            continue
        domain = body.get("domain") or None
        kind = body.get("kind") or None
        resolved_rows.append({
            "sid": sid, "name": name, "domain": domain, "kind": kind,
        })
        fresh[sid] = ResolvedPrincipal(
            sid=sid, name=name, domain=domain, kind=kind,
            status="resolved", last_attempt_at=now,
        )

    seen = {r["sid"] for r in resolved_rows}
    really_unresolved = [s for s in unresolved_payload if s not in seen]
    for sid in really_unresolved:
        fresh[sid] = ResolvedPrincipal(
            sid=sid, name=None, domain=None, kind=None,
            status="unresolved", last_attempt_at=now,
        )

    await _persist_results(db, source_id, resolved_rows, really_unresolved, now)
    return fresh


__all__ = ["ResolvedPrincipal", "resolve_principals", "POSITIVE_TTL", "NEGATIVE_TTL"]
