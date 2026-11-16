"""GET /access?identity= : union lookup over a merged identity's tokens, with a deny-ACE flag."""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from akashic.auth.dependencies import get_principal
from akashic.models.audit_event import AuditEvent
from akashic.models.directory import DirectoryEntry
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.routers.access import DENY_WARNING
from tests.principals import as_principal

S1, G1 = "S-1-5-21-1-2-3-1001", "S-1-5-21-1-2-3-2001"


async def _login(client):
    await client.post("/api/v1/users/register", json={"username": "admin", "password": "testpass123"})
    r = await client.post("/api/v1/users/login", json={"username": "admin", "password": "testpass123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _source(db, name):
    s = Source(name=name, type="local", connection_config={"path": "/tmp"}, status="online")
    db.add(s)
    await db.flush()
    return s


def _person(**kw):
    kw.setdefault("groups_status", "ok")
    kw.setdefault("groups_synced_at", datetime.now(timezone.utc))
    return DirectoryEntry(
        backend="ad", ref=uuid.uuid4().hex, kind="person", display_name="Alice", sid=S1,
        group_sids=[G1], synced_at=datetime.now(timezone.utc), **kw)


def _file(src, tokens, size, **kw):
    n = uuid.uuid4().hex[:8]
    return Entry(source_id=src.id, kind="file", parent_path="/", path=f"/{n}", name=n,
                 size_bytes=size, viewable_by_read=tokens, **kw)


def _meili(hits=()):
    resp = AsyncMock()
    resp.hits = list(hits)
    index = AsyncMock()
    index.search = AsyncMock(return_value=resp)
    client = AsyncMock()
    client.get_index = AsyncMock(return_value=index)
    return index, patch("akashic.routers.access.get_meili_client", AsyncMock(return_value=client))


async def _get(client, headers, query, hits=()):
    index, p = _meili(hits)
    with p:
        r = await client.get(f"/api/v1/access?{query}", headers=headers)
    return r, index


@pytest.mark.asyncio
async def test_identity_union_totals(client, db_session):
    h = await _login(client)
    src = await _source(db_session, "s")
    db_session.add(_person())
    db_session.add_all([
        _file(src, [f"sid:{S1}"], 100), _file(src, [f"sid:{G1}"], 200),
        _file(src, [f"sid:{S1}", f"sid:{G1}"], 50), _file(src, ["sid:other"], 999),
    ])
    await db_session.commit()
    r, index = await _get(client, h, f"identity=sid:{S1}")
    assert r.status_code == 200
    s = r.json()["summary"]
    assert (s["file_count"], s["total_size_bytes"]) == (3, 350)
    flt = index.search.call_args.kwargs["filter"]
    assert "viewable_by_read IN [" in flt and f'"sid:{S1}"' in flt and f'"sid:{G1}"' in flt


@pytest.mark.asyncio
async def test_identity_response_fields(client, db_session):
    h = await _login(client)
    db_session.add(_person())
    await db_session.commit()
    r, _ = await _get(client, h, f"identity=sid:{S1}")
    b = r.json()
    assert b["identity"]["id"] == f"sid:{S1}"
    assert b["principal"] == {"token": f"sid:{S1}", "name": "Alice", "domain": None, "kind": "user"}
    origins = {t["token"]: t["origin"] for t in b["tokens_used"]}
    assert origins == {f"sid:{S1}": "ad", f"sid:{G1}": "ad", "*": "builtin", "auth": "builtin"}
    assert b["expansion"]["status"] == "ok"
    assert b["expansion"]["missing_identity_kinds"] == ["posix"]
    assert b["warnings"] == [DENY_WARNING]


@pytest.mark.asyncio
async def test_identity_without_tokens_returns_empty_and_skips_search(client, db_session):
    h = await _login(client)
    db_session.add(DirectoryEntry(
        backend="authentik", ref="pk1", kind="group", display_name="Nobody",
        groups_status="none", synced_at=datetime.now(timezone.utc)))
    await db_session.commit()
    r, index = await _get(client, h, "identity=authentik:pk1")
    b = r.json()
    assert r.status_code == 200 and b["expansion"]["status"] == "failed"
    assert b["summary"]["file_count"] == 0 and b["sample"] == [] and b["principal"]["kind"] == "group"
    index.search.assert_not_called()


@pytest.mark.asyncio
async def test_sample_flags_deny_ace(client, db_session):
    h = await _login(client)
    src = await _source(db_session, "s")

    def nt(ace_type):
        return {"type": "nt", "control": [], "owner": None, "group": None, "entries": [
            {"sid": S1, "name": "a", "ace_type": "allow", "flags": [], "mask": ["READ_DATA"]},
            {"sid": G1, "name": "g", "ace_type": ace_type, "flags": [], "mask": ["READ_DATA"]}]}
    denied = _file(src, [f"sid:{S1}"], 1, acl=nt("deny"))
    allowed = _file(src, [f"sid:{S1}"], 1, acl=nt("allow"))
    no_acl = _file(src, [f"sid:{S1}"], 1)
    db_session.add_all([denied, allowed, no_acl])
    await db_session.commit()
    hits = [{"id": str(e.id), "source_id": str(src.id), "path": e.path, "filename": e.name}
            for e in (denied, allowed, no_acl)]
    r, _ = await _get(client, h, "principal=sid:" + S1, hits)
    flags = [x["has_deny_ace"] for x in r.json()["sample"]]
    assert flags == [True, False, False]


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [
    f"identity=sid:{S1}&principal=sid:x",
    f"identity=sid:{S1}&file=11111111-2222-3333-4444-555555555555",
])
async def test_identity_param_exclusivity(client, query):
    h = await _login(client)
    r, _ = await _get(client, h, query)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_identity_unknown_404(client):
    h = await _login(client)
    r, _ = await _get(client, h, "identity=sid:S-1-nope")
    assert r.status_code == 404 and r.json()["message"] == "Identity not found"


@pytest.mark.asyncio
async def test_identity_respects_source_limited_token(client, db_session):
    h = await _login(client)
    a, b = await _source(db_session, "a"), await _source(db_session, "b")
    db_session.add(_person())
    db_session.add_all([_file(a, [f"sid:{S1}"], 10), _file(b, [f"sid:{G1}"], 20)])
    await db_session.commit()
    user = (await db_session.execute(select(User))).scalars().first()
    client._transport.app.dependency_overrides[get_principal] = as_principal(
        user, allowed_source_ids={a.id})
    r, index = await _get(client, h, f"identity=sid:{S1}")
    s = r.json()["summary"]
    assert (s["file_count"], s["total_size_bytes"]) == (1, 10)
    assert f'(source_id = "{a.id}")' in index.search.call_args.kwargs["filter"]


@pytest.mark.asyncio
async def test_identity_lookup_audited(client, db_session):
    h = await _login(client)
    db_session.add(_person())
    await db_session.commit()
    r, _ = await _get(client, h, f"identity=sid:{S1}")
    assert r.status_code == 200
    ev = (await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "access_lookup"))).scalars().one()
    assert ev.payload["requested_identity"] == f"sid:{S1}" and ev.payload["identity"] == f"sid:{S1}"
    assert ev.payload["token_count"] == 4
    assert ev.payload["identity_name"] == "Alice" and ev.payload["expansion"] == "ok"


@pytest.mark.asyncio
async def test_stale_hit_deny_flag_is_unknown(client, db_session):
    h = await _login(client)
    src = await _source(db_session, "s")
    live = _file(src, [f"sid:{S1}"], 1)
    db_session.add(live)
    await db_session.commit()
    hits = [{"id": str(live.id), "source_id": str(src.id), "path": "/a", "filename": "a"},
            {"id": str(uuid.uuid4()), "source_id": str(src.id), "path": "/b", "filename": "b"}]
    r, _ = await _get(client, h, "principal=sid:" + S1, hits)
    assert [x["has_deny_ace"] for x in r.json()["sample"]] == [False, None]
