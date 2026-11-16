import uuid

import pytest

from akashic.models.source import Source
from akashic.services.mirror_links import (
    MirrorConfigError,
    normalize_mapping,
    prefix_with_sep,
    load_mappings,
    validate_mirror_of,
)


def _m(remote, local, sid="00000000-0000-0000-0000-000000000001"):
    return {"source_id": sid, "remote_prefix": remote, "local_prefix": local}


@pytest.mark.parametrize(
    "remote,local,btype,exp",
    [
        ("/data/", "/host/mnt/immich/data/", "local", ("/data", "/host/mnt/immich/data")),
        ("/", "/", "local", ("", "")),
        ("", "/photos/", "s3", ("", "photos")),
        ("//data//x/", " /a//b/ ", "smb", ("/data/x", "/a/b")),
    ],
)
def test_normalize_prefixes(remote, local, btype, exp):
    out = normalize_mapping(_m(remote, local), btype)
    assert (out["remote_prefix"], out["local_prefix"]) == exp


def test_normalize_rejects_relative_remote_prefix():
    with pytest.raises(MirrorConfigError) as e:
        normalize_mapping(_m("data", "/x"), "local")
    assert e.value.status_code == 400


def test_normalize_rejects_unknown_key():
    raw = _m("", "/x")
    raw["bogus"] = 1
    with pytest.raises(MirrorConfigError) as e:
        normalize_mapping(raw, "local")
    assert e.value.status_code == 400


def test_normalize_rejects_relative_local_prefix_non_s3():
    with pytest.raises(MirrorConfigError):
        normalize_mapping(_m("", "x"), "local")


async def _src(db, type_):
    s = Source(name=f"s-{uuid.uuid4()}", type=type_, connection_config={})
    db.add(s)
    await db.flush()
    return s


async def _call(db, target, *, backing_id=None, backing_type="local", **kw):
    m = _m(kw.get("remote", ""), kw.get("local", "/x"), str(target))
    return await validate_mirror_of(
        db, backing_id=backing_id, backing_type=backing_type, raw=[m]
    )


@pytest.mark.asyncio
async def test_validate_ok_and_order(db_session):
    a = await _src(db_session, "immich")
    b = await _src(db_session, "paperless")
    out = await validate_mirror_of(
        db_session,
        backing_id=None,
        backing_type="local",
        raw=[_m("/d/", "/x/", str(a.id)), _m("", "/y", str(b.id))],
    )
    assert [o["source_id"] for o in out] == [str(a.id), str(b.id)]
    assert out[0]["remote_prefix"] == "/d"


@pytest.mark.asyncio
async def test_validate_rejects_non_app_target(db_session):
    t = await _src(db_session, "local")
    with pytest.raises(MirrorConfigError) as e:
        await _call(db_session, t.id)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_unknown_target_404(db_session):
    with pytest.raises(MirrorConfigError) as e:
        await _call(db_session, uuid.uuid4())
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_validate_rejects_self(db_session):
    t = await _src(db_session, "immich")
    with pytest.raises(MirrorConfigError) as e:
        await _call(db_session, t.id, backing_id=t.id)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_rejects_backing_type_webdav(db_session):
    t = await _src(db_session, "immich")
    with pytest.raises(MirrorConfigError) as e:
        await _call(db_session, t.id, backing_type="webdav")
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_paperless_requires_empty_remote_prefix(db_session):
    t = await _src(db_session, "paperless")
    with pytest.raises(MirrorConfigError) as e:
        await _call(db_session, t.id, remote="/docs")
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_rejects_duplicate(db_session):
    t = await _src(db_session, "immich")
    m = _m("", "/x", str(t.id))
    with pytest.raises(MirrorConfigError):
        await validate_mirror_of(db_session, backing_id=None, backing_type="local", raw=[m, dict(m)])


@pytest.mark.asyncio
async def test_validate_none_and_empty_list_return_empty(db_session):
    for raw in (None, []):
        assert await validate_mirror_of(db_session, backing_id=None, backing_type="local", raw=raw) == []


@pytest.mark.asyncio
async def test_validate_rejects_non_list(db_session):
    with pytest.raises(MirrorConfigError):
        await validate_mirror_of(db_session, backing_id=None, backing_type="local", raw={"a": 1})


def test_prefix_with_sep():
    assert prefix_with_sep("", "s3") == ""
    assert prefix_with_sep("", "local") == "/"
    assert prefix_with_sep("/m", "nfs") == "/m/"


@pytest.mark.asyncio
async def test_load_mappings_resolves_kind(db_session):
    t = await _src(db_session, "paperless")
    b = await _src(db_session, "local")
    b.connection_config = {"mirror_of": [_m("", "/x", str(t.id))]}
    out = await load_mappings(db_session, b)
    assert len(out) == 1 and out[0].kind == "paperless" and out[0].source_id == t.id


@pytest.mark.asyncio
async def test_load_mappings_missing_target_raises(db_session):
    b = await _src(db_session, "local")
    b.connection_config = {"mirror_of": [_m("", "/x", str(uuid.uuid4()))]}
    with pytest.raises(MirrorConfigError) as e:
        await load_mappings(db_session, b)
    assert e.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{}, {"source_id": "nope", "remote_prefix": "", "local_prefix": ""}])
async def test_load_mappings_corrupt_raises(db_session, bad):
    b = await _src(db_session, "local")
    b.connection_config = {"mirror_of": [bad]}
    with pytest.raises(MirrorConfigError) as e:
        await load_mappings(db_session, b)
    assert e.value.status_code == 409
