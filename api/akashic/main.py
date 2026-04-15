from fastapi import FastAPI

from akashic.routers import users


def create_app() -> FastAPI:
    app = FastAPI(title="Akashic", version="0.1.0")
    app.include_router(users.router)
    return app


app = create_app()
