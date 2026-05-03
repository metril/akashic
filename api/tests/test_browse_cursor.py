"""Cursor encode/decode round-trip + invalid-cursor handling for the
v0.4.11 Phase 5 browse pagination.

Pure-function tests against `_encode_cursor` / `_decode_cursor` in
api/akashic/routers/browse.py. Full-stack pagination behaviour
(seekable predicates, page boundaries, server-side `q=` filter) is
covered by the existing browse integration tests + manual smoke;
those need a postgres fixture.
"""
import pytest
from fastapi import HTTPException

from akashic.routers.browse import _decode_cursor, _encode_cursor


def test_cursor_round_trips():
    payload = {
        "kf": 1,
        "s": "Final.mkv",
        "id": "00000000-0000-0000-0000-000000000001",
        "sort": "name",
        "order": "asc",
    }
    enc = _encode_cursor(payload)
    dec = _decode_cursor(enc)
    assert dec == payload


def test_cursor_handles_special_chars():
    # Names with quotes / backslashes / unicode shouldn't break the
    # base64+JSON pipeline.
    payload = {
        "kf": 0,
        "s": 'tricky "name" with \\ and 漢字',
        "id": "deadbeef-dead-beef-dead-beefdeadbeef",
        "sort": "name",
        "order": "desc",
    }
    enc = _encode_cursor(payload)
    dec = _decode_cursor(enc)
    assert dec["s"] == payload["s"]


def test_cursor_handles_null_sort_value():
    # fs_modified_at can be null — encoded as JSON null and round-trips.
    payload = {
        "kf": 1,
        "s": None,
        "id": "00000000-0000-0000-0000-000000000002",
        "sort": "modified",
        "order": "desc",
    }
    enc = _encode_cursor(payload)
    dec = _decode_cursor(enc)
    assert dec["s"] is None


def test_decode_garbage_raises_400():
    with pytest.raises(HTTPException) as exc:
        _decode_cursor("not-a-valid-cursor!!!")
    assert exc.value.status_code == 400


def test_decode_truncated_b64_raises_400():
    # Truncate a valid cursor — the json layer should fail.
    valid = _encode_cursor({"kf": 0, "s": "x", "id": "u", "sort": "name", "order": "asc"})
    with pytest.raises(HTTPException) as exc:
        _decode_cursor(valid[:5])
    assert exc.value.status_code == 400
