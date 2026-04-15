from fastapi import FastAPI

from akashic.routers import users, ingest, sources, search, files, directories, duplicates, tags, analytics, purge, webhooks, scans


def create_app() -> FastAPI:
    app = FastAPI(title="Akashic", version="0.1.0")
    app.include_router(users.router)
    app.include_router(ingest.router)
    app.include_router(sources.router)
    app.include_router(search.router)
    app.include_router(files.router)
    app.include_router(directories.router)
    app.include_router(duplicates.router)
    app.include_router(tags.router)
    app.include_router(analytics.router)
    app.include_router(purge.router)
    app.include_router(webhooks.router)
    app.include_router(scans.router)
    return app


app = create_app()
