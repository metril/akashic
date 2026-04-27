from akashic.services.ingest import acl_equal


def test_acl_equal_handles_key_reorder():
    a = {"type": "posix", "entries": [{"tag": "user", "qualifier": "x", "perms": "rwx"}]}
    b = {"entries": [{"perms": "rwx", "qualifier": "x", "tag": "user"}], "type": "posix"}
    assert acl_equal(a, b) is True


def test_acl_equal_detects_change():
    a = {"type": "posix", "entries": [{"tag": "user", "qualifier": "x", "perms": "rwx"}]}
    b = {"type": "posix", "entries": [{"tag": "user", "qualifier": "x", "perms": "r-x"}]}
    assert acl_equal(a, b) is False


def test_acl_equal_both_none():
    assert acl_equal(None, None) is True


def test_acl_equal_one_none():
    a = {"type": "posix", "entries": []}
    assert acl_equal(a, None) is False
    assert acl_equal(None, a) is False
