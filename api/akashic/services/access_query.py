"""Translate user identity → permission tokens; build SQL clauses against
the denormalized `entries.viewable_by_*` columns.

Single source of truth for "what tokens does this caller represent?" — used
by Search (Meili filter + DB fallback), Browse permission trim, the admin
blast-radius endpoints, and any future place that needs to ask "can this
caller see entry X?".

The token vocabulary is fixed at acl_denorm.py — `posix:uid:N`,
`posix:gid:N`, `sid:S-…`, `nfsv4:NAME`, `nfsv4:GROUP:NAME`, `s3:user:ID`,
plus the implicit `*` (anyone) and `auth` (authenticated). This module
builds set-overlap queries against arrays of those tokens.
"""
from __future__ import annotations

from typing import Literal

from sqlalchemy import false, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from akashic.models.entry import Entry
from akashic.models.fs_person import FsBinding, FsPerson
from akashic.models.user import User
from akashic.schemas.audit import SearchAsOverride
from akashic.services.acl_denorm import (
    ANYONE,
    AUTH,
    nfsv4_group,
    nfsv4_user,
    posix_gid,
    posix_uid,
    s3_user,
    sid,
)

Right = Literal["read", "write", "delete"]


def binding_to_tokens(binding: FsBinding) -> list[str]:
    """Translate one FsBinding → tokens that represent it (self + groups)."""
    tokens: list[str] = []
    if binding.identity_type == "posix_uid":
        tokens.append(posix_uid(binding.identifier))
        tokens.extend(posix_gid(g) for g in binding.groups)
    elif binding.identity_type == "sid":
        tokens.append(sid(binding.identifier))
        tokens.extend(sid(g) for g in binding.groups)
    elif binding.identity_type == "nfsv4_principal":
        tokens.append(nfsv4_user(binding.identifier))
        tokens.extend(nfsv4_group(g) for g in binding.groups)
    elif binding.identity_type == "s3_canonical":
        tokens.append(s3_user(binding.identifier))
    return tokens


async def user_has_any_bindings(user: User, db: AsyncSession) -> bool:
    """Cheap probe — drives the "should we filter at all?" default."""
    result = await db.execute(
        select(FsPerson.id).where(FsPerson.user_id == user.id).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def user_principal_tokens(user: User, db: AsyncSession) -> list[str]:
    """All tokens that represent a user across every binding, plus implicit
    `*` / `auth`. Sorted for determinism (callers often inline this into
    Meili filter strings or audit payloads)."""
    bindings = (await db.execute(
        select(FsBinding)
        .join(FsPerson, FsBinding.fs_person_id == FsPerson.id)
        .where(FsPerson.user_id == user.id)
    )).scalars().all()
    tokens: set[str] = {ANYONE, AUTH}
    for b in bindings:
        tokens.update(binding_to_tokens(b))
    return sorted(tokens)


def override_tokens(override: SearchAsOverride) -> list[str]:
    """Token set for a `search_as` override (admin "what does user X see?"
    debug path). Same shape as user_principal_tokens output."""
    tokens: set[str] = {ANYONE, AUTH}
    if override.type == "posix_uid":
        tokens.add(posix_uid(override.identifier))
        tokens.update(posix_gid(g) for g in override.groups)
    elif override.type == "sid":
        tokens.add(sid(override.identifier))
        tokens.update(sid(g) for g in override.groups)
    elif override.type == "nfsv4_principal":
        tokens.add(nfsv4_user(override.identifier))
        tokens.update(nfsv4_group(g) for g in override.groups)
    elif override.type == "s3_canonical":
        tokens.add(s3_user(override.identifier))
    return sorted(tokens)


_RIGHT_TO_COLUMN = {
    "read": Entry.viewable_by_read,
    "write": Entry.viewable_by_write,
    "delete": Entry.viewable_by_delete,
}


def viewable_clause(tokens: list[str], right: Right) -> ColumnElement[bool]:
    """SQL predicate: "the caller, represented by `tokens`, has `right` on
    the entry". Uses Postgres array overlap (`&&`) against the GIN-indexed
    `viewable_by_<right>` column.

    Caller is responsible for the "no tokens means no access" case — the
    overlap with an empty array would be FALSE for every row, but we
    short-circuit to a literal FALSE so the planner skips the index scan.
    """
    column = _RIGHT_TO_COLUMN[right]
    if not tokens:
        return false()
    return column.op("&&")(tokens)


async def user_can_view(entry, user: User, db: AsyncSession) -> bool:
    """Phase-5 per-user ACL check used by entry-detail / content / preview
    endpoints. Returns True (allow) when:

    - the deployment-wide feature flag is off,
    - the caller is an admin (admins always see everything; the show_all
      query param on Browse is a separate UX concern),
    - the caller has no FsBindings (legacy users keep see-all behaviour
      until an admin attaches one).

    Otherwise returns whether the caller's tokens overlap the entry's
    `viewable_by_read` array. Mirrors the semantics of `viewable_clause`
    used by the SQL-side trim, just on a single already-loaded row.
    """
    from akashic.config import settings  # avoid module-load cycle

    if not settings.browse_enforce_perms:
        return True
    if user.role == "admin":
        return True
    if not await user_has_any_bindings(user, db):
        return True
    tokens = await user_principal_tokens(user, db)
    viewable = entry.viewable_by_read or []
    return any(t in viewable for t in tokens)
