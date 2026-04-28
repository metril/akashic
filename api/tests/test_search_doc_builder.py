import uuid
from datetime import datetime, timezone

from akashic.services.search import build_entry_doc


class _FakeEntry:
    """Minimal duck for build_entry_doc — avoids needing a DB session."""
    def __init__(self):
        self.id = uuid.uuid4()
        self.source_id = uuid.uuid4()
        self.path = "/tmp/x"
        self.name = "x"
        self.extension = None
        self.mime_type = "text/plain"
        self.size_bytes = 12
        self.owner_name = "alice"
        self.group_name = "wheel"
        self.fs_modified_at = datetime.now(timezone.utc)
        self.acl = {
            "type": "posix",
            "entries": [
                {"tag": "user_obj",  "qualifier": "",     "perms": "rwx"},
                {"tag": "group_obj", "qualifier": "",     "perms": "r--"},
                {"tag": "other",     "qualifier": "",     "perms": "r--"},
            ],
            "default_entries": None,
        }
        self.mode = 0o644
        self.uid = 1000
        self.gid = 100


def test_build_entry_doc_includes_viewable_by_arrays():
    doc = build_entry_doc(_FakeEntry())
    assert "viewable_by_read" in doc
    assert "viewable_by_write" in doc
    assert "viewable_by_delete" in doc
    assert "posix:uid:1000" in doc["viewable_by_read"]
    assert "*" in doc["viewable_by_read"]


def test_build_entry_doc_with_content_text():
    doc = build_entry_doc(_FakeEntry(), content_text="hello world")
    assert doc["content_text"] == "hello world"
