import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from akashic.routers import users, ingest, sources, source_test, search, entries, entry_content, browse, duplicates, tags, analytics, purge, webhooks, scans, scan_progress, scan_websocket, auth, effective_perms, identities, admin_audit, group_resolution, principals, access, dashboard, storage_explorer, scanners, scanner_discovery, server_settings

logger = logging.getLogger(__name__)


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
