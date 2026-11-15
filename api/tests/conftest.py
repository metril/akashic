import asyncio
import os
from collections.abc import AsyncGenerator

import tempfile as _tempfile

# Per-worker secret key file so Settings never touches /data in tests.
os.environ["SECRET_KEY_FILE"] = os.path.join(
    _tempfile.mkdtemp(prefix="akashic-test-key-"), "secret.key",
)

# xdist isolation: each worker gets its own Postgres DB (<base>_gwN) and
# its own Redis logical DB (N+1 mod 16). Must run before akashic.config
# is imported so Settings picks up REDIS_URL. Serial runs keep the base
# DB name and Redis db 0.
_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
if _WORKER.startswith("gw") and _WORKER[2:].isnumeric():
    _redis_base = os.environ.get("REDIS_URL", "redis://localhost:6379/0").rsplit("/", 1)[0]
    os.environ["REDIS_URL"] = f"{_redis_base}/{(int(_WORKER[2:]) + 1) % 16}"

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from akashic.database import Base, get_db
from akashic.main import create_app
from akashic.models import *  # noqa: F401,F403

_BASE_DB_URL = os.environ.get(
    "TEST_DB_URL",
    "postgresql+asyncpg://akashic:changeme@localhost:5432/akashic_test",
)
_base_prefix, _base_name = _BASE_DB_URL.rsplit("/", 1)
_base_name, _, _query = _base_name.partition("?")
_db_name = f"{_base_name}_{_WORKER}" if _WORKER else _base_name
TEST_DB_URL = f"{_base_prefix}/{_db_name}" + (f"?{_query}" if _query else "")
# Re-export so tests that read TEST_DB_URL from the env directly (e.g.
# test_backfill_viewable) hit this worker's DB, not the shared base one.
os.environ["TEST_DB_URL"] = TEST_DB_URL


def _ensure_test_db() -> None:
    """Create this worker's database if missing (via the postgres maintenance DB)."""
    async def _go() -> None:
        admin = create_async_engine(
            f"{_base_prefix}/postgres", isolation_level="AUTOCOMMIT", poolclass=NullPool
        )
        try:
            async with admin.connect() as conn:
                exists = await conn.scalar(
                    text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": _db_name}
                )
                if not exists:
                    try:
                        await conn.execute(text(f'CREATE DATABASE "{_db_name}"'))
                    except ProgrammingError:
                        pass  # created concurrently
        finally:
            await admin.dispose()

    asyncio.run(_go())


_ensure_test_db()


@pytest.fixture(autouse=True)
def rt_set(monkeypatch):
    """Override runtime settings for one test: ``rt_set(cookie_secure=False)``.

    Also resets the in-memory snapshot to registry defaults per test.
    """
    from akashic.services import runtime_settings as _rs
    monkeypatch.setattr(_rs, "_values", {})

    def _set(**kw):
        for k, v in kw.items():
            assert k in _rs.REGISTRY, k
            _rs._values[k] = v
    return _set


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    engine = create_async_engine(TEST_DB_URL, echo=False, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    # Clear in-process rate-limit buckets between tests so the
    # claim / discover / oauth-callback limiters don't drift over the
    # test session and 429 unrelated tests. The shared rate_limit
    # service module tracks every Limiter instance so one call resets
    # all of them.
    try:
        from akashic.services.rate_limit import reset_all
        reset_all()
    except Exception:
        pass
    # Reset the shared Redis client too — scan_pubsub caches it on the
    # event loop pytest-asyncio just closed, so a stale reference will
    # raise "Event loop is closed" on the next publish in any test
    # that exercises probe_dispatch / pubsub paths. The aclose() call
    # is safe even if the cached client was bound to a dead loop.
    try:
        from akashic.services import scan_pubsub
        await scan_pubsub.aclose()
    except Exception:
        pass
    yield session_maker
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    try:
        from akashic.services import scan_pubsub
        await scan_pubsub.aclose()
    except Exception:
        pass


@pytest_asyncio.fixture
async def client(setup_db):
    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with setup_db() as session:
            yield session
    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session(setup_db):
    async with setup_db() as session:
        yield session


async def seed_scan(db_session, source_id, *, status: str = "pending"):
    """Pre-create a Scan row and return its UUID. Ingest tests need
    this since /api/v1/ingest/batch refuses unknown scan_ids
    (review A-I6) — every batch must reference a Scan row that the
    api created via /api/v1/scans/trigger or the lease path."""
    import uuid as _uuid

    from akashic.models.scan import Scan

    sid = _uuid.uuid4()
    db_session.add(
        Scan(id=sid, source_id=source_id, scan_type="incremental", status=status)
    )
    await db_session.commit()
    return sid


# ── Runtime check of documented responses ─────────────────────────────────
# Every HTTP response produced during the run (any test, any client) is
# recorded as (method, matched route template, status). At session end each
# observed status must be declared in that route's OpenAPI responses, so the
# existing tests double as evidence that the spec lists what routes return.
# Requests matching no route (unknown paths, middleware-level refusals) are
# not recorded.
_OBSERVED: set[tuple[str, str, str, int]] = set()


@pytest.fixture(scope="session", autouse=True)
def _documented_responses_audit():
    from fastapi import FastAPI

    original = FastAPI.__call__

    async def recording_call(self, scope, receive, send):
        status_box: list[int] = []

        async def recording_send(message):
            if message["type"] == "http.response.start":
                status_box.append(message["status"])
            await send(message)

        try:
            await original(self, scope, receive, recording_send)
        finally:
            route = scope.get("route")
            path = getattr(route, "path", None)
            if scope["type"] == "http" and path and status_box and scope["method"] not in ("HEAD", "OPTIONS"):
                _OBSERVED.add((scope["method"], path, scope["path"], status_box[0]))

    FastAPI.__call__ = recording_call
    yield
    FastAPI.__call__ = original

    spec = create_app().openapi()
    import re

    def template_for(method: str, route_path: str, concrete: str):
        """Full spec path for a matched route (its own `path` may lack the router prefix)."""
        for tpl, ops in spec["paths"].items():
            if method.lower() in ops and tpl.endswith(route_path):
                if re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", tpl), concrete):
                    return tpl
        return None

    problems = []
    matched = 0
    for method, route_path, concrete, status in sorted(_OBSERVED):
        tpl = template_for(method, route_path, concrete)
        if tpl is None:  # not part of the schema (include_in_schema=False)
            continue
        matched += 1
        responses = spec["paths"][tpl][method.lower()]["responses"]
        if str(status) not in responses and "default" not in responses:
            problems.append(f"{method} {tpl} -> {status} (documented: {sorted(responses)})")
    assert matched or not _OBSERVED, "runtime audit matched no observed responses to the spec"
    assert not problems, "responses observed in tests but not documented in OpenAPI:\n  " + "\n  ".join(sorted(set(problems)))
