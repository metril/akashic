"""Scope enforcement: every scoped route denies a caller lacking its scope."""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import Principal, get_principal
from akashic.auth.jwt import create_access_token
from akashic.auth.scopes import ROLE_SCOPES, SCOPES
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.user import User
from tests.principals import as_principal
from tests.pre_3a_matrix import PRE_3A_VIEWER_ALLOWED
from tests.test_openapi_contract import _required_scopes, _routes

_APP = create_app()
_SCOPED = sorted(
    (key, scopes[0]) for key, route in _routes(_APP) if len(scopes := _required_scopes(route)) == 1
)


def _fill(path: str) -> str:
    out = path
    for part in path.split("/"):
        if part.startswith("{"):
            out = out.replace(part, str(uuid.uuid4()))
    return out


def _user(role: str) -> User:
    return User(id=uuid.uuid4(), username=role, email=None, password_hash="x", role=role)


@pytest_asyncio.fixture
async def make_client(setup_db):
    clients: list[AsyncClient] = []

    async def _make(principal_override=None) -> AsyncClient:
        async def _db():
            async with setup_db() as session:
                yield session

        app = create_app()
        app.dependency_overrides[get_db] = _db
        if principal_override is not None:
            app.dependency_overrides[get_principal] = principal_override
        c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(c)
        return c

    yield _make
    for c in clients:
        await c.aclose()


# Added after the pre-scope matrix: a viewer manages their own API tokens.
_TOKEN_ROUTES_FOR_VIEWER = frozenset(k for k, sc in _SCOPED if sc in ("tokens:read", "tokens:write"))


def test_scoped_routes_are_discovered():
    assert len(_SCOPED) > 100
    assert {s for _, s in _SCOPED} <= set(SCOPES)


@pytest.mark.asyncio
@pytest.mark.parametrize("key,scope", _SCOPED)
async def test_missing_scope_is_403_insufficient_scope(make_client, key, scope):
    reduced = frozenset(SCOPES) - {scope}
    client = await make_client(as_principal(_user("admin"), scopes=reduced))
    method, path = key.split(" ", 1)
    resp = await client.request(method, _fill(path))
    assert resp.status_code == 403, (key, resp.text)
    body = resp.json()
    assert body["code"] == "insufficient_scope"
    assert scope in body["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("key,scope", _SCOPED)
async def test_unknown_role_is_denied_everywhere(make_client, key, scope):
    client = await make_client(as_principal(_user("nobody")))
    method, path = key.split(" ", 1)
    resp = await client.request(method, _fill(path))
    assert resp.status_code == 403 and resp.json()["code"] == "insufficient_scope"


@pytest.mark.asyncio
@pytest.mark.parametrize("key,scope", _SCOPED)
async def test_viewer_access_matches_pre_scope_behaviour(make_client, key, scope):
    """A viewer-bundle principal reaches exactly the routes a viewer reached at e7bdc73."""
    client = await make_client(as_principal(_user("viewer")))
    method, path = key.split(" ", 1)
    resp = await client.request(method, _fill(path))
    denied = resp.status_code == 403 and resp.json().get("code") == "insufficient_scope"
    assert denied == (key not in PRE_3A_VIEWER_ALLOWED | _TOKEN_ROUTES_FOR_VIEWER), (
        key, resp.status_code, resp.text[:200],
    )


def test_pre_3a_matrix_routes_exist():
    assert (PRE_3A_VIEWER_ALLOWED - {"GET /api/v1/users/me"}) <= {k for k, _ in _SCOPED}


@pytest.mark.asyncio
async def test_viewer_cannot_touch_another_users_identities_or_webhooks(make_client, jwt_users, setup_db):
    from akashic.models.fs_person import FsPerson
    from akashic.models.webhook import Webhook

    owner = uuid.uuid4()
    async with setup_db() as s:
        s.add(User(id=owner, username="own", email=None, password_hash="x", role="viewer"))
        await s.flush()
        person = FsPerson(user_id=owner, label="x", is_primary=False)
        s.add(person)
        await s.commit()
        pid = person.id
    client = await make_client()
    h = jwt_users["viewer"]
    assert (await client.patch(f"/api/v1/identities/{pid}", json={"label": "y"}, headers=h)).status_code == 404
    assert (await client.delete(f"/api/v1/identities/{pid}", headers=h)).status_code == 404
    assert (await client.delete(f"/api/v1/webhooks/{uuid.uuid4()}", headers=h)).status_code == 404


@pytest.mark.asyncio
async def test_scans_write_alone_can_trigger_and_cancel(make_client, setup_db):
    """The route's declared scope is the only gate: scans:write needs no sources:write."""
    from akashic.models.scan import Scan
    from akashic.models.source import Source

    async with setup_db() as s:
        src = Source(id=uuid.uuid4(), name="s", type="local", connection_config={"path": "/tmp"}, status="online")
        s.add(src)
        await s.flush()
        scan = Scan(id=uuid.uuid4(), source_id=src.id, scan_type="full", status="pending")
        s.add(scan)
        await s.commit()
    only = frozenset({"scans:write"})
    client = await make_client(as_principal(_user("admin"), scopes=only))
    r = await client.post("/api/v1/scans/trigger", json={"source_id": str(src.id)})
    assert r.status_code not in (401, 403), r.text
    r = await client.post(f"/api/v1/scans/{scan.id}/cancel")
    assert r.status_code not in (401, 403), r.text


def test_viewer_bundle_matches_previous_admin_boundary():
    viewer = ROLE_SCOPES["viewer"]
    for admin_only in (
        "settings:read", "settings:write", "users:read", "users:write", "audit:read",
        "credentials:read", "credentials:write", "sources:write", "scans:write",
        "scanners:read", "scanners:write", "hosts:write", "health:read", "tags:write",
        "maintenance:write", "data:purge", "tokens:admin",
    ):
        assert admin_only not in viewer
    assert {"files:read", "files:content", "tags:read", "tokens:write"} <= viewer


@pytest_asyncio.fixture
async def jwt_users(setup_db):
    async with setup_db() as s:
        admin, viewer = _user("admin"), _user("viewer")
        admin.username, viewer.username = "adm", "vwr"
        s.add_all([admin, viewer])
        await s.commit()
    return {
        r.role: {"Authorization": f"Bearer {create_access_token({'sub': str(r.id)})}"}
        for r in (admin, viewer)
    }


@pytest.mark.asyncio
async def test_real_jwt_viewer_and_admin(make_client, jwt_users):
    client = await make_client()
    viewer, admin = jwt_users["viewer"], jwt_users["admin"]

    r = await client.get("/api/v1/server-settings", headers=viewer)
    assert r.status_code == 403 and r.json()["code"] == "insufficient_scope"
    assert (await client.get("/api/v1/server-settings", headers=admin)).status_code == 200
    assert (await client.get("/api/v1/sources", headers=viewer)).status_code == 200
    assert (await client.get("/api/v1/sources")).status_code == 401


@pytest.mark.asyncio
async def test_me_and_scope_catalog(make_client, jwt_users):
    client = await make_client()
    for role in ("viewer", "admin"):
        me = (await client.get("/api/v1/users/me", headers=jwt_users[role])).json()
        assert me["role"] == role
        assert set(me["scopes"]) == ROLE_SCOPES[role]
    cat = (await client.get("/api/v1/scopes", headers=jwt_users["viewer"])).json()
    assert {s["name"] for s in cat["scopes"]} == set(SCOPES)
    assert set(cat["roles"]["viewer"]) == ROLE_SCOPES["viewer"]
    assert (await client.get("/api/v1/scopes")).status_code == 401


@pytest.mark.asyncio
async def test_role_dependent_behaviour_follows_scopes(make_client, jwt_users):
    client = await make_client()
    viewer, admin = jwt_users["viewer"], jwt_users["admin"]
    # access-risks: viewers get a null placeholder, admins the real value.
    assert (await client.get("/api/v1/dashboard/access-risks", headers=viewer)).json() == {"access_risks": None}
    assert (await client.get("/api/v1/dashboard/access-risks", headers=admin)).json()["access_risks"] is not None
    # unbound identities of another user: admin-only.
    other = uuid.uuid4()
    r = await client.get(f"/api/v1/identities/unbound?user_id={other}", headers=viewer)
    assert r.status_code == 403
    assert (await client.get(f"/api/v1/identities/unbound?user_id={other}", headers=admin)).status_code == 200
