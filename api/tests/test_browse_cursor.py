"""Browse cursor: round trip through the shared helpers, and every malformed
shape answering 422 (never 500)."""
import uuid

import pytest
from fastapi import HTTPException

from akashic.routers.browse import BrowseCursor
from akashic.schemas.common import encode_cursor, parse_cursor

_ID = "00000000-0000-0000-0000-000000000001"


def _ok(**over):
    p = {"kf": 1, "s": "Final.mkv", "id": _ID, "sort": "name", "order": "asc"}
    p.update(over)
    return p


def test_cursor_round_trips():
    c = parse_cursor(encode_cursor(_ok()), BrowseCursor)
    assert c.s == "Final.mkv" and c.id == uuid.UUID(_ID) and c.kf == 1


def test_cursor_handles_special_chars():
    s = 'tricky "name" with \\ and 漢字'
    assert parse_cursor(encode_cursor(_ok(s=s, kf=0, order="desc")), BrowseCursor).s == s


def test_cursor_handles_null_sort_value():
    assert parse_cursor(encode_cursor(_ok(s=None, sort="modified")), BrowseCursor).s is None


def test_size_and_modified_cursors_accept_their_types():
    assert parse_cursor(encode_cursor(_ok(s=123, sort="size")), BrowseCursor).s == 123
    assert parse_cursor(encode_cursor(_ok(s="2024-01-02T03:04:05+00:00", sort="modified")), BrowseCursor)


@pytest.mark.parametrize("cursor", [
    "not-a-valid-cursor!!!",
    encode_cursor(_ok())[:5],  # truncated
    encode_cursor([1, 2]),  # JSON list, not an object
    encode_cursor({"kf": 1}),  # missing keys
    encode_cursor(_ok(kf=7)),
    encode_cursor(_ok(id="not-a-uuid")),
    encode_cursor(_ok(sort="bogus")),
    encode_cursor(_ok(s="abc", sort="size")),  # non-numeric size
    encode_cursor(_ok(s=5, sort="name")),
    encode_cursor(_ok(s="not a date", sort="modified")),
    encode_cursor(_ok(extra=1)),
])
def test_malformed_cursor_is_422(cursor):
    with pytest.raises(HTTPException) as exc:
        parse_cursor(cursor, BrowseCursor)
    assert exc.value.status_code == 422
