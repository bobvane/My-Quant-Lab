"""FastAPI application factory and app instance."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routers import (
    ai as ai_router,
)
from app.api.routers import (
    assets as assets_router,
)
from app.api.routers import (
    backtests as backtests_router,
)
from app.api.routers import (
    health as health_router,
)
from app.api.routers import (
    importer as importer_router,
)
from app.api.routers import (
    market_data as market_data_router,
)
from app.api.routers import (
    paper as paper_router,
)
from app.api.routers import (
    research as research_router,
)
from app.api.routers import (
    settings as settings_router,
)
from app.api.routers import (
    signals as signals_router,
)
from app.api.routers import (
    strategies as strategies_router,
)
from app.core.config import settings
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)

DESCRIPTION = """
My Quant Lab — Personal Quantitative Research Laboratory.

**AI never owns numbers.** Every price, indicator, return, drawdown and Sharpe in
this API is computed by the deterministic quant engine. The AI layer only explains
pre-computed facts.

**No auto trading.** V1 exposes information, backtests, paper trading and signals.
There is deliberately no broker order endpoint.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("starting %s %s", settings.app_name, settings.app_version)
    yield
    logger.info("shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version=settings.app_version,
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):  # pragma: no cover
        logger.exception("unhandled error on %s", request.url.path)
        details: dict[str, object] = {"path": str(request.url.path)}
        if not settings.is_production:
            # Outside production the cause is returned to the caller so CI and
            # local debugging do not have to dig through container logs.
            details["exception"] = f"{type(exc).__name__}: {exc}"
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "internal server error",
                    "details": details,
                }
            },
        )

    for router in (
        health_router.router,
        assets_router.router,
        market_data_router.router,
        strategies_router.router,
        backtests_router.router,
        research_router.router,
        paper_router.router,
        signals_router.router,
        settings_router.router,
        importer_router.router,
        ai_router.router,
    ):
        app.include_router(router, prefix=settings.api_prefix)

    return app


app = create_app()
