import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from akashic.routers import users, ingest, sources, source_test, search, entries, entry_content, browse, duplicates, tags, analytics, purge, webhooks, scans, scan_progress, scan_websocket, auth, effective_perms, identities, admin_audit, group_resolution, principals, access, dashboard

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    # Import all models so Alembic's `target_metadata` (env.py) sees them.
    from akashic import models  # noqa: F401

    from akashic.database import ensure_schema
    await ensure_schema()

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
    return app


app = create_app()
