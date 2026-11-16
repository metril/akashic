"""Default-search hiding of linked backing files.

A backing file linked to an Immich/Paperless entry is redundant next to that
entry, so default search hides it, but only from viewers who can also see
the linked entry: by ACL tokens (`link_target_viewable`) and by token source
scope (`link_target_source_id`). Everyone else still gets the backing file.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Text, and_, cast, not_, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.sql.elements import ColumnElement

from akashic.models.entry import Entry


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def hide_linked_meili(
    tokens: list[str] | None, allowed_source_ids: "list[uuid.UUID] | None",
) -> str:
    """Meili filter excluding linked files whose target the caller can see.

    ``tokens=None``: caller is not ACL-filtered. ``allowed_source_ids=None``:
    caller is not source-limited. An empty list means the caller can see no
    target, so nothing is hidden and the empty string is returned (callers
    skip it).

    Meili ``link_role`` is non-null exactly when the entry is linked:
    ``build_entry_doc`` emits it only when ``linked_entry_id`` is set.
    """
    if tokens == [] or allowed_source_ids == []:
        return ""
    parts = ["link_role IS NOT NULL"]
    if allowed_source_ids is not None:
        ids = ",".join(_q(str(s)) for s in sorted(str(s) for s in allowed_source_ids))
        parts.append(f"link_target_source_id IN [{ids}]")
    if tokens is not None:
        parts.append(f"link_target_viewable IN [{','.join(_q(t) for t in tokens)}]")
    return f"NOT ({' AND '.join(parts)})"


def hide_linked_sql(
    tokens: list[str] | None, allowed_source_ids: "list[uuid.UUID] | None",
) -> ColumnElement[bool]:
    """SQL twin of :func:`hide_linked_meili` (empty list: nothing hidden)."""
    if tokens == [] or allowed_source_ids == []:
        return true()
    conds: list[ColumnElement[bool]] = [Entry.linked_entry_id.is_not(None)]
    if allowed_source_ids is not None:
        conds.append(Entry.link_target_source_id.in_(list(allowed_source_ids)))
    if tokens is not None:
        conds.append(
            Entry.link_target_viewable.op("&&")(cast(list(tokens), ARRAY(Text)))
        )
    return not_(and_(*conds))
