"""GET /api/sources returns the lean shape (v0.4.3+).

The list endpoint drops `connection_config`, `security_metadata`,
and `exclude_patterns` to keep the page-load payload small. Detail
panel must still get the full shape via GET /sources/{id}.

Also exercises the server-rendered `summary` field that replaces
the client-side computation for the SourceCard subtitle.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.credential_profile import CredentialProfile
from akashic.models.host import Host
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services.credential_crypto import encrypt_credentials


@pytest_asyncio.fixture
async def admin_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(), username="admin", email="a@b.c",
            password_hash="x", role="admin",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def _admin_client(setup_db, user: User) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_get_current_user():
        return user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_smb_source(setup_db) -> uuid.UUID:
    async with setup_db() as db:
        src = Source(
            id=uuid.uuid4(),
            name="rack-3",
            type="smb",
            connection_config={
                "host": "rack3.example.net",
                "username": "akashic",
                "share": "Public",
                "password": "super-secret-do-not-leak",
            },
        )
        db.add(src)
        await db.commit()
        return src.id


@pytest.mark.asyncio
async def test_list_endpoint_omits_heavy_fields(setup_db, admin_user):
    await _seed_smb_source(setup_db)
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.get("/api/sources")
    assert r.status_code == 200
    [body] = r.json()
    # Heavy fields gone:
    assert "connection_config" not in body
    assert "security_metadata" not in body
    assert "exclude_patterns" not in body
    # Lean fields present:
    assert body["name"] == "rack-3"
    assert body["type"] == "smb"
    # Server-rendered summary derived from the (now-omitted) config:
    assert body["summary"] == "\\\\rack3.example.net\\Public"


@pytest.mark.asyncio
async def test_detail_endpoint_returns_full_config(setup_db, admin_user):
    src_id = await _seed_smb_source(setup_db)
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.get(f"/api/sources/{src_id}")
    assert r.status_code == 200
    body = r.json()
    assert "connection_config" in body
    # Secrets are scrubbed at the schema layer.
    assert body["connection_config"]["password"] == "***"
    assert body["connection_config"]["host"] == "rack3.example.net"


@pytest.mark.asyncio
async def test_detail_host_carries_credential_profile_id(setup_db, admin_user):
    """v0.31.4 — a host-backed source inherits its credentials from the
    host's credential profile; the source's own `credential_profile_id`
    stays NULL by design. The inlined `host` on the source response must
    expose the host's `credential_profile_id` so the detail panel can
    show "inherited from host" honestly instead of "Inline credentials".
    """
    async with setup_db() as db:
        profile = CredentialProfile(
            id=uuid.uuid4(), name="nas-creds", type="smb",
            credentials_encrypted=encrypt_credentials(
                {"username": "svc", "password": "pw"}
            ),
        )
        db.add(profile)
        host = Host(
            id=uuid.uuid4(), name="nas-1", type="smb",
            connection_config={"host": "nas-1.example.net"},
            credential_profile_id=profile.id,
        )
        db.add(host)
        await db.flush()
        src = Source(
            id=uuid.uuid4(), name="nas-share", type="smb",
            connection_config={"share": "Public"},
            host_id=host.id,
        )
        db.add(src)
        await db.commit()
        src_id, profile_id = src.id, profile.id

    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.get(f"/api/sources/{src_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    # The source itself carries no profile — it inherits the host's.
    assert body["credential_profile_id"] is None
    assert body["host"] is not None
    assert body["host"]["credential_profile_id"] == str(profile_id)


@pytest.mark.asyncio
async def test_summary_renders_per_source_type(setup_db, admin_user):
    """Spot-check the server-side summary shapes for each source
    type so a future refactor doesn't silently break the cards."""
    async with setup_db() as db:
        for cfg, expect in [
            ({"path": "/srv/anime"}, "/srv/anime"),
            ({"host": "nas", "export_path": "/exports/movies"}, "nas:/exports/movies"),
            ({"host": "filer", "share": "Music"}, "\\\\filer\\Music"),
            ({"bucket": "backups", "region": "us-east-1"}, "backups (us-east-1)"),
        ]:
            kind = "local" if "path" in cfg else (
                "nfs" if "export_path" in cfg else (
                    "smb" if "share" in cfg else "s3"
                )
            )
            db.add(Source(
                id=uuid.uuid4(), name=f"{kind}-{uuid.uuid4().hex[:6]}",
                type=kind, connection_config=cfg,
            ))
        await db.commit()
    async with _admin_client(setup_db, admin_user) as ac:
        rows = (await ac.get("/api/sources")).json()
    # Map summary by type for a stable assertion.
    by_type = {r["type"]: r["summary"] for r in rows}
    assert by_type["local"] == "/srv/anime"
    assert by_type["nfs"] == "nas:/exports/movies"
    assert by_type["smb"] == "\\\\filer\\Music"
    assert by_type["s3"] == "backups (us-east-1)"
