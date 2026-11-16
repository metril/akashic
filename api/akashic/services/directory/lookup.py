"""Directory lookup: search, backend status, identity load, token resolution."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, TypedDict

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.directory import DirectoryEntry, DirectorySyncRun
from akashic.models.fs_person import FsBinding, FsPerson
from akashic.models.principals_cache import PrincipalsCache
from akashic.models.user import User
from akashic.schemas.directory import IdentityBackendRef, IdentityResult
from akashic.services.access_query import binding_to_tokens
from akashic.services.acl_denorm import ANYONE, AUTH, sid as sid_token
from akashic.services.directory.ad import ad_configured
from akashic.services.directory.authentik import authentik_configured
from akashic.services.directory.merge import MergedIdentity, merge_identities
from akashic.services.directory.records import DirRecord

DIRECTORY_FRESH_SECONDS = 300
LOAD_MAX_ROUNDS = 3
LOAD_MAX_RECORDS = 50

Status = Literal["ok", "cached", "partial", "failed"]
_SEVERITY = {"ok": 0, "cached": 1, "partial": 2}


@dataclass
class KeySet:
    """Strong merge keys: upper-case SIDs, lower-case DNs and emails."""
    sids: set[str] = field(default_factory=set)
    dns: set[str] = field(default_factory=set)
    emails: set[str] = field(default_factory=set)

    @classmethod
    def of(cls, records: list[DirRecord]) -> "KeySet":
        return cls(
            sids={s for r in records for s in ([r.sid] if r.sid else []) + list(r.alt_sids)},
            dns={r.dn.lower() for r in records if r.dn},
            emails={r.email.lower() for r in records if r.email},
        )

    def __bool__(self) -> bool:
        return bool(self.sids or self.dns or self.emails)


class BackendStatus(TypedDict):
    backend: str
    status: Literal["ok", "unavailable", "not_configured"]
    detail: str | None


@dataclass
class TokenUsed:
    token: str
    origin: Literal["ad", "authentik", "akashic_binding", "email", "builtin"]
    name: str | None = None


@dataclass
class ResolvedTokens:
    tokens: list[str]
    used: list[TokenUsed]
    status: Status
    cached_at: datetime | None
    detail: str | None
    missing_identity_kinds: list[str]


# (backend, ref) -> (synced_at, groups_synced_at) of cached directory rows.
_Fresh = dict[tuple[str, str], tuple[datetime, datetime | None]]


def _escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _to_record(e: DirectoryEntry) -> DirRecord:
    return DirRecord(
        backend=e.backend, ref=e.ref, kind=e.kind, display_name=e.display_name,
        username=e.username, email=e.email, sid=e.sid, dn=e.dn,
        group_sids=tuple(e.group_sids or ()), groups_status=e.groups_status,
        uid_number=e.uid_number, gid_number=e.gid_number,
    )


async def _entries(
    db: AsyncSession, *, q: str | None = None, kind: str = "any",
    keys: KeySet | None = None, backend_ref: tuple[str, str] | None = None,
    limit: int | None = None,
) -> list[DirectoryEntry]:
    conds = []
    if q is not None:
        pat = f"%{_escape_like(q)}%"
        conds += [
            DirectoryEntry.display_name.ilike(pat, escape="\\"),
            DirectoryEntry.username.ilike(pat, escape="\\"),
            DirectoryEntry.email.ilike(pat, escape="\\"),
        ]
    if keys:
        if keys.sids:
            conds.append(DirectoryEntry.sid.in_(keys.sids))
        if keys.dns:
            conds.append(func.lower(DirectoryEntry.dn).in_(keys.dns))
        if keys.emails:
            conds.append(func.lower(DirectoryEntry.email).in_(keys.emails))
    if backend_ref:
        conds.append(and_(DirectoryEntry.backend == backend_ref[0],
                          DirectoryEntry.ref == backend_ref[1]))
    if not conds:
        return []
    stmt = select(DirectoryEntry).where(or_(*conds))
    if kind != "any":
        stmt = stmt.where(DirectoryEntry.kind == kind)
    stmt = stmt.order_by(DirectoryEntry.display_name, DirectoryEntry.backend, DirectoryEntry.ref)
    if limit:
        stmt = stmt.limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def akashic_user_records(
    db: AsyncSession, *, q: str | None = None, user_ids: list[uuid.UUID] | None = None,
    keys: KeySet | None = None, limit: int | None = None,
) -> list[DirRecord]:
    conds = []
    if q is not None:
        pat = f"%{_escape_like(q)}%"
        conds += [User.username.ilike(pat, escape="\\"), User.email.ilike(pat, escape="\\")]
    if user_ids:
        conds.append(User.id.in_(user_ids))
    if keys:
        if keys.emails:
            conds.append(func.lower(User.email).in_(keys.emails))
        if keys.dns:
            conds.append(and_(User.auth_provider == "ldap",
                              func.lower(User.external_id).in_(keys.dns)))
        if keys.sids:
            conds.append(User.id.in_(
                select(FsPerson.user_id).join(FsBinding, FsBinding.fs_person_id == FsPerson.id)
                .where(FsBinding.identity_type == "sid",
                       func.upper(FsBinding.identifier).in_(keys.sids))))
    if not conds:
        return []
    stmt = select(User).where(or_(*conds)).order_by(User.username)
    if limit:
        stmt = stmt.limit(limit)
    users = list((await db.execute(stmt)).scalars().all())
    if not users:
        return []
    sids_of: dict[uuid.UUID, list[str]] = {}
    rows = (await db.execute(
        select(FsPerson.user_id, FsBinding.identifier)
        .join(FsBinding, FsBinding.fs_person_id == FsPerson.id)
        .where(FsPerson.user_id.in_([u.id for u in users]), FsBinding.identity_type == "sid")
        .order_by(FsBinding.created_at, FsBinding.identifier)
    )).all()
    for uid, ident in rows:
        if ident.upper() not in sids_of.setdefault(uid, []):
            sids_of[uid].append(ident.upper())
    return [
        DirRecord(
            backend="akashic", ref=str(u.id), kind="person", display_name=u.username,
            username=u.username, email=u.email,
            sid=(sids_of.get(u.id) or [None])[0], alt_sids=tuple(sids_of.get(u.id, [])[1:]),
            dn=u.external_id if u.auth_provider == "ldap" else None,
        )
        for u in users
    ]


def _gather(
    fresh: _Fresh, records: dict[tuple[str, str], DirRecord],
    *, entries: list[DirectoryEntry], akashic: list[DirRecord],
) -> None:
    for e in entries:
        k = (e.backend, e.ref)
        records[k] = _to_record(e)
        fresh[k] = (e.synced_at, e.groups_synced_at)
    for r in akashic:
        records[(r.backend, r.ref)] = r


async def _closure(
    db: AsyncSession, fresh: _Fresh, records: dict[tuple[str, str], DirRecord],
    max_records: int = LOAD_MAX_RECORDS,
) -> bool:
    """Grow ``records`` with rows sharing a strong key, same kind, to a fixed
    point within LOAD_MAX_ROUNDS rounds / ``max_records`` records. Returns
    True when it stopped before the fixed point (the result is truncated)."""
    for rnd in range(LOAD_MAX_ROUNDS + 1):
        found: dict[tuple[str, str], DirRecord] = {}
        found_fresh: _Fresh = {}
        full = False
        for kind in sorted({r.kind for r in records.values()}):
            keys = KeySet.of([r for r in records.values() if r.kind == kind])
            if not keys:
                continue
            entries = await _entries(db, keys=keys, kind=kind, limit=max_records + 1)
            akashic = (await akashic_user_records(db, keys=keys, limit=max_records + 1)
                       if kind == "person" else [])
            full = full or len(entries) + len(akashic) > max_records
            _gather(found_fresh, found, entries=entries, akashic=akashic)
        new = [k for k in found if k not in records]
        if not new:
            return full
        if rnd == LOAD_MAX_ROUNDS:
            return True
        room = max(max_records - len(records), 0)
        for k in new[:room]:
            records[k] = found[k]
            if k in found_fresh:
                fresh[k] = found_fresh[k]
        if full or len(new) > room:
            return True
    return True


async def _finish(
    db: AsyncSession, fresh: _Fresh, records: dict[tuple[str, str], DirRecord],
    truncated: set[tuple[str, str]],
) -> list[MergedIdentity]:
    idents = merge_identities(list(records.values()))
    user_ids = [uuid.UUID(r.ref) for i in idents for r in i.records if r.backend == "akashic"]
    bound: set[uuid.UUID] = set()
    if user_ids:
        bound = set((await db.execute(
            select(FsPerson.user_id).distinct().join(
                FsBinding, FsBinding.fs_person_id == FsPerson.id,
            ).where(FsPerson.user_id.in_(user_ids))
        )).scalars().all())
    for i in idents:
        i.freshness = {(r.backend, r.ref): fresh[(r.backend, r.ref)]
                       for r in i.records if (r.backend, r.ref) in fresh}
        i.truncated = any((r.backend, r.ref) in truncated for r in i.records)
        i.has_binding = any(
            r.backend == "akashic" and uuid.UUID(r.ref) in bound for r in i.records)
    return idents


def _rank(ident: MergedIdentity, q: str) -> tuple[int, int, str]:
    needle = q.casefold()
    names = [(r.username or "").casefold() for r in ident.records]
    emails = [(r.email or "").casefold() for r in ident.records]
    exact = needle in names or needle in emails
    prefixed = any(
        (v or "").casefold().startswith(needle)
        for r in ident.records for v in (r.display_name, r.username)
    )
    return (0 if exact else 1, 0 if prefixed else 1, (ident.display_name or "").casefold())


async def search_identities(
    db: AsyncSession, q: str, kind: Literal["person", "group", "any"], limit: int,
) -> list[MergedIdentity]:
    cap = limit * 3
    fresh: _Fresh = {}
    hits: dict[tuple[str, str], DirRecord] = {}
    _gather(
        fresh, hits,
        entries=await _entries(db, q=q, kind=kind, limit=cap),
        akashic=await akashic_user_records(db, q=q, limit=cap) if kind != "group" else [],
    )
    # One batched closure over all hits; the bound is global, so a hit on the
    # bound still returns merged results (resolve re-expands per identity).
    records = dict(hits)
    capped = await _closure(db, fresh, records, max_records=cap + LOAD_MAX_RECORDS)
    truncated = set(records) if capped else set()
    idents = [i for i in await _finish(db, fresh, records, truncated)
              if kind == "any" or i.kind == kind]
    idents.sort(key=lambda i: _rank(i, q))
    return idents[:limit]


async def backends_status(db: AsyncSession) -> list[BackendStatus]:
    out: list[BackendStatus] = []
    for backend, configured in (("ad", ad_configured()), ("authentik", authentik_configured())):
        if not configured:
            out.append({"backend": backend, "status": "not_configured", "detail": None})
            continue
        last = (await db.execute(
            select(DirectorySyncRun)
            .where(DirectorySyncRun.backend == backend, DirectorySyncRun.status != "running")
            .order_by(DirectorySyncRun.started_at.desc()).limit(1)
        )).scalar_one_or_none()
        if last is None:
            out.append({"backend": backend, "status": "unavailable", "detail": "never synced"})
        elif last.status == "failed":
            out.append({"backend": backend, "status": "unavailable",
                        "detail": last.error or "sync failed"})
        else:
            out.append({"backend": backend, "status": "ok", "detail": None})
    out.append({"backend": "akashic", "status": "ok", "detail": None})
    return out


async def _seed_by_keys(
    db: AsyncSession, keys: KeySet,
) -> tuple[list[DirectoryEntry], list[DirRecord]]:
    n = LOAD_MAX_RECORDS + 1
    return (await _entries(db, keys=keys, limit=n),
            await akashic_user_records(db, keys=keys, limit=n))


async def load_identity(db: AsyncSession, identity_id: str) -> MergedIdentity | None:
    prefix, _, value = identity_id.partition(":")
    if not value:
        return None
    fresh: _Fresh = {}
    records: dict[tuple[str, str], DirRecord] = {}
    if prefix == "sid":
        keys = KeySet(sids={value.upper()})
        entries, akashic = await _seed_by_keys(db, keys)
    elif prefix == "dn":
        keys = KeySet(dns={value.lower()})
        entries, akashic = await _seed_by_keys(db, keys)
    elif prefix == "email":
        keys = KeySet(emails={value.lower()})
        entries, akashic = await _seed_by_keys(db, keys)
    elif prefix in ("ad", "authentik"):
        entries, akashic = await _entries(db, backend_ref=(prefix, value), limit=1), []
    elif prefix == "akashic":
        try:
            uid = uuid.UUID(value)
        except ValueError:
            return None
        entries, akashic = [], await akashic_user_records(db, user_ids=[uid])
    else:
        return None
    _gather(fresh, records, entries=entries, akashic=akashic)
    if not records:
        return None
    capped = len(records) > LOAD_MAX_RECORDS
    for k in list(records)[LOAD_MAX_RECORDS:]:
        del records[k]
        fresh.pop(k, None)
    seeds = set(records)
    capped = (await _closure(db, fresh, records)) or capped
    truncated = set(records) if capped else set()
    idents = [i for i in await _finish(db, fresh, records, truncated)
              if any((r.backend, r.ref) in seeds for r in i.records)]
    # Exact id first; otherwise (seed spans clusters that do not merge, e.g. an
    # email shared by a person and a group) prefer a person, then the lowest id.
    idents.sort(key=lambda i: (i.id != identity_id, i.kind != "person", i.id))
    return idents[0] if idents else None


def identity_result(ident: MergedIdentity) -> IdentityResult:
    return IdentityResult(
        id=ident.id,
        kind=ident.kind,
        display_name=ident.display_name,
        username=ident.username,
        email=ident.email,
        sid=ident.sid,
        dn=ident.dn,
        backends=[IdentityBackendRef(backend=r.backend, ref=r.ref) for r in ident.records],
        match_quality=ident.match_quality,
        possible_match_of=ident.possible_match_of,
        akashic_user_id=ident.akashic_user_id,
        groups_synced_at=ident.groups_synced_at,
        has_fs_identity=ident.has_fs_identity,
    )


async def _sid_names(db: AsyncSession, sids: set[str]) -> dict[str, str]:
    if not sids:
        return {}
    names: dict[str, str] = {}
    rows = (await db.execute(
        select(DirectoryEntry.sid, DirectoryEntry.display_name)
        .where(DirectoryEntry.sid.in_(sids), DirectoryEntry.display_name.isnot(None))
        .order_by(DirectoryEntry.backend)
    )).all()
    for s, n in rows:
        names.setdefault(s, n)
    missing = sids - names.keys()
    if missing:
        rows = (await db.execute(
            select(PrincipalsCache.sid, PrincipalsCache.name)
            .where(PrincipalsCache.sid.in_(missing), PrincipalsCache.name.isnot(None))
        )).all()
        for s, n in rows:
            names.setdefault(s, n)
    return names


def _expansion(ident: MergedIdentity, has_ad_row: bool) -> tuple[Status, str | None]:
    """Worst per-row verdict over the identity's AD/Authentik rows."""
    worst: Status = "ok"
    detail: str | None = None
    fresh_after = datetime.now(timezone.utc) - timedelta(seconds=DIRECTORY_FRESH_SECONDS)

    def bump(status: Status, why: str | None) -> None:
        nonlocal worst, detail
        if _SEVERITY[status] > _SEVERITY[worst]:
            worst, detail = status, why

    for r in ident.records:
        if r.backend == "akashic":
            continue
        synced_at, groups_synced_at = ident.freshness.get(
            (r.backend, r.ref), (None, None))
        if r.groups_status == "failed":
            if groups_synced_at is None:
                bump("partial", f"group membership could not be read from {r.backend}")
            else:
                bump("cached", f"latest {r.backend} group read failed; showing earlier groups")
        elif synced_at is None or synced_at < fresh_after:
            bump("cached", f"{r.backend} data is not fresh (older than {DIRECTORY_FRESH_SECONDS} seconds)")
    if ident.kind == "person" and not has_ad_row and any(
        r.sid or r.alt_sids for r in ident.records
    ):
        bump("partial", "SID known without an AD row; group membership not expanded")
    if ident.truncated:
        bump("partial", f"identity has more linked records than the {LOAD_MAX_RECORDS}-record cap")
    return worst, detail


async def resolve_tokens(db: AsyncSession, ident: MergedIdentity) -> ResolvedTokens:
    used: set[tuple[str, str]] = set()
    ad_rows = [r for r in ident.records if r.backend == "ad"]

    def add(token: str, origin: str) -> None:
        used.add((token, origin))

    if ident.kind == "group":
        for r in ident.records:
            if r.backend == "akashic":
                continue
            if r.sid:
                add(sid_token(r.sid), r.backend)
            for g in r.group_sids:
                add(sid_token(g), r.backend)
    else:
        for r in ad_rows:
            if r.sid:
                add(sid_token(r.sid), "ad")
            for g in r.group_sids:
                add(sid_token(g), "ad")
            if r.uid_number is not None:
                add(f"posix:uid:{r.uid_number}", "ad")
            if r.gid_number is not None:
                add(f"posix:gid:{r.gid_number}", "ad")
        if not ad_rows:
            for r in ident.records:
                if r.backend == "authentik" and r.sid:
                    add(sid_token(r.sid), "authentik")
        for email in {r.email.lower() for r in ident.records if r.email}:
            local, _, domain = email.rpartition("@")
            if local and domain:
                add(f"cloud_drive:user:{email}", "email")
                add(f"cloud_drive:domain:{domain}", "email")
        user_ids = ident.akashic_user_ids
        if user_ids:
            bindings = (await db.execute(
                select(FsBinding).join(FsPerson, FsBinding.fs_person_id == FsPerson.id)
                .where(FsPerson.user_id.in_(user_ids))
            )).scalars().all()
            for b in bindings:
                for t in binding_to_tokens(b):
                    add(t, "akashic_binding")
        add(ANYONE, "builtin")
        add(AUTH, "builtin")

    tokens = sorted({t for t, _ in used})
    names = await _sid_names(db, {t[4:] for t in tokens if t.startswith("sid:")})
    used_list = [
        TokenUsed(token=t, origin=o, name=names.get(t[4:]) if t.startswith("sid:") else None)
        for t, o in sorted(used)
    ]
    missing = [k for k, p in (("posix", "posix:"), ("sid", "sid:"))
               if not any(t.startswith(p) for t in tokens)]
    cached_at = min(
        (g or s for s, g in ident.freshness.values()), default=None)

    if not any(o in ("ad", "authentik", "akashic_binding") for _, o in used):
        return ResolvedTokens(tokens, used_list, "failed", cached_at,
                              "no file-system identity", missing)
    status, detail = _expansion(ident, bool(ad_rows))
    return ResolvedTokens(tokens, used_list, status, cached_at, detail, missing)
