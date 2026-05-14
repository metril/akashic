"""Ingest tokens cannot be replayed against admin endpoints (review A-C1).

The scanner agent gets a 24h JWT scoped to the ingest audience. The
audience boundary is the only thing keeping a compromised scanner
host from pivoting to admin endpoints (POST /api/users/create,
DELETE /api/sources/*, etc.) — these tests guard that boundary."""
import uuid

import pytest

from akashic.auth.jwt import (
    create_access_token,
    create_ingest_token,
    decode_access_token,
    decode_ingest_token,
)
from akashic.models.user import User


def test_ingest_token_rejected_by_access_decoder():
    """A token minted with create_ingest_token must NOT decode as an
    access token — that's the whole point of the audience split."""
    tok = create_ingest_token("00000000-0000-0000-0000-000000000000")
    assert decode_access_token(tok) is None
    payload = decode_ingest_token(tok)
    assert payload is not None
    assert payload["aud"] == "akashic-ingest"


def test_access_token_rejected_by_ingest_decoder():
    """And vice-versa — a regular access token can't satisfy the
    ingest dependency."""
    tok = create_access_token({"sub": "00000000-0000-0000-0000-000000000000"})
    assert decode_ingest_token(tok) is None


def test_ingest_token_carries_scanner_id_claim_when_minted_with_one():
    """v0.28.2 — `_mint_ingest_jwt` at lease time embeds the leasing
    scanner.id as a `scanner_id` claim so scan-progress endpoints can
    attribute heartbeat / log / stderr rows to the right scanner
    without trusting a client-supplied header."""
    user_id = "00000000-0000-0000-0000-000000000000"
    scanner_id = "11111111-1111-1111-1111-111111111111"
    tok = create_ingest_token(user_id, scanner_id=scanner_id)
    payload = decode_ingest_token(tok)
    assert payload is not None
    assert payload["scanner_id"] == scanner_id


def test_ingest_token_without_scanner_id_decodes_cleanly():
    """Tokens minted without a scanner_id (legacy path) must still
    decode — the claim is optional, not required."""
    tok = create_ingest_token("00000000-0000-0000-0000-000000000000")
    payload = decode_ingest_token(tok)
    assert payload is not None
    assert "scanner_id" not in payload


@pytest.mark.asyncio
async def test_ingest_token_cannot_call_admin_endpoint(client, db_session):
    """End-to-end: an ingest-scoped token presented to /api/users/me
    (admin/user endpoint) returns 401."""
    user = User(
        id=uuid.uuid4(), username="ingester", email="i@e",
        password_hash="x", role="admin",
    )
    db_session.add(user)
    await db_session.commit()

    ingest_tok = create_ingest_token(str(user.id))
    r = await client.get(
        "/api/users/me",
        headers={"Authorization": f"Bearer {ingest_tok}"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_access_token_cannot_call_ingest(client, db_session):
    """Symmetric: a regular user token can't reach /api/ingest/batch.
    The access-token-rejected path 401s before scan_id resolution
    runs, so a fresh UUID is fine here."""
    user = User(
        id=uuid.uuid4(), username="useraccess", email="u@e",
        password_hash="x", role="admin",
    )
    db_session.add(user)
    await db_session.commit()

    access_tok = create_access_token({"sub": str(user.id)})
    payload = {
        "source_id": str(uuid.uuid4()),
        "scan_id": str(uuid.uuid4()),  # OK — 401 fires before scan lookup
        "is_final": False,
        "entries": [],
    }
    r = await client.post(
        "/api/ingest/batch", json=payload,
        headers={"Authorization": f"Bearer {access_tok}"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_access_token_cannot_call_scan_progress(client, db_session):
    """v0.27.1 regression: scan-progress POSTs (heartbeat / log /
    stderr) are sibling endpoints of /api/ingest/batch — they accept
    the ingest-audience JWT minted at lease time and must reject
    plain access tokens. Pre-fix all three required get_current_user,
    so the scanner's ingest token 401'd silently and Live Log
    showed "Waiting for output…" forever."""
    user = User(
        id=uuid.uuid4(), username="progressuser", email="p@e",
        password_hash="x", role="admin",
    )
    db_session.add(user)
    await db_session.commit()
    access_tok = create_access_token({"sub": str(user.id)})

    scan_id = uuid.uuid4()
    headers = {"Authorization": f"Bearer {access_tok}"}
    # Each endpoint should 401 on the access-audience token before any
    # body validation runs.
    r = await client.post(
        f"/api/scans/{scan_id}/heartbeat",
        json={"files_found": 0}, headers=headers,
    )
    assert r.status_code == 401, f"heartbeat: {r.status_code} {r.text}"
    r = await client.post(
        f"/api/scans/{scan_id}/log",
        json={"lines": []}, headers=headers,
    )
    assert r.status_code == 401, f"log: {r.status_code} {r.text}"
    r = await client.post(
        f"/api/scans/{scan_id}/stderr",
        json={"chunks": []}, headers=headers,
    )
    assert r.status_code == 401, f"stderr: {r.status_code} {r.text}"


@pytest.mark.asyncio
async def test_ingest_token_can_call_scan_progress(client, db_session):
    """The ingest token IS valid for scan-progress POSTs — that's the
    happy path. With an empty body the endpoints short-circuit before
    they need a real scan, but the auth dep must accept the token."""
    user = User(
        id=uuid.uuid4(), username="progressingest", email="pi@e",
        password_hash="x", role="admin",
    )
    db_session.add(user)
    await db_session.commit()
    ingest_tok = create_ingest_token(str(user.id))

    scan_id = uuid.uuid4()
    headers = {"Authorization": f"Bearer {ingest_tok}"}
    # log + stderr return 204 on empty bodies (post_log_batch /
    # post_stderr_batch return early). heartbeat will 404 because
    # scan_id doesn't exist — that's the auth-passed signal we want.
    r = await client.post(
        f"/api/scans/{scan_id}/log",
        json={"lines": []}, headers=headers,
    )
    assert r.status_code == 204, f"log: {r.status_code} {r.text}"
    r = await client.post(
        f"/api/scans/{scan_id}/stderr",
        json={"chunks": []}, headers=headers,
    )
    assert r.status_code == 204, f"stderr: {r.status_code} {r.text}"
    r = await client.post(
        f"/api/scans/{scan_id}/heartbeat",
        json={"files_found": 0}, headers=headers,
    )
    # 404 = auth passed, scan lookup failed — the signal we want.
    assert r.status_code == 404, f"heartbeat: {r.status_code} {r.text}"
