"""GET/PATCH /api/v1/server-settings + cache invalidation."""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.principals import as_principal
from akashic.auth.dependencies import get_principal
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.user import User
from akashic.services.server_settings import (
    KEY_DISCOVERY_ENABLED, get_setting, invalidate_all,
)


@pytest_asyncio.fixture(autouse=True)
def _reset_cache():
    invalidate_all()
    yield
    invalidate_all()


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
    app.dependency_overrides[get_principal] = as_principal(_override_get_current_user)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_admin_can_set_and_read_setting(setup_db, admin_user):
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.patch(
            f"/api/v1/server-settings/{KEY_DISCOVERY_ENABLED}",
            json={"value": True},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["key"] == KEY_DISCOVERY_ENABLED
        assert body["value"] is True

        listed = (await ac.get("/api/v1/server-settings")).json()
        flat = {x["key"]: x for g in listed["groups"] for x in g["settings"]}
        assert flat[KEY_DISCOVERY_ENABLED]["value"] is True


@pytest.mark.asyncio
async def test_non_admin_cannot_patch(setup_db):
    async with setup_db() as session:
        viewer = User(
            id=uuid.uuid4(), username="v", email="v@b.c",
            password_hash="x", role="viewer",
        )
        session.add(viewer)
        await session.commit()
    async with _admin_client(setup_db, viewer) as ac:
        r = await ac.patch(
            f"/api/v1/server-settings/{KEY_DISCOVERY_ENABLED}",
            json={"value": True},
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_unknown_setting_returns_404(setup_db, admin_user):
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.get("/api/v1/server-settings/no_such_key")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cache_busts_after_patch(setup_db, admin_user):
    """First read primes cache with the old value; PATCH must
    invalidate so the next read returns the new value, not the
    stale cached one."""
    async with setup_db() as session:
        # Default-when-missing: function returns the supplied default.
        v = await get_setting(session, KEY_DISCOVERY_ENABLED, default=False)
        assert v is False

    async with _admin_client(setup_db, admin_user) as ac:
        await ac.patch(
            f"/api/v1/server-settings/{KEY_DISCOVERY_ENABLED}",
            json={"value": True},
        )

    async with setup_db() as session:
        v = await get_setting(session, KEY_DISCOVERY_ENABLED, default=False)
        assert v is True


# ── registry / masking / snapshot / env import / secret key ──

from akashic.services import runtime_settings as rs  # noqa: E402


@pytest.mark.asyncio
async def test_patch_validates_by_registry_type(setup_db, admin_user):
    async with _admin_client(setup_db, admin_user) as ac:
        assert (await ac.patch("/api/v1/server-settings/slow_query_ms", json={"value": "abc"})).status_code == 422
        assert (await ac.patch("/api/v1/server-settings/oidc_strategy", json={"value": "nope"})).status_code == 422
        assert (await ac.patch("/api/v1/server-settings/cors_allow_origins", json={"value": "x"})).status_code == 422
        assert (await ac.patch("/api/v1/server-settings/bogus", json={"value": 1})).status_code == 404
        r = await ac.patch("/api/v1/server-settings/slow_query_ms", json={"value": "250"})
        assert r.status_code == 200 and r.json()["value"] == 250


@pytest.mark.asyncio
async def test_bulk_patch_atomic_and_snapshot_reloads(setup_db, admin_user):
    async with _admin_client(setup_db, admin_user) as ac:
        bad = await ac.patch("/api/v1/server-settings", json={"values": {"cookie_secure": False, "slow_query_ms": "x"}})
        assert bad.status_code == 422
        d = bad.json()["detail"]
        assert isinstance(d, list) and d[0]["loc"][-1] == "slow_query_ms" and d[0]["msg"]
        assert rs.rt().cookie_secure is True
        ok = await ac.patch("/api/v1/server-settings", json={"values": {
            "cookie_secure": False, "cors_allow_origins": ["https://a.example"],
        }})
        assert ok.status_code == 200
    assert rs.rt().cookie_secure is False
    assert rs.rt().cors_allow_origins == ["https://a.example"]


@pytest.mark.asyncio
async def test_secret_masked_encrypted_and_semantics(setup_db, admin_user):
    from sqlalchemy import select
    from akashic.models.server_setting import ServerSetting
    async with _admin_client(setup_db, admin_user) as ac:
        await ac.patch("/api/v1/server-settings/oidc_client_secret", json={"value": "s3cret"})
        r = await ac.get("/api/v1/server-settings/oidc_client_secret")
        assert r.json()["value"] == rs.MASK and r.json()["set"] is True
        listed = (await ac.get("/api/v1/server-settings")).json()
        flat = {x["key"]: x for g in listed["groups"] for x in g["settings"]}
        assert flat["oidc_client_secret"]["set"] is True
        assert "s3cret" not in str(listed)
        async with setup_db() as s:
            raw = (await s.execute(select(ServerSetting.value).where(ServerSetting.key == "oidc_client_secret"))).scalar_one()
        assert raw != "s3cret"
        assert rs.rt().oidc_client_secret == "s3cret"
        # mask / null = unchanged
        await ac.patch("/api/v1/server-settings/oidc_client_secret", json={"value": rs.MASK})
        await ac.patch("/api/v1/server-settings/oidc_client_secret", json={"value": None})
        assert rs.rt().oidc_client_secret == "s3cret"
        # explicit "" clears
        r = await ac.patch("/api/v1/server-settings/oidc_client_secret", json={"value": ""})
        assert r.json()["set"] is False
        assert rs.rt().oidc_client_secret == ""


def test_snapshot_defaults_and_override(rt_set):
    assert rs.rt().access_token_expire_minutes == 60
    rt_set(access_token_expire_minutes=5)
    assert rs.rt().access_token_expire_minutes == 5


@pytest.mark.asyncio
async def test_legacy_env_import_once(setup_db, monkeypatch):
    from akashic.services.server_settings import import_legacy_env, set_setting
    monkeypatch.setenv("SLOW_QUERY_MS", "321")
    monkeypatch.setenv("AKASHIC_FETCH_CONCURRENCY", "4")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "envsecret")
    async with setup_db() as s:
        imported = await import_legacy_env(s)
        assert {"slow_query_ms", "fetch_concurrency", "oidc_client_secret"} <= set(imported)
        await rs.load_snapshot(s)
    assert rs.rt().slow_query_ms == 321 and rs.rt().fetch_concurrency == 4
    assert rs.rt().oidc_client_secret == "envsecret"
    # UI edit wins; env is not re-imported
    async with setup_db() as s:
        await set_setting(s, "slow_query_ms", 7)
        await s.commit()
        assert await import_legacy_env(s) == []
        await rs.load_snapshot(s)
    assert rs.rt().slow_query_ms == 7


def test_secret_key_generated_and_persisted(tmp_path, monkeypatch):
    import stat
    from akashic.config import load_or_create_secret_key
    monkeypatch.delenv("SECRET_KEY", raising=False)
    p = tmp_path / "d" / "secret.key"
    k1 = load_or_create_secret_key(str(p))
    assert len(k1) >= 64 and stat.S_IMODE(p.stat().st_mode) == 0o600
    assert load_or_create_secret_key(str(p)) == k1


def test_secret_key_legacy_env_persisted(tmp_path, monkeypatch):
    from akashic.config import load_or_create_secret_key
    monkeypatch.setenv("SECRET_KEY", "my-legacy-key-value")
    p = tmp_path / "secret.key"
    assert load_or_create_secret_key(str(p)) == "my-legacy-key-value"
    monkeypatch.delenv("SECRET_KEY")
    assert load_or_create_secret_key(str(p)) == "my-legacy-key-value"


def test_secret_key_legacy_default_ignored(tmp_path, monkeypatch):
    from akashic.config import load_or_create_secret_key
    monkeypatch.setenv("SECRET_KEY", "changeme-secret-key")
    assert load_or_create_secret_key(str(tmp_path / "k")) != "changeme-secret-key"


def test_settings_persists_legacy_secret_key_env(tmp_path, monkeypatch):
    """SECRET_KEY env must flow through load_or_create_secret_key (and be
    persisted), not be read straight into the field."""
    from akashic.config import Settings
    p = tmp_path / "secret.key"
    monkeypatch.setenv("SECRET_KEY", "legacy-env-key-value")
    s = Settings(secret_key_file=str(p))
    assert s.secret_key == "legacy-env-key-value"
    assert p.read_text().strip() == "legacy-env-key-value"
    monkeypatch.delenv("SECRET_KEY")
    assert Settings(secret_key_file=str(p)).secret_key == "legacy-env-key-value"


@pytest.mark.asyncio
async def test_legacy_env_import_idempotent_under_race(setup_db, db_session, monkeypatch):
    import asyncio
    from sqlalchemy.ext.asyncio import async_sessionmaker
    async_session = async_sessionmaker(db_session.bind, expire_on_commit=False)
    from akashic.services.server_settings import import_legacy_env
    monkeypatch.setenv("TIKA_URL", "http://example:9998")

    async def _run():
        async with async_session() as s:
            return await import_legacy_env(s)

    results = await asyncio.gather(_run(), _run(), _run())
    assert sum("tika_url" in r for r in results) == 1


@pytest.mark.asyncio
async def test_invalidator_reconnects_after_error(monkeypatch):
    import asyncio
    from akashic.services import scan_pubsub, server_settings as ss
    calls = {"n": 0}

    async def _sub():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("redis down")
        yield {"kind": "setting.changed", "key": "x"}
        await asyncio.sleep(3600)

    reloads = {"n": 0}

    async def _reload():
        reloads["n"] += 1

    monkeypatch.setattr(scan_pubsub, "subscribe_scanners", _sub)
    monkeypatch.setattr(rs, "reload_snapshot", _reload)
    monkeypatch.setattr(ss, "_RECONNECT_BACKOFF_MAX", 0.01)
    real_sleep = asyncio.sleep

    async def _fast(d):
        await real_sleep(0.01 if d >= 1 else d)
    monkeypatch.setattr(ss.asyncio, "sleep", _fast)
    task = asyncio.create_task(ss.listen_for_invalidations(reload_interval=0.02))
    await real_sleep(0.3)
    task.cancel()
    assert calls["n"] >= 2
    assert reloads["n"] >= 2


@pytest.mark.asyncio
async def test_oidc_cache_invalidated_when_oidc_setting_changes(setup_db, db_session):
    from akashic.auth import oidc
    from akashic.models.server_setting import ServerSetting
    await rs.load_snapshot(db_session)
    oidc._discovery_cache = {"stale": True}
    await rs.load_snapshot(db_session)  # unchanged -> kept
    assert oidc._discovery_cache == {"stale": True}
    db_session.add(ServerSetting(key="oidc_client_id", value="new-id"))
    await db_session.commit()
    await rs.load_snapshot(db_session)
    assert oidc._discovery_cache is None
