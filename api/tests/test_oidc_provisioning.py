"""OIDC claim → FsBinding bridge.

Covers:
  - extract_identities across the four strategies (claim, ldap_fallback,
    name_match, auto)
  - objectSid binary-blob decoding (Authentik's default form)
  - principal_domain matching: SIDs whose prefix matches go to bindings,
    others go to fs_unbound_identities
  - sync_fs_bindings_from_claims end-to-end with seeded sources
  - GET /api/identities/unbound (admin-only)

The actual OIDC code-flow (discovery, JWKS, token decode) is separate
and hits external services; this file isolates the post-token logic
that's pure-Python.
"""
import base64

import pytest
from sqlalchemy import select

from akashic.auth.oidc_provisioning import (
    ExtractedIdentity,
    _decode_object_sid,
    _source_matches,
    extract_identities,
    sync_fs_bindings_from_claims,
)
from akashic.config import Settings
from akashic.models.fs_person import FsBinding, FsPerson
from akashic.models.fs_unbound_identity import FsUnboundIdentity
from akashic.models.source import Source
from akashic.models.user import User


def _settings(strategy: str = "auto", **kw) -> Settings:
    """Build a Settings with overridden defaults for strategy testing.
    Keeps tests free of env-var setup."""
    base = Settings()
    base.oidc_strategy = strategy
    for k, v in kw.items():
        setattr(base, k, v)
    return base


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


def test_claim_strategy_emits_sid_and_groups():
    settings = _settings(strategy="claim")
    claims = {
        "preferred_username": "alice",
        "onprem_sid": "S-1-5-21-DOMAIN-1001",
        "groups": ["S-1-5-21-DOMAIN-1500", "S-1-5-21-DOMAIN-1501"],
    }
    out = extract_identities(claims, settings)
    assert len(out) == 1
    assert out[0].identity_type == "sid"
    assert out[0].identifier == "S-1-5-21-DOMAIN-1001"
    assert out[0].groups == ["S-1-5-21-DOMAIN-1500", "S-1-5-21-DOMAIN-1501"]
    assert out[0].confidence == "claim"


def test_claim_strategy_with_no_sid_returns_empty():
    settings = _settings(strategy="claim")
    claims = {"preferred_username": "alice"}
    assert extract_identities(claims, settings) == []


def test_claim_strategy_attribute_nesting():
    """Authentik can put SIDs under attributes.<claim> rather than at the
    token's top level. The extractor handles both."""
    settings = _settings(strategy="claim")
    claims = {
        "preferred_username": "alice",
        "attributes": {"onprem_sid": "S-1-5-21-X-1001"},
        "groups": [],
    }
    out = extract_identities(claims, settings)
    assert out[0].identifier == "S-1-5-21-X-1001"


def test_claim_strategy_includes_posix_uid_when_present():
    settings = _settings(strategy="claim")
    claims = {
        "preferred_username": "bob",
        "uidNumber": 1001,
        "groups": ["engineers"],
    }
    out = extract_identities(claims, settings)
    # SID claim missing → only the posix_uid identity.
    assert len(out) == 1
    assert out[0].identity_type == "posix_uid"
    assert out[0].identifier == "1001"
    assert out[0].groups == ["engineers"]


def test_claim_strategy_groups_format_name():
    settings = _settings(strategy="claim", oidc_groups_format="name")
    claims = {
        "preferred_username": "carol",
        "onprem_sid": "S-1-5-21-X-1001",
        "groups": ["Engineering", "Admins"],
    }
    out = extract_identities(claims, settings)
    assert out[0].groups == ["Engineering", "Admins"]


# ── extract_identities — name_match strategy ──────────────────────────────


def test_name_match_strategy_emits_synthetic_posix_binding():
    settings = _settings(strategy="name_match", oidc_groups_format="name")
    claims = {
        "preferred_username": "dave",
        "groups": ["finance", "operations"],
    }
    out = extract_identities(claims, settings)
    assert len(out) == 1
    assert out[0].identity_type == "posix_uid"
    assert out[0].identifier == "-1"
    assert out[0].groups == ["finance", "operations"]
    assert out[0].confidence == "name"


def test_name_match_strategy_returns_empty_with_no_groups():
    settings = _settings(strategy="name_match")
    assert extract_identities({"preferred_username": "x"}, settings) == []


# ── extract_identities — auto strategy ────────────────────────────────────


def test_auto_prefers_claim_when_sid_present():
    settings = _settings(strategy="auto")
    claims = {
        "preferred_username": "eve",
        "onprem_sid": "S-1-5-21-X-1001",
        "groups": ["S-1-5-21-X-1500"],
    }
    out = extract_identities(claims, settings)
    assert out[0].confidence == "claim"


def test_auto_falls_through_to_name_match_when_no_sid():
    settings = _settings(strategy="auto", oidc_groups_format="name")
    claims = {"preferred_username": "frank", "groups": ["devs"]}
    out = extract_identities(claims, settings)
    assert out[0].confidence == "name"


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


def test_source_matches_posix_uid_to_local_ssh_nfs():
    ident = ExtractedIdentity(
        identity_type="posix_uid", identifier="-1", groups=["g"], confidence="name",
    )
    assert _source_matches(_src(type_="local"), ident) is True
    assert _source_matches(_src(type_="ssh"), ident) is True
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
    assert b.groups_source == "auto"

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


# ── /api/identities/unbound endpoint ─────────────────────────────────────


async def _register_login(client, username="admin", password="testpass123"):
    await client.post("/api/users/register", json={"username": username, "password": password})
    login = await client.post(
        "/api/users/login",
        json={"username": username, "password": password},
    )
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_unbound_endpoint_admin_only(client, db_session):
    admin_token = await _register_login(client)
    # regular user
    await client.post(
        "/api/users/create",
        json={"username": "regular", "password": "testpass123", "role": "user"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    login = await client.post(
        "/api/users/login",
        json={"username": "regular", "password": "testpass123"},
    )
    user_token = login.json()["access_token"]

    r = await client.get(
        "/api/identities/unbound",
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
        "/api/identities/unbound",
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
        f"/api/identities/unbound?user_id={alice.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["identifier"] == "S-1-5-21-X-A"
