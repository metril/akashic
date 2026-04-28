import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from akashic.routers import users, ingest, sources, search, entries, browse, duplicates, tags, analytics, purge, webhooks, scans, auth, effective_perms

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    from akashic.database import Base, engine
    # Import all models so create_all sees them.
    from akashic import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema ensured")

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


def create_app() -> FastAPI:
    app = FastAPI(title="Akashic", version="0.1.0", lifespan=lifespan)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(ingest.router)
    app.include_router(sources.router)
    app.include_router(search.router)
    app.include_router(entries.router)
    app.include_router(browse.router)
    app.include_router(duplicates.router)
    app.include_router(tags.router)
    app.include_router(analytics.router)
    app.include_router(purge.router)
    app.include_router(webhooks.router)
    app.include_router(scans.router)
    app.include_router(effective_perms.router)
    return app


app = create_app()
