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

from akashic.schemas.common import decode_offset_cursor
import uuid

import pytest

from akashic.models.entry import Entry
from akashic.models.principals_cache import PrincipalsCache
from akashic.models.source import Source


async def _register_login(client, username="admin", password="testpass123"):
    await client.post("/api/v1/users/register", json={"username": username, "password": password})
    login = await client.post(
        "/api/v1/users/login",
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
        "/api/v1/access",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
    # Both.
    r = await client.get(
        f"/api/v1/access?principal=sid:S-1-5-X&file={uuid_from_str()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_admin_only(client):
    """Non-admin viewer gets 403 even with valid args."""
    admin_token = await _register_login(client)
    await client.post(
        "/api/v1/users/create",
        json={"username": "viewer1", "password": "testpass123"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    login = await client.post(
        "/api/v1/users/login",
        json={"username": "viewer1", "password": "testpass123"},
    )
    user_token = login.json()["access_token"]

    r = await client.get(
        "/api/v1/access?principal=sid:S-1-5-X",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert r.status_code == 403


def uuid_from_str() -> str:
    """Stable UUID for test assertions where the value doesn't matter."""
    return "11111111-2222-3333-4444-555555555555"


# ── principal → files (Meili-backed) ─────────────────────────────────────


def _stub_sample(hits):
    """Meili is only used for the paginated sample; counts and sizes
    come from Postgres."""

    class _Resp:
        def __init__(self, hits):
            self.hits = hits

    return _Resp(hits)


async def _seed(db, src, n, size, *, tok="sid:S-1-5-21-X-1001", kind="file",
                deleted=False, right="viewable_by_read", tag="f"):
    for i in range(n):
        db.add(Entry(
            source_id=src.id, kind=kind, parent_path="/", path=f"/{tag}{src.name}{i}",
            name=f"{tag}{i}", size_bytes=size, is_deleted=deleted,
            **{right: [tok]},
        ))


@pytest.mark.asyncio
async def test_principal_to_files_returns_summary_and_sample(client, db_session):
    token = await _register_login(client)
    src_a = await _make_source(db_session, "share-a")
    src_b = await _make_source(db_session, "share-b")
    await db_session.commit()

    await _seed(db_session, src_a, 1, 100)
    await _seed(db_session, src_b, 1, 200)
    # Excluded: deleted, directory, other principal, other right.
    await _seed(db_session, src_a, 1, 999, deleted=True, tag="d")
    await _seed(db_session, src_a, 1, 999, kind="directory", tag="k")
    await _seed(db_session, src_a, 1, 999, tok="sid:other", tag="o")
    await _seed(db_session, src_a, 1, 999, right="viewable_by_write", tag="w")
    await db_session.commit()

    sample = _stub_sample([
        {"id": str(uuid.uuid4()), "source_id": str(src_a.id), "path": "/x/a", "filename": "a", "size_bytes": 100},
        {"id": str(uuid.uuid4()), "source_id": str(src_b.id), "path": "/x/b", "filename": "b", "size_bytes": 200},
    ])

    fake_index = AsyncMock()
    fake_index.search = AsyncMock(return_value=sample)
    fake_client = AsyncMock()
    fake_client.get_index = AsyncMock(return_value=fake_index)

    with patch(
        "akashic.routers.access.get_meili_client", AsyncMock(return_value=fake_client),
    ):
        r = await client.get(
            "/api/v1/access?principal=sid:S-1-5-21-X-1001&right=read",
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
    # next_cursor is None when sample size < limit (default limit=20).
    assert body["next_cursor"] is None
    assert body["limit"] == 20
    assert body["identity"] is None and body["warnings"] == []
    assert all(h["has_deny_ace"] is False for h in body["sample"])


@pytest.mark.asyncio
async def test_principal_to_files_pagination_signals_next_cursor(client, db_session):
    token = await _register_login(client)
    src = await _make_source(db_session)
    await db_session.commit()

    await _seed(db_session, src, 50, 10, tok="sid:S-1-5-X")
    await db_session.commit()
    hits = [
        {"id": str(uuid.uuid4()), "source_id": str(src.id), "path": f"/p/{i}",
         "filename": f"f{i}", "size_bytes": 10}
        for i in range(5)
    ]
    sample = _stub_sample(hits)

    fake_index = AsyncMock()
    fake_index.search = AsyncMock(return_value=sample)
    fake_client = AsyncMock()
    fake_client.get_index = AsyncMock(return_value=fake_index)

    with patch(
        "akashic.routers.access.get_meili_client", AsyncMock(return_value=fake_client),
    ):
        r = await client.get(
            f"/api/v1/access?principal=sid:S-1-5-X&limit=5",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["file_count"] == 50
    assert body["summary"]["total_size_bytes"] == 500
    assert decode_offset_cursor(body["next_cursor"]) == 5  # offset 0 + limit 5


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

    sample = _stub_sample([])
    fake_index = AsyncMock()
    fake_index.search = AsyncMock(return_value=sample)
    fake_client = AsyncMock()
    fake_client.get_index = AsyncMock(return_value=fake_index)

    with patch(
        "akashic.routers.access.get_meili_client", AsyncMock(return_value=fake_client),
    ):
        r = await client.get(
            "/api/v1/access?principal=sid:S-1-5-21-X-1001",
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
        f"/api/v1/access?file={entry.id}&right=read",
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
        f"/api/v1/access?file={uuid_from_str()}&right=read",
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
        f"/api/v1/access?file={entry.id}&right=read",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    sid_principals = [p for p in body["principals"] if p["token"].startswith("sid:")]
    by_token = {p["token"]: p for p in sid_principals}
    assert "sid:S-1-5-21-X-1001" in by_token
    assert by_token["sid:S-1-5-21-X-1001"]["name"] == "alice"


# ── source limits ────────────────────────────────────────────────────────


async def _limited_lookup(client, db_session, allowed, query):
    from sqlalchemy import select
    from akashic.auth.dependencies import get_principal
    from akashic.models.user import User
    from tests.principals import as_principal

    token = await _register_login(client)
    user = (await db_session.execute(select(User))).scalars().first()
    app = client._transport.app
    app.dependency_overrides[get_principal] = as_principal(
        user, allowed_source_ids=allowed,
    )
    fake_index = AsyncMock()
    fake_index.search = AsyncMock(return_value=_stub_sample([]))
    fake_client = AsyncMock()
    fake_client.get_index = AsyncMock(return_value=fake_index)
    with patch(
        "akashic.routers.access.get_meili_client", AsyncMock(return_value=fake_client),
    ):
        return await client.get(
            f"/api/v1/access?principal=sid:S-1-5-21-X-1001&{query}",
            headers={"Authorization": f"Bearer {token}"},
        )


@pytest.mark.asyncio
async def test_totals_respect_token_source_limit(client, db_session):
    src_a = await _make_source(db_session, "share-a")
    src_b = await _make_source(db_session, "share-b")
    await _seed(db_session, src_a, 2, 100)
    await _seed(db_session, src_b, 3, 1000)
    await db_session.commit()

    r = await _limited_lookup(client, db_session, {src_a.id}, "right=read")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"] == {"file_count": 2, "total_size_bytes": 200, "source_count": 1}
    assert [(x["source_id"], x["file_count"]) for x in body["by_source"]] == [(str(src_a.id), 2)]


@pytest.mark.asyncio
async def test_totals_with_source_id_param(client, db_session):
    src_a = await _make_source(db_session, "share-a")
    src_b = await _make_source(db_session, "share-b")
    await _seed(db_session, src_a, 2, 100)
    await _seed(db_session, src_b, 3, 1000)
    await db_session.commit()

    # Unlimited admin, narrowed by source_id.
    r = await _limited_lookup(client, db_session, None, f"source_id={src_b.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"] == {"file_count": 3, "total_size_bytes": 3000, "source_count": 1}
    assert [x["source_id"] for x in body["by_source"]] == [str(src_b.id)]

    # Limited token asking for a source outside its limit -> 404.
    r = await _limited_lookup(client, db_session, {src_a.id}, f"source_id={src_b.id}")
    assert r.status_code == 404
