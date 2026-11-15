"""Shared API schemas: the error envelope and the pagination envelope."""
from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Generic, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorResponse(BaseModel):
    """Body of every non-2xx response."""

    code: str = Field(description="Stable machine-readable error code, e.g. `not_found` or `validation_error`.")
    message: str = Field(description="Human-readable summary of what went wrong.")
    detail: Any | None = Field(
        default=None,
        description=(
            "Optional structured extra. For `validation_error` this is the list of "
            "field errors (`loc`, `msg`, `type`); other errors may carry an object."
        ),
    )
    request_id: str = Field(description="Correlation id, also returned in the `X-Request-ID` header.")


# Shared `responses=` set applied to every router in create_app().
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, invalid or expired credentials."},
    403: {"model": ErrorResponse, "description": "Authenticated but not permitted."},
    404: {"model": ErrorResponse, "description": "Resource not found."},
    409: {"model": ErrorResponse, "description": "Conflicts with the current resource state."},
    422: {"model": ErrorResponse, "description": "Request failed validation."},
}


class Page(BaseModel, Generic[T]):
    """Cursor-paginated list envelope."""

    items: list[T] = Field(description="The page of results.")
    next_cursor: str | None = Field(
        default=None,
        description="Opaque cursor for the next page; null when this is the last page.",
    )
    limit: int = Field(description="Maximum number of items requested for this page.")


def encode_cursor(payload: dict[str, Any]) -> str:
    """Pack a JSON-serialisable position into an opaque URL-safe cursor."""
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Inverse of :func:`encode_cursor`. Raises ValueError on any malformed cursor."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise ValueError("invalid cursor") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid cursor")
    return payload


def encode_offset_cursor(offset: int) -> str:
    """Cursor for routes that page by offset internally."""
    return encode_cursor({"o": offset})


def decode_offset_cursor(cursor: str | None) -> int:
    """Offset encoded by :func:`encode_offset_cursor`; 0 for no cursor. ValueError if malformed."""
    if not cursor:
        return 0
    offset = decode_cursor(cursor).get("o")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("invalid cursor")
    return offset


def offset_from_cursor(cursor: str | None) -> int:
    """Offset for an offset-paged route; HTTP 422 on a malformed cursor."""
    try:
        return decode_offset_cursor(cursor)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid cursor") from None


def offset_page(rows: list[Any], offset: int, limit: int) -> dict[str, Any]:
    """Build a :class:`Page` body from ``limit + 1`` fetched rows starting at ``offset``."""
    more = len(rows) > limit
    return {
        "items": rows[:limit],
        "next_cursor": encode_offset_cursor(offset + limit) if more else None,
        "limit": limit,
    }
