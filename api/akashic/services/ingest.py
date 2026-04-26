"""Pure helpers for the ingest pipeline — kept testable and away from FastAPI."""
from akashic.models.entry import Entry
from akashic.schemas.entry import EntryIn


# Fields whose change should trigger a new EntryVersion row.
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


def _normalize_acl(value):
    """ACL on incoming EntryIn is a list[ACLEntry]; on Entry it's a list[dict]
    (loaded from JSONB). Compare in the same shape."""
    if value is None:
        return None
    if not value:
        return []
    out = []
    for item in value:
        if hasattr(item, "model_dump"):
            out.append(item.model_dump())
        else:
            out.append(dict(item))
    return out


def entry_state_changed(existing: Entry, incoming: EntryIn) -> bool:
    """True if any versioned field differs between the stored entry and the incoming one."""
    for field in VERSIONED_FIELDS:
        existing_val = getattr(existing, field)
        incoming_val = getattr(incoming, field)
        if field == "acl":
            if _normalize_acl(existing_val) != _normalize_acl(incoming_val):
                return True
        elif existing_val != incoming_val:
            return True
    return False


def serialize_acl(acl):
    """Convert incoming ACL (list[ACLEntry]) to JSONB-storable list[dict]."""
    return _normalize_acl(acl)
