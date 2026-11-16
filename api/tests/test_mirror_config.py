import uuid

import pytest

from akashic.models.source import Source
from akashic.services.mirror_links import (
    MirrorConfigError,
    normalize_mapping,
    prefix_with_sep,
    load_mappings,
    validate_mirrors,
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


async def _src(db, type_, name=None, cfg=None):
    s = Source(name=name or f"s-{uuid.uuid4()}", type=type_, connection_config=cfg or {})
    db.add(s)
    await db.flush()
    return s


async def _call(db, backing, *, app_id=None, app_type="immich", **kw):
    m = _m(kw.get("remote", ""), kw.get("local", "/x"), str(backing))
    return await validate_mirrors(db, app_id=app_id, app_type=app_type, raw=[m])


@pytest.mark.asyncio
async def test_validate_ok_and_order(db_session):
    a = await _src(db_session, "local")
    b = await _src(db_session, "nfs")
    out = await validate_mirrors(
        db_session,
        app_id=None,
        app_type="immich",
        raw=[_m("/d/", "/x/", str(a.id)), _m("", "/y", str(b.id))],
    )
    assert [o["source_id"] for o in out] == [str(a.id), str(b.id)]
    assert out[0]["remote_prefix"] == "/d"


@pytest.mark.asyncio
async def test_validate_rejects_non_backing_target(db_session):
    t = await _src(db_session, "immich")
    with pytest.raises(MirrorConfigError) as e:
        await _call(db_session, t.id)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_unknown_backing_404(db_session):
    with pytest.raises(MirrorConfigError) as e:
        await _call(db_session, uuid.uuid4())
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_validate_rejects_self(db_session):
    t = await _src(db_session, "immich")
    with pytest.raises(MirrorConfigError) as e:
        await _call(db_session, t.id, app_id=t.id)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_rejects_app_type_local(db_session):
    t = await _src(db_session, "local")
    with pytest.raises(MirrorConfigError) as e:
        await _call(db_session, t.id, app_type="local")
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_paperless_requires_empty_remote_prefix(db_session):
    t = await _src(db_session, "local")
    with pytest.raises(MirrorConfigError) as e:
        await _call(db_session, t.id, app_type="paperless", remote="/docs")
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_s3_backing_strips_leading_slash(db_session):
    t = await _src(db_session, "s3")
    out = await _call(db_session, t.id, local="/photos/")
    assert out[0]["local_prefix"] == "photos"


@pytest.mark.asyncio
async def test_validate_rejects_duplicate(db_session):
    t = await _src(db_session, "local")
    m = _m("", "/x", str(t.id))
    with pytest.raises(MirrorConfigError):
        await validate_mirrors(db_session, app_id=None, app_type="immich", raw=[m, dict(m)])


@pytest.mark.asyncio
async def test_validate_none_and_empty_list_return_empty(db_session):
    for raw in (None, []):
        assert await validate_mirrors(db_session, app_id=None, app_type="immich", raw=raw) == []


@pytest.mark.asyncio
async def test_validate_rejects_non_list(db_session):
    with pytest.raises(MirrorConfigError):
        await validate_mirrors(db_session, app_id=None, app_type="immich", raw={"a": 1})


def test_prefix_with_sep():
    assert prefix_with_sep("", "s3") == ""
    assert prefix_with_sep("", "local") == "/"
    assert prefix_with_sep("/m", "nfs") == "/m/"


@pytest.mark.asyncio
async def test_load_mappings_resolves_kind(db_session):
    b = await _src(db_session, "local")
    await _src(db_session, "paperless", cfg={"mirrors": [_m("", "/x", str(b.id))]})
    out = await load_mappings(db_session, b)
    assert len(out) == 1 and out[0].kind == "paperless"


@pytest.mark.asyncio
async def test_load_mappings_orders_by_app_name_then_index(db_session):
    b = await _src(db_session, "local")
    m0, m1, m2 = (_m("/d", f"/p{i}", str(b.id)) for i in range(3))
    a_app = await _src(db_session, "immich", name=f"a-app-{uuid.uuid4()}", cfg={"mirrors": [m0, m1]})
    b_app = await _src(db_session, "immich", name=f"b-app-{uuid.uuid4()}", cfg={"mirrors": [m2]})
    out = await load_mappings(db_session, b)
    assert [(o.source_id, o.local_prefix) for o in out] == [
        (a_app.id, "/p0"), (a_app.id, "/p1"), (b_app.id, "/p2"),
    ]


@pytest.mark.asyncio
async def test_load_mappings_ignores_other_backings(db_session):
    b = await _src(db_session, "local")
    other = await _src(db_session, "local")
    await _src(db_session, "immich", cfg={"mirrors": [_m("", "/x", str(other.id))]})
    assert await load_mappings(db_session, b) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{}, {"remote_prefix": 5, "local_prefix": ""}])
async def test_load_mappings_corrupt_raises(db_session, bad):
    b = await _src(db_session, "local")
    await _src(db_session, "immich", cfg={"mirrors": [{"source_id": str(b.id), **bad}]})
    with pytest.raises(MirrorConfigError) as e:
        await load_mappings(db_session, b)
    assert e.value.status_code == 409
