import pytest
from pydantic import ValidationError, TypeAdapter

from akashic.schemas.acl import (
    ACL,
    PosixACL,
    PosixACE,
    NfsV4ACL,
    NfsV4ACE,
    NtACL,
    NtACE,
    NtPrincipal,
    S3ACL,
    S3Grant,
    S3Owner,
)

acl_adapter = TypeAdapter(ACL)


def test_posix_acl_round_trip():
    payload = {
        "type": "posix",
        "entries": [{"tag": "user", "qualifier": "alice", "perms": "rwx"}],
        "default_entries": None,
    }
    parsed = acl_adapter.validate_python(payload)
    assert isinstance(parsed, PosixACL)
    assert parsed.entries[0].qualifier == "alice"


def test_posix_default_entries_optional():
    payload = {
        "type": "posix",
        "entries": [{"tag": "user_obj", "qualifier": "", "perms": "rwx"}],
    }
    parsed = acl_adapter.validate_python(payload)
    assert parsed.default_entries is None


def test_nfsv4_acl():
    payload = {
        "type": "nfsv4",
        "entries": [
            {
                "principal": "alice@example.com",
                "ace_type": "allow",
                "flags": ["file_inherit"],
                "mask": ["read_data", "write_data"],
            }
        ],
    }
    parsed = acl_adapter.validate_python(payload)
    assert isinstance(parsed, NfsV4ACL)
    assert parsed.entries[0].ace_type == "allow"


def test_nt_acl_with_owner():
    payload = {
        "type": "nt",
        "owner": {"sid": "S-1-5-21-1-2-3-1013", "name": "DOMAIN\\alice"},
        "group": {"sid": "S-1-5-21-1-2-3-513", "name": "DOMAIN\\Domain Users"},
        "control": ["dacl_present"],
        "entries": [
            {
                "sid": "S-1-5-21-1-2-3-1013",
                "name": "DOMAIN\\alice",
                "ace_type": "allow",
                "flags": [],
                "mask": ["read_data"],
            }
        ],
    }
    parsed = acl_adapter.validate_python(payload)
    assert isinstance(parsed, NtACL)
    assert parsed.owner.name == "DOMAIN\\alice"


def test_s3_acl():
    payload = {
        "type": "s3",
        "owner": {"id": "abc", "display_name": "owner"},
        "grants": [
            {
                "grantee_type": "canonical_user",
                "grantee_id": "abc",
                "grantee_name": "owner",
                "permission": "FULL_CONTROL",
            }
        ],
    }
    parsed = acl_adapter.validate_python(payload)
    assert isinstance(parsed, S3ACL)


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        acl_adapter.validate_python({"type": "windows7", "entries": []})


def test_posix_perms_pattern_validated():
    with pytest.raises(ValidationError):
        acl_adapter.validate_python({
            "type": "posix",
            "entries": [{"tag": "user", "qualifier": "alice", "perms": "BOGUS"}],
        })
