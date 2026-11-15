"""User role management (PATCH /users/{id}) and IdP group -> admin mapping."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from akashic.auth import ldap as ldap_auth
from akashic.auth import oidc
from akashic.models.audit_event import AuditEvent
from akashic.models.user import User


def _h(t):
    return {"Authorization": f"Bearer {t}"}


async def _login(client, username, password="testpass123"):
    r = await client.post("/api/v1/users/login", json={"username": username, "password": password})
    return r.json()["access_token"]


async def _setup(client):
    """First registered user is admin; returns (admin_token, viewer_id, viewer_token)."""
    await client.post("/api/v1/users/register", json={"username": "admin", "password": "testpass123"})
    admin = await _login(client, "admin")
    r = await client.post(
        "/api/v1/users/create", json={"username": "vic", "password": "testpass123"}, headers=_h(admin),
    )
    return admin, r.json()["id"], await _login(client, "vic")


@pytest.mark.asyncio
async def test_promote_demote_unpin_and_audit(client, db_session):
    admin, vid, _ = await _setup(client)
    r = await client.patch(f"/api/v1/users/{vid}", json={"role": "admin"}, headers=_h(admin))
    assert r.status_code == 200
    assert r.json()["role"] == "admin" and r.json()["role_pinned"] is True

    r = await client.patch(f"/api/v1/users/{vid}", json={"role": "viewer", "role_pinned": False}, headers=_h(admin))
    assert r.json()["role"] == "viewer" and r.json()["role_pinned"] is False

    r = await client.patch(f"/api/v1/users/{vid}", json={"role_pinned": True}, headers=_h(admin))
    assert r.json()["role_pinned"] is True
    r = await client.patch(f"/api/v1/users/{vid}", json={"role_pinned": False}, headers=_h(admin))
    assert r.json()["role_pinned"] is False

    events = (await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "user_role_changed")
    )).scalars().all()
    assert len(events) == 4
    first = sorted(events, key=lambda e: e.occurred_at)[0].payload
    assert first["old"] == "viewer" and first["new"] == "admin" and first["via"] == "manual"
    assert first["username"] == "vic" and first["pinned"] is True


@pytest.mark.asyncio
async def test_viewer_forbidden_and_validation(client):
    admin, vid, viewer = await _setup(client)
    r = await client.patch(f"/api/v1/users/{vid}", json={"role": "admin"}, headers=_h(viewer))
    assert r.status_code == 403
    assert (await client.patch(f"/api/v1/users/{vid}", json={}, headers=_h(admin))).status_code == 422
    assert (await client.patch(f"/api/v1/users/{vid}", json={"role": "root"}, headers=_h(admin))).status_code == 422
    assert (await client.patch(f"/api/v1/users/{uuid.uuid4()}", json={"role": "admin"}, headers=_h(admin))).status_code == 404


@pytest.mark.asyncio
async def test_last_admin_guard_and_self_demotion(client):
    admin, vid, _ = await _setup(client)
    me = (await client.get("/api/v1/users/me", headers=_h(admin))).json()["id"]
    r = await client.patch(f"/api/v1/users/{me}", json={"role": "viewer"}, headers=_h(admin))
    assert r.status_code == 409
    await client.patch(f"/api/v1/users/{vid}", json={"role": "admin"}, headers=_h(admin))
    r = await client.patch(f"/api/v1/users/{me}", json={"role": "viewer"}, headers=_h(admin))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_demoted_users_api_token_loses_admin_scopes(client):
    admin, vid, _ = await _setup(client)
    await client.patch(f"/api/v1/users/{vid}", json={"role": "admin"}, headers=_h(admin))
    vtok = await _login(client, "vic")
    r = await client.post("/api/v1/me/tokens", json={"name": "t", "scopes": ["users:read"]}, headers=_h(vtok))
    assert r.status_code == 201, r.text
    api = r.json()["token"]
    assert (await client.get("/api/v1/users", headers=_h(api))).status_code == 200
    await client.patch(f"/api/v1/users/{vid}", json={"role": "viewer"}, headers=_h(admin))
    assert (await client.get("/api/v1/users", headers=_h(api))).status_code == 403


@pytest.mark.asyncio
async def test_create_user_with_role(client):
    await client.post("/api/v1/users/register", json={"username": "admin", "password": "testpass123"})
    admin = await _login(client, "admin")
    r = await client.post(
        "/api/v1/users/create", json={"username": "boss", "password": "testpass123", "role": "admin"}, headers=_h(admin),
    )
    assert r.status_code == 201 and r.json()["role"] == "admin" and r.json()["auth_provider"] == "local"


# ── group mapping ───────────────────────────────────────────────────────────


async def _oidc(db, sub, groups, **claims):
    return await oidc.get_or_create_user(
        db, {"sub": sub, "preferred_username": sub, "groups": groups, **claims},
    )


@pytest.mark.asyncio
async def test_oidc_mapping_promotes_demotes(db_session, rt_set):
    rt_set(oidc_admin_groups=["Akashic-Admins"], oidc_groups_format="name")
    db_session.add(User(username="root", password_hash="x", role="admin"))
    await db_session.commit()
    u = await _oidc(db_session, "alice", ["akashic-admins"])
    assert u.role == "admin" and u.role_pinned is False
    u = await _oidc(db_session, "alice", ["other"])
    assert u.role == "viewer"
    ev = (await db_session.execute(select(AuditEvent).where(AuditEvent.event_type == "user_role_changed"))).scalars().all()
    assert {e.payload["via"] for e in ev} == {"oidc_groups"} and len(ev) == 2


@pytest.mark.asyncio
async def test_oidc_empty_setting_is_noop(db_session, rt_set):
    rt_set(oidc_groups_format="name")
    u = await _oidc(db_session, "bob", ["Akashic-Admins"])
    assert u.role == "viewer"


@pytest.mark.asyncio
async def test_oidc_pinned_user_untouched(db_session, rt_set):
    rt_set(oidc_admin_groups=["adm"], oidc_groups_format="name")
    db_session.add(User(username="root", password_hash="x", role="admin"))
    u = await _oidc(db_session, "carol", ["adm"])
    u.role, u.role_pinned = "viewer", True
    await db_session.commit()
    u = await _oidc(db_session, "carol", ["adm"])
    assert u.role == "viewer"


@pytest.mark.asyncio
async def test_oidc_dn_and_sid_matching(db_session, rt_set):
    rt_set(oidc_admin_groups=["CN=Admins,DC=x", "S-1-5-21-1-512"], oidc_groups_format="dn")
    db_session.add(User(username="root", password_hash="x", role="admin"))
    await db_session.commit()
    assert (await _oidc(db_session, "d1", ["cn=admins,dc=x"])).role == "admin"
    rt_set(oidc_admin_groups=["S-1-5-21-1-512"], oidc_groups_format="sid")
    assert (await _oidc(db_session, "d2", ["S-1-5-21-1-512"])).role == "admin"


@pytest.mark.asyncio
async def test_sync_never_removes_last_admin(db_session, rt_set):
    rt_set(oidc_admin_groups=["adm"], oidc_groups_format="name")
    u = await _oidc(db_session, "solo", ["adm"])
    assert u.role == "admin"
    u = await _oidc(db_session, "solo", [])
    assert u.role == "admin"


@pytest.mark.asyncio
async def test_ldap_mapping(db_session, rt_set):
    rt_set(ldap_admin_groups=["cn=admins,dc=x"])
    db_session.add(User(username="root", password_hash="x", role="admin"))
    await db_session.commit()
    info = {"dn": "uid=l,dc=x", "uid": "l", "groups": ["CN=Admins,DC=x"]}
    u = await ldap_auth.get_or_create_user(db_session, info)
    assert u.role == "admin"
    u = await ldap_auth.get_or_create_user(db_session, {**info, "groups": []})
    assert u.role == "viewer"
    ev = (await db_session.execute(select(AuditEvent).where(AuditEvent.event_type == "user_role_changed"))).scalars().all()
    assert {e.payload["via"] for e in ev} == {"ldap_groups"}


@pytest.mark.asyncio
async def test_ldap_dn_spacing_and_case_insensitive(db_session, rt_set):
    rt_set(ldap_admin_groups=["CN=Admins, OU=Groups,DC=x"])
    db_session.add(User(username="root", password_hash="x", role="admin"))
    await db_session.commit()
    info = {"dn": "uid=s,dc=x", "uid": "s", "groups": ["cn=admins,ou=groups, dc=X"]}
    assert (await ldap_auth.get_or_create_user(db_session, info)).role == "admin"


@pytest.mark.asyncio
async def test_sync_reloads_stale_pin(db_session, setup_db, rt_set):
    """A pin committed by another session after this one loaded the user wins."""
    rt_set(oidc_admin_groups=["adm"], oidc_groups_format="name")
    db_session.add(User(username="root", password_hash="x", role="admin"))
    u = await _oidc(db_session, "stale", ["adm"])
    assert u.role == "admin"
    async with setup_db() as other:
        row = (await other.execute(select(User).where(User.id == u.id))).scalar_one()
        row.role, row.role_pinned = "admin", True
        await other.commit()
    u = await _oidc(db_session, "stale", [])
    assert u.role == "admin"


@pytest.mark.asyncio
async def test_role_change_and_audit_commit_together(client, db_session):
    admin, vid, _ = await _setup(client)
    await client.patch(f"/api/v1/users/{vid}", json={"role": "admin"}, headers=_h(admin))
    ev = (await db_session.execute(select(AuditEvent).where(AuditEvent.event_type == "user_role_changed"))).scalars().all()
    assert len(ev) == 1
