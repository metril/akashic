"""Denormalize an ACL → string-keyed identifier sets per canonical right.

Output sets feed Meilisearch's `viewable_by_*` filterable fields. Reuses
the per-type evaluators from `effective_perms.py` to do the actual rights
math — this service is pure orchestration: enumerate principals from the
ACL, ask "does this principal have read?" via compute_effective(), bucket.

POSIX `delete` is intentionally not computed (spec — depends on parent dir).
"""
from __future__ import annotations

from akashic.schemas.acl import (
    ACL,
    NfsV4ACL,
    NtACL,
    PosixACL,
    S3ACL,
)
from akashic.schemas.effective import GroupRef, PrincipalRef
from akashic.services.effective_perms import compute_effective


# ── Identifier vocabulary ────────────────────────────────────────────────────

ANYONE = "*"
AUTH = "auth"


def posix_uid(uid: int | str) -> str:
    return f"posix:uid:{uid}"


def posix_gid(gid: int | str) -> str:
    return f"posix:gid:{gid}"


def sid(s: str) -> str:
    return f"sid:{s}"


def nfsv4_user(principal: str) -> str:
    return f"nfsv4:{principal}"


def nfsv4_group(principal: str) -> str:
    return f"nfsv4:GROUP:{principal}"


def s3_user(canonical_id: str) -> str:
    return f"s3:user:{canonical_id}"


# ── Per-model principal enumeration ──────────────────────────────────────────


def _posix_principals(
    acl: PosixACL | None,
    base_uid: int | None,
    base_gid: int | None,
) -> list[tuple[str, PrincipalRef, list[GroupRef]]]:
    """Returns list of (token, principal, groups) for POSIX."""
    out: list[tuple[str, PrincipalRef, list[GroupRef]]] = []
    if base_uid is not None:
        out.append((posix_uid(base_uid), PrincipalRef(type="posix_uid", identifier=str(base_uid)), []))
    if base_gid is not None:
        # A pseudo-principal that's a member of base_gid — represents "any user
        # in the owning group". Identifier is "-1" so it can't accidentally
        # match a real user.
        out.append((
            posix_gid(base_gid),
            PrincipalRef(type="posix_uid", identifier="-1"),
            [GroupRef(type="posix_uid", identifier=str(base_gid))],
        ))
    if acl is not None:
        for ace in acl.entries:
            if ace.tag == "user" and ace.qualifier:
                token = posix_uid(ace.qualifier)
                if not any(t == token for t, _, _ in out):
                    out.append((
                        token,
                        PrincipalRef(type="posix_uid", identifier=ace.qualifier),
                        [],
                    ))
            elif ace.tag == "group" and ace.qualifier:
                token = posix_gid(ace.qualifier)
                if not any(t == token for t, _, _ in out):
                    out.append((
                        token,
                        PrincipalRef(type="posix_uid", identifier="-1"),
                        [GroupRef(type="posix_uid", identifier=ace.qualifier)],
                    ))
    return out


def _nfsv4_principals(acl: NfsV4ACL) -> list[tuple[str, PrincipalRef, list[GroupRef]]]:
    out: list[tuple[str, PrincipalRef, list[GroupRef]]] = []
    seen: set[str] = set()
    for ace in acl.entries:
        if ace.principal in ("OWNER@", "GROUP@"):
            continue  # OWNER@/GROUP@ require explicit caller context
        if ace.principal == "EVERYONE@":
            continue  # handled via the ANYONE probe
        if "identifier_group" in ace.flags:
            token = nfsv4_group(ace.principal)
            if token in seen:
                continue
            seen.add(token)
            out.append((
                token,
                PrincipalRef(type="nfsv4_principal", identifier="__none__"),
                [GroupRef(type="nfsv4_principal", identifier=ace.principal)],
            ))
        else:
            token = nfsv4_user(ace.principal)
            if token in seen:
                continue
            seen.add(token)
            out.append((
                token,
                PrincipalRef(type="nfsv4_principal", identifier=ace.principal),
                [],
            ))
    return out


def _nt_principals(acl: NtACL) -> list[tuple[str, PrincipalRef, list[GroupRef]]]:
    out: list[tuple[str, PrincipalRef, list[GroupRef]]] = []
    seen: set[str] = set()
    EVERYONE = "S-1-1-0"
    AUTH_SID = "S-1-5-11"
    for ace in acl.entries:
        if ace.sid in (EVERYONE, AUTH_SID):
            continue
        token = sid(ace.sid)
        if token in seen:
            continue
        seen.add(token)
        out.append((
            token,
            PrincipalRef(type="sid", identifier=ace.sid),
            [],
        ))
    if acl.owner is not None and acl.owner.sid not in (EVERYONE, AUTH_SID):
        token = sid(acl.owner.sid)
        if token not in seen:
            seen.add(token)
            out.append((
                token,
                PrincipalRef(type="sid", identifier=acl.owner.sid),
                [],
            ))
    return out


def _s3_principals(acl: S3ACL) -> list[tuple[str, PrincipalRef, list[GroupRef]]]:
    out: list[tuple[str, PrincipalRef, list[GroupRef]]] = []
    seen: set[str] = set()
    for grant in acl.grants:
        if grant.grantee_type == "group":
            continue  # AllUsers/AuthenticatedUsers handled via probes
        token = s3_user(grant.grantee_id)
        if token in seen:
            continue
        seen.add(token)
        out.append((
            token,
            PrincipalRef(type="s3_canonical", identifier=grant.grantee_id),
            [],
        ))
    if acl.owner is not None:
        token = s3_user(acl.owner.id)
        if token not in seen:
            seen.add(token)
            out.append((
                token,
                PrincipalRef(type="s3_canonical", identifier=acl.owner.id),
                [],
            ))
    return out


# ── Probes for catch-all grants ──────────────────────────────────────────────

# Synthetic principals for the ANYONE / AUTH probes. Use IDs that won't
# collide with real ones.

_ANYONE_PROBES = {
    "posix": (PrincipalRef(type="posix_uid", identifier="999999999"), []),
    "nfsv4": (PrincipalRef(type="nfsv4_principal", identifier="EVERYONE@"), []),
    "nt":    (PrincipalRef(type="sid", identifier="S-1-1-0"), []),
    "s3":    (PrincipalRef(type="s3_canonical", identifier="__nobody__"), []),
}

# AUTH probe: only NT and S3 distinguish authenticated from anonymous.
_AUTH_PROBES = {
    "nt":    (PrincipalRef(type="sid", identifier="S-1-5-9999"), []),
    "s3":    (PrincipalRef(type="s3_canonical", identifier="__authenticated__"), []),
}


def _grants(
    acl: ACL | None, principal: PrincipalRef, groups: list[GroupRef],
    base_mode: int | None, base_uid: int | None, base_gid: int | None,
) -> dict[str, bool]:
    result = compute_effective(
        acl=acl,
        base_mode=base_mode,
        base_uid=base_uid,
        base_gid=base_gid,
        principal=principal,
        groups=groups,
    )
    return {
        "read":   result.rights["read"].granted,
        "write":  result.rights["write"].granted,
        "delete": result.rights["delete"].granted,
    }


# ── Public entry point ───────────────────────────────────────────────────────


def denormalize_acl(
    acl: ACL | None,
    base_mode: int | None,
    base_uid: int | None,
    base_gid: int | None,
) -> dict[str, list[str]]:
    """Returns {'read': [...], 'write': [...], 'delete': [...]} of identifier strings."""
    buckets: dict[str, list[str]] = {"read": [], "write": [], "delete": []}

    if acl is None and base_mode is None:
        return buckets

    model = "posix"
    if isinstance(acl, NfsV4ACL):
        model = "nfsv4"
    elif isinstance(acl, NtACL):
        model = "nt"
    elif isinstance(acl, S3ACL):
        model = "s3"

    if model == "posix":
        principals = _posix_principals(acl if isinstance(acl, PosixACL) else None, base_uid, base_gid)
    elif model == "nfsv4":
        principals = _nfsv4_principals(acl)  # type: ignore[arg-type]
    elif model == "nt":
        principals = _nt_principals(acl)  # type: ignore[arg-type]
    elif model == "s3":
        principals = _s3_principals(acl)  # type: ignore[arg-type]
    else:
        principals = []

    for token, principal, groups in principals:
        rights = _grants(acl, principal, groups, base_mode, base_uid, base_gid)
        for right, granted in rights.items():
            if granted and (right != "delete" or model != "posix"):
                buckets[right].append(token)

    # ANYONE probe.
    anyone_principal, anyone_groups = _ANYONE_PROBES.get(model, (None, []))
    if anyone_principal is not None:
        rights = _grants(acl, anyone_principal, anyone_groups, base_mode, base_uid, base_gid)
        for right, granted in rights.items():
            if granted and (right != "delete" or model != "posix"):
                if ANYONE not in buckets[right]:
                    buckets[right].append(ANYONE)

    # AUTH probe.
    auth_principal, auth_groups = _AUTH_PROBES.get(model, (None, []))
    if auth_principal is not None:
        rights = _grants(acl, auth_principal, auth_groups, base_mode, base_uid, base_gid)
        for right, granted in rights.items():
            if granted and AUTH not in buckets[right]:
                buckets[right].append(AUTH)

    return buckets
