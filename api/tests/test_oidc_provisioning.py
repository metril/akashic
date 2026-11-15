"""OIDC claim → FsBinding bridge.

Covers:
  - extract_identities across the four strategies (claim, ldap_fallback,
    name_match, auto)
  - objectSid binary-blob decoding (Authentik's default form)
  - principal_domain matching: SIDs whose prefix matches go to bindings,
    others go to fs_unbound_identities
  - sync_fs_bindings_from_claims end-to-end with seeded sources
  - GET /api/v1/identities/unbound (admin-only)

The actual OIDC code-flow (discovery, JWKS, token decode) is separate
and hits external services; this file isolates the post-token logic
that's pure-Python.
"""
import base64
import uuid as uuid_module

import pytest
from sqlalchemy import select

from akashic.auth.oidc_provisioning import (
    ExtractedIdentity,
    _decode_object_sid,
    _source_matches,
    extract_identities,
    sync_fs_bindings_from_claims,
)
from akashic.models.fs_person import FsBinding, FsPerson
from akashic.models.fs_unbound_identity import FsUnboundIdentity
from akashic.models.source import Source
from akashic.models.user import User


def _settings(strategy: str = "auto", **kw):
    """Snapshot-like object with registry defaults + overrides."""
    from types import SimpleNamespace
    from akashic.services.runtime_settings import REGISTRY
    vals = {k: sp.default for k, sp in REGISTRY.items()}
    vals["oidc_strategy"] = strategy
    vals.update(kw)
    return SimpleNamespace(**vals)


# ── _decode_object_sid ─────────────────────────────────────────────────────


def test_decode_string_sid_passes_through():
    assert _decode_object_sid("S-1-5-21-1234567890-987654321-1001") == "S-1-5-21-1234567890-987654321-1001"


def test_decode_lowercase_sid_normalises_to_upper():
    # Raw input: "s-1-5-...". Output should be canonical caps.
    assert _decode_object_sid("s-1-5-32-544") == "S-1-5-32-544"


def test_decode_base64_binary_sid():
    # Build a known SID: revision=1, ident_auth=5, sub-authorities = [21, 1, 2, 3, 1001]
    # Layout: rev(1) sa_count(1) ident_auth(6 BE) sa1(4 LE) ... saN(4 LE)
    blob = bytes([
        1,        # revision
        5,        # sub-authority count
        0, 0, 0, 0, 0, 5,  # ident-authority big-endian (= 5)
    ])
    for sa in [21, 1, 2, 3, 1001]:
        blob += sa.to_bytes(4, "little")
    encoded = base64.b64encode(blob).decode("ascii")
    assert _decode_object_sid(encoded) == "S-1-5-21-1-2-3-1001"


def test_decode_invalid_returns_none():
    assert _decode_object_sid(None) is None
    assert _decode_object_sid("") is None
    assert _decode_object_sid("not a sid or base64") is None
    # Valid base64 but wrong length for a SID.
    assert _decode_object_sid(base64.b64encode(b"hello").decode("ascii")) is None


# ── extract_identities — claim strategy ────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_strategy_emits_sid_and_groups():
    settings = _settings(strategy="claim")
    claims = {
        "preferred_username": "alice",
        "onprem_sid": "S-1-5-21-DOMAIN-1001",
        "groups": ["S-1-5-21-DOMAIN-1500", "S-1-5-21-DOMAIN-1501"],
    }
    out = await extract_identities(claims, settings)
    assert len(out) == 1
    assert out[0].identity_type == "sid"
    assert out[0].identifier == "S-1-5-21-DOMAIN-1001"
    assert out[0].groups == ["S-1-5-21-DOMAIN-1500", "S-1-5-21-DOMAIN-1501"]
    assert out[0].confidence == "claim"


@pytest.mark.asyncio
async def test_claim_strategy_with_no_sid_returns_empty():
    settings = _settings(strategy="claim")
    claims = {"preferred_username": "alice"}
    assert await extract_identities(claims, settings) == []


@pytest.mark.asyncio
async def test_claim_strategy_attribute_nesting():
    """Authentik can put SIDs under attributes.<claim> rather than at the
    token's top level. The extractor handles both."""
    settings = _settings(strategy="claim")
    claims = {
        "preferred_username": "alice",
        "attributes": {"onprem_sid": "S-1-5-21-X-1001"},
        "groups": [],
    }
    out = await extract_identities(claims, settings)
    assert out[0].identifier == "S-1-5-21-X-1001"


@pytest.mark.asyncio
async def test_claim_strategy_includes_posix_uid_when_present():
    settings = _settings(strategy="claim")
    claims = {
        "preferred_username": "bob",
        "uidNumber": 1001,
        "groups": ["engineers"],
    }
    out = await extract_identities(claims, settings)
    # SID claim missing → only the posix_uid identity.
    assert len(out) == 1
    assert out[0].identity_type == "posix_uid"
    assert out[0].identifier == "1001"
    assert out[0].groups == ["engineers"]


@pytest.mark.asyncio
async def test_claim_strategy_groups_format_name():
    settings = _settings(strategy="claim", oidc_groups_format="name")
    claims = {
        "preferred_username": "carol",
        "onprem_sid": "S-1-5-21-X-1001",
        "groups": ["Engineering", "Admins"],
    }
    out = await extract_identities(claims, settings)
    assert out[0].groups == ["Engineering", "Admins"]


# ── extract_identities — name_match strategy ──────────────────────────────


@pytest.mark.asyncio
async def test_name_match_strategy_emits_synthetic_posix_binding():
    settings = _settings(strategy="name_match", oidc_groups_format="name")
    claims = {
        "preferred_username": "dave",
        "groups": ["finance", "operations"],
    }
    out = await extract_identities(claims, settings)
    assert len(out) == 1
    assert out[0].identity_type == "posix_uid"
    assert out[0].identifier == "-1"
    assert out[0].groups == ["finance", "operations"]
    assert out[0].confidence == "name"


@pytest.mark.asyncio
async def test_name_match_strategy_returns_empty_with_no_groups():
    settings = _settings(strategy="name_match")
    assert await extract_identities({"preferred_username": "x"}, settings) == []


# ── extract_identities — auto strategy ────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_prefers_claim_when_sid_present():
    settings = _settings(strategy="auto")
    claims = {
        "preferred_username": "eve",
        "onprem_sid": "S-1-5-21-X-1001",
        "groups": ["S-1-5-21-X-1500"],
    }
    out = await extract_identities(claims, settings)
    assert out[0].confidence == "claim"


@pytest.mark.asyncio
async def test_auto_falls_through_to_name_match_when_no_sid():
    settings = _settings(strategy="auto", oidc_groups_format="name")
    claims = {"preferred_username": "frank", "groups": ["devs"]}
    out = await extract_identities(claims, settings)
    assert out[0].confidence == "name"


# ── extract_identities — ldap_fallback strategy ───────────────────────────


@pytest.mark.asyncio
async def test_ldap_fallback_uses_lookup_result_when_claim_missing(monkeypatch):
    """In `auto` mode, when the claim path returns nothing, ldap_fallback
    runs and produces an `ldap`-confidence identity."""
    from akashic.auth import oidc_provisioning

    oidc_provisioning.reset_ldap_breaker()

    async def _fake_run_in_executor(executor, fn, *args, **kw):
        return oidc_provisioning._LdapLookup(
            sid="S-1-5-21-FROM-LDAP-1001",
            groups=["S-1-5-21-FROM-LDAP-2001"],
        )

    settings = _settings(
        strategy="auto",
        ldap_server="ldap://example.com",
        ldap_bind_dn="cn=svc,dc=example,dc=com",
        ldap_bind_password="x",
    )
    monkeypatch.setattr(
        "asyncio.get_running_loop",
        lambda: type("L", (), {"run_in_executor": _fake_run_in_executor})(),
    )

    out = await extract_identities({"email": "alice@example.com"}, settings)
    assert len(out) == 1
    assert out[0].identifier == "S-1-5-21-FROM-LDAP-1001"
    assert out[0].confidence == "ldap"
    assert out[0].groups == ["S-1-5-21-FROM-LDAP-2001"]


@pytest.mark.asyncio
async def test_ldap_fallback_falls_through_to_name_match_when_lookup_fails(monkeypatch):
    from akashic.auth import oidc_provisioning

    oidc_provisioning.reset_ldap_breaker()

    async def _failing_run_in_executor(executor, fn, *args, **kw):
        raise RuntimeError("AD is down")

    settings = _settings(
        strategy="auto",
        ldap_server="ldap://example.com",
        ldap_bind_dn="cn=svc,dc=example,dc=com",
        ldap_bind_password="x",
        oidc_groups_format="name",
    )
    monkeypatch.setattr(
        "asyncio.get_running_loop",
        lambda: type("L", (), {"run_in_executor": _failing_run_in_executor})(),
    )

    out = await extract_identities(
        {"preferred_username": "g", "groups": ["engineers"]}, settings,
    )
    # Falls through to name_match, not 401-ing.
    assert len(out) == 1
    assert out[0].confidence == "name"


@pytest.mark.asyncio
async def test_ldap_fallback_circuit_breaker_opens_after_failures(monkeypatch):
    from akashic.auth import oidc_provisioning

    oidc_provisioning.reset_ldap_breaker()
    call_count = {"n": 0}

    async def _failing(executor, fn, *args, **kw):
        call_count["n"] += 1
        raise RuntimeError("AD down")

    settings = _settings(
        strategy="ldap_fallback",
        ldap_server="ldap://example.com",
        ldap_bind_dn="cn=svc,dc=example,dc=com",
        ldap_bind_password="x",
        oidc_groups_format="name",
    )
    monkeypatch.setattr(
        "asyncio.get_running_loop",
        lambda: type("L", (), {"run_in_executor": _failing})(),
    )

    # Drive 3 consecutive failures — threshold is 3.
    for _ in range(3):
        await extract_identities(
            {"preferred_username": "g", "groups": []}, settings,
        )
    assert call_count["n"] == 3
    # Breaker is now open. The 4th call must skip the lookup entirely.
    await extract_identities({"preferred_username": "g", "groups": []}, settings)
    assert call_count["n"] == 3  # unchanged


@pytest.mark.asyncio
async def test_ldap_fallback_no_op_when_ldap_not_configured(monkeypatch):
    """If ldap_server / ldap_bind_dn are blank, the lookup short-circuits
    to None without attempting to import python-ldap. This is the path
    deployments take when they pin to ldap_fallback but haven't filled
    in the LDAP config yet."""
    from akashic.auth import oidc_provisioning

    oidc_provisioning.reset_ldap_breaker()
    settings = _settings(strategy="ldap_fallback", ldap_server="", ldap_bind_dn="")

    out = await extract_identities(
        {"preferred_username": "g", "groups": ["engineers"]}, settings,
    )
    # Falls through to name_match.
    assert out[0].confidence == "name"


def test_decode_binary_sid_round_trip():
    """The binary-SID decoder used during LDAP lookups produces the same
    canonical S-1-5-… form as the base64 path in oidc_provisioning."""
    from akashic.auth.oidc_provisioning import _decode_binary_sid

    blob = bytes([1, 5, 0, 0, 0, 0, 0, 5])
    for sa in [21, 1, 2, 3, 1001]:
        blob += sa.to_bytes(4, "little")
    assert _decode_binary_sid(blob) == "S-1-5-21-1-2-3-1001"


def test_decode_binary_sid_rejects_truncated():
    from akashic.auth.oidc_provisioning import _decode_binary_sid
    assert _decode_binary_sid(b"") is None
    assert _decode_binary_sid(b"\x01\x05\x00") is None
    # Length doesn't match sub-auth count.
    assert _decode_binary_sid(b"\x01\x05" + b"\x00" * 6 + b"\x00" * 12) is None


# ── _source_matches ────────────────────────────────────────────────────────


def _src(name="s", type_="smb", principal_domain=None):
    cfg = {"path": "/x"}
    if principal_domain is not None:
        cfg["principal_domain"] = principal_domain
    return Source(name=name, type=type_, connection_config=cfg, status="online")


def test_source_matches_sid_by_principal_domain_prefix():
    src = _src(principal_domain="S-1-5-21-1234567890-987654321")
    ident = ExtractedIdentity(
        identity_type="sid",
        identifier="S-1-5-21-1234567890-987654321-1001",
        groups=[], confidence="claim",
    )
    assert _source_matches(src, ident) is True


def test_source_matches_sid_rejects_different_domain():
    src = _src(principal_domain="S-1-5-21-AAA")
    ident = ExtractedIdentity(
        identity_type="sid",
        identifier="S-1-5-21-BBB-1001",
        groups=[], confidence="claim",
    )
    assert _source_matches(src, ident) is False


def test_source_without_principal_domain_binds_smb_only():
    """A legacy source with no principal_domain matches all SID identities
    if it's an SMB source, on the theory that the operator can fix the
    bindings later. Other source types reject."""
    smb = _src(type_="smb", principal_domain=None)
    nfs = _src(type_="nfs", principal_domain=None)
    ident = ExtractedIdentity(
        identity_type="sid", identifier="S-1-5-21-X-1001",
        groups=[], confidence="claim",
    )
    assert _source_matches(smb, ident) is True
    assert _source_matches(nfs, ident) is False


def test_source_matches_posix_uid_to_local_nfs():
    ident = ExtractedIdentity(
        identity_type="posix_uid", identifier="-1", groups=["g"], confidence="name",
    )
    assert _source_matches(_src(type_="local"), ident) is True
    assert _source_matches(_src(type_="nfs"), ident) is True
    assert _source_matches(_src(type_="smb"), ident) is False
    assert _source_matches(_src(type_="s3"), ident) is False


# ── sync_fs_bindings_from_claims (end-to-end) ─────────────────────────────


async def _seed_user(db, username="alice"):
    u = User(
        username=username,
        email=f"{username}@example.com",
        password_hash="x",  # not used for OIDC users; nullable column
        role="viewer",
        auth_provider="oidc",
        external_id=f"sub-{username}",
    )
    db.add(u)
    await db.flush()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_sync_creates_person_and_binding_for_matching_source(db_session):
    settings = _settings(strategy="claim")
    user = await _seed_user(db_session)
    db_session.add(_src(name="finance-share", type_="smb",
                       principal_domain="S-1-5-21-DOMAIN"))
    await db_session.commit()

    claims = {
        "preferred_username": user.username,
        "onprem_sid": "S-1-5-21-DOMAIN-1001",
        "groups": ["S-1-5-21-DOMAIN-1500"],
    }
    await sync_fs_bindings_from_claims(db_session, user, claims, settings)
    await db_session.commit()

    persons = (await db_session.execute(
        select(FsPerson).where(FsPerson.user_id == user.id)
    )).scalars().all()
    assert len(persons) == 1
    assert persons[0].label.startswith("OIDC: ")

    bindings = (await db_session.execute(
        select(FsBinding).where(FsBinding.fs_person_id == persons[0].id)
    )).scalars().all()
    assert len(bindings) == 1
    b = bindings[0]
    assert b.identity_type == "sid"
    assert b.identifier == "S-1-5-21-DOMAIN-1001"
    assert b.groups == ["S-1-5-21-DOMAIN-1500"]
    # Phase 2b: groups_source captures the strategy that produced
    # the binding (claim / ldap / name), not the catch-all "auto".
    assert b.groups_source == "claim"

    # Nothing went unbound.
    unbound = (await db_session.execute(
        select(FsUnboundIdentity).where(FsUnboundIdentity.user_id == user.id)
    )).scalars().all()
    assert unbound == []


@pytest.mark.asyncio
async def test_sync_records_unbound_when_no_source_matches(db_session):
    settings = _settings(strategy="claim")
    user = await _seed_user(db_session)
    # Source whose principal_domain doesn't match the claim's SID.
    db_session.add(_src(name="other", type_="smb",
                       principal_domain="S-1-5-21-OTHER"))
    await db_session.commit()

    claims = {
        "preferred_username": user.username,
        "onprem_sid": "S-1-5-21-DOMAIN-1001",
        "groups": [],
    }
    await sync_fs_bindings_from_claims(db_session, user, claims, settings)
    await db_session.commit()

    bindings = (await db_session.execute(
        select(FsBinding)
    )).scalars().all()
    assert bindings == []

    unbound = (await db_session.execute(
        select(FsUnboundIdentity).where(FsUnboundIdentity.user_id == user.id)
    )).scalars().all()
    assert len(unbound) == 1
    assert unbound[0].identifier == "S-1-5-21-DOMAIN-1001"
    assert unbound[0].confidence == "claim"


@pytest.mark.asyncio
async def test_sync_is_idempotent_on_re_login(db_session):
    """Logging in twice with the same claims should not duplicate FsPerson
    or FsBinding rows."""
    settings = _settings(strategy="claim")
    user = await _seed_user(db_session)
    db_session.add(_src(name="x", type_="smb",
                       principal_domain="S-1-5-21-DOMAIN"))
    await db_session.commit()

    claims = {
        "preferred_username": user.username,
        "onprem_sid": "S-1-5-21-DOMAIN-1001",
        "groups": ["S-1-5-21-DOMAIN-1500"],
    }
    await sync_fs_bindings_from_claims(db_session, user, claims, settings)
    await sync_fs_bindings_from_claims(db_session, user, claims, settings)
    await db_session.commit()

    persons = (await db_session.execute(
        select(FsPerson).where(FsPerson.user_id == user.id)
    )).scalars().all()
    assert len(persons) == 1
    bindings = (await db_session.execute(select(FsBinding))).scalars().all()
    assert len(bindings) == 1


@pytest.mark.asyncio
async def test_sync_updates_existing_binding_when_groups_change(db_session):
    settings = _settings(strategy="claim")
    user = await _seed_user(db_session)
    db_session.add(_src(name="x", type_="smb",
                       principal_domain="S-1-5-21-DOMAIN"))
    await db_session.commit()

    # First login.
    await sync_fs_bindings_from_claims(db_session, user, {
        "preferred_username": user.username,
        "onprem_sid": "S-1-5-21-DOMAIN-1001",
        "groups": ["S-1-5-21-DOMAIN-OLD"],
    }, settings)
    await db_session.commit()

    # Second login with a new group set.
    await sync_fs_bindings_from_claims(db_session, user, {
        "preferred_username": user.username,
        "onprem_sid": "S-1-5-21-DOMAIN-1001",
        "groups": ["S-1-5-21-DOMAIN-NEW1", "S-1-5-21-DOMAIN-NEW2"],
    }, settings)
    await db_session.commit()

    bindings = (await db_session.execute(select(FsBinding))).scalars().all()
    assert len(bindings) == 1
    assert set(bindings[0].groups) == {"S-1-5-21-DOMAIN-NEW1", "S-1-5-21-DOMAIN-NEW2"}


# ── /api/v1/identities/unbound endpoint ─────────────────────────────────────


async def _register_login(client, username="admin", password="testpass123"):
    await client.post("/api/v1/users/register", json={"username": username, "password": password})
    login = await client.post(
        "/api/v1/users/login",
        json={"username": username, "password": password},
    )
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_unbound_endpoint_self_scope_for_non_admin(client, db_session):
    """A non-admin calling /unbound with no user_id sees only THEIR own
    unbound rows. A non-admin passing somebody else's user_id is 403'd."""
    admin_token = await _register_login(client)
    # Make a regular user.
    await client.post(
        "/api/v1/users/create",
        json={"username": "regular", "password": "testpass123"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    login = await client.post(
        "/api/v1/users/login",
        json={"username": "regular", "password": "testpass123"},
    )
    user_token = login.json()["access_token"]

    # Default scope is self; empty list when nothing seeded.
    r = await client.get(
        "/api/v1/identities/unbound",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert r.status_code == 200
    assert r.json() == []

    # Trying to peek at someone else's unbound is 403.
    r = await client.get(
        f"/api/v1/identities/unbound?user_id={uuid_module.uuid4()}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_unbound_endpoint_lists_unbound_rows(client, db_session):
    admin_token = await _register_login(client)
    user = await _seed_user(db_session, username="alice")

    db_session.add(FsUnboundIdentity(
        user_id=user.id,
        identity_type="sid",
        identifier="S-1-5-21-X-1001",
        confidence="claim",
        groups=["S-1-5-21-X-1500"],
    ))
    await db_session.commit()

    r = await client.get(
        "/api/v1/identities/unbound",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["identifier"] == "S-1-5-21-X-1001"
    assert body[0]["confidence"] == "claim"
    assert body[0]["groups"] == ["S-1-5-21-X-1500"]


@pytest.mark.asyncio
async def test_unbound_endpoint_filters_by_user(client, db_session):
    admin_token = await _register_login(client)
    alice = await _seed_user(db_session, username="alice")
    bob = await _seed_user(db_session, username="bob")

    db_session.add_all([
        FsUnboundIdentity(
            user_id=alice.id, identity_type="sid",
            identifier="S-1-5-21-X-A", confidence="claim", groups=[],
        ),
        FsUnboundIdentity(
            user_id=bob.id, identity_type="sid",
            identifier="S-1-5-21-X-B", confidence="claim", groups=[],
        ),
    ])
    await db_session.commit()

    r = await client.get(
        f"/api/v1/identities/unbound?user_id={alice.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["identifier"] == "S-1-5-21-X-A"


# ── smb_lookup strategy ───────────────────────────────────────────────────

import json as _json
import subprocess as _subprocess
from types import SimpleNamespace as _NS

from akashic.auth import oidc_provisioning as _op
from akashic.services import group_resolver as _gr

_DOM = "S-1-5-21-111-222-333"


def _smb_src(name, host="dc1", username="svc", **extra):
    cfg = {"host": host, "port": 445, "username": username, "password": "pw", "path": "/x"}
    cfg.update(extra)
    return Source(name=name, type="smb", connection_config=cfg, status="online")


def _proc(rc=0, out="", err=""):
    return _NS(returncode=rc, stdout=out, stderr=err)


def _fake_scanner(monkeypatch, name_proc, calls=None):
    """name_proc(argv) -> proc for resolve-name; resolve-groups returns 2 SIDs."""
    monkeypatch.setattr(_gr, "_scanner_binary_path", lambda: "/bin/fake")

    def run(argv, password="", timeout=30):
        if calls is not None:
            calls.append(argv)
        if argv[1] == "resolve-name":
            return name_proc(argv)
        return _proc(0, _json.dumps({"groups": [f"{_DOM}-513", "junk"], "source": "samr"}))

    monkeypatch.setattr(_gr, "_run_scanner", run)


def _found(argv):
    return _proc(0, _json.dumps({"sid": f"{_DOM}-1105", "domain": "CORP",
                                 "domain_sid": _DOM, "kind": "user"}))


@pytest.fixture(autouse=True)
def _reset_smb_breaker():
    _op.reset_smb_breaker()
    yield
    _op.reset_smb_breaker()


@pytest.mark.asyncio
async def test_smb_lookup_found_dedupes_endpoints(db_session, monkeypatch):
    db_session.add_all([_smb_src("a"), _smb_src("b"), _smb_src("c", host="dc2")])
    await db_session.commit()
    calls = []
    _fake_scanner(monkeypatch, _found, calls)
    out = await extract_identities(
        {"preferred_username": "alice", "groups": ["staff"]},
        _settings(strategy="smb_lookup"), db_session,
    )
    assert len(out) == 1
    i = out[0]
    assert (i.identity_type, i.identifier, i.confidence) == ("sid", f"{_DOM}-1105", "smb")
    assert i.groups == [f"{_DOM}-513"]
    assert i.domain_sid == _DOM
    assert len(i.endpoints) == 2  # a/b deduped, dc2 attributed
    assert sum(1 for c in calls if c[1] == "resolve-name") == 2
    assert sum(1 for c in calls if c[1] == "resolve-groups") == 1
    assert "alice" in calls[0]


@pytest.mark.asyncio
async def test_smb_lookup_email_local_part_fallback(db_session, monkeypatch):
    db_session.add(_smb_src("a"))
    await db_session.commit()
    calls = []
    _fake_scanner(monkeypatch, _found, calls)
    out = await extract_identities({"email": "bob@example.com"},
                                   _settings(strategy="smb_lookup"), db_session)
    assert out and calls[0][calls[0].index("--name") + 1] == "bob"


@pytest.mark.asyncio
async def test_smb_lookup_not_found_falls_to_name_match(db_session, monkeypatch):
    db_session.add(_smb_src("a"))
    await db_session.commit()
    _fake_scanner(monkeypatch, lambda a: _proc(2, "", "not found"))
    out = await extract_identities(
        {"preferred_username": "alice", "groups": ["staff"]},
        _settings(strategy="smb_lookup"), db_session)
    assert [(i.identifier, i.confidence) for i in out] == [("-1", "name")]
    assert not any(b.failures for b in _op._smb_breakers.values())  # not-found is not an outage


@pytest.mark.asyncio
async def test_smb_lookup_per_endpoint_breaker_isolated(db_session, monkeypatch):
    db_session.add_all([_smb_src("a", host="bad"), _smb_src("b", host="good")])
    await db_session.commit()
    calls = []

    def name(argv):
        if "bad" in argv:
            raise _subprocess.TimeoutExpired(argv, 1)
        return _found(argv)

    _fake_scanner(monkeypatch, name, calls)
    s = _settings(strategy="smb_lookup")
    for _ in range(3):
        _op._smb_name_cache.clear()  # force re-query
        out = await extract_identities({"preferred_username": "alice"}, s, db_session)
        assert len(out) == 1  # good endpoint still works
    assert _op._smb_breakers[("bad", 445, "svc")].is_open()
    assert not _op._smb_breakers[("good", 445, "svc")].is_open()
    _op._smb_name_cache.clear()
    calls.clear()
    await extract_identities({"preferred_username": "alice"}, s, db_session)
    assert not any("bad" in c for c in calls)  # skipped while open


@pytest.mark.asyncio
async def test_smb_lookup_cache_hit_and_not_found_cached(db_session, monkeypatch):
    db_session.add(_smb_src("a"))
    await db_session.commit()
    calls = []
    _fake_scanner(monkeypatch, _found, calls)
    s = _settings(strategy="smb_lookup")
    await extract_identities({"preferred_username": "alice"}, s, db_session)
    n = len(calls)
    await extract_identities({"preferred_username": "alice"}, s, db_session)
    assert len(calls) == n
    calls.clear()
    _fake_scanner(monkeypatch, lambda a: _proc(2), calls)
    await extract_identities({"preferred_username": "nobody"}, s, db_session)
    await extract_identities({"preferred_username": "nobody"}, s, db_session)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,extra", [
    ({"sid": "S-1-5-32-544", "domain_sid": "S-1-5-32", "kind": "user"}, {}),
    ({"sid": f"{_DOM}-512", "domain_sid": _DOM, "kind": "group"}, {}),
    ({"sid": f"{_DOM}-1105", "domain_sid": _DOM, "kind": "user"},
     {"principal_domain": "S-1-5-21-999"}),
])
async def test_smb_lookup_rejects_bad_results(db_session, monkeypatch, payload, extra):
    db_session.add(_smb_src("a", **extra))
    await db_session.commit()
    _fake_scanner(monkeypatch, lambda a: _proc(0, _json.dumps(payload)))
    out = await extract_identities({"preferred_username": "alice"},
                                   _settings(strategy="smb_lookup"), db_session)
    assert out == []


@pytest.mark.asyncio
async def test_smb_lookup_passes_domain_and_runs_concurrently(db_session, monkeypatch):
    import time as _t
    db_session.add_all([_smb_src(n, host=f"dc{n}", domain="CORP") for n in "abc"])
    await db_session.commit()
    calls = []

    def slow(argv):
        _t.sleep(0.3)
        return _found(argv)

    _fake_scanner(monkeypatch, slow, calls)
    t0 = _t.monotonic()
    out = await extract_identities({"preferred_username": "alice"},
                                   _settings(strategy="smb_lookup"), db_session)
    assert _t.monotonic() - t0 < 0.8  # 3 x 0.3s serial would be 0.9s+
    assert len(out) == 1 and len(out[0].endpoints) == 3
    assert all(c[c.index("--domain") + 1] == "CORP" for c in calls if c[1] == "resolve-name")


@pytest.mark.asyncio
async def test_smb_lookup_overall_deadline(db_session, monkeypatch):
    import time as _t
    db_session.add(_smb_src("a"))
    await db_session.commit()
    monkeypatch.setattr(_op, "_SMB_DEADLINE_S", 0.2)

    def hang(argv):
        _t.sleep(1)
        return _found(argv)

    _fake_scanner(monkeypatch, hang)
    t0 = _t.monotonic()
    out = await extract_identities({"preferred_username": "alice"},
                                   _settings(strategy="smb_lookup"), db_session)
    assert out == [] and _t.monotonic() - t0 < 0.8
    assert _op._smb_breakers[("dc1", 445, "svc")].failures


@pytest.mark.asyncio
async def test_auto_order_claim_beats_smb_and_smb_beats_name(db_session, monkeypatch):
    db_session.add(_smb_src("a"))
    await db_session.commit()
    calls = []
    _fake_scanner(monkeypatch, _found, calls)
    s = _settings(strategy="auto")
    out = await extract_identities(
        {"preferred_username": "alice", "onprem_sid": "S-1-5-21-9-9-9-1"}, s, db_session)
    assert out[0].confidence == "claim" and not calls
    out = await extract_identities(
        {"preferred_username": "alice", "groups": ["staff"]}, s, db_session)
    assert out[0].confidence == "smb"


@pytest.mark.asyncio
async def test_auto_without_db_skips_smb():
    out = await extract_identities(
        {"preferred_username": "alice", "groups": ["staff"]}, _settings(strategy="auto"))
    assert out[0].confidence == "name"


def test_source_matches_smb_identity_by_endpoint():
    a, b = _smb_src("a"), _smb_src("b", host="other")
    ident = ExtractedIdentity(
        identity_type="sid", identifier=f"{_DOM}-1105", groups=[],
        confidence="smb", domain_sid=_DOM,
        endpoints=frozenset({("dc1", 445, "svc")}),
    )
    assert _source_matches(a, ident) is True
    assert _source_matches(b, ident) is False
    # principal_domain still takes precedence
    c = _smb_src("c", host="other", principal_domain=_DOM)
    assert _source_matches(c, ident) is True


@pytest.mark.asyncio
async def test_sync_smb_binds_matching_endpoint_and_clears_stale_name_rows(db_session, monkeypatch):
    user = await _seed_user(db_session, "carol")
    s1 = _smb_src("a")
    s2 = _smb_src("b", host="elsewhere")
    db_session.add_all([s1, s2])
    db_session.add(FsUnboundIdentity(
        user_id=user.id, identity_type="posix_uid", identifier="-1",
        confidence="name", groups=["staff"],
    ))
    await db_session.commit()
    _fake_scanner(monkeypatch, lambda a: _found(a) if "dc1" in a else _proc(2))
    await sync_fs_bindings_from_claims(
        db_session, user, {"preferred_username": "carol", "groups": ["staff"]},
        _settings(strategy="auto"))
    await db_session.commit()
    bindings = (await db_session.execute(select(FsBinding))).scalars().all()
    assert [(b.source_id, b.groups_source) for b in bindings] == [(s1.id, "smb")]
    left = (await db_session.execute(
        select(FsUnboundIdentity).where(FsUnboundIdentity.user_id == user.id)
    )).scalars().all()
    assert left == []
