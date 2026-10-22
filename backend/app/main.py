"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, documents, health, integrations, media, tenants, webhooks
from app.core.config import get_settings
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    worker = None
    if s.embedded_worker:
        from app.workers.runner import Worker

        worker = Worker()
        worker.start()
    log.info("startup", extra={"env": s.app_env, "embedded_worker": s.embedded_worker})
    try:
        yield
    finally:
        if worker is not None:
            worker.stop()


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    app = FastAPI(
        title=s.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if s.app_env == "production" else "/docs",
        redoc_url=None,
        openapi_url=None if s.app_env == "production" else "/openapi.json",
    )
    install_exception_handlers(app)
    if s.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=s.cors_origin_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
        )
    app.add_middleware(RequestContextMiddleware, max_body_bytes=s.max_webhook_body_bytes)

    app.include_router(health.router)
    v1 = APIRouter(prefix="/api/v1")
    for r in (
        auth.router,
        tenants.router,
        documents.router,
        integrations.router,
        webhooks.router,
        media.router,
    ):
        v1.include_router(r)
    app.include_router(v1)
    return app


app = create_app()
