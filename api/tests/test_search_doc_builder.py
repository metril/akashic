import uuid
from datetime import datetime, timezone

from akashic.services.search import build_entry_doc


class _FakeEntry:
    """Minimal duck for build_entry_doc — avoids needing a DB session."""
    def __init__(self, viewable_read=None, viewable_write=None, viewable_delete=None):
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
        # Pre-computed columns: when populated build_entry_doc reads from
        # them; when None it falls back to recomputing. Tests cover both.
        self.viewable_by_read = viewable_read
        self.viewable_by_write = viewable_write
        self.viewable_by_delete = viewable_delete


def test_build_entry_doc_recomputes_when_columns_null():
    # Legacy / unbackfilled rows have NULL columns — recompute path.
    doc = build_entry_doc(_FakeEntry())
    assert "viewable_by_read" in doc
    assert "viewable_by_write" in doc
    assert "viewable_by_delete" in doc
    assert "posix:uid:1000" in doc["viewable_by_read"]
    assert "*" in doc["viewable_by_read"]


def test_build_entry_doc_uses_pre_computed_columns():
    # Phase-4 ingest populates the columns at write time. The Meili doc
    # must use those values verbatim (single source of truth).
    pre_read = ["posix:uid:42", "sid:S-1-5-21-CUSTOM"]
    pre_write = ["posix:uid:42"]
    pre_delete = []
    doc = build_entry_doc(_FakeEntry(
        viewable_read=pre_read,
        viewable_write=pre_write,
        viewable_delete=pre_delete,
    ))
    assert doc["viewable_by_read"] == pre_read
    assert doc["viewable_by_write"] == pre_write
    assert doc["viewable_by_delete"] == pre_delete


def test_build_entry_doc_with_content_text():
    doc = build_entry_doc(_FakeEntry(), content_text="hello world")
    assert doc["content_text"] == "hello world"
