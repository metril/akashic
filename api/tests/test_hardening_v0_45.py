"""v0.45.0 hardening: gzip bomb cap, download filename, terminal-scan
ingest guard, batch cap, login rate limit, refresh-rotation lock."""
import gzip
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from akashic.auth.jwt import create_ingest_token
from akashic.auth.passwords import hash_password
from akashic.main import _GzipRequestMiddleware
from akashic.models.source import Source
from akashic.models.user import User
from akashic.routers.entry_content import _content_disposition
from tests.conftest import seed_scan


def test_content_disposition_sanitises_and_encodes():
    h = _content_disposition('a"b\r\nc é.txt', True)
    assert h.startswith('attachment; filename="')
    ascii_part = h.split('filename="')[1].split('"')[0]
    assert ascii_part == "a_b__c _.txt" and ascii_part.isascii()
    assert "\r" not in h and "\n" not in h
    assert "filename*=UTF-8''a%22b%0D%0Ac%20%C3%A9.txt" in h


@pytest.mark.asyncio
async def test_gzip_bomb_rejected_413(monkeypatch):
    monkeypatch.setattr(_GzipRequestMiddleware, "_MAX_DECOMPRESSED_BYTES", 1024)
    sent = []
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    async def send(m):
        sent.append(m)

    def rx(body):
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        return receive

    mw = _GzipRequestMiddleware(app)
    scope = {"type": "http", "headers": [(b"content-encoding", b"gzip")]}
    await mw(scope, rx(gzip.compress(b"\0" * 1_000_000)), send)
    assert sent[0]["status"] == 413 and not called

    sent.clear()
    await mw(scope, rx(b"not gzip"), send)
    assert sent[0]["status"] == 400

    sent.clear()
    await mw(scope, rx(gzip.compress(b"ok" * 10)), send)
    assert called


async def _post(client, token, source_id, scan_id, entries=()):
    return await client.post(
        "/api/ingest/batch",
        json={"source_id": str(source_id), "scan_id": str(scan_id),
              "is_final": False, "entries": list(entries)},
        headers={"Authorization": f"Bearer {token}"},
    )


async def _seed(db_session, state):
    user = User(id=uuid.uuid4(), username="u", email="u@e",
                password_hash="x", role="admin")
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add_all([user, source])
    await db_session.commit()
    scan_id = await seed_scan(db_session, source.id, status=state)
    return create_ingest_token(str(user.id)), source.id, scan_id


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["cancelled", "failed"])
async def test_ingest_rejects_cancelled_or_failed_scan(client, db_session, state):
    token, sid, scan_id = await _seed(db_session, state)
    assert (await _post(client, token, sid, scan_id)).status_code == 409


@pytest.mark.asyncio
async def test_ingest_completed_scan_is_noop_200(client, db_session):
    token, sid, scan_id = await _seed(db_session, "completed")
    r = await _post(client, token, sid, scan_id)
    assert r.status_code == 200 and r.json()["files_processed"] == 0


@pytest.mark.asyncio
async def test_ingest_oversize_batch_413(client, db_session):
    token, sid, scan_id = await _seed(db_session, "running")
    r = await _post(client, token, sid, scan_id, [{"path": "/a", "name": "a"}] * 10_001)
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_login_rate_limited(client, db_session):
    db_session.add(User(id=uuid.uuid4(), username="rl", email="rl@e",
                        password_hash=hash_password("hunter2hunter2"), role="user"))
    await db_session.commit()
    codes = []
    for _ in range(12):
        r = await client.post("/api/users/login",
                              json={"username": "rl", "password": "wrong"})
        codes.append(r.status_code)
    assert codes[0] == 401 and codes[-1] == 429
    # A different username from the same IP has its own bucket.
    r = await client.post("/api/users/login",
                          json={"username": "other", "password": "wrong"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_rotate_selects_for_update(db_session):
    from akashic.auth import refresh as refresh_service
    captured = []
    real = db_session.execute

    async def spy(stmt, *a, **k):
        captured.append(stmt)
        return await real(stmt, *a, **k)

    db_session.execute = spy
    assert await refresh_service.rotate("nope", db_session) is None
    sel = str(captured[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sel
