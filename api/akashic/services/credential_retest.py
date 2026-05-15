"""Auto-retest reachability after a CredentialProfile update (v0.29.5).

When a user fixes broken credentials, the old ``reachability_results``
rows still reflect the previous probe — the panel keeps showing green/
red against the OLD credentials until the user manually retests every
source. Combined with the v0.29.1 SMB guest-fallback bug and the
v0.29.5 empty-password bypass, a green-against-wrong-creds result
could persist indefinitely after a credential fix.

After a successful ``PATCH /api/credential-profiles/{id}`` (or any
write that changes the credentials dict), call
:func:`retest_sources_for_profile`. The fan-out runs as a FastAPI
``BackgroundTasks`` so the PATCH endpoint returns immediately —
slow-scanner round trips don't gate the credential-save UX.

A profile attached to many sources triggers many probe fan-outs;
:const:`_PER_UPDATE_FANOUT_CAP` caps each invocation at 50 sources.
The remainder can be retested via the per-source panel.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.host import Host
from akashic.models.source import Source

logger = logging.getLogger(__name__)


# Cap the fan-out at 50 sources per profile-update so a profile shared
# across dozens of sources doesn't fire a thundering-herd of probes.
# Past the cap, the user retests manually via the per-source panel.
_PER_UPDATE_FANOUT_CAP = 50


async def _affected_source_ids(
    db: AsyncSession, profile_id: uuid.UUID,
) -> list[uuid.UUID]:
    """Return the ids of every Source whose effective credentials
    derive from ``profile_id`` — directly attached, or inherited via
    its host.

    Single round-trip via a UNION of the two paths. Order by id for
    deterministic capping when the result exceeds the fan-out cap.
    """
    res = await db.execute(
        select(Source.id)
        .outerjoin(Host, Host.id == Source.host_id)
        .where(
            or_(
                Source.credential_profile_id == profile_id,
                Host.credential_profile_id == profile_id,
            )
        )
        .order_by(Source.id)
    )
    return [row[0] for row in res.fetchall()]


async def retest_sources_for_profile(
    db: AsyncSession,
    *,
    profile_id: uuid.UUID,
    user_id: uuid.UUID,
) -> int:
    """Fan out a reachability probe to every eligible scanner for every
    source whose credentials derive from ``profile_id``.

    Returns the count of (source, scanner) pairs the dispatch was
    issued against — useful for the call-site log line. A return of 0
    means no sources reference the profile or no scanners are eligible.

    Designed for use as a ``background_tasks.add_task`` payload from
    :mod:`akashic.routers.credential_profiles`; mutates the db (writes
    probe-dispatch rows via ``probe_dispatch.dispatch_remote``) and
    commits.
    """
    from akashic.models.scanner import Scanner
    from akashic.routers.sources import _eligible_scanners_for
    from akashic.services import probe_dispatch

    affected = await _affected_source_ids(db, profile_id)
    if not affected:
        logger.info(
            "credential_retest: profile=%s — no sources affected, skipping",
            profile_id,
        )
        return 0

    if len(affected) > _PER_UPDATE_FANOUT_CAP:
        logger.info(
            "credential_retest: profile=%s — %d sources affected, "
            "capping fan-out at %d (remainder can be retested via panel)",
            profile_id, len(affected), _PER_UPDATE_FANOUT_CAP,
        )
        affected = affected[:_PER_UPDATE_FANOUT_CAP]

    all_scanners = list(
        (await db.execute(select(Scanner))).scalars().all()
    )

    total_dispatched = 0
    for source_id in affected:
        src = (await db.execute(
            select(Source).where(Source.id == source_id)
        )).scalar_one_or_none()
        if src is None:
            continue
        eligible = _eligible_scanners_for(src, all_scanners, online_only=True)
        if not eligible:
            logger.debug(
                "credential_retest: source=%s — no eligible online scanners",
                source_id,
            )
            continue
        try:
            await probe_dispatch.dispatch_remote(
                db=db, source=src,
                scanner_ids=[s.id for s in eligible],
                timeout_s=5.0,
                triggered_by=user_id,
            )
            total_dispatched += len(eligible)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "credential_retest: dispatch failed source=%s: %s",
                source_id, exc,
            )

    if total_dispatched:
        logger.info(
            "credential_retest: profile=%s — dispatched probes across "
            "%d (source, scanner) pair(s) for %d source(s)",
            profile_id, total_dispatched, len(affected),
        )
    return total_dispatched
