"""FastAPI application factory and app instance."""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware

from app.api.routers import (
    ai as ai_router,
)
from app.api.routers import (
    assets as assets_router,
)
from app.api.routers import (
    audit as audit_router,
)
from app.api.routers import (
    backtest_metrics as backtest_metrics_router,
)
from app.api.routers import (
    backtests as backtests_router,
)
from app.api.routers import (
    feature_snapshots as feature_snapshots_router,
)
from app.api.routers import (
    features as features_router,
)
from app.api.routers import (
    health as health_router,
)
from app.api.routers import (
    importer as importer_router,
)
from app.api.routers import (
    lifecycle as lifecycle_router,
)
from app.api.routers import (
    market_data as market_data_router,
)
from app.api.routers import (
    notifications as notifications_router,
)
from app.api.routers import (
    paper as paper_router,
)
from app.api.routers import (
    research as research_router,
)
from app.api.routers import (
    resources as resources_router,
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
from app.api.routers import (
    strategy_versions as strategy_versions_router,
)
from app.api.routers.health import warm_dependency_probes
from app.core.config import settings
from app.core.logging import configure_logging
from app.infrastructure.rate_limit import limiter

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
    # Warm the dependency probes once, off the request path: the first probe in a
    # process pays for a cold resolver and the broker transport, which is not a
    # bound we control. Results are thrown away — `/health` still measures live
    # (ADR-069).
    warm_dependency_probes()
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

    # Optional bearer auth. Off by default: the API binds to 127.0.0.1 and is
    # reached through the web proxy, so the default deployment is unaffected.
    # When API_AUTH_TOKEN is set, every /api/v1 route except the two health
    # probes requires `Authorization: Bearer <token>` (the bundled web container
    # injects it while proxying /api).
    open_paths = {f"{settings.api_prefix}/healthz", f"{settings.api_prefix}/health"}

    @app.middleware("http")
    async def enforce_api_auth(request: Request, call_next):
        token = settings.api_auth_token
        if not token or request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        if not path.startswith(settings.api_prefix) or path in open_paths:
            return await call_next(request)
        scheme, _, presented = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer" and secrets.compare_digest(presented, token):
            return await call_next(request)
        logger.warning("rejected unauthenticated request to %s", path)
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "code": "unauthorized",
                    "message": "missing or invalid API token",
                    "details": {},
                }
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Lightweight per-IP limit on mutating requests (docs/14 §4). Skipped in the
    # test environment and when the limit is 0; read-only endpoints are never
    # limited so the dashboard can keep polling.
    @app.middleware("http")
    async def enforce_rate_limit(request: Request, call_next):
        limit = settings.rate_limit_per_minute
        if (
            limit <= 0
            or settings.environment == "test"
            or request.method in {"GET", "HEAD", "OPTIONS"}
        ):
            return await call_next(request)
        path = request.url.path
        if not path.startswith(settings.api_prefix) or path in open_paths:
            return await call_next(request)
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(f"{client}:{path}", limit):
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "too many requests; slow down",
                        "details": {},
                    }
                },
                headers={"Retry-After": "60"},
            )
        return await call_next(request)

    # Added after auth so CORS is the outermost layer and 401s still carry the
    # CORS headers a cross-origin browser needs to read them.
    #
    # gzip is here for the ensemble report: it ships an equity point per bar for the
    # portfolio *and* for each member (up to 12), which is hundreds of KB of very
    # compressible JSON. The threshold keeps small responses untouched.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
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
        strategy_versions_router.router,
        feature_snapshots_router.router,
        features_router.router,
        lifecycle_router.router,
        backtests_router.router,
        backtest_metrics_router.router,
        research_router.router,
        resources_router.router,
        paper_router.router,
        signals_router.router,
        notifications_router.router,
        settings_router.router,
        importer_router.router,
        ai_router.router,
        audit_router.router,
    ):
        app.include_router(router, prefix=settings.api_prefix)

    return app


app = create_app()
