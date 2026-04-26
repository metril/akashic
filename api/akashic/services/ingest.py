"""Pure helpers for the ingest pipeline — kept testable and away from FastAPI."""
import json

from akashic.models.entry import Entry
from akashic.schemas.entry import EntryIn


VERSIONED_FIELDS = (
    "content_hash",
    "size_bytes",
    "mode",
    "uid",
    "gid",
    "owner_name",
    "group_name",
    "acl",
    "xattrs",
)


def acl_equal(a: dict | None, b: dict | None) -> bool:
    """Stable comparison for ACL JSONB values — survives key-order differences."""
    if a is None or b is None:
        return a is b
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _to_dict(value):
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return dict(value)


def entry_state_changed(existing: Entry, incoming: EntryIn) -> bool:
    for field in VERSIONED_FIELDS:
        existing_val = getattr(existing, field)
        incoming_val = getattr(incoming, field)
        if field == "acl":
            if not acl_equal(_to_dict(existing_val), _to_dict(incoming_val)):
                return True
        elif existing_val != incoming_val:
            return True
    return False


def serialize_acl(acl):
    """Convert incoming ACL (Pydantic model or dict) to JSONB-storable dict."""
    return _to_dict(acl)
