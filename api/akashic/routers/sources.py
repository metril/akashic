import uuid
from datetime import datetime

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
from akashic.schemas.reachability import (
    PerScannerHistory,
    ReachabilityHistory,
    ReachabilityOutcome,
)
from akashic.schemas.source import (
    SourceCreate, SourceListResponse, SourceResponse, SourceUpdate,
)
from akashic.services.audit import record_event
from akashic.services.source_config import (
    merge_host_and_source,
    validate_scan_controls,
)
from akashic.services.source_defaults import infer_is_removable
from akashic.services.source_merge import (
    field_diff,
    merge_connection_config,
    reject_sentinel_in_create,
)

router = APIRouter(prefix="/api/sources", tags=["sources"])

# Source types that don't attach to a Host row. `local` predates the
# Host abstraction; `paperless` (v0.7.0) and `immich` (v0.8.0) are
# self-hosted libraries where the URL + API token / key live on the
# source's connection_config directly. `webdav` (v0.11.0) follows
# the same hostless pattern — URL + auth fields all on the source.
# All create+update paths key off this set when validating host_id
# semantics.
HOSTLESS_SOURCE_TYPES = {
    "local", "paperless", "immich", "webdav",
    "gdrive", "onedrive", "dropbox",
}


def _config_safe_summary(cfg: dict | None) -> dict:
    """Audit-safe snapshot of a connection_config: state tokens for
    secret keys, real values for the rest."""
    return {
        k: ("<set>" if v else "<empty>") if any(s in k.lower() for s in {"password", "secret", "key", "token", "credentials", "private_key"}) else v
        for k, v in (cfg or {}).items()
    }


async def _validate_smb_password_requirement(
    *,
    db: AsyncSession,
    host_id: uuid.UUID | None,
    credential_profile_id: uuid.UUID | None,
    connection_config: dict | None,
) -> None:
    """v0.29.5 — refuse SMB sources whose merged config ends up with
    an empty password.

    Bypass surface ([scanner/internal/probe/probe.go:runSMB](scanner/internal/probe/probe.go)):
    go-smb2's `NTLMInitiator` accepts ``Password: ""``. Some servers
    (Samba `force user`, Windows null-password accounts, anonymous
    shares) respond with a fully authenticated session — not guest —
    so the v0.29.1 IsGuest/IsAnonymous rejection never fires and the
    probe lands `ok=true` against credentials the user knew were
    wrong.

    The explicit opt-in is `connection_config.allow_empty_password ==
    True` at the source level; profiles are NOT permitted to carry
    this opt-in (they're reusable across sources where it might not
    apply).
    """
    cfg = connection_config or {}
    if cfg.get("allow_empty_password") is True:
        return

    # Compose the merged credential dict in the same precedence the
    # scan-time `merge_host_and_source` helper uses (last write wins):
    # host_profile < host_inline < source_profile < source_inline.
    from akashic.models.credential_profile import CredentialProfile
    from akashic.services.source_config import credentials_from_profile
    merged: dict = {}

    if host_id is not None:
        host = (await db.execute(
            select(Host).where(Host.id == host_id)
        )).scalar_one_or_none()
        if host is not None:
            if host.credential_profile_id is not None:
                hp = (await db.execute(
                    select(CredentialProfile).where(
                        CredentialProfile.id == host.credential_profile_id
                    )
                )).scalar_one_or_none()
                # credentials_from_profile decrypts the v0.29.5
                # `credentials_encrypted` column — reading the raw
                # `.credentials` JSONB would be NULL for every modern
                # profile and wrongly fail the password check below.
                merged.update(credentials_from_profile(hp))
            merged.update(host.connection_config or {})

    if credential_profile_id is not None:
        sp = (await db.execute(
            select(CredentialProfile).where(
                CredentialProfile.id == credential_profile_id
            )
        )).scalar_one_or_none()
        merged.update(credentials_from_profile(sp))

    merged.update(cfg)

    pw = merged.get("password")
    if not isinstance(pw, str) or pw == "":
        raise HTTPException(
            status_code=422,
            detail=(
                "SMB source: merged connection_config has no non-empty "
                "password. Either attach a credential profile that "
                "carries a password, set `password` on the source / host, "
                "or set `connection_config.allow_empty_password=true` "
                "for an explicit anonymous-share scan."
            ),
        )


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

    # v0.29.5 — SMB sources must end up with a non-empty password at
    # scan time unless explicitly opted out via
    # `connection_config.allow_empty_password=true`. See
    # scanner/internal/probe/probe.go runSMB for the bypass rationale.
    if data.type == "smb":
        await _validate_smb_password_requirement(
            db=db, host_id=data.host_id,
            credential_profile_id=data.credential_profile_id,
            connection_config=data.connection_config,
        )

    payload = data.model_dump()
    # v0.35.0 — a NULL max_parallel_scanners / scan_chunk_size now means
    # "inherit from the host" (or the built-in default), so it is kept
    # as NULL rather than forced to a concrete value here.
    ctrl_err = validate_scan_controls(
        max_parallel_scanners=payload.get("max_parallel_scanners"),
        scan_chunk_size=payload.get("scan_chunk_size"),
    )
    if ctrl_err:
        raise HTTPException(status_code=400, detail=ctrl_err)
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
        # Ownership check (review A-I2): only attach credentials that
        # are still unattached. Pre-fix any admin could supply any
        # other admin's unattached credential UUID and hijack it.
        # We can't compare to "the user who created the credential"
        # because that field isn't stored; instead, refuse to attach
        # a credential that's already bound to a different source.
        if cred is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="oauth_credential_id not found",
            )
        if cred.source_id is not None and cred.source_id != source.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="oauth_credential is already attached to another source",
            )
        if cred.source_id is None:
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
        "preferred_pool": source.preferred_pool,
        "max_parallel_scanners": source.max_parallel_scanners,
        "scan_chunk_size": source.scan_chunk_size,
        "is_removable": source.is_removable,
        "host_id": str(source.host_id) if source.host_id else None,
        "credential_profile_id": (
            str(source.credential_profile_id)
            if source.credential_profile_id else None
        ),
    }

    incoming = data.model_dump(exclude_unset=True)
    if "max_parallel_scanners" in incoming or "scan_chunk_size" in incoming:
        ctrl_err = validate_scan_controls(
            max_parallel_scanners=incoming.get("max_parallel_scanners"),
            scan_chunk_size=incoming.get("scan_chunk_size"),
        )
        if ctrl_err:
            raise HTTPException(status_code=400, detail=ctrl_err)
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

    # v0.29.5 — re-validate the SMB password requirement on the
    # merged post-update state. Catches the case where the user
    # updates `credential_profile_id` to NULL (removing the only
    # source of password) while leaving connection_config without a
    # password and without the allow_empty_password opt-in.
    if source.type == "smb":
        await _validate_smb_password_requirement(
            db=db, host_id=source.host_id,
            credential_profile_id=source.credential_profile_id,
            connection_config=source.connection_config,
        )

    await db.commit()
    await db.refresh(source)
    # Push to /ws/scans subscribers so other tabs/users see the edit
    # without polling (mirrors source.created / source.deleted).
    from akashic.services import scan_pubsub
    await scan_pubsub.publish_source_event({
        "kind": "source.updated",
        "source_id": str(source.id),
    })

    after = {
        "name": source.name,
        "type": source.type,
        "connection_config": dict(source.connection_config or {}),
        "scan_schedule": source.scan_schedule,
        "exclude_patterns": list(source.exclude_patterns or []),
        "preferred_pool": source.preferred_pool,
        "max_parallel_scanners": source.max_parallel_scanners,
        "scan_chunk_size": source.scan_chunk_size,
        "is_removable": source.is_removable,
        "host_id": str(source.host_id) if source.host_id else None,
        "credential_profile_id": (
            str(source.credential_profile_id)
            if source.credential_profile_id else None
        ),
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


# ── On-demand reachability (v0.28.0) ────────────────────────────────────
#
# Replaces the old continuous-poll model:
#   * /test-scanners   — user-triggered, runs probes for one or more
#                        scanners against this source. Inline for
#                        non-local sources; long-poll dispatched to the
#                        agent for local sources.
#   * /scanner-reachability — reads the latest result per scanner from
#                             reachability_results (no staleness gate).
#   * /reachability-summary — single-source-level "is this source up at
#                             all" derived from latest probe across all
#                             scanners (or any successful scan).


class TestScannersRequest(BaseModel):
    """Optional scanner_ids subset for POST /test-scanners.

    Omit (or send empty) → test against every scanner whose pool /
    allowed_source_ids permits this source. Supplying a single id lets
    a per-row "Test now" button reuse the same endpoint.
    """

    scanner_ids: list[uuid.UUID] | None = None


class TestScannersResultRow(BaseModel):
    scanner_id: uuid.UUID | None
    ok: bool | None
    step: str | None
    error: str | None
    pending: bool = False
    completed_at: datetime | None = None


class TestScannersResponse(BaseModel):
    results: list[TestScannersResultRow]


_ONLINE_THRESHOLD_SECONDS = 120

# v0.29.5 — softer threshold for *notification* paths. The scan-join
# Redis queue is durable (LTRIM 50, 7-day TTL — agents pick up the
# notification on their next BRPOP), so notifying a marginally-stale
# scanner is essentially free. A *missed* notification is the bug the
# user reported on re-scans. 10 min covers any reasonable inter-scan
# idle without making the rare actually-dead scanner cost anything.
_NOTIFY_ONLINE_THRESHOLD_SECONDS = 600


def _eligible_scanners_for(
    source: Source, all_scanners, *,
    online_only: bool = True,
    online_threshold_seconds: int = _ONLINE_THRESHOLD_SECONDS,
) -> list:
    """Filter scanners whose pool/allowed_source_ids permits this source.

    Default `online_only=True` skips scanners that haven't checked in
    within `online_threshold_seconds` (default 120 s). Without that
    filter, the bulk-test paths waste 5 s of long-poll timeout per
    offline scanner. Callers that explicitly named scanner_ids in the
    request override this (offline scanners stay listed as
    `pending=true` so the user sees they were skipped).

    v0.29.5 — `online_threshold_seconds` parameter so callers with a
    cheap-miss profile (the scan-join notify) can widen the window
    without affecting the bulk-probe path.
    """
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=online_threshold_seconds)
    out = []
    for s in all_scanners:
        if not s.enabled:
            continue
        if source.preferred_pool is not None and s.pool != source.preferred_pool:
            continue
        if s.allowed_source_ids and source.id not in s.allowed_source_ids:
            continue
        if online_only and (s.last_seen_at is None or s.last_seen_at < cutoff):
            continue
        out.append(s)
    return out


@router.post(
    "/{source_id}/test-scanners",
    response_model=TestScannersResponse,
)
async def test_source_scanners(
    source_id: uuid.UUID,
    body: TestScannersRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """User-triggered reachability probe — for one source, against one
    or more scanners.

    Every source type — local or remote — routes through the scanner
    long-poll. Reachability is what a scanner reports from its own
    network position with its own credentials; the API doesn't have
    credentials to honestly answer for a scanner.

    Results that arrive within 5 s are returned inline (`pending=false`);
    slow scanners are returned as `pending=true` and their results land
    later via the source-reachability WS channel.
    """
    await check_source_access(source_id, user, db, required_level="write")
    src = (await db.execute(
        select(Source).where(Source.id == source_id)
    )).scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")

    from akashic.models.scanner import Scanner
    all_scanners = list((await db.execute(select(Scanner))).scalars().all())

    requested_ids = (body.scanner_ids if body else None) or None
    if requested_ids:
        wanted = [s for s in all_scanners if s.id in set(requested_ids)]
    else:
        wanted = _eligible_scanners_for(src, all_scanners)

    from akashic.services import probe_dispatch
    delivered = await probe_dispatch.dispatch_remote(
        db=db, source=src,
        scanner_ids=[s.id for s in wanted],
        timeout_s=5.0,
        triggered_by=user.id,
    )
    rows: list[TestScannersResultRow] = []
    for s in wanted:
        report = delivered.get(s.id)
        if report is None:
            rows.append(TestScannersResultRow(
                scanner_id=s.id, ok=None, step=None, error=None,
                pending=True,
            ))
        else:
            rows.append(TestScannersResultRow(
                scanner_id=s.id,
                ok=report.get("ok"),
                step=report.get("step"),
                error=report.get("error"),
                pending=False,
                completed_at=report.get("completed_at"),
            ))
    await db.commit()
    return TestScannersResponse(results=rows)


class ReachabilitySummary(BaseModel):
    """Compact source-level reachability for badges + cards.

    Derived from the latest reachability_results row across all
    scanners (or implicit from a successful scan). `ok=None` means
    "no data yet" — no probe has run and no successful scan has
    landed for this source.
    """

    ok: bool | None
    last_at: datetime | None
    last_step: str | None
    last_error: str | None
    last_scanner_id: uuid.UUID | None
    # Scanner display name when last_scanner_id is set, so the badge
    # tooltip can say "Verified by scanner X" without a follow-up GET.
    last_scanner_name: str | None = None


@router.get(
    "/{source_id}/reachability-summary",
    response_model=ReachabilitySummary,
)
async def get_source_reachability_summary(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Single-source reachability snapshot for the badge component.

    "Reachable" is read as "the most recent probe by any scanner
    succeeded, OR the most recent successful scan landed after the
    most recent failed probe." Successful scans are an implicit
    reachability proof — the scanner just walked the source.
    """
    await check_source_access(source_id, user, db, required_level="read")
    src = (await db.execute(
        select(Source).where(Source.id == source_id)
    )).scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")

    from sqlalchemy import text
    row = (await db.execute(text("""
        WITH latest_probe AS (
            SELECT rr.ok, rr.step, rr.error, rr.completed_at,
                   rr.scanner_id, sc.name AS scanner_name
              FROM reachability_results rr
              LEFT JOIN scanners sc ON sc.id = rr.scanner_id
             WHERE rr.source_id = :source_id
             ORDER BY rr.completed_at DESC
             LIMIT 1
        ),
        latest_scan AS (
            SELECT completed_at AS last_at
              FROM scans
             WHERE source_id = :source_id AND status = 'completed'
             ORDER BY completed_at DESC NULLS LAST
             LIMIT 1
        )
        SELECT
            (SELECT ok FROM latest_probe)             AS probe_ok,
            (SELECT completed_at FROM latest_probe)   AS probe_at,
            (SELECT step FROM latest_probe)           AS probe_step,
            (SELECT error FROM latest_probe)          AS probe_error,
            (SELECT scanner_id FROM latest_probe)     AS probe_scanner_id,
            (SELECT scanner_name FROM latest_probe)   AS probe_scanner_name,
            (SELECT last_at FROM latest_scan)         AS scan_at
    """), {"source_id": source_id})).first()

    (probe_ok, probe_at, probe_step, probe_error, probe_scanner_id,
     probe_scanner_name, scan_at) = row

    # If the latest scan completed successfully more recently than the
    # latest probe, treat the source as reachable.
    if scan_at is not None and (probe_at is None or scan_at > probe_at):
        return ReachabilitySummary(
            ok=True,
            last_at=scan_at,
            last_step=None,
            last_error=None,
            last_scanner_id=None,
            last_scanner_name=None,
        )
    if probe_at is None:
        return ReachabilitySummary(
            ok=None, last_at=None,
            last_step=None, last_error=None,
            last_scanner_id=None, last_scanner_name=None,
        )
    return ReachabilitySummary(
        ok=probe_ok,
        last_at=probe_at,
        last_step=probe_step,
        last_error=probe_error,
        last_scanner_id=probe_scanner_id,
        last_scanner_name=probe_scanner_name,
    )


# v0.41.0 — full reachability history per (source, scanner) for the
# new Reachability tab in SourceDetail. Separate endpoint from
# /scanner-reachability (which keeps a small 5-row slice for the
# eligibility panel) so the tab gets deeper history without
# bloating the panel's payload.


@router.get(
    "/{source_id}/reachability-history",
    response_model=ReachabilityHistory,
)
async def get_source_reachability_history(
    source_id: uuid.UUID,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await check_source_access(source_id, user, db, required_level="read")
    src = (await db.execute(
        select(Source).where(Source.id == source_id)
    )).scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")

    from akashic.services import reachability_results as rr_service
    # Cap user-supplied limit so the daily prune's ceiling (20 rows
    # per pair) defines the upper bound. Bigger values would just
    # return fewer rows than asked.
    safe_limit = max(1, min(int(limit or 20), 20))
    groups = await rr_service.list_history(
        db=db, source_id=source_id, limit_per_scanner=safe_limit,
    )

    per_scanner: list[PerScannerHistory] = []
    for scanner_id, scanner_name, outcomes in groups:
        per_scanner.append(PerScannerHistory(
            scanner_id=scanner_id,
            scanner_name=scanner_name,
            outcomes=[
                ReachabilityOutcome(
                    ok=r.ok, step=r.step, error=r.error,
                    started_at=r.started_at, completed_at=r.completed_at,
                )
                for r in outcomes
            ],
        ))
    return ReachabilityHistory(per_scanner=per_scanner)


# ── Eligibility-management UI (v0.5.7, rewritten v0.28.0) ──────────────


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
    history: list[dict] = []  # [{ok, completed_at, step, error}, ...]


@router.get("/{source_id}/scanner-reachability", response_model=list[ScannerReachabilityRow])
async def list_source_scanner_reachability(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """For a given source, return every registered scanner's current
    allow-state and the latest probe outcome by that scanner. Feeds
    the AllowedScannersPanel checklist on SourceDetail.

    v0.28.0: reads from `reachability_results` (no staleness gate)
    and includes a small history slice for the per-row disclosure.
    """
    await check_source_access(source_id, user, db, required_level="read")
    src = (await db.execute(
        select(Source).where(Source.id == source_id)
    )).scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")

    from sqlalchemy import text
    # Latest result per scanner.
    rows = (await db.execute(text("""
        SELECT
            s.id, s.name, s.pool,
            (s.last_seen_at IS NOT NULL
             AND s.last_seen_at > now() - interval '2 minutes') AS online,
            s.allowed_source_ids,
            rr.ok, rr.completed_at, rr.step, rr.error
          FROM scanners s
          LEFT JOIN LATERAL (
              SELECT ok, completed_at, step, error
                FROM reachability_results
               WHERE source_id = :source_id
                 AND scanner_id = s.id
               ORDER BY completed_at DESC
               LIMIT 1
          ) rr ON true
         ORDER BY s.name ASC
    """), {"source_id": source_id})).fetchall()

    # History (last 5) per scanner — single roundtrip via window function.
    history_rows = (await db.execute(text("""
        SELECT scanner_id, ok, completed_at, step, error
          FROM (
              SELECT scanner_id, ok, completed_at, step, error,
                     row_number() OVER (
                         PARTITION BY scanner_id
                         ORDER BY completed_at DESC
                     ) AS rn
                FROM reachability_results
               WHERE source_id = :source_id
                 AND scanner_id IS NOT NULL
          ) t
         WHERE rn <= 5
         ORDER BY scanner_id, completed_at DESC
    """), {"source_id": source_id})).fetchall()
    history_by_scanner: dict = {}
    for hr in history_rows:
        history_by_scanner.setdefault(hr[0], []).append({
            "ok": hr[1],
            "completed_at": hr[2].isoformat() if hr[2] else None,
            "step": hr[3],
            "error": hr[4],
        })

    out: list[ScannerReachabilityRow] = []
    for r in rows:
        allowed_set = set(r[4] or [])
        # NULL allowed_source_ids = scanner allows all sources.
        currently_allowed = (r[4] is None) or (source_id in allowed_set)
        out.append(ScannerReachabilityRow(
            scanner_id=r[0], name=r[1], pool=r[2], online=bool(r[3]),
            currently_allowed=currently_allowed,
            ok=r[5], last_probed_at=r[6], step=r[7], error=r[8],
            history=history_by_scanner.get(r[0], []),
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

    # Pre-compute the "all sources except this one" list once
    # (review D-I5). Pre-fix this re-ran inside the loop for every
    # scanner currently set to NULL (allow-all), N extra SELECTs for
    # N unrestricted scanners.
    other_source_ids_cache: list[uuid.UUID] | None = None

    updated = 0
    for s in scanners:
        current = list(s.allowed_source_ids or [])
        in_current = source_id in current
        in_requested = s.id in requested

        if s.allowed_source_ids is None:
            # Scanner currently allows ALL sources. If this one isn't
            # requested, materialise an explicit list excluding it.
            if not in_requested:
                if other_source_ids_cache is None:
                    other_source_ids_cache = list((await db.execute(
                        select(Source.id).where(Source.id != source_id)
                    )).scalars().all())
                s.allowed_source_ids = list(other_source_ids_cache)
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
