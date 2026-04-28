"""Pure-function effective-permissions evaluator. Dispatches per ACL type.

Returns the canonical 5-right shape (`read`, `write`, `execute`, `delete`, `change_perms`)
regardless of model. Mapping per model is documented in the plan.

NO state mutation. NO caching. Safe to call from anywhere.
"""
from __future__ import annotations

from akashic.schemas.acl import (
    ACL,
    NfsV4ACE,
    NfsV4ACL,
    NtACE,
    NtACL,
    PosixACE,
    PosixACL,
    S3ACL,
)
from akashic.schemas.effective import (
    ACEReference,
    EffectivePerms,
    EffectivePermsEvaluatedWith,
    GroupRef,
    PrincipalRef,
    RightName,
    RightResult,
)

_ALL_RIGHTS: tuple[RightName, ...] = (
    "read", "write", "execute", "delete", "change_perms",
)


def _empty_rights() -> dict[RightName, RightResult]:
    return {r: RightResult(granted=False, by=[]) for r in _ALL_RIGHTS}


def _effective(
    rights: dict[RightName, RightResult],
    model: str,
    principal: PrincipalRef,
    groups: list[GroupRef],
    caveats: list[str],
) -> EffectivePerms:
    return EffectivePerms(
        rights=rights,
        evaluated_with=EffectivePermsEvaluatedWith(
            model=model,  # type: ignore[arg-type]
            principal=principal,
            groups=groups,
            caveats=caveats,
        ),
    )


def compute_effective(
    *,
    acl: ACL | None,
    base_mode: int | None,
    base_uid: int | None,
    base_gid: int | None,
    principal: PrincipalRef,
    groups: list[GroupRef] | None = None,
    source_security: dict | None = None,
) -> EffectivePerms:
    groups = groups or []
    if isinstance(acl, PosixACL) or (acl is None and base_mode is not None):
        return _eval_posix(acl, base_mode, base_uid, base_gid, principal, groups)
    if acl is None:
        return _effective(_empty_rights(), "none", principal, groups, [])
    if isinstance(acl, NfsV4ACL):
        return _eval_nfsv4(acl, principal, groups)
    if isinstance(acl, NtACL):
        return _eval_nt(acl, principal, groups)
    if isinstance(acl, S3ACL):
        return _eval_s3(acl, principal, groups, source_security)
    return _effective(_empty_rights(), "none", principal, groups, [])


# ── POSIX ────────────────────────────────────────────────────────────────────

def _perms_to_rwx(perms: str) -> tuple[bool, bool, bool]:
    return (perms[0] == "r", perms[1] == "w", perms[2] == "x")


def _mode_bits(mode: int, shift: int) -> tuple[bool, bool, bool]:
    bits = (mode >> shift) & 0b111
    return (bool(bits & 0b100), bool(bits & 0b010), bool(bits & 0b001))


def _rwx_str(r: bool, w: bool, x: bool) -> str:
    return ("r" if r else "-") + ("w" if w else "-") + ("x" if x else "-")


def _posix_native(
    acl_entries: list[PosixACE],
    base_mode: int | None,
    base_uid: int | None,
    base_gid: int | None,
    principal_id: str,
    group_ids: set[str],
) -> tuple[bool, bool, bool, list[ACEReference]]:
    refs: list[ACEReference] = []

    # 1. owner
    if base_uid is not None and principal_id == str(base_uid):
        if base_mode is None:
            return False, False, False, []
        r, w, x = _mode_bits(base_mode, 6)
        refs.append(ACEReference(ace_index=-1, summary=f"base mode owner {oct(base_mode)}"))
        return r, w, x, refs

    mask: tuple[bool, bool, bool] | None = None
    for ace in acl_entries:
        if ace.tag == "mask":
            mask = _perms_to_rwx(ace.perms)
            break

    # 2. user ACE
    for i, ace in enumerate(acl_entries):
        if ace.tag == "user" and ace.qualifier == principal_id:
            r, w, x = _perms_to_rwx(ace.perms)
            mask_note = ""
            if mask is not None:
                r, w, x = (r and mask[0], w and mask[1], x and mask[2])
                mask_note = f" (masked by {_rwx_str(*mask)})"
            refs.append(ACEReference(
                ace_index=i,
                summary=f"user:{ace.qualifier} {ace.perms}{mask_note}",
            ))
            return r, w, x, refs

    # 3. group(s)
    matched_any_group = False
    union_r = union_w = union_x = False

    # No ACL entries: fall back to base mode group bits when principal is in the owning group.
    if not acl_entries and base_mode is not None and base_gid is not None and str(base_gid) in group_ids:
        r, w, x = _mode_bits(base_mode, 3)
        refs.append(ACEReference(ace_index=-1, summary=f"base mode group {oct(base_mode)}"))
        return r, w, x, refs

    for i, ace in enumerate(acl_entries):
        if ace.tag == "group_obj":
            if base_gid is not None and str(base_gid) in group_ids:
                r, w, x = _perms_to_rwx(ace.perms)
                if mask is not None:
                    r, w, x = (r and mask[0], w and mask[1], x and mask[2])
                union_r |= r; union_w |= w; union_x |= x
                refs.append(ACEReference(ace_index=i, summary=f"group_obj {ace.perms}"))
                matched_any_group = True
        elif ace.tag == "group" and ace.qualifier in group_ids:
            r, w, x = _perms_to_rwx(ace.perms)
            if mask is not None:
                r, w, x = (r and mask[0], w and mask[1], x and mask[2])
            union_r |= r; union_w |= w; union_x |= x
            refs.append(ACEReference(ace_index=i, summary=f"group:{ace.qualifier} {ace.perms}"))
            matched_any_group = True
    if matched_any_group:
        return union_r, union_w, union_x, refs

    # 4. other
    if base_mode is None:
        return False, False, False, refs
    r, w, x = _mode_bits(base_mode, 0)
    refs.append(ACEReference(ace_index=-1, summary=f"base mode other {oct(base_mode)}"))
    return r, w, x, refs


def _eval_posix(
    acl: PosixACL | None,
    base_mode: int | None,
    base_uid: int | None,
    base_gid: int | None,
    principal: PrincipalRef,
    groups: list[GroupRef],
) -> EffectivePerms:
    group_ids = {g.identifier for g in groups}
    entries = acl.entries if acl is not None else []
    r, w, x, refs = _posix_native(entries, base_mode, base_uid, base_gid, principal.identifier, group_ids)
    rights = _empty_rights()
    rights["read"]         = RightResult(granted=r, by=list(refs) if r else [])
    rights["write"]        = RightResult(granted=w, by=list(refs) if w else [])
    rights["execute"]      = RightResult(granted=x, by=list(refs) if x else [])
    rights["delete"]       = RightResult(granted=w, by=list(refs) if w else [])
    rights["change_perms"] = RightResult(granted=w, by=list(refs) if w else [])
    caveats = ["POSIX folds delete and change_perms into write (parent-dir ACL not consulted)."]
    return _effective(rights, "posix", principal, groups, caveats)


# Stubs for NFSv4 / NT / S3 — implemented in later tasks.

def _eval_nfsv4(acl: NfsV4ACL, principal: PrincipalRef, groups: list[GroupRef]) -> EffectivePerms:
    return _effective(_empty_rights(), "nfsv4", principal, groups, ["NFSv4 evaluator not yet implemented"])


def _eval_nt(acl: NtACL, principal: PrincipalRef, groups: list[GroupRef]) -> EffectivePerms:
    return _effective(_empty_rights(), "nt", principal, groups, ["NT evaluator not yet implemented"])


def _eval_s3(
    acl: S3ACL,
    principal: PrincipalRef,
    groups: list[GroupRef],
    source_security: dict | None,
) -> EffectivePerms:
    return _effective(_empty_rights(), "s3", principal, groups, ["S3 evaluator not yet implemented"])
