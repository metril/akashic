"""Pure helpers for the ingest pipeline — kept testable and away from FastAPI."""
import json

from pydantic import TypeAdapter

from akashic.models.entry import Entry
from akashic.schemas.acl import ACL
from akashic.schemas.entry import EntryIn
from akashic.services.acl_denorm import denormalize_acl


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


_ACL_ADAPTER: TypeAdapter[ACL] | None = None


def _coerce_acl(acl) -> ACL | None:
    """Accept whatever shape the caller has (Pydantic model, JSONB dict/list,
    None) and return a discriminator-validated ACL, or None when the value
    can't be parsed (e.g. legacy rows with malformed JSONB)."""
    if acl is None:
        return None
    if hasattr(acl, "model_dump"):
        return acl  # already a typed ACL
    global _ACL_ADAPTER
    if _ACL_ADAPTER is None:
        _ACL_ADAPTER = TypeAdapter(ACL)
    try:
        return _ACL_ADAPTER.validate_python(acl)
    except Exception:
        return None


def compute_viewable_buckets(
    acl,
    mode: int | None,
    uid: int | None,
    gid: int | None,
) -> dict[str, list[str]]:
    """Compute the denormalized ACL projection (read/write/delete) for an
    entry, accepting any of the shapes the caller might have on hand.

    This is the single funnel that feeds both sinks:
    - `services/search.build_entry_doc` (Meili `viewable_by_*` fields)
    - `routers/ingest` (entries.viewable_by_* SQL columns)

    The two sinks must always be in sync — callers should never recompute
    `denormalize_acl` directly on Entry-shaped data when they could call
    this instead.
    """
    return denormalize_acl(
        acl=_coerce_acl(acl),
        base_mode=mode,
        base_uid=uid,
        base_gid=gid,
    )
