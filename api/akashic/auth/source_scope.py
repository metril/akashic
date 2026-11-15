"""Per-source limits for a Principal (an API token's optional ``source_ids``).

``principal.allowed_source_ids is None`` means every source. Anything outside
a limited principal's list is reported as *not found* (404), never 403, so
the caller cannot confirm that a source or an entry exists.

Routes that return source-scoped data declare ``require_scope(..., source_scoped=True)``
and must apply one of these helpers. A limited principal reaching a route that
does not declare it is refused outright (fail closed).
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import false
from sqlalchemy.sql import Select

from akashic.auth.dependencies import Principal
from akashic.models.entry import Entry

_NO_SOURCE = "00000000-0000-0000-0000-000000000000"


def restrict_sources(stmt: Select, principal: Principal, column: Any = Entry.source_id) -> Select:
    """Narrow ``stmt`` to the principal's sources. A no-op when unlimited.

    Rows whose ``column`` is NULL (orphans) are excluded for a limited principal."""
    allowed = principal.allowed_source_ids
    if allowed is None:
        return stmt
    return stmt.where(column.in_(allowed) if allowed else false())


def meili_source_filter(principal: Principal) -> str | None:
    """A Meilisearch filter clause restricting to the principal's sources, or None."""
    allowed = principal.allowed_source_ids
    if allowed is None:
        return None
    ids = sorted(str(s) for s in allowed) or [_NO_SOURCE]
    return "(" + " OR ".join(f'source_id = "{s}"' for s in ids) + ")"


def ensure_source_allowed(
    principal: Principal, source_id: uuid.UUID | None, detail: str = "Source not found",
) -> None:
    """404 unless ``source_id`` is inside the principal's limit."""
    if not principal.allows_source(source_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
