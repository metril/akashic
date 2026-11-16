"""Personal API tokens: lifecycle, scope intersection, per-source limits."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from akashic.auth.api_tokens import USAGE_THROTTLE
from akashic.auth.jwt import create_access_token, create_ingest_token
from akashic.models.api_token import ApiToken
from akashic.models.audit_event import AuditEvent
from akashic.models.entry import Entry
from akashic.models.scan import Scan
from akashic.models.source import Source
from akashic.models.user import User
from tests.test_openapi_contract import DATA_TAGS, _routes, _source_scoped

NAME_B = "SRC-B-MARK"


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _jwt(user: User) -> dict:
    return _h(create_access_token({"sub": str(user.id)}))


def _entry(source_id, name, content_hash=None, kind="file") -> Entry:
    return Entry(
        id=uuid.uuid4(), source_id=source_id, kind=kind, path=f"/d/{name}", parent_path="/d",
        name=name, extension="txt", size_bytes=10, content_hash=content_hash, mode=0o644, uid=0, gid=0,
    )


@pytest_asyncio.fixture
async def world(setup_db):
    """An admin, a viewer, two sources (A, B) each with one file and one scan."""
    async with setup_db() as s:
        admin = User(id=uuid.uuid4(), username="adm", email=None, password_hash="x", role="admin")
        viewer = User(id=uuid.uuid4(), username="vwr", email=None, password_hash="x", role="viewer")
        a = Source(id=uuid.uuid4(), name="SRC-A", type="local", connection_config={"path": "/tmp"})
        b = Source(id=uuid.uuid4(), name=NAME_B, type="local", connection_config={"path": "/tmp"})
        s.add_all([admin, viewer, a, b])
        await s.flush()
        ea, eb = _entry(a.id, "needle-a", "h1"), _entry(b.id, "needle-b", "h1")
        sa = Scan(id=uuid.uuid4(), source_id=a.id, scan_type="full", status="completed")
        sb = Scan(id=uuid.uuid4(), source_id=b.id, scan_type="full", status="completed")
        s.add_all([ea, eb, sa, sb])
        await s.commit()
    return {
        "admin": admin, "viewer": viewer, "a": a.id, "b": b.id,
        "ea": ea.id, "eb": eb.id, "sa": sa.id, "sb": sb.id, "hash": "h1",
    }


async def _mint(client, user, scopes, **extra) -> str:
    r = await client.post("/api/v1/me/tokens", json={"name": "t", "scopes": scopes, **extra}, headers=_jwt(user))
    assert r.status_code == 201, r.text
    return r.json()["token"]


# ---------------------------------------------------------------- lifecycle


@pytest.mark.asyncio
async def test_create_returns_plaintext_once_and_stores_only_hash(client, world, db_session):
    r = await client.post(
        "/api/v1/me/tokens", json={"name": "ci", "scopes": ["files:read"]}, headers=_jwt(world["viewer"]),
    )
    assert r.status_code == 201
    body = r.json()
    tok = body["token"]
    assert tok.startswith("akp_") and len(tok) == 4 + 43 and tok.isalnum() is False or tok[4:].isalnum()
    assert body["token_prefix"] == tok[:12]
    row = (await db_session.execute(select(ApiToken))).scalar_one()
    assert row.token_hash == hashlib.sha256(tok.encode()).hexdigest() and row.token_hash != tok
    listed = (await client.get("/api/v1/me/tokens", headers=_jwt(world["viewer"]))).json()
    assert len(listed) == 1 and "token" not in listed[0]


@pytest.mark.asyncio
async def test_files_read_token_searches_but_cannot_create_sources(client, world):
    tok = await _mint(client, world["admin"], ["files:read"])
    assert (await client.get("/api/v1/search?q=needle", headers=_h(tok))).status_code == 200
    r = await client.post(
        "/api/v1/sources", headers=_h(tok),
        json={"name": "x", "type": "local", "connection_config": {"path": "/tmp"}},
    )
    assert r.status_code == 403 and r.json()["code"] == "insufficient_scope"


@pytest.mark.asyncio
async def test_revoked_expired_and_unknown_tokens_are_401(client, world, db_session):
    tok = await _mint(client, world["viewer"], ["files:read"])
    assert (await client.get("/api/v1/search?q=x", headers=_h(tok))).status_code == 200
    tid = (await client.get("/api/v1/me/tokens", headers=_jwt(world["viewer"]))).json()[0]["id"]
    assert (await client.delete(f"/api/v1/me/tokens/{tid}", headers=_jwt(world["viewer"]))).status_code == 204
    assert (await client.get("/api/v1/search?q=x", headers=_h(tok))).status_code == 401

    tok2 = await _mint(client, world["viewer"], ["files:read"])
    row = (await db_session.execute(select(ApiToken).where(ApiToken.revoked_at.is_(None)))).scalar_one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()
    assert (await client.get("/api/v1/search?q=x", headers=_h(tok2))).status_code == 401

    forged = tok2[:-1] + ("A" if tok2[-1] != "A" else "B")
    assert (await client.get("/api/v1/search?q=x", headers=_h(forged))).status_code == 401
    assert (await client.get("/api/v1/search?q=x", headers=_h("akp_" + "0" * 43))).status_code == 401


@pytest.mark.asyncio
async def test_deleted_owner_token_is_401(client, world, db_session):
    tok = await _mint(client, world["viewer"], ["files:read"])
    await db_session.delete(await db_session.get(User, world["viewer"].id))
    await db_session.commit()
    assert (await client.get("/api/v1/search?q=x", headers=_h(tok))).status_code == 401


@pytest.mark.asyncio
async def test_demotion_shrinks_token_immediately(client, world, db_session):
    tok = await _mint(client, world["admin"], ["files:read", "sources:write"])
    body = {"name": "x", "type": "local", "connection_config": {"path": "/tmp"}}
    assert (await client.post("/api/v1/sources", json=body, headers=_h(tok))).status_code == 201
    (await db_session.get(User, world["admin"].id)).role = "viewer"
    await db_session.commit()
    r = await client.post("/api/v1/sources", json={**body, "name": "y"}, headers=_h(tok))
    assert r.status_code == 403 and r.json()["code"] == "insufficient_scope"
    assert (await client.get("/api/v1/search?q=x", headers=_h(tok))).status_code == 200


@pytest.mark.asyncio
async def test_usage_is_throttled_and_events_are_audited(client, world, db_session):
    tok = await _mint(client, world["viewer"], ["files:read"])
    for _ in range(3):
        assert (await client.get("/api/v1/search?q=x", headers=_h(tok))).status_code == 200
    row = (await db_session.execute(select(ApiToken))).scalar_one()
    first = row.last_used_at
    assert first is not None and row.last_used_ip
    # within the throttle window the row is not rewritten
    assert (await client.get("/api/v1/search?q=x", headers=_h(tok))).status_code == 200
    await db_session.refresh(row)
    assert row.last_used_at == first
    # once the window passes it is refreshed, without a second first-use event
    row.last_used_at = first - USAGE_THROTTLE - timedelta(seconds=1)
    await db_session.commit()
    assert (await client.get("/api/v1/search?q=x", headers=_h(tok))).status_code == 200
    await db_session.refresh(row)
    assert row.last_used_at > first - USAGE_THROTTLE
    await client.delete(f"/api/v1/me/tokens/{row.id}", headers=_jwt(world["viewer"]))
    await client.delete(f"/api/v1/me/tokens/{row.id}", headers=_jwt(world["viewer"]))  # idempotent
    types = [e.event_type for e in (await db_session.execute(select(AuditEvent))).scalars()]
    assert types.count("api_token_created") == 1
    assert types.count("api_token_first_used") == 1
    assert types.count("api_token_revoked") == 1


# ------------------------------------------------- creation rules and PATCH


@pytest.mark.asyncio
async def test_token_cannot_exceed_creators_bundle(client, world):
    h = _jwt(world["viewer"])
    for scopes in (["sources:write"], ["files:read", "tokens:admin"], ["nope:read"]):
        r = await client.post("/api/v1/me/tokens", json={"name": "x", "scopes": scopes}, headers=h)
        assert r.status_code == 422, scopes
    # an admin can mint it
    assert (await client.post(
        "/api/v1/me/tokens", json={"name": "x", "scopes": ["sources:write"]}, headers=_jwt(world["admin"]),
    )).status_code == 201


@pytest.mark.asyncio
async def test_source_limit_creation_rules(client, world):
    h = _jwt(world["admin"])
    ok = await client.post(
        "/api/v1/me/tokens", headers=h,
        json={"name": "x", "scopes": ["files:read", "sources:write", "scans:write"], "source_ids": [str(world["a"])]},
    )
    assert ok.status_code == 201 and ok.json()["source_ids"] == [str(world["a"])]
    for bad_scope in ("users:write", "webhooks:write", "settings:write", "maintenance:write", "data:purge", "tokens:write"):
        r = await client.post(
            "/api/v1/me/tokens", headers=h,
            json={"name": "x", "scopes": ["files:read", bad_scope], "source_ids": [str(world["a"])]},
        )
        assert r.status_code == 422, bad_scope
    r = await client.post(
        "/api/v1/me/tokens", headers=h,
        json={"name": "x", "scopes": ["files:read"], "source_ids": [str(uuid.uuid4())]},
    )
    assert r.status_code == 422
    r = await client.post(
        "/api/v1/me/tokens", headers=h, json={"name": "x", "scopes": ["files:read"], "source_ids": []},
    )
    assert r.status_code == 422
    r = await client.post(
        "/api/v1/me/tokens", headers=h,
        json={"name": "x", "scopes": ["files:read"], "expires_at": "2000-01-01T00:00:00Z"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_renames_and_only_narrows(client, world):
    h = _jwt(world["viewer"])
    tid = (await client.post(
        "/api/v1/me/tokens", json={"name": "a", "scopes": ["files:read", "tags:read"]}, headers=h,
    )).json()["id"]
    r = await client.patch(f"/api/v1/me/tokens/{tid}", json={"name": "b", "scopes": ["files:read"]}, headers=h)
    assert r.status_code == 200 and r.json()["name"] == "b" and r.json()["scopes"] == ["files:read"]
    r = await client.patch(f"/api/v1/me/tokens/{tid}", json={"scopes": ["files:read", "tags:read"]}, headers=h)
    assert r.status_code == 422
    # another user's token is invisible
    other = _jwt(world["admin"])
    assert (await client.patch(f"/api/v1/me/tokens/{tid}", json={"name": "z"}, headers=other)).status_code == 404
    assert (await client.delete(f"/api/v1/me/tokens/{tid}", headers=other)).status_code == 404


@pytest.mark.asyncio
async def test_admin_lists_and_revokes_any_token(client, world):
    tok = await _mint(client, world["viewer"], ["files:read"])
    assert (await client.get("/api/v1/admin/tokens", headers=_jwt(world["viewer"]))).status_code == 403
    rows = (await client.get("/api/v1/admin/tokens", headers=_jwt(world["admin"]))).json()
    assert len(rows) == 1 and rows[0]["username"] == "vwr"
    assert (await client.delete(f"/api/v1/admin/tokens/{rows[0]['id']}", headers=_jwt(world["admin"]))).status_code == 204
    assert (await client.get("/api/v1/search?q=x", headers=_h(tok))).status_code == 401
    assert (await client.delete(f"/api/v1/admin/tokens/{uuid.uuid4()}", headers=_jwt(world["admin"]))).status_code == 404


# ------------------------------------------------------- ingest / scanner


@pytest.mark.asyncio
async def test_ingest_and_scanner_audiences_reject_api_tokens(client, world):
    tok = await _mint(client, world["admin"], ["sources:write", "scans:write"])
    payload = {"source_id": str(world["a"]), "scan_id": str(world["sa"]), "is_final": False, "entries": []}
    assert (await client.post("/api/v1/ingest/batch", json=payload, headers=_h(tok))).status_code == 401
    assert (await client.post(f"/api/v1/scans/{world['sa']}/heartbeat", json={}, headers=_h(tok))).status_code == 401
    assert (await client.post("/api/v1/scans/lease", json={}, headers=_h(tok))).status_code == 401


@pytest.mark.asyncio
async def test_ingest_token_dies_when_holder_loses_sources_write(client, world, db_session):
    ingest = _h(create_ingest_token(str(world["admin"].id)))
    payload = {"source_id": str(world["a"]), "scan_id": str(world["sa"]), "is_final": False, "entries": []}
    r = await client.post("/api/v1/ingest/batch", json=payload, headers=ingest)
    assert r.status_code not in (401, 403), r.text
    (await db_session.get(User, world["admin"].id)).role = "viewer"
    await db_session.commit()
    r = await client.post("/api/v1/ingest/batch", json=payload, headers=ingest)
    assert r.status_code == 403 and r.json()["code"] == "insufficient_scope"


# ------------------------------------------------------ per-source limits


@pytest_asyncio.fixture
async def limited(client, world):
    """A token limited to source A holding every scope a limited token may use."""
    scopes = [
        "files:read", "files:content", "tags:read", "tags:write", "analytics:read", "sources:read",
        "sources:write", "scans:read", "scans:write", "identities:read", "access:read",
    ]
    return await _mint(client, world["admin"], scopes, source_ids=[str(world["a"])])


@pytest.mark.asyncio
async def test_limited_search_never_returns_other_sources(client, world, limited, monkeypatch):
    # SQL path (glob mode forces it)
    r = await client.get("/api/v1/search?q=needle*&mode=glob", headers=_h(limited))
    assert r.status_code == 200
    assert [h["filename"] for h in r.json()["items"]] == ["needle-a"]
    # Meilisearch path: the filter sent to the index carries the limit
    seen: dict = {}

    async def fake(q, filters=None, **kw):
        seen["filters"] = filters

        class R:
            hits: list = []
            estimated_total_hits = 0
            facet_distribution = None
        return R()

    monkeypatch.setattr("akashic.services.search.search_files", fake)
    r = await client.get("/api/v1/search?q=needle", headers=_h(limited))
    assert r.status_code == 200
    assert f'source_id = "{world["a"]}"' in seen["filters"] and str(world["b"]) not in seen["filters"]
    # an explicit request for B is a 404, not an empty page
    r = await client.get(f"/api/v1/search?q=needle&source_id={world['b']}", headers=_h(limited))
    assert r.status_code == 404


def test_meili_filter_helper():
    from akashic.auth.dependencies import Principal
    from akashic.auth.source_scope import meili_source_filter

    u = User(id=uuid.uuid4(), username="u", role="admin")
    a, b = uuid.uuid4(), uuid.uuid4()
    assert meili_source_filter(Principal(user=u, scopes=frozenset())) is None
    f = meili_source_filter(Principal(user=u, scopes=frozenset(), allowed_source_ids=frozenset({a, b})))
    assert f.startswith("(") and f'source_id = "{a}"' in f and f'source_id = "{b}"' in f and " OR " in f
    empty = meili_source_filter(Principal(user=u, scopes=frozenset(), allowed_source_ids=frozenset()))
    assert "00000000-0000-0000-0000-000000000000" in empty


def _cases(w: dict):
    """(route template, method, path, json) probing source B with a source-A-limited token.

    ``missing`` cases must be 404; ``filtered`` cases must succeed but leak nothing of B."""
    b, eb, sb, h = w["b"], w["eb"], w["sb"], w["hash"]
    q = f"source_id={b}"
    missing = [
        ("GET /api/v1/search", "GET", f"/api/v1/search?q=needle&{q}", None),
        ("GET /api/v1/browse", "GET", f"/api/v1/browse?{q}&path=/", None),
        ("GET /api/v1/browse/effective-counts", "GET", f"/api/v1/browse/effective-counts?{q}", None),
        ("GET /api/v1/analytics/storage-by-link-role", "GET", f"/api/v1/analytics/storage-by-link-role?{q}", None),
        ("GET /api/v1/entries/{entry_id}", "GET", f"/api/v1/entries/{eb}", None),
        ("GET /api/v1/entries/{entry_id}/versions", "GET", f"/api/v1/entries/{eb}/versions", None),
        ("GET /api/v1/entries/{entry_id}/content", "GET", f"/api/v1/entries/{eb}/content", None),
        ("GET /api/v1/entries/{entry_id}/preview", "GET", f"/api/v1/entries/{eb}/preview", None),
        ("GET /api/v1/entries/{entry_id}/tags", "GET", f"/api/v1/entries/{eb}/tags", None),
        ("POST /api/v1/entries/{entry_id}/tags", "POST", f"/api/v1/entries/{eb}/tags", {"tags": ["x"]}),
        ("DELETE /api/v1/entries/{entry_id}/tags/{tag}", "DELETE", f"/api/v1/entries/{eb}/tags/x", None),
        ("POST /api/v1/tags/bulk-apply", "POST", "/api/v1/tags/bulk-apply", {"entry_ids": [str(eb)], "tags": ["x"]}),
        ("POST /api/v1/entries/{entry_id}/effective-permissions", "POST", f"/api/v1/entries/{eb}/effective-permissions",
         {"principal": {"type": "posix_uid", "identifier": "0"}}),
        ("POST /api/v1/duplicates/{content_hash}/delete-copies", "POST", f"/api/v1/duplicates/{h}/delete-copies",
         {"keep_entry_id": str(w["ea"]), "delete_entry_ids": [str(eb)]}),
        ("GET /api/v1/analytics/timeseries", "GET", f"/api/v1/analytics/timeseries?{q}", None),
        ("GET /api/v1/analytics/forecast", "GET", f"/api/v1/analytics/forecast?{q}", None),
        ("GET /api/v1/analytics/extension-trend", "GET", f"/api/v1/analytics/extension-trend?{q}&extensions=pdf", None),
        ("GET /api/v1/analytics/owner-distribution", "GET", f"/api/v1/analytics/owner-distribution?{q}", None),
        ("GET /api/v1/storage/children", "GET", f"/api/v1/storage/children?{q}", None),
        ("GET /api/v1/storage/tree", "GET", f"/api/v1/storage/tree?{q}", None),
        ("GET /api/v1/access", "GET", f"/api/v1/access?file={eb}", None),
        ("GET /api/v1/scans/{scan_id}", "GET", f"/api/v1/scans/{sb}", None),
        ("GET /api/v1/scans/{scan_id}/log", "GET", f"/api/v1/scans/{sb}/log", None),
        ("POST /api/v1/scans/{scan_id}/cancel", "POST", f"/api/v1/scans/{sb}/cancel", None),
        ("POST /api/v1/scans/trigger", "POST", "/api/v1/scans/trigger", {"source_id": str(b)}),
        ("GET /api/v1/scans", "GET", f"/api/v1/scans?{q}", None),
        ("GET /api/v1/entries", "GET", f"/api/v1/entries?{q}", None),
        ("GET /api/v1/sources/{source_id}", "GET", f"/api/v1/sources/{b}", None),
        ("GET /api/v1/sources/{source_id}/audit", "GET", f"/api/v1/sources/{b}/audit", None),
        ("PATCH /api/v1/sources/{source_id}", "PATCH", f"/api/v1/sources/{b}", {"name": "hijack"}),
        ("DELETE /api/v1/sources/{source_id}", "DELETE", f"/api/v1/sources/{b}", None),
        ("GET /api/v1/sources/{source_id}/entry-count", "GET", f"/api/v1/sources/{b}/entry-count", None),
        ("POST /api/v1/sources/{source_id}/test-scanners", "POST", f"/api/v1/sources/{b}/test-scanners", None),
        ("GET /api/v1/sources/{source_id}/reachability-summary", "GET", f"/api/v1/sources/{b}/reachability-summary", None),
        ("GET /api/v1/sources/{source_id}/reachability-history", "GET", f"/api/v1/sources/{b}/reachability-history", None),
        ("GET /api/v1/sources/{source_id}/scanner-reachability", "GET", f"/api/v1/sources/{b}/scanner-reachability", None),
    ]
    filtered = [
        ("GET /api/v1/entries/by-hash/{content_hash}", "GET", f"/api/v1/entries/by-hash/{h}", None),
        ("GET /api/v1/duplicates/{content_hash}/files", "GET", f"/api/v1/duplicates/{h}/files", None),
        ("GET /api/v1/duplicates", "GET", "/api/v1/duplicates", None),
        ("GET /api/v1/sources", "GET", "/api/v1/sources", None),
        ("GET /api/v1/analytics/storage-by-type", "GET", "/api/v1/analytics/storage-by-type", None),
        ("GET /api/v1/analytics/storage-by-source", "GET", "/api/v1/analytics/storage-by-source", None),
        ("GET /api/v1/analytics/largest-files", "GET", "/api/v1/analytics/largest-files", None),
        ("GET /api/v1/dashboard/summary", "GET", "/api/v1/dashboard/summary", None),
        ("GET /api/v1/dashboard/access-risks", "GET", "/api/v1/dashboard/access-risks", None),
        ("GET /api/v1/storage/sources", "GET", "/api/v1/storage/sources", None),
        ("GET /api/v1/tags/usage", "GET", "/api/v1/tags/usage", None),
        ("GET /api/v1/tags", "GET", "/api/v1/tags", None),
    ]
    # Refused outright: they act outside any one source.
    refused = [
        ("POST /api/v1/sources", "POST", "/api/v1/sources", {"name": "n", "type": "local", "connection_config": {"path": "/tmp"}}),
        ("POST /api/v1/sources/test", "POST", "/api/v1/sources/test", {"type": "local", "connection_config": {"path": "/tmp"}}),
        ("POST /api/v1/tags", "POST", "/api/v1/tags", {"name": "t"}),
        ("DELETE /api/v1/tags/{tag_id}", "DELETE", f"/api/v1/tags/{uuid.uuid4()}", None),
        ("GET /api/v1/sources/{source_id}/orphan-match-count", "GET", f"/api/v1/sources/{w['a']}/orphan-match-count", None),
        ("POST /api/v1/sources/{source_id}/reattach-orphans", "POST", f"/api/v1/sources/{w['a']}/reattach-orphans", {}),
        ("PATCH /api/v1/sources/{source_id}/allowed-scanners", "PATCH", f"/api/v1/sources/{w['a']}/allowed-scanners", {"scanner_ids": []}),
    ]
    return missing, filtered, refused


async def _cases_for(world):
    return _cases(world)


_PLACEHOLDER = {k: uuid.uuid4() for k in ("a", "b", "ea", "eb", "sa", "sb")} | {"hash": "h1"}
_MISSING, _FILTERED, _REFUSED = _cases(_PLACEHOLDER)


def _remap(world, path, body):
    """Cases were built with placeholder ids; swap in the real ones."""
    for k, ph in _PLACEHOLDER.items():
        if k != "hash":
            path = path.replace(str(ph), str(world[k]))
            if body is not None:
                body = _swap(body, str(ph), str(world[k]))
    return path, body


def _swap(obj, old, new):
    if isinstance(obj, dict):
        return {k: _swap(v, old, new) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_swap(v, old, new) for v in obj]
    return new if obj == old else obj


@pytest.mark.asyncio
@pytest.mark.parametrize("key,method,path,body", _MISSING, ids=[c[0] for c in _MISSING])
async def test_limited_token_gets_404_for_other_sources(client, world, limited, key, method, path, body):
    path, body = _remap(world, path, body)
    r = await client.request(method, path, json=body, headers=_h(limited))
    assert r.status_code == 404, (key, r.status_code, r.text[:200])


@pytest.mark.asyncio
@pytest.mark.parametrize("key,method,path,body", _FILTERED, ids=[c[0] for c in _FILTERED])
async def test_limited_token_listings_leak_nothing_from_other_sources(client, world, limited, key, method, path, body):
    path, body = _remap(world, path, body)
    r = await client.request(method, path, json=body, headers=_h(limited))
    assert r.status_code == 200, (key, r.status_code, r.text[:200])
    assert str(world["b"]) not in r.text and NAME_B not in r.text and "needle-b" not in r.text, key
    assert str(world["eb"]) not in r.text and str(world["sb"]) not in r.text, key


@pytest.mark.asyncio
@pytest.mark.parametrize("key,method,path,body", _REFUSED, ids=[c[0] for c in _REFUSED])
async def test_limited_token_is_refused_actions_beyond_its_sources(client, world, limited, key, method, path, body):
    path, body = _remap(world, path, body)
    r = await client.request(method, path, json=body, headers=_h(limited))
    assert r.status_code == 403 and r.json()["code"] == "source_limited", (key, r.status_code, r.text[:200])


@pytest.mark.asyncio
async def test_limited_token_can_still_use_its_own_source(client, world, limited):
    h = _h(limited)
    assert (await client.get(f"/api/v1/browse?source_id={world['a']}&path=/", headers=h)).status_code == 200
    assert (await client.get(f"/api/v1/entries/{world['ea']}", headers=h)).status_code == 200
    assert (await client.get(f"/api/v1/sources/{world['a']}", headers=h)).status_code == 200
    r = await client.get("/api/v1/sources", headers=h)
    assert [s["name"] for s in r.json()] == ["SRC-A"]
    r = await client.get("/api/v1/scans", headers=h)
    assert [s["id"] for s in r.json()["items"]] == [str(world["sa"])]


@pytest.mark.asyncio
async def test_other_source_404_is_indistinguishable_from_missing(client, world, limited):
    h = _h(limited)
    real = await client.get(f"/api/v1/entries/{world['eb']}", headers=h)
    fake = await client.get(f"/api/v1/entries/{uuid.uuid4()}", headers=h)
    assert real.status_code == fake.status_code == 404
    assert real.json()["message"] == fake.json()["message"]
    real = await client.get(f"/api/v1/entries/{world['eb']}/content", headers=h)
    fake = await client.get(f"/api/v1/entries/{uuid.uuid4()}/content", headers=h)
    assert real.json()["message"] == fake.json()["message"]


@pytest.mark.asyncio
async def test_limited_token_is_refused_on_routes_that_ignore_limits(client, world):
    tok = await _mint(
        client, world["admin"], ["webhooks:read", "users:read", "tokens:read"], source_ids=[str(world["a"])],
    )
    for path in ("/api/v1/webhooks", "/api/v1/users", "/api/v1/me/tokens"):
        r = await client.get(path, headers=_h(tok))
        assert r.status_code == 403 and r.json()["code"] == "source_limited", path


@pytest.mark.asyncio
async def test_unlimited_token_sees_every_source(client, world):
    tok = await _mint(client, world["admin"], ["sources:read", "files:read"])
    names = sorted(s["name"] for s in (await client.get("/api/v1/sources", headers=_h(tok))).json())
    assert names == ["SRC-A", NAME_B]


# ------------------------------------------------ every data route is covered


def test_every_data_route_is_exercised_by_the_limit_tests():
    covered = {c[0] for c in _MISSING + _FILTERED + _REFUSED}
    data_routes = {
        key.replace(" /api/v1", " /api/v1")
        for key, route in _routes(__import__("akashic.main", fromlist=["create_app"]).create_app())
        if DATA_TAGS & set(route.tags or []) and "scanner" not in (route.tags or []) and _source_scoped(route)
    }
    assert data_routes - covered == set(), f"data routes without a source-limit test: {sorted(data_routes - covered)}"


# ------------------------------------------------------------ review fixes


@pytest.mark.asyncio
async def test_tokens_cannot_create_or_patch_tokens_but_can_list_and_revoke(client, world):
    tok = await _mint(client, world["admin"], ["tokens:read", "tokens:write", "files:read"])
    r = await client.post("/api/v1/me/tokens", json={"name": "x", "scopes": ["files:read"]}, headers=_h(tok))
    assert r.status_code == 403 and r.json()["code"] == "interactive_session_required"
    tid = (await client.get("/api/v1/me/tokens", headers=_h(tok))).json()[0]["id"]
    r = await client.patch(f"/api/v1/me/tokens/{tid}", json={"name": "y"}, headers=_h(tok))
    assert r.status_code == 403 and r.json()["code"] == "interactive_session_required"
    assert (await client.delete(f"/api/v1/me/tokens/{tid}", headers=_h(tok))).status_code == 204


@pytest.mark.asyncio
async def test_patch_is_audited_with_old_and_new_scopes(client, world, db_session):
    h = _jwt(world["viewer"])
    tid = (await client.post(
        "/api/v1/me/tokens", json={"name": "a", "scopes": ["files:read", "tags:read"]}, headers=h,
    )).json()["id"]
    await client.patch(f"/api/v1/me/tokens/{tid}", json={"name": "b", "scopes": ["files:read"]}, headers=h)
    ev = (await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "api_token_updated")
    )).scalar_one()
    assert ev.payload["old_scopes"] == ["files:read", "tags:read"] and ev.payload["new_scopes"] == ["files:read"]
    assert ev.payload["old_name"] == "a" and ev.payload["new_name"] == "b"


@pytest.mark.asyncio
async def test_limited_token_applies_only_existing_catalogue_tags(client, world, limited, db_session):
    from akashic.models.tag import Tag
    db_session.add(Tag(id=uuid.uuid4(), name="known"))
    await db_session.commit()
    h = _h(limited)
    ea = world["ea"]
    r = await client.post(f"/api/v1/entries/{ea}/tags", json={"tags": ["known", "brand-new"]}, headers=h)
    assert r.status_code == 403 and r.json()["code"] == "source_limited"
    r = await client.post(
        "/api/v1/tags/bulk-apply", json={"entry_ids": [str(ea)], "tags": ["known", "brand-new"]}, headers=h,
    )
    assert r.status_code == 403 and r.json()["code"] == "source_limited"
    assert (await client.get(f"/api/v1/entries/{ea}/tags", headers=h)).json() == []  # nothing partially applied
    db_session.expire_all()
    assert (await db_session.execute(select(Tag).where(Tag.name == "brand-new"))).first() is None
    assert (await client.post(f"/api/v1/entries/{ea}/tags", json={"tags": ["known"]}, headers=h)).status_code == 204


@pytest.mark.asyncio
async def test_limited_token_lists_only_tags_used_in_its_sources(client, world, limited, db_session):
    from akashic.models.tag import EntryTag, Tag
    db_session.add_all([Tag(id=uuid.uuid4(), name="in-a"), Tag(id=uuid.uuid4(), name="in-b"), Tag(id=uuid.uuid4(), name="unused")])
    await db_session.flush()
    db_session.add_all([
        EntryTag(entry_id=world["ea"], tag="in-a", created_by_user_id=world["admin"].id), EntryTag(entry_id=world["eb"], tag="in-b", created_by_user_id=world["admin"].id),
    ])
    await db_session.commit()
    assert [t["name"] for t in (await client.get("/api/v1/tags", headers=_h(limited))).json()] == ["in-a"]
    full = await _mint(client, world["admin"], ["tags:read"])
    assert {t["name"] for t in (await client.get("/api/v1/tags", headers=_h(full))).json()} == {"in-a", "in-b", "unused"}


@pytest.mark.asyncio
async def test_limited_token_cannot_repoint_a_source(client, world, limited):
    h = _h(limited)
    url = f"/api/v1/sources/{world['a']}"
    for body in (
        {"connection_config": {"path": "/etc"}},
        {"credential_profile_id": str(uuid.uuid4())},
        {"host_id": str(uuid.uuid4())},
    ):
        r = await client.patch(url, json=body, headers=h)
        assert r.status_code == 403 and r.json()["code"] == "source_limited", body
    r = await client.patch(url, json={"name": "renamed", "exclude_patterns": ["*.tmp"]}, headers=h)
    assert r.status_code == 200 and r.json()["name"] == "renamed"
