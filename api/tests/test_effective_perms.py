import pytest

from akashic.schemas.acl import PosixACE, PosixACL
from akashic.schemas.effective import GroupRef, PrincipalRef
from akashic.services.effective_perms import compute_effective


def _posix(entries: list[dict], default: list[dict] | None = None) -> PosixACL:
    return PosixACL.model_validate({
        "type": "posix",
        "entries": entries,
        "default_entries": default,
    })


@pytest.mark.parametrize(
    "principal_uid,groups,base_mode,base_uid,base_gid,acl_entries,expect_read,expect_write,expect_exec",
    [
        # Owner: full rwx via base mode.
        ("1000", [], 0o755, 1000, 100, [], True,  True,  True),
        # Other: mode 755 grants r-x.
        ("9999", [], 0o755, 1000, 100, [], True,  False, True),
        # Group via base_gid (group_obj rwx).
        ("9999", [GroupRef(type="posix_uid", identifier="100")], 0o775, 1000, 100, [], True, True, True),
        # ACL: user:alice gets r-x; mask narrows write off.
        (
            "1001", [],
            0o600, 1000, 100,
            [
                {"tag": "user_obj",  "qualifier": "",     "perms": "rw-"},
                {"tag": "user",      "qualifier": "1001", "perms": "rwx"},
                {"tag": "group_obj", "qualifier": "",     "perms": "r--"},
                {"tag": "mask",      "qualifier": "",     "perms": "r-x"},
                {"tag": "other",     "qualifier": "",     "perms": "---"},
            ],
            True, False, True,
        ),
        # ACL: matching group with rwx, mask r-x → rwx & r-x = r-x.
        (
            "1001",
            [GroupRef(type="posix_uid", identifier="200")],
            0o600, 1000, 100,
            [
                {"tag": "user_obj",  "qualifier": "",    "perms": "rwx"},
                {"tag": "group",     "qualifier": "200", "perms": "rwx"},
                {"tag": "mask",      "qualifier": "",    "perms": "r-x"},
                {"tag": "other",     "qualifier": "",    "perms": "---"},
            ],
            True, False, True,
        ),
    ],
)
def test_posix_evaluator(
    principal_uid, groups, base_mode, base_uid, base_gid,
    acl_entries, expect_read, expect_write, expect_exec,
):
    acl = _posix(acl_entries) if acl_entries else None
    result = compute_effective(
        acl=acl,
        base_mode=base_mode,
        base_uid=base_uid,
        base_gid=base_gid,
        principal=PrincipalRef(type="posix_uid", identifier=principal_uid),
        groups=groups,
    )
    assert result.rights["read"].granted is expect_read
    assert result.rights["write"].granted is expect_write
    assert result.rights["execute"].granted is expect_exec
    # POSIX folds delete and change_perms into write.
    assert result.rights["delete"].granted is expect_write
    assert result.rights["change_perms"].granted is expect_write
    assert result.evaluated_with.model == ("posix" if acl else "posix")  # base_mode path uses posix


def test_posix_owner_by_field_references_base():
    result = compute_effective(
        acl=None,
        base_mode=0o755,
        base_uid=1000,
        base_gid=100,
        principal=PrincipalRef(type="posix_uid", identifier="1000"),
        groups=[],
    )
    assert result.rights["read"].granted
    # Non-empty `by` traceable to "base mode owner".
    assert any("owner" in ref.summary.lower() for ref in result.rights["read"].by)


def test_posix_no_acl_no_base_mode_returns_all_denied_for_unknown_principal():
    result = compute_effective(
        acl=None,
        base_mode=None,
        base_uid=None,
        base_gid=None,
        principal=PrincipalRef(type="posix_uid", identifier="1000"),
        groups=[],
    )
    for r in ("read", "write", "execute", "delete", "change_perms"):
        assert result.rights[r].granted is False
    assert result.evaluated_with.model == "none"


def test_posix_caveats_include_fold_note():
    result = compute_effective(
        acl=None,
        base_mode=0o755,
        base_uid=1000,
        base_gid=100,
        principal=PrincipalRef(type="posix_uid", identifier="1000"),
        groups=[],
    )
    assert any("delete" in c.lower() and "write" in c.lower() for c in result.evaluated_with.caveats)


from akashic.schemas.acl import NfsV4ACE, NfsV4ACL


def _nfs(*entries) -> NfsV4ACL:
    return NfsV4ACL.model_validate({
        "type": "nfsv4",
        "entries": [
            {
                "principal": e[0],
                "ace_type":  e[1],
                "mask":      list(e[2]),
                "flags":     list(e[3]) if len(e) > 3 else [],
            }
            for e in entries
        ],
    })


@pytest.mark.parametrize(
    "principal_id,groups,acl,expect_read,expect_write,expect_exec",
    [
        # First-match: explicit allow read.
        ("alice@dom", [],
         _nfs(("alice@dom", "allow", ["read_data"])),
         True, False, False),
        # Deny precedes allow for the same right.
        ("alice@dom", [],
         _nfs(
             ("alice@dom", "deny",  ["read_data"]),
             ("alice@dom", "allow", ["read_data"]),
         ),
         False, False, False),
        # EVERYONE@ allow grants to anyone.
        ("alice@dom", [],
         _nfs(("EVERYONE@", "allow", ["read_data"])),
         True, False, False),
        # Group match via identifier_group flag.
        ("alice@dom",
         [GroupRef(type="nfsv4_principal", identifier="eng@dom")],
         _nfs(("eng@dom", "allow", ["write_data"], ["identifier_group"])),
         False, True, False),
        # Multiple right bits in one allow.
        ("alice@dom", [],
         _nfs(("alice@dom", "allow", ["read_data", "write_data", "execute"])),
         True, True, True),
        # Right with no addressing ACE → denied.
        ("alice@dom", [],
         _nfs(("alice@dom", "allow", ["read_data"])),
         True, False, False),
    ],
)
def test_nfsv4_evaluator(principal_id, groups, acl, expect_read, expect_write, expect_exec):
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="nfsv4_principal", identifier=principal_id),
        groups=groups,
    )
    assert result.rights["read"].granted is expect_read
    assert result.rights["write"].granted is expect_write
    assert result.rights["execute"].granted is expect_exec
    assert result.evaluated_with.model == "nfsv4"


def test_nfsv4_delete_distinct_from_write():
    acl = _nfs(("alice@dom", "allow", ["write_data"]))
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="nfsv4_principal", identifier="alice@dom"),
        groups=[],
    )
    assert result.rights["write"].granted is True
    # delete is its own bit in NFSv4 — not folded.
    assert result.rights["delete"].granted is False


def test_nfsv4_change_perms_via_write_acl():
    acl = _nfs(("alice@dom", "allow", ["write_acl"]))
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="nfsv4_principal", identifier="alice@dom"),
        groups=[],
    )
    assert result.rights["change_perms"].granted is True


def test_nfsv4_by_field_references_ace():
    acl = _nfs(("alice@dom", "allow", ["read_data"]))
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="nfsv4_principal", identifier="alice@dom"),
        groups=[],
    )
    assert result.rights["read"].by[0].ace_index == 0
    assert "alice@dom" in result.rights["read"].by[0].summary


from akashic.schemas.acl import NtACE, NtACL, NtPrincipal


def _nt(
    entries: list[dict],
    owner: dict | None = None,
    group: dict | None = None,
) -> NtACL:
    return NtACL.model_validate({
        "type": "nt",
        "owner": owner,
        "group": group,
        "control": [],
        "entries": entries,
    })


def _nt_ace(sid: str, ace_type: str, mask: list[str], flags: list[str] | None = None) -> dict:
    return {
        "sid": sid, "name": "",
        "ace_type": ace_type, "flags": flags or [], "mask": mask,
    }


@pytest.mark.parametrize(
    "principal_sid,group_sids,acl,expect_read,expect_write,expect_delete",
    [
        # Direct allow.
        ("S-1-5-21-1-2-3-1013", [],
         _nt([_nt_ace("S-1-5-21-1-2-3-1013", "allow", ["READ_DATA", "WRITE_DATA"])]),
         True, True, False),
        # Deny precedes allow.
        ("S-1-5-21-1-2-3-1013", [],
         _nt([
             _nt_ace("S-1-5-21-1-2-3-1013", "deny",  ["WRITE_DATA"]),
             _nt_ace("S-1-5-21-1-2-3-1013", "allow", ["WRITE_DATA"]),
         ]),
         False, False, False),
        # Everyone SID matches anyone.
        ("S-1-5-21-9-9-9-1234", [],
         _nt([_nt_ace("S-1-1-0", "allow", ["READ_DATA"])]),
         True, False, False),
        # Group match via identifier_group.
        ("S-1-5-21-1-2-3-1013",
         ["S-1-5-21-1-2-3-513"],
         _nt([_nt_ace("S-1-5-21-1-2-3-513", "allow", ["WRITE_DATA"], ["identifier_group"])]),
         False, True, False),
        # GENERIC_READ maps to read.
        ("S-1-5-21-1-2-3-1013", [],
         _nt([_nt_ace("S-1-5-21-1-2-3-1013", "allow", ["GENERIC_READ"])]),
         True, False, False),
        # DELETE bit grants delete.
        ("S-1-5-21-1-2-3-1013", [],
         _nt([_nt_ace("S-1-5-21-1-2-3-1013", "allow", ["DELETE"])]),
         False, False, True),
    ],
)
def test_nt_evaluator(principal_sid, group_sids, acl, expect_read, expect_write, expect_delete):
    groups = [GroupRef(type="sid", identifier=g) for g in group_sids]
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="sid", identifier=principal_sid),
        groups=groups,
    )
    assert result.rights["read"].granted is expect_read
    assert result.rights["write"].granted is expect_write
    assert result.rights["delete"].granted is expect_delete
    assert result.evaluated_with.model == "nt"


def test_nt_owner_implicit_change_perms():
    sid = "S-1-5-21-1-2-3-1013"
    acl = _nt(
        [_nt_ace("S-1-1-0", "allow", ["READ_DATA"])],
        owner={"sid": sid, "name": "DOM\\alice"},
    )
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="sid", identifier=sid),
        groups=[],
    )
    assert result.rights["change_perms"].granted is True
    # Synthetic ace_index=-1 indicates "owner implicit".
    assert any(ref.ace_index == -1 for ref in result.rights["change_perms"].by)


def test_nt_group_sid_matches_via_token_membership_no_flag():
    """Windows tokens contain both user and group SIDs uniformly. A DACL ACE
    granting access to a group SID matches a user whose token includes that SID,
    even though the ACE has no NFSv4-style 'identifier_group' flag.
    """
    user_sid = "S-1-5-21-1-2-3-1013"
    group_sid = "S-1-5-21-1-2-3-513"  # Domain Users
    acl = _nt([_nt_ace(group_sid, "allow", ["WRITE_DATA"])])  # no flags
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="sid", identifier=user_sid),
        groups=[GroupRef(type="sid", identifier=group_sid)],
    )
    assert result.rights["write"].granted is True
    assert result.rights["read"].granted is False


def test_nt_authenticated_users_matches_any_sid_principal():
    acl = _nt([_nt_ace("S-1-5-11", "allow", ["READ_DATA"])])
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="sid", identifier="S-1-5-21-9-9-9-1234"),
        groups=[],
    )
    assert result.rights["read"].granted is True


from akashic.schemas.acl import S3ACL, S3Grant, S3Owner


def _s3(grants: list[dict], owner: dict | None = None) -> S3ACL:
    return S3ACL.model_validate({
        "type": "s3",
        "owner": owner,
        "grants": grants,
    })


@pytest.mark.parametrize(
    "principal_id,grants,expect_read,expect_write,expect_delete",
    [
        # Direct canonical_user grant.
        ("acct-1",
         [{"grantee_type": "canonical_user", "grantee_id": "acct-1", "grantee_name": "", "permission": "READ"}],
         True, False, False),
        # FULL_CONTROL grants everything (except execute).
        ("acct-1",
         [{"grantee_type": "canonical_user", "grantee_id": "acct-1", "grantee_name": "", "permission": "FULL_CONTROL"}],
         True, True, True),
        # AllUsers group grants to everyone.
        ("anyone",
         [{"grantee_type": "group", "grantee_id": "AllUsers", "grantee_name": "", "permission": "READ"}],
         True, False, False),
        # WRITE permission yields delete granted.
        ("acct-1",
         [{"grantee_type": "canonical_user", "grantee_id": "acct-1", "grantee_name": "", "permission": "WRITE"}],
         False, True, True),
        # Non-matching grantee.
        ("acct-2",
         [{"grantee_type": "canonical_user", "grantee_id": "acct-1", "grantee_name": "", "permission": "READ"}],
         False, False, False),
    ],
)
def test_s3_evaluator(principal_id, grants, expect_read, expect_write, expect_delete):
    acl = _s3(grants)
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="s3_canonical", identifier=principal_id),
        groups=[],
    )
    assert result.rights["read"].granted is expect_read
    assert result.rights["write"].granted is expect_write
    assert result.rights["delete"].granted is expect_delete
    assert result.rights["execute"].granted is False  # always n/a
    assert result.evaluated_with.model == "s3"


def test_s3_authenticated_users_matches_any_principal():
    acl = _s3([
        {"grantee_type": "group", "grantee_id": "AuthenticatedUsers", "grantee_name": "", "permission": "READ"},
    ])
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="s3_canonical", identifier="any-acct"),
        groups=[],
    )
    assert result.rights["read"].granted is True


def test_s3_caveats_always_include_iam_note():
    acl = _s3([])
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="s3_canonical", identifier="acct-1"),
        groups=[],
    )
    assert any("IAM" in c for c in result.evaluated_with.caveats)
