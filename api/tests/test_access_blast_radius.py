"""Admin blast-radius endpoints (Phase 3).

The principal→files direction queries Meilisearch; we mock at the
meili-client boundary so the test doesn't need a live Meili process.
The file→principals direction calls denormalize_acl directly with
seeded ACL data, no mocking needed.

Both endpoints are admin-only — the admin gate is the same one used
by other admin endpoints (require_admin), so we cover the 403 path
once and skip duplicating it on every assertion.
"""
from unittest.mock import AsyncMock, patch

import pytest

from akashic.models.entry import Entry
from akashic.models.principals_cache import PrincipalsCache
from akashic.models.source import Source


async def _register_login(client, username="admin", password="testpass123"):
    await client.post("/api/users/register", json={"username": username, "password": password})
    login = await client.post(
        "/api/users/login",
        json={"username": username, "password": password},
    )
    return login.json()["access_token"]


async def _make_source(db, name="src"):
    s = Source(name=name, type="local", connection_config={"path": "/tmp"}, status="online")
    db.add(s)
    await db.flush()
    await db.refresh(s)
    return s


# ── Validation ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_requires_exactly_one_of_principal_or_file(client):
    token = await _register_login(client)
    # Neither.
    r = await client.get(
        "/api/access",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
    # Both.
    r = await client.get(
        f"/api/access?principal=sid:S-1-5-X&file={uuid_from_str()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_admin_only(client):
    """Non-admin viewer gets 403 even with valid args."""
    admin_token = await _register_login(client)
    await client.post(
        "/api/users/create",
        json={"username": "viewer1", "password": "testpass123", "role": "user"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    login = await client.post(
        "/api/users/login",
        json={"username": "viewer1", "password": "testpass123"},
    )
    user_token = login.json()["access_token"]

    r = await client.get(
        "/api/access?principal=sid:S-1-5-X",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert r.status_code == 403


def uuid_from_str() -> str:
    """Stable UUID for test assertions where the value doesn't matter."""
    return "11111111-2222-3333-4444-555555555555"


# ── principal → files (Meili-backed) ─────────────────────────────────────


def _stub_search_response(*, hits, dist, sum_bytes, total):
    """Build the shape the meili sdk returns from index.search.

    Two calls happen in _principal_to_files: the first retrieves the
    paginated sample (limit=N, no facets), the second is a count-only
    pass with limit=0 and facets=['source_id','size_bytes']. The mock
    needs to return different shapes based on which call it is."""

    class _Resp:
        def __init__(self, hits=None, distribution=None, stats=None, total=0):
            self.hits = hits or []
            self.facet_distribution = distribution or {}
            self.facet_stats = stats or {}
            self.estimated_total_hits = total

    sample = _Resp(hits=hits, total=total)
    facets = _Resp(
        distribution={"source_id": dist},
        stats={"size_bytes": {"sum": sum_bytes, "min": 0, "max": sum_bytes}},
        total=total,
    )
    return sample, facets


@pytest.mark.asyncio
async def test_principal_to_files_returns_summary_and_sample(client, db_session):
    token = await _register_login(client)
    src_a = await _make_source(db_session, "share-a")
    src_b = await _make_source(db_session, "share-b")
    await db_session.commit()

    sample, facets = _stub_search_response(
        hits=[
            {"id": "e1", "source_id": str(src_a.id), "path": "/x/a", "filename": "a", "size_bytes": 100},
            {"id": "e2", "source_id": str(src_b.id), "path": "/x/b", "filename": "b", "size_bytes": 200},
        ],
        dist={str(src_a.id): 1, str(src_b.id): 1},
        sum_bytes=300,
        total=2,
    )

    fake_index = AsyncMock()
    fake_index.search = AsyncMock(side_effect=[sample, facets])
    fake_client = AsyncMock()
    fake_client.get_index = AsyncMock(return_value=fake_index)

    with patch(
        "akashic.routers.access.get_meili_client", AsyncMock(return_value=fake_client),
    ):
        r = await client.get(
            "/api/access?principal=sid:S-1-5-21-X-1001&right=read",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert r.status_code == 200
    body = r.json()
    assert body["principal"]["token"] == "sid:S-1-5-21-X-1001"
    assert body["principal"]["kind"] == "user"  # SID tokens classify as user
    assert body["right"] == "read"
    assert body["summary"]["file_count"] == 2
    assert body["summary"]["total_size_bytes"] == 300
    assert body["summary"]["source_count"] == 2

    # Per-source rollup is sorted by file_count desc; ties keep lookup
    # working regardless of dict iteration order.
    sids = [r["source_id"] for r in body["by_source"]]
    assert set(sids) == {str(src_a.id), str(src_b.id)}
    # source_name was hydrated from the sources table.
    names = {r["source_id"]: r["source_name"] for r in body["by_source"]}
    assert names[str(src_a.id)] == "share-a"

    assert len(body["sample"]) == 2
    # next_offset is None when sample size < limit (default limit=20).
    assert body["next_offset"] is None


@pytest.mark.asyncio
async def test_principal_to_files_pagination_signals_next_offset(client, db_session):
    token = await _register_login(client)
    src = await _make_source(db_session)
    await db_session.commit()

    # 5 hits with limit=5 → next_offset should advance.
    hits = [
        {"id": f"e{i}", "source_id": str(src.id), "path": f"/p/{i}",
         "filename": f"f{i}", "size_bytes": 10}
        for i in range(5)
    ]
    sample, facets = _stub_search_response(
        hits=hits, dist={str(src.id): 50}, sum_bytes=500, total=50,
    )

    fake_index = AsyncMock()
    fake_index.search = AsyncMock(side_effect=[sample, facets])
    fake_client = AsyncMock()
    fake_client.get_index = AsyncMock(return_value=fake_index)

    with patch(
        "akashic.routers.access.get_meili_client", AsyncMock(return_value=fake_client),
    ):
        r = await client.get(
            f"/api/access?principal=sid:S-1-5-X&limit=5",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["file_count"] == 50
    assert body["next_offset"] == 5  # offset 0 + limit 5


@pytest.mark.asyncio
async def test_principal_to_files_hydrates_sid_friendly_name(client, db_session):
    """When principals_cache has a name for the queried SID, it's
    returned alongside the token so the UI doesn't need a second call."""
    token = await _register_login(client)
    src = await _make_source(db_session)
    db_session.add(PrincipalsCache(
        source_id=src.id,
        sid="S-1-5-21-X-1001",
        name="alice",
        domain="EXAMPLE",
        kind="user",
    ))
    await db_session.commit()

    sample, facets = _stub_search_response(
        hits=[], dist={}, sum_bytes=0, total=0,
    )
    fake_index = AsyncMock()
    fake_index.search = AsyncMock(side_effect=[sample, facets])
    fake_client = AsyncMock()
    fake_client.get_index = AsyncMock(return_value=fake_index)

    with patch(
        "akashic.routers.access.get_meili_client", AsyncMock(return_value=fake_client),
    ):
        r = await client.get(
            "/api/access?principal=sid:S-1-5-21-X-1001",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["principal"]["name"] == "alice"
    assert body["principal"]["domain"] == "EXAMPLE"


# ── file → principals (denormalize_acl-backed) ──────────────────────────


@pytest.mark.asyncio
async def test_file_to_principals_returns_acl_grants(client, db_session):
    token = await _register_login(client)
    src = await _make_source(db_session)

    # Seed an entry with a POSIX 0o644 mode + uid 1001 + gid 100.
    # denormalize_acl produces:
    #   read: posix:uid:1001 (owner), posix:gid:100 (group), '*' (world)
    #   write: posix:uid:1001
    entry = Entry(
        source_id=src.id, kind="file", parent_path="/", path="/a.txt",
        name="a.txt", extension="txt", size_bytes=100,
        mode=0o644, uid=1001, gid=100,
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    r = await client.get(
        f"/api/access?file={entry.id}&right=read",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["entry_id"] == str(entry.id)
    assert body["filename"] == "a.txt"
    assert body["right"] == "read"
    tokens = {p["token"] for p in body["principals"]}
    assert "posix:uid:1001" in tokens
    assert "posix:gid:100" in tokens
    # 0o644 makes the file world-readable.
    assert "*" in tokens

    # Wildcard tokens classify as 'wildcard' so the UI can flag them.
    wildcard = next(p for p in body["principals"] if p["token"] == "*")
    assert wildcard["kind"] == "wildcard"


@pytest.mark.asyncio
async def test_file_to_principals_404_for_missing_entry(client):
    token = await _register_login(client)
    r = await client.get(
        f"/api/access?file={uuid_from_str()}&right=read",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_file_to_principals_hydrates_sid_names(client, db_session):
    """An entry with an NT ACL grant for SID `S-1-5-21-X-1001` should
    return that SID with its cached friendly name."""
    token = await _register_login(client)
    src = await _make_source(db_session)
    db_session.add(PrincipalsCache(
        source_id=src.id,
        sid="S-1-5-21-X-1001",
        name="alice",
        domain="EXAMPLE",
        kind="user",
    ))

    # NtACL schema: ace_type ∈ {allow, deny, audit}; mask is a list of
    # NT-permission strings; control is a list of flag strings.
    nt_acl = {
        "type": "nt",
        "control": [],
        "owner": {"sid": "S-1-5-21-X-500", "name": "Administrator"},
        "group": {"sid": "S-1-5-21-X-513", "name": "Domain Users"},
        "entries": [
            {
                "sid": "S-1-5-21-X-1001",
                "name": "alice",
                "ace_type": "allow",
                "flags": [],
                "mask": ["GENERIC_READ", "READ_DATA"],
            },
        ],
    }
    entry = Entry(
        source_id=src.id, kind="file", parent_path="/", path="/win.txt",
        name="win.txt", extension="txt", size_bytes=100,
        acl=nt_acl,
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    r = await client.get(
        f"/api/access?file={entry.id}&right=read",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    sid_principals = [p for p in body["principals"] if p["token"].startswith("sid:")]
    by_token = {p["token"]: p for p in sid_principals}
    assert "sid:S-1-5-21-X-1001" in by_token
    assert by_token["sid:S-1-5-21-X-1001"]["name"] == "alice"
