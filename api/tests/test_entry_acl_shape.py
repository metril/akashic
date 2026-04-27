import pytest
from pydantic import ValidationError

from akashic.schemas.entry import EntryIn


def test_entry_in_accepts_wrapped_posix_acl():
    payload = {
        "path": "/tmp/x",
        "name": "x",
        "kind": "file",
        "acl": {
            "type": "posix",
            "entries": [{"tag": "user", "qualifier": "alice", "perms": "rwx"}],
        },
    }
    e = EntryIn.model_validate(payload)
    assert e.acl.type == "posix"


def test_entry_in_rejects_flat_acl_list():
    payload = {
        "path": "/tmp/x",
        "name": "x",
        "kind": "file",
        "acl": [{"tag": "user", "qualifier": "alice", "perms": "rwx"}],
    }
    with pytest.raises(ValidationError):
        EntryIn.model_validate(payload)


def test_entry_in_acl_optional():
    payload = {"path": "/tmp/x", "name": "x", "kind": "file"}
    e = EntryIn.model_validate(payload)
    assert e.acl is None
