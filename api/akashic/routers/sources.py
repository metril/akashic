import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import delete as sql_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import check_source_access, get_current_user, require_admin
from akashic.database import get_db
from akashic.models.audit_event import AuditEvent
from akashic.models.entry import Entry
from akashic.models.host import Host
from akashic.models.source import Source
from akashic.models.user import SourcePermission, User
from akashic.schemas.audit import AuditEventList, AuditEventOut
from akashic.schemas.source import (
    SourceCreate, SourceListResponse, SourceResponse, SourceUpdate,
)
from akashic.services.audit import record_event
from akashic.services.source_config import merge_host_and_source
from akashic.services.source_defaults import infer_is_removable
from akashic.services.source_merge import (
    field_diff,
    merge_connection_config,
    reject_sentinel_in_create,
)
from akashic.services.source_tester import TestResult, test_connection

router = APIRouter(prefix="/api/sources", tags=["sources"])

# Source types that don't attach to a Host row. `local` predates the
# Host abstraction; `paperless` (v0.7.0) and `immich` (v0.8.0) are
# self-hosted libraries where the URL + API token / key live on the
# source's connection_config directly. `azureblob` (v0.9.0), `gcs`
# (v0.10.0), and `webdav` (v0.11.0) follow the same hostless
# pattern — bucket / container / URL + auth fields all on the
# source. All create+update paths key off this set when validating
# host_id semantics.
HOSTLESS_SOURCE_TYPES = {
    "local", "paperless", "immich", "azureblob", "gcs", "webdav",
    "gdrive", "onedrive",
}


def _config_safe_summary(cfg: dict | None) -> dict:
    """Audit-safe snapshot of a connection_config: state tokens for
    secret keys, real values for the rest."""
    return {
        k: ("<set>" if v else "<empty>") if any(s in k.lower() for s in {"password", "secret", "key", "token", "credentials", "private_key"}) else v
        for k, v in (cfg or {}).items()
    }


@router.post("", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def create_source(
    data: SourceCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    err = reject_sentinel_in_create(data.connection_config)
    if err:
        raise HTTPException(status_code=400, detail=err)

    # Validate host_id semantics: local sources can't have one; non-local
    # sources without one will keep working today (their connection_config
    # carries everything), but users on the new flow are encouraged to
    # attach a Host so credentials are reusable across shares.
    if data.host_id is not None:
        if data.type in HOSTLESS_SOURCE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"{data.type} sources cannot attach to a host",
            )
        host = (await db.execute(
            select(Host).where(Host.id == data.host_id)
        )).scalar_one_or_none()
        if host is None:
            raise HTTPException(status_code=404, detail="host_id not found")
        if host.type != data.type:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"host type {host.type!r} does not match source type "
                    f"{data.type!r}"
                ),
            )

    if data.credential_profile_id is not None:
        from akashic.models.credential_profile import CredentialProfile
        p = (await db.execute(
            select(CredentialProfile).where(CredentialProfile.id == data.credential_profile_id)
        )).scalar_one_or_none()
        if p is None:
            raise HTTPException(status_code=404, detail="credential_profile_id not found")
        if p.type != data.type:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Credential profile type {p.type!r} does not match "
                    f"source type {data.type!r}."
                ),
            )

    payload = data.model_dump()
    mps = payload.get("max_parallel_scanners")
    if mps is None:
        payload["max_parallel_scanners"] = 1
    elif not (1 <= int(mps) <= 16):
        raise HTTPException(
            status_code=400,
            detail="max_parallel_scanners must be between 1 and 16",
        )
    if payload.get("is_removable") is None:
        # Inference uses the merged config — host fields (e.g. NFS host)
        # contribute too, but for "is removable?" we still only key off
        # local mount-point prefixes today.
        payload["is_removable"] = infer_is_removable(
            data.type, data.connection_config
        )
    # v0.14.0 — OAuth-shaped source types (gdrive, …) carry an
    # oauth_credential_id in connection_config that points at a
    # SourceOAuthCredential row created by the Sign-in flow with
    # source_id=NULL. Strip it from the persisted config and use it to
    # attach the credential to the new source post-create.
    oauth_credential_id = None
    if isinstance(payload.get("connection_config"), dict):
        oauth_credential_id = payload["connection_config"].pop(
            "oauth_credential_id", None
        )
    source = Source(**payload)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    if oauth_credential_id is not None:
        from akashic.models.oauth_credential import SourceOAuthCredential
        cred = await db.get(SourceOAuthCredential, oauth_credential_id)
        if cred is not None and cred.source_id is None:
            cred.source_id = source.id
            await db.commit()
    # Push to /ws/scans subscribers so the Sources page sees the
    # new card without polling.
    from akashic.services import scan_pubsub
    await scan_pubsub.publish_source_event({
        "kind": "source.created",
        "source_id": str(source.id),
        "source_status": source.status,
        "name": source.name,
        "type": source.type,
    })
    await record_event(
        db=db,
        user=user,
        event_type="source_created",
        source_id=source.id,
        request=request,
        payload={
            "name": source.name,
            "type": source.type,
            "config": _config_safe_summary(source.connection_config),
            "scan_schedule": source.scan_schedule,
        },
    )
    return source


@router.get("", response_model=list[SourceListResponse])
async def list_sources(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Lean list view — drops connection_config/security_metadata/
    exclude_patterns and ships a server-rendered `summary` for the
    SourceCard subtitle. Per-source detail (with full config) is
    served by GET /sources/{id} which the panel fetches on click."""
    if user.role == "admin":
        result = await db.execute(select(Source).order_by(Source.name))
    else:
        # Non-admins only see sources they have permission for
        result = await db.execute(
            select(Source)
            .join(SourcePermission, Source.id == SourcePermission.source_id)
            .where(SourcePermission.user_id == user.id)
            .order_by(Source.name)
        )
    return [SourceListResponse.from_source(s) for s in result.scalars().all()]


@router.get("/{source_id}", response_model=SourceResponse)
async def get_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await check_source_access(source_id, user, db)
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.get("/{source_id}/audit", response_model=AuditEventList)
async def get_source_audit(
    source_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Per-source audit timeline. Visible to any user with read access
    to the source — the same gate that lets them browse its files.

    Includes orphaned `source_deleted` events whose payload encodes the
    original source ID (the source row is gone by the time we record
    the event, so we can't FK it). Without this, the timeline would
    abruptly end at the second-to-last event for any deleted source.

    Pagination is offset-based but `total` is intentionally not
    computed — for high-traffic sources the COUNT(*) doubles every
    page request's cost. The UI uses page-by-page navigation; if it
    needs an exact total later we can add an opt-in flag.
    """
    await check_source_access(source_id, user, db, required_level="read")

    from sqlalchemy import or_
    stmt = (
        select(AuditEvent)
        .where(or_(
            AuditEvent.source_id == source_id,
            # Orphaned deletion events live without a source_id but
            # encode the original UUID in their payload.
            AuditEvent.payload["deleted_source_id"].astext == str(source_id),
        ))
        .order_by(AuditEvent.occurred_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size + 1)  # +1 to detect "more pages exist"
    )
    rows = (await db.execute(stmt)).scalars().all()
    has_more = len(rows) > page_size
    if has_more:
        rows = rows[:page_size]
    return AuditEventList(
        items=[AuditEventOut.model_validate(r) for r in rows],
        # `total = -1` signals "unknown — use page+has_more instead".
        # Frontend reads `len(items) < page_size or has_more` to render
        # next/prev controls.
        total=-1,
        page=page,
        page_size=page_size,
    )


@router.patch("/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: uuid.UUID,
    data: SourceUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    # Snapshot the before-state for the audit diff. Capture this BEFORE
    # any mutation so we have stable old values to compare against.
    before = {
        "name": source.name,
        "type": source.type,
        "connection_config": dict(source.connection_config or {}),
        "scan_schedule": source.scan_schedule,
        "exclude_patterns": list(source.exclude_patterns or []),
    }

    incoming = data.model_dump(exclude_unset=True)
    if "max_parallel_scanners" in incoming and incoming["max_parallel_scanners"] is not None:
        if not (1 <= int(incoming["max_parallel_scanners"]) <= 16):
            raise HTTPException(
                status_code=400,
                detail="max_parallel_scanners must be between 1 and 16",
            )
    if "host_id" in incoming:
        new_host_id = incoming["host_id"]
        if new_host_id is None:
            if source.type not in HOSTLESS_SOURCE_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail="host_id is required for this source type",
                )
        else:
            if source.type in HOSTLESS_SOURCE_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=f"{source.type} sources cannot attach to a host",
                )
            host = (await db.execute(
                select(Host).where(Host.id == new_host_id)
            )).scalar_one_or_none()
            if host is None:
                raise HTTPException(status_code=404, detail="host_id not found")
            if host.type != source.type:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"host type {host.type!r} does not match source type "
                        f"{source.type!r}"
                    ),
                )
    if "connection_config" in incoming and incoming["connection_config"]:
        # Reject "***" on non-secret keys at the validation layer —
        # never a meaningful value. Secret-named keys with "***" are
        # legitimate (the merge will preserve the existing secret),
        # so let those pass through to merge_connection_config.
        for k, v in incoming["connection_config"].items():
            if v == "***" and not any(
                token in k.lower()
                for token in {"password", "secret", "key", "token", "credentials", "private_key"}
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"connection_config.{k} = \"***\" — that's the "
                        "masked-secret sentinel; not a valid value for a "
                        "non-secret field."
                    ),
                )
    if "credential_profile_id" in incoming and incoming["credential_profile_id"] is not None:
        from akashic.models.credential_profile import CredentialProfile
        p = (await db.execute(
            select(CredentialProfile).where(CredentialProfile.id == incoming["credential_profile_id"])
        )).scalar_one_or_none()
        if p is None:
            raise HTTPException(status_code=404, detail="credential_profile_id not found")
        if p.type != source.type:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Credential profile type {p.type!r} does not match "
                    f"source type {source.type!r}."
                ),
            )

    for field, value in incoming.items():
        if field == "connection_config":
            # Secret-merge: preserve real secrets when the UI sends back
            # the masked sentinel `"***"`. See source_merge.py for why.
            value = merge_connection_config(source.connection_config, value)
        setattr(source, field, value)
    await db.commit()
    await db.refresh(source)

    after = {
        "name": source.name,
        "type": source.type,
        "connection_config": dict(source.connection_config or {}),
        "scan_schedule": source.scan_schedule,
        "exclude_patterns": list(source.exclude_patterns or []),
    }
    diff_payload: dict = {}
    for field, after_val in after.items():
        if field == "connection_config":
            cfg_diff = field_diff(before["connection_config"], after_val)
            if cfg_diff:
                diff_payload["connection_config"] = cfg_diff
        elif before[field] != after_val:
            diff_payload[field] = {"before": before[field], "after": after_val}

    if diff_payload:
        await record_event(
            db=db,
            user=user,
            event_type="source_updated",
            source_id=source.id,
            request=request,
            payload={"diff": diff_payload},
        )
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: uuid.UUID,
    request: Request,
    purge_entries: bool = Query(
        False,
        description=(
            "When true, also delete every indexed entry from this source. "
            "Default false: source row is removed but entries survive with "
            "source_id=NULL — they stay searchable, content fetch returns "
            "404, and they can be re-attached to a new source via "
            "POST /sources/{id}/reattach-orphans."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    snapshot = {
        "deleted_source_id": str(source.id),
        "name": source.name,
        "type": source.type,
        "config": _config_safe_summary(source.connection_config),
    }
    deleted_id = source.id

    # Snapshot the affected entry ids BEFORE the source delete so we
    # can sync Meilisearch in either flavour. On the preserve path
    # the FK rule will SET source_id=NULL after `db.delete(source)`;
    # on the purge path we explicitly delete the entries first
    # (otherwise the SET-NULL FK rule would fire and orphan them
    # before the purge runs).
    affected_entry_ids = list((await db.execute(
        select(Entry.id).where(Entry.source_id == source_id)
    )).scalars().all())

    if purge_entries:
        await db.execute(sql_delete(Entry).where(Entry.source_id == source_id))

    await db.delete(source)
    await db.commit()

    # Sync Meilisearch. Failures here are logged but don't break the
    # delete — search index drift is recoverable, a half-deleted
    # source row is not.
    from akashic.services import search
    try:
        if purge_entries:
            await search.delete_files_batch([str(i) for i in affected_entry_ids])
        elif affected_entry_ids:
            await search.update_files_partial(
                [{"id": str(i), "source_id": None} for i in affected_entry_ids]
            )
    except Exception:  # noqa: BLE001
        # Caller already saw the source delete succeed; surface the
        # search-sync issue via logs rather than 500'ing.
        import logging
        logging.getLogger(__name__).warning(
            "delete_source: search-index sync failed for %s entries",
            len(affected_entry_ids),
        )

    from akashic.services import scan_pubsub
    await scan_pubsub.publish_source_event({
        "kind": "source.deleted",
        "source_id": str(deleted_id),
    })
    # Pass source_id=None — the row is gone and the FK on audit_events
    # would reject an INSERT referencing it. The original ID lives in
    # the payload so the timeline still surfaces the deletion.
    await record_event(
        db=db,
        user=user,
        event_type="source_deleted",
        source_id=None,
        request=request,
        payload={
            **snapshot,
            "purge_entries": purge_entries,
            "affected_entry_count": len(affected_entry_ids),
        },
    )


class SourceEntryCount(BaseModel):
    count: int


@router.get("/{source_id}/entry-count", response_model=SourceEntryCount)
async def get_source_entry_count(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Cheap COUNT used by the delete-source modal to show blast
    radius. Excludes soft-deleted entries (`is_deleted = true`)
    since those wouldn't be visibly affected by the delete."""
    await check_source_access(source_id, user, db, required_level="read")
    cnt = (await db.execute(
        select(func.count())
        .select_from(Entry)
        .where(Entry.source_id == source_id, Entry.is_deleted.is_(False))
    )).scalar_one()
    return SourceEntryCount(count=int(cnt))


# ── Orphan recovery (v0.4.0) ─────────────────────────────────────────────


class OrphanMatchCount(BaseModel):
    count: int


@router.get(
    "/{source_id}/orphan-match-count",
    response_model=OrphanMatchCount,
)
async def get_orphan_match_count(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """How many orphaned entries (source_id IS NULL) share a path
    with an entry of THIS source. Used by the source-detail
    banner to decide whether to surface the 'Recover orphans'
    affordance — returns instantly because it's a single COUNT on
    indexed columns."""
    from akashic.services.orphan_matcher import count_potential_matches
    cnt = await count_potential_matches(db, source_id)
    return OrphanMatchCount(count=cnt)


class ReattachRequest(BaseModel):
    strategy: str = "path"  # "path" | "path_and_hash"
    dry_run: bool = True


class ReattachResponse(BaseModel):
    matched: int
    conflicts: int
    ambiguous: int
    committed: bool


@router.post(
    "/{source_id}/reattach-orphans",
    response_model=ReattachResponse,
)
async def reattach_orphans(
    source_id: uuid.UUID,
    body: ReattachRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Re-attach orphaned entries (source_id IS NULL) into this
    source where (path, name, kind) — and optionally content_hash
    — match a freshly-scanned entry. The orphan keeps its history
    (tags, version history, audit trail); the duplicate fresh
    entry is deleted."""
    from akashic.services.orphan_matcher import (
        Strategy, commit_matches, find_matches,
    )
    from akashic.services import search

    if body.strategy not in ("path", "path_and_hash"):
        raise HTTPException(
            status_code=400,
            detail=f"unknown strategy '{body.strategy}'; "
                   "expected 'path' or 'path_and_hash'",
        )
    # Source must exist (otherwise there are no fresh entries to
    # match against, but we want a clear 404 either way).
    src = (await db.execute(
        select(Source).where(Source.id == source_id)
    )).scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")

    summary = await find_matches(db, source_id, body.strategy)  # type: ignore[arg-type]

    if body.dry_run:
        return ReattachResponse(
            matched=summary.matched,
            conflicts=summary.conflicts,
            ambiguous=summary.ambiguous,
            committed=False,
        )

    reattached_ids = await commit_matches(db, source_id, summary.pairs)
    await db.commit()

    # Sync Meilisearch — re-attached docs get the new source_id.
    if reattached_ids:
        try:
            await search.update_files_partial(
                [{"id": str(i), "source_id": str(source_id)} for i in reattached_ids]
            )
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "reattach_orphans: search-index sync failed for %s entries",
                len(reattached_ids),
            )

    await record_event(
        db=db, user=user, event_type="source_orphans_reattached",
        source_id=source_id, request=request,
        payload={
            "strategy": body.strategy,
            "matched": summary.matched,
            "conflicts": summary.conflicts,
            "ambiguous": summary.ambiguous,
        },
    )
    return ReattachResponse(
        matched=summary.matched,
        conflicts=summary.conflicts,
        ambiguous=summary.ambiguous,
        committed=True,
    )


# ── Reachability (v0.4.21) ───────────────────────────────────────────────


class CheckReachabilityResponse(BaseModel):
    """Result of POST /api/sources/{id}/check-reachability.

    `result` is the raw probe outcome (ok/step/error/tier/warn);
    `source` is the refreshed source row so the UI gets the new
    `is_reachable` / `last_reachable_at` / `last_reachability_check_at`
    in the same response and doesn't need a follow-up GET.
    """

    result: TestResult
    source: SourceResponse


@router.post(
    "/{source_id}/check-reachability",
    response_model=CheckReachabilityResponse,
)
async def check_source_reachability(
    source_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """On-demand reachability probe for a source. Reuses the same
    pre-flight tester used by the create-source form, but runs against
    the persisted connection_config rather than form data — so the
    caller doesn't need to re-supply credentials.

    Persists `is_reachable` and `last_reachability_check_at` on every
    call; bumps `last_reachable_at` on success only. The probe error
    (when ok=false) is returned in the response payload but NOT
    persisted: a stale "old error" sticking around after the drive
    is reconnected would be more confusing than helpful.
    """
    await check_source_access(source_id, user, db, required_level="read")
    src = (await db.execute(
        select(Source).where(Source.id == source_id)
    )).scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")

    # source_tester probes are blocking subprocess calls (5–60s for
    # NFS, lower for the rest). Run in the default thread executor so
    # we don't pin the event loop. The probe wants the merged
    # host+source config; the host's connection-level fields layer
    # under the source's share-only fields.
    import asyncio
    merged_config = merge_host_and_source(src.host, src)
    # v0.14.0 — OAuth-shaped sources (gdrive, …) need a fresh access
    # token in the probe config too. mint_access_token_for_source
    # refreshes if needed; missing credential surfaces as the probe's
    # own auth-step error so the user gets a clear "sign in" message.
    from akashic.services.source_oauth import (
        OAuthExchangeFailed,
        mint_access_token_for_source,
    )
    try:
        oauth_pair = await mint_access_token_for_source(db, src.id)
    except OAuthExchangeFailed as exc:
        return CheckReachabilityResponse(
            result=TestResult(
                ok=False, step="auth",
                error=f"oauth refresh failed: {exc.detail[:200]}",
            ),
            source=src,
        )
    if oauth_pair is not None:
        merged_config["access_token"] = oauth_pair[0]
    result = await asyncio.to_thread(
        test_connection, src.type, merged_config
    )

    now = datetime.now(timezone.utc)
    src.last_reachability_check_at = now
    src.is_reachable = result.ok
    if result.ok:
        src.last_reachable_at = now
    await db.commit()
    await db.refresh(src)

    await record_event(
        db=db, user=user,
        event_type="source_reachability_checked",
        source_id=src.id,
        request=request,
        payload={
            "ok": result.ok,
            "step": result.step,
            "error": result.error,
        },
    )

    return CheckReachabilityResponse(result=result, source=src)


# ── Eligibility-management UI (v0.5.7) ─────────────────────────────────────


class ScannerReachabilityRow(BaseModel):
    """One row in the source detail's eligibility checklist."""

    scanner_id: uuid.UUID
    name: str
    pool: str | None
    online: bool
    currently_allowed: bool
    ok: bool | None
    last_probed_at: datetime | None
    step: str | None
    error: str | None


@router.get("/{source_id}/scanner-reachability", response_model=list[ScannerReachabilityRow])
async def list_source_scanner_reachability(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """For a given source, return every registered scanner's current
    allow-state and the latest probe outcome by that scanner. Feeds
    the AllowedScannersPanel checklist on SourceDetail.
    """
    await check_source_access(source_id, user, db, required_level="read")
    src = (await db.execute(
        select(Source).where(Source.id == source_id)
    )).scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")

    from sqlalchemy import text
    rows = (await db.execute(text("""
        SELECT
            s.id, s.name, s.pool,
            (s.last_seen_at IS NOT NULL
             AND s.last_seen_at > now() - interval '2 minutes') AS online,
            s.allowed_source_ids,
            rc.result_ok, rc.completed_at, rc.result_step, rc.result_error
          FROM scanners s
          LEFT JOIN LATERAL (
              SELECT result_ok, completed_at, result_step, result_error
                FROM reachability_checks
               WHERE source_id = :source_id
                 AND assigned_scanner_id = s.id
                 AND status IN ('completed', 'failed')
               ORDER BY completed_at DESC NULLS LAST
               LIMIT 1
          ) rc ON true
         ORDER BY s.name ASC
    """), {"source_id": source_id})).fetchall()

    out: list[ScannerReachabilityRow] = []
    for r in rows:
        allowed_set = set(r[4] or [])
        # NULL allowed_source_ids = scanner allows all sources.
        currently_allowed = (r[4] is None) or (source_id in allowed_set)
        out.append(ScannerReachabilityRow(
            scanner_id=r[0], name=r[1], pool=r[2], online=bool(r[3]),
            currently_allowed=currently_allowed,
            ok=r[5], last_probed_at=r[6], step=r[7], error=r[8],
        ))
    return out


class AllowedScannersRequest(BaseModel):
    scanner_ids: list[uuid.UUID]


class AllowedScannersResponse(BaseModel):
    updated_scanners: int


@router.patch("/{source_id}/allowed-scanners", response_model=AllowedScannersResponse)
async def patch_source_allowed_scanners(
    source_id: uuid.UUID,
    body: AllowedScannersRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Set the per-scanner allow-list for ONE source.

    For each registered scanner:
      - If id ∈ body.scanner_ids and the source isn't yet in
        scanner.allowed_source_ids → append.
      - If id ∉ body.scanner_ids and the source IS in
        scanner.allowed_source_ids → remove.
      - If scanner.allowed_source_ids is NULL ("all sources") and id
        ∉ body.scanner_ids → write a list excluding this source so the
        scanner stops claiming it.

    Idempotent: re-applying the same set is a no-op. Audit:
    `source_allowed_scanners_updated` per affected scanner.
    """
    src = (await db.execute(
        select(Source).where(Source.id == source_id)
    )).scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")

    from akashic.models.scanner import Scanner
    scanners = list((await db.execute(select(Scanner))).scalars().all())
    requested = set(body.scanner_ids)
    all_known = {s.id for s in scanners}
    unknown = requested - all_known
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown scanner_ids: {', '.join(str(u) for u in sorted(unknown))}",
        )

    updated = 0
    for s in scanners:
        current = list(s.allowed_source_ids or [])
        in_current = source_id in current
        in_requested = s.id in requested

        if s.allowed_source_ids is None:
            # Scanner currently allows ALL sources. If this one isn't
            # requested, materialise an explicit list excluding it.
            if not in_requested:
                # Build "everything except source_id" — practical
                # equivalent: allow every source currently in the db
                # except this one. Cheaper alternative: only restrict
                # against sources we know exist.
                all_source_ids = list((await db.execute(
                    select(Source.id).where(Source.id != source_id)
                )).scalars().all())
                s.allowed_source_ids = all_source_ids
                updated += 1
            # else: already implicitly allowed; nothing to do.
            continue

        if in_requested and not in_current:
            current.append(source_id)
            s.allowed_source_ids = current
            updated += 1
        elif not in_requested and in_current:
            current.remove(source_id)
            s.allowed_source_ids = current
            updated += 1

    await db.commit()
    await record_event(
        db=db, user=user,
        event_type="source_allowed_scanners_updated",
        source_id=source_id,
        request=request,
        payload={
            "source_id": str(source_id),
            "scanner_ids": [str(i) for i in sorted(requested)],
            "scanners_changed": updated,
        },
    )
    return AllowedScannersResponse(updated_scanners=updated)
