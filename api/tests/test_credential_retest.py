"""v0.29.5 — auto-retest reachability after CredentialProfile update.

Covers `services.credential_retest.retest_sources_for_profile`:
  - Enumerates affected sources via direct + via-host attachment.
  - Caps fan-out at 50 sources per call.
  - Dispatches via probe_dispatch.dispatch_remote per affected source.
  - No-op when no sources reference the profile.
"""
from __future__ import annotations

import uuid

import pytest

from akashic.models.credential_profile import CredentialProfile
from akashic.models.host import Host
from akashic.models.scanner import Scanner
from akashic.models.source import Source
from akashic.services import credential_retest
from akashic.services.scanner_keys import generate_keypair


async def _seed_profile(db, *, name="cp", type_="smb"):
    p = CredentialProfile(
        id=uuid.uuid4(),
        name=f"{name}-{uuid.uuid4().hex[:6]}",
        type=type_,
        credentials={"username": "u", "password": "p"},
    )
    db.add(p)
    await db.commit()
    return p


async def _seed_scanner(db, *, name="scn"):
    kp = generate_keypair()
    from datetime import datetime, timezone
    s = Scanner(
        id=uuid.uuid4(),
        name=f"{name}-{uuid.uuid4().hex[:6]}",
        public_key_pem=kp.public_pem,
        key_fingerprint=kp.fingerprint,
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(s)
    await db.commit()
    return s


async def _seed_source(db, *, profile_id=None, host_id=None, type_="smb"):
    s = Source(
        id=uuid.uuid4(),
        name=f"src-{uuid.uuid4().hex[:6]}",
        type=type_,
        connection_config={"host": "h", "share": "s"},
        credential_profile_id=profile_id,
        host_id=host_id,
    )
    db.add(s)
    await db.commit()
    return s


async def _seed_host(db, *, profile_id=None, type_="smb"):
    h = Host(
        id=uuid.uuid4(),
        name=f"h-{uuid.uuid4().hex[:6]}",
        type=type_,
        connection_config={"host": "h"},
        credential_profile_id=profile_id,
    )
    db.add(h)
    await db.commit()
    return h


@pytest.mark.asyncio
async def test_no_affected_sources_returns_zero(db_session):
    p = await _seed_profile(db_session)
    n = await credential_retest.retest_sources_for_profile(
        db_session, profile_id=p.id, user_id=uuid.uuid4(),
    )
    assert n == 0


@pytest.mark.asyncio
async def test_direct_attachment_enumerated(db_session):
    p = await _seed_profile(db_session)
    src1 = await _seed_source(db_session, profile_id=p.id)
    src2 = await _seed_source(db_session, profile_id=p.id)
    ids = await credential_retest._affected_source_ids(db_session, p.id)
    assert set(ids) == {src1.id, src2.id}


@pytest.mark.asyncio
async def test_via_host_attachment_enumerated(db_session):
    p = await _seed_profile(db_session)
    h = await _seed_host(db_session, profile_id=p.id)
    src = await _seed_source(db_session, host_id=h.id)
    ids = await credential_retest._affected_source_ids(db_session, p.id)
    assert ids == [src.id]


@pytest.mark.asyncio
async def test_fanout_caps_at_50(db_session, monkeypatch):
    """When the profile has 51 attached sources, only 50 are
    dispatched against. The cap is observable via the call-count
    on a stubbed dispatch_remote."""
    p = await _seed_profile(db_session)
    await _seed_scanner(db_session)
    sources = []
    for _ in range(51):
        sources.append(await _seed_source(db_session, profile_id=p.id))

    calls: list[uuid.UUID] = []

    async def stub_dispatch(*, db, source, scanner_ids, timeout_s, triggered_by):
        calls.append(source.id)
        return {}

    from akashic.services import probe_dispatch
    monkeypatch.setattr(probe_dispatch, "dispatch_remote", stub_dispatch)

    await credential_retest.retest_sources_for_profile(
        db_session, profile_id=p.id, user_id=uuid.uuid4(),
    )
    assert len(calls) == 50, f"expected 50 dispatches, got {len(calls)}"


@pytest.mark.asyncio
async def test_dispatch_invoked_per_affected_source(db_session, monkeypatch):
    p = await _seed_profile(db_session)
    await _seed_scanner(db_session)
    src1 = await _seed_source(db_session, profile_id=p.id)
    src2 = await _seed_source(db_session, profile_id=p.id)

    calls: list[uuid.UUID] = []

    async def stub_dispatch(*, db, source, scanner_ids, timeout_s, triggered_by):
        calls.append(source.id)
        return {}

    from akashic.services import probe_dispatch
    monkeypatch.setattr(probe_dispatch, "dispatch_remote", stub_dispatch)

    n = await credential_retest.retest_sources_for_profile(
        db_session, profile_id=p.id, user_id=uuid.uuid4(),
    )
    assert n > 0
    assert set(calls) == {src1.id, src2.id}
