import pytest

from akashic.schemas.acl import (
    NfsV4ACL, NfsV4ACE, NtACL, NtACE, NtPrincipal,
    PosixACL, PosixACE, S3ACL, S3Grant, S3Owner,
)
from akashic.services.acl_denorm import (
    ANYONE,
    AUTH,
    denormalize_acl,
    posix_uid,
    posix_gid,
    sid,
    nfsv4_user,
    nfsv4_group,
    s3_user,
)


def _posix(entries, default=None):
    return PosixACL.model_validate({
        "type": "posix",
        "entries": entries,
        "default_entries": default,
    })


def test_posix_owner_user_and_other_get_read():
    acl = _posix([
        {"tag": "user_obj",  "qualifier": "",     "perms": "rwx"},
        {"tag": "user",      "qualifier": "1001", "perms": "r-x"},
        {"tag": "group_obj", "qualifier": "",     "perms": "r--"},
        {"tag": "mask",      "qualifier": "",     "perms": "rwx"},
        {"tag": "other",     "qualifier": "",     "perms": "r--"},
    ])
    out = denormalize_acl(acl, base_mode=0o644, base_uid=1000, base_gid=100)
    assert posix_uid(1000) in out["read"]
    assert posix_uid(1001) in out["read"]
    assert posix_gid(100) in out["read"]
    assert ANYONE in out["read"]
    # POSIX delete is intentionally not denormalized.
    assert out["delete"] == []


def test_posix_user_ace_in_write_set():
    acl = _posix([
        {"tag": "user_obj",  "qualifier": "",     "perms": "rwx"},
        {"tag": "user",      "qualifier": "1001", "perms": "rwx"},
        {"tag": "mask",      "qualifier": "",     "perms": "rwx"},
        {"tag": "other",     "qualifier": "",     "perms": "---"},
    ])
    out = denormalize_acl(acl, base_mode=0o600, base_uid=1000, base_gid=100)
    assert posix_uid(1001) in out["write"]
    assert ANYONE not in out["read"]


def test_posix_no_acl_uses_base_mode():
    out = denormalize_acl(acl=None, base_mode=0o755, base_uid=1000, base_gid=100)
    assert posix_uid(1000) in out["read"]
    assert posix_gid(100)  in out["read"]
    assert ANYONE          in out["read"]


def test_nfsv4_users_and_groups():
    acl = NfsV4ACL.model_validate({
        "type": "nfsv4",
        "entries": [
            {"principal": "alice@dom", "ace_type": "allow", "mask": ["read_data"], "flags": []},
            {"principal": "eng@dom",   "ace_type": "allow", "mask": ["write_data"], "flags": ["identifier_group"]},
            {"principal": "EVERYONE@", "ace_type": "allow", "mask": ["execute"],   "flags": []},
        ],
    })
    out = denormalize_acl(acl, base_mode=None, base_uid=None, base_gid=None)
    assert nfsv4_user("alice@dom") in out["read"]
    assert nfsv4_group("eng@dom")  in out["write"]
    # EVERYONE@ allow is mask=[execute] — does NOT address read or write.
    assert ANYONE not in out["read"]
    assert ANYONE not in out["write"]


def test_nfsv4_deny_excludes_principal():
    acl = NfsV4ACL.model_validate({
        "type": "nfsv4",
        "entries": [
            {"principal": "alice@dom", "ace_type": "deny",  "mask": ["read_data"], "flags": []},
            {"principal": "alice@dom", "ace_type": "allow", "mask": ["read_data"], "flags": []},
        ],
    })
    out = denormalize_acl(acl, base_mode=None, base_uid=None, base_gid=None)
    assert nfsv4_user("alice@dom") not in out["read"]


def test_nt_sids_in_buckets():
    acl = NtACL.model_validate({
        "type": "nt",
        "owner": {"sid": "S-1-5-21-1-2-3-1013", "name": ""},
        "group": None,
        "control": [],
        "entries": [
            {"sid": "S-1-5-21-1-2-3-1013", "name": "", "ace_type": "allow",
             "mask": ["READ_DATA", "WRITE_DATA"], "flags": []},
            {"sid": "S-1-1-0", "name": "", "ace_type": "allow",
             "mask": ["READ_DATA"], "flags": []},
            {"sid": "S-1-5-11", "name": "", "ace_type": "allow",
             "mask": ["READ_DATA"], "flags": []},
        ],
    })
    out = denormalize_acl(acl, base_mode=None, base_uid=None, base_gid=None)
    assert sid("S-1-5-21-1-2-3-1013") in out["read"]
    assert sid("S-1-5-21-1-2-3-1013") in out["write"]
    assert ANYONE in out["read"]   # S-1-1-0 (Everyone)
    assert AUTH   in out["read"]   # S-1-5-11 (Authenticated Users)


def test_s3_canonical_user_and_groups():
    acl = S3ACL.model_validate({
        "type": "s3",
        "owner": {"id": "acct-1", "display_name": ""},
        "grants": [
            {"grantee_type": "canonical_user", "grantee_id": "acct-1",
             "grantee_name": "", "permission": "FULL_CONTROL"},
            {"grantee_type": "group", "grantee_id": "AllUsers",
             "grantee_name": "", "permission": "READ"},
        ],
    })
    out = denormalize_acl(acl, base_mode=None, base_uid=None, base_gid=None)
    assert s3_user("acct-1") in out["read"]
    assert s3_user("acct-1") in out["write"]
    assert ANYONE            in out["read"]
    # FULL_CONTROL → write+delete via S3 mapping (W FULL_CONTROL grants delete).
    assert s3_user("acct-1") in out["delete"]


def test_none_acl_no_base_mode_returns_empty_buckets():
    out = denormalize_acl(acl=None, base_mode=None, base_uid=None, base_gid=None)
    assert out == {"read": [], "write": [], "delete": []}


def test_token_constants():
    assert ANYONE == "*"
    assert AUTH == "auth"
    assert posix_uid(1000) == "posix:uid:1000"
    assert posix_gid(100) == "posix:gid:100"
    assert sid("S-1-5-32-544") == "sid:S-1-5-32-544"
    assert nfsv4_user("alice@dom") == "nfsv4:alice@dom"
    assert nfsv4_group("eng@dom") == "nfsv4:GROUP:eng@dom"
    assert s3_user("acct-1") == "s3:user:acct-1"
