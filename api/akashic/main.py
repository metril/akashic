import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from starlette.middleware.base import BaseHTTPMiddleware

from akashic.routers import users, ingest, sources, source_test, search, entries, entry_content, browse, duplicates, tags, analytics, purge, webhooks, scans, scan_progress, scan_websocket, auth, effective_perms, identities, admin_audit, group_resolution, principals, access, dashboard, storage_explorer, scanners, scanner_discovery, server_settings
from akashic.services import metrics as metrics_svc

logger = logging.getLogger(__name__)


# Slow-request observability + Prometheus instrumentation, both
# served by one middleware (Phase 6 + Phase 10 of v0.4.3). Every
# request gets observed for the metrics histogram; only requests
# beyond _REQUEST_SLOW_MS additionally hit the slow-log.
_REQUEST_SLOW_MS = 250
# Don't instrument the metrics endpoint itself — would self-emit
# every scrape, and /health is called constantly enough to dwarf
# real api traffic in the histogram.
_INSTRUMENT_SKIP_PATHS = frozenset({"/metrics", "/health"})


class _TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path_template = (
            request.scope.get("route").path
            if request.scope.get("route") is not None
            else request.url.path
        )
        if path_template in _INSTRUMENT_SKIP_PATHS:
            return await call_next(request)
        t0 = time.perf_counter()
        response = await call_next(request)
        dur_s = time.perf_counter() - t0
        # Record metrics first — slow log is just diagnostic on top.
        metrics_svc.observe_http_request(
            request.method, path_template, response.status_code, dur_s,
        )
        if dur_s * 1000 >= _REQUEST_SLOW_MS:
            logger.warning(
                "slow request: %s %s → %s in %.0fms",
                request.method, path_template,
                response.status_code, dur_s * 1000,
            )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    # Import all models so Alembic's `target_metadata` (env.py) sees them.
    from akashic import models  # noqa: F401

    from akashic.database import ensure_schema
    await ensure_schema()

    # First-boot bootstrap: seed `discovery_enabled` from env if the
    # row doesn't exist yet. Runtime UI PATCHes win after that.
    from akashic.config import settings
    from akashic.database import async_session
    from akashic.services.server_settings import (
        KEY_DISCOVERY_ENABLED, seed_from_env_if_missing,
    )
    if settings.scanner_discovery_enabled is not None:
        try:
            async with async_session() as session:
                await seed_from_env_if_missing(
                    session, KEY_DISCOVERY_ENABLED,
                    bool(settings.scanner_discovery_enabled),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("discovery setting seed failed: %s", exc)

    try:
        from akashic.services.search import ensure_index
        await ensure_index()
        logger.info("Meilisearch index initialized")
    except Exception as e:
        logger.warning("Meilisearch not available at startup: %s", e)

    from akashic.scheduler import start_scheduler
    start_scheduler()
    logger.info("Scan scheduler started")

    yield

    # Shutdown
    from akashic.scheduler import stop_scheduler
    stop_scheduler()
    from akashic.services import scan_pubsub
    await scan_pubsub.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="Akashic", version="0.1.0", lifespan=lifespan)

    # Slow-request observability + Prometheus instrumentation.
    # Order matters: install before the routers so it wraps every
    # endpoint. `/health` and `/metrics` are excluded inside the
    # middleware (see _INSTRUMENT_SKIP_PATHS).
    app.add_middleware(_TimingMiddleware)

    # Liveness probe for compose healthchecks. Deliberately doesn't
    # touch the DB / Meili / Redis — we want this to flip green the
    # moment uvicorn is accepting connections, so dependent services
    # (the scanner) can wait via `depends_on: condition: service_healthy`
    # instead of racing the api on startup. A more thorough readiness
    # probe could check the downstream stack, but that belongs on a
    # separate /ready endpoint if and when we need it.
    @app.get("/health", include_in_schema=False)
    def health():
        return {"ok": True}

    # Prometheus scrape endpoint. Renders the global registry of
    # akashic_* metrics; meant to be polled by a Prometheus server
    # (see compose's `metrics` profile).
    @app.get("/metrics", include_in_schema=False)
    def metrics():
        body, content_type = metrics_svc.render_metrics()
        return Response(content=body, media_type=content_type)

    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(ingest.router)
    app.include_router(sources.router)
    app.include_router(source_test.router)
    app.include_router(search.router)
    app.include_router(entries.router)
    app.include_router(entry_content.router)
    app.include_router(browse.router)
    app.include_router(duplicates.router)
    app.include_router(tags.router)
    app.include_router(analytics.router)
    app.include_router(purge.router)
    app.include_router(webhooks.router)
    app.include_router(scans.router)
    app.include_router(scan_progress.router)
    app.include_router(scan_websocket.router)
    app.include_router(effective_perms.router)
    app.include_router(identities.router)
    app.include_router(admin_audit.router)
    app.include_router(group_resolution.router)
    app.include_router(principals.router)
    app.include_router(access.router)
    app.include_router(dashboard.router)
    app.include_router(storage_explorer.router)
    app.include_router(scanners.router)
    app.include_router(scanner_discovery.router)
    app.include_router(server_settings.router)
    return app


app = create_app()
