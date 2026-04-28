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
