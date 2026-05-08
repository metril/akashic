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
