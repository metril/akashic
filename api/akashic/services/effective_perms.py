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

_NFSV4_BITS: dict[RightName, set[str]] = {
    "read":         {"read_data", "list_directory"},
    "write":        {"write_data", "append_data", "add_file"},
    "execute":      {"execute"},
    "delete":       {"delete", "delete_child"},
    "change_perms": {"write_acl", "write_owner"},
}

_NT_BITS: dict[RightName, set[str]] = {
    "read":         {"READ_DATA", "LIST_DIRECTORY", "GENERIC_READ", "GENERIC_ALL"},
    "write":        {"WRITE_DATA", "ADD_FILE", "APPEND_DATA", "ADD_SUBDIRECTORY", "GENERIC_WRITE", "GENERIC_ALL"},
    "execute":      {"EXECUTE", "TRAVERSE", "GENERIC_EXECUTE", "GENERIC_ALL"},
    "delete":       {"DELETE", "DELETE_CHILD", "GENERIC_ALL"},
    "change_perms": {"WRITE_DAC", "WRITE_OWNER", "GENERIC_ALL"},
}

_NT_EVERYONE_SID = "S-1-1-0"
_NT_AUTHENTICATED_USERS_SID = "S-1-5-11"

_S3_PERMS: dict[RightName, set[str]] = {
    "read":         {"READ", "FULL_CONTROL"},
    "write":        {"WRITE", "FULL_CONTROL"},
    "delete":       {"WRITE", "FULL_CONTROL"},
    "change_perms": {"WRITE_ACP", "FULL_CONTROL"},
    # execute intentionally absent — S3 has no execute concept.
}

_S3_ALL_USERS = "AllUsers"
_S3_AUTHENTICATED_USERS = "AuthenticatedUsers"
_S3_CAVEAT_IAM = (
    "S3 evaluation does not include IAM user/role policies; "
    "bucket policy condition keys not evaluated."
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


# ── NFSv4 ────────────────────────────────────────────────────────────────────

def _nfsv4_principal_matches(
    ace: NfsV4ACE, principal: PrincipalRef, groups: list[GroupRef],
) -> bool:
    if ace.principal == "EVERYONE@":
        return True
    if "identifier_group" in ace.flags:
        return any(g.identifier == ace.principal for g in groups)
    if ace.principal == principal.identifier:
        return True
    if ace.principal == "OWNER@" and principal.identifier == "OWNER@":
        return True
    if ace.principal == "GROUP@":
        return any(g.identifier == "GROUP@" for g in groups)
    return False


def _eval_nfsv4(acl: NfsV4ACL, principal: PrincipalRef, groups: list[GroupRef]) -> EffectivePerms:
    rights = _empty_rights()
    for right, bit_set in _NFSV4_BITS.items():
        for i, ace in enumerate(acl.entries):
            if ace.ace_type not in ("allow", "deny"):
                continue  # audit/alarm don't affect access
            if not _nfsv4_principal_matches(ace, principal, groups):
                continue
            if not (set(ace.mask) & bit_set):
                continue
            granted = ace.ace_type == "allow"
            ref = ACEReference(
                ace_index=i,
                summary=f"{ace.principal} {ace.ace_type} {','.join(ace.mask)}",
            )
            rights[right] = RightResult(granted=granted, by=[ref])
            break  # first match wins per RFC 7530 §6.2.1
    return _effective(rights, "nfsv4", principal, groups, [])


# ── NT (CIFS) ────────────────────────────────────────────────────────────────

def _nt_principal_matches(
    ace: NtACE, principal: PrincipalRef, groups: list[GroupRef],
) -> bool:
    if ace.sid == _NT_EVERYONE_SID:
        return True
    if ace.sid == _NT_AUTHENTICATED_USERS_SID and principal.type == "sid":
        return True
    if ace.sid == principal.identifier:
        return True
    # Windows tokens treat user and group SIDs uniformly at access-check time;
    # the captured DACL doesn't carry the NFSv4-style identifier_group flag.
    return any(g.identifier == ace.sid for g in groups)


def _eval_nt(acl: NtACL, principal: PrincipalRef, groups: list[GroupRef]) -> EffectivePerms:
    rights = _empty_rights()
    for right, bit_set in _NT_BITS.items():
        for i, ace in enumerate(acl.entries):
            if ace.ace_type not in ("allow", "deny"):
                continue
            if not _nt_principal_matches(ace, principal, groups):
                continue
            if not (set(ace.mask) & bit_set):
                continue
            granted = ace.ace_type == "allow"
            label = ace.name or ace.sid
            ref = ACEReference(
                ace_index=i,
                summary=f"{label} {ace.ace_type} {','.join(ace.mask)}",
            )
            rights[right] = RightResult(granted=granted, by=[ref])
            break

    # Owner implicit change_perms (READ_CONTROL + WRITE_DAC).
    caveats: list[str] = []
    if acl.owner is not None and acl.owner.sid == principal.identifier:
        if not rights["change_perms"].granted:
            ref = ACEReference(
                ace_index=-1,
                summary=f"owner implicit (READ_CONTROL+WRITE_DAC): {acl.owner.name or acl.owner.sid}",
            )
            rights["change_perms"] = RightResult(granted=True, by=[ref])
            caveats.append("Owner is implicitly granted READ_CONTROL and WRITE_DAC even without explicit ACEs.")

    return _effective(rights, "nt", principal, groups, caveats)


def _s3_grant_matches(grant, principal: PrincipalRef) -> bool:
    if grant.grantee_type == "group":
        if grant.grantee_id == _S3_ALL_USERS:
            return True
        if grant.grantee_id == _S3_AUTHENTICATED_USERS:
            return True
        return False
    # canonical_user / amazon_customer_by_email
    return grant.grantee_id == principal.identifier


def _eval_s3(
    acl: S3ACL,
    principal: PrincipalRef,
    groups: list[GroupRef],
    source_security: dict | None,
) -> EffectivePerms:
    rights = _empty_rights()
    for right, perm_set in _S3_PERMS.items():
        for i, grant in enumerate(acl.grants):
            if not _s3_grant_matches(grant, principal):
                continue
            if grant.permission not in perm_set:
                continue
            label = grant.grantee_name or grant.grantee_id
            ref = ACEReference(
                ace_index=i,
                summary=f"{grant.grantee_type}:{label} {grant.permission}",
            )
            rights[right] = RightResult(granted=True, by=[ref])
            break  # any matching allow grants — no deny in S3 ACL grammar
    caveats = [_S3_CAVEAT_IAM]
    if source_security and source_security.get("is_public_inferred"):
        caveats.append("Bucket is publicly accessible per Public Access Block / bucket policy.")
    return _effective(rights, "s3", principal, groups, caveats)
