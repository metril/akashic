import pytest
from pydantic import BaseModel

from akashic.schemas.common import (
    Page, decode_cursor, decode_offset_cursor, encode_cursor, encode_offset_cursor,
)


@pytest.fixture(autouse=True)
def setup_db():  # pure unit tests: no DB
    yield


class Item(BaseModel):
    id: int


def test_page_roundtrip_and_schema():
    page = Page[Item](items=[Item(id=1)], next_cursor=None, limit=50)
    assert page.model_dump() == {"items": [{"id": 1}], "next_cursor": None, "limit": 50}
    props = Page[Item].model_json_schema()["properties"]
    assert all(p.get("description") for p in props.values())


def test_cursor_roundtrip_is_opaque_urlsafe():
    payload = {"k": "a/b c", "n": 7}
    cur = encode_cursor(payload)
    assert "=" not in cur and "/" not in cur and "+" not in cur
    assert decode_cursor(cur) == payload


def test_offset_cursor():
    assert decode_offset_cursor(None) == 0
    assert decode_offset_cursor("") == 0
    assert decode_offset_cursor(encode_offset_cursor(250)) == 250


@pytest.mark.parametrize("bad", ["!!!", "bm90LWpzb24", encode_cursor({"x": 1}), encode_offset_cursor(-1)])
def test_bad_offset_cursor_rejected(bad):
    with pytest.raises(ValueError):
        decode_offset_cursor(bad)


@pytest.mark.parametrize("bad", ["!!!", "bm90LWpzb24", "WzFd"])
def test_bad_cursor_rejected(bad):
    with pytest.raises(ValueError):
        decode_cursor(bad)
