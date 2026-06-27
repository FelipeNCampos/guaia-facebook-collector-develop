import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.routes import router
from app.services.bootstrap import bootstrap_platform


def _load_allowed_hosts() -> list[str]:
    value = os.getenv("ALLOWED_HOSTS")
    if value is None:
        return ["*"]
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or ["*"]


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await bootstrap_platform(app)
        yield

    app = FastAPI(
        title="GUIAI Platform Foundation API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_load_allowed_hosts())
    app.include_router(router)
    return app


app = create_app()
