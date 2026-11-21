"""
gateway/app.py

TwinOps Gateway — FastAPI application factory.

WHAT IT IS DOING:
  Creates and configures the FastAPI application for the Gateway service.
  On startup it:
    1. Connects to Redis and verifies the connection (fail-fast).
    2. Creates all Redis Streams consumer groups so downstream services
       can start consuming immediately (XGROUP CREATE … MKSTREAM).
  On every request:
    - CORS headers are added (allow all origins in dev; scope in prod).
    - Telemetry and connector routes are mounted.
    - Unhandled exceptions are caught and returned as structured JSON.

HOW IT IS CODED:
  Uses FastAPI lifespan context manager (startup/shutdown) instead of the
  deprecated @app.on_event("startup") pattern.
  CORS is wide-open in dev (GATEWAY_ALLOW_ORIGINS=* default); set it to
  the actual frontend origin in production.

DIAGNOSTIC GUIDE:
  Startup fails with "Cannot reach Redis" → check REDIS_URL in .env and
  that the Redis process is running (redis-cli ping).
  Routes return 404 for all paths → check that the router prefixes match
  (/api/v1/telemetry and /api/v1/connectors).
  CORS errors in browser → set GATEWAY_ALLOW_ORIGINS to the frontend URL.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from bus.streams import get_bus_client
from gateway.routes.connectors import router as connectors_router
from gateway.routes.read_api import router as read_api_router
from gateway.routes.telemetry import router as telemetry_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle for the Gateway."""
    logger.info("Gateway starting up...")

    # Verify Redis and create consumer groups
    try:
        bus = get_bus_client()
        bus.create_groups()
        logger.info("Bus consumer groups ready")
    except Exception as exc:
        logger.error("Cannot reach Redis on startup: %s", exc)
        raise RuntimeError(f"Gateway startup failed — Redis unavailable: {exc}") from exc

    logger.info("TwinOps Gateway ready")
    yield

    # Shutdown
    logger.info("Gateway shutting down")


def create_gateway_app() -> FastAPI:
    """Create and configure the Gateway FastAPI app."""
    app = FastAPI(
        title="TwinOps Gateway",
        description=(
            "Single entry point for telemetry from customer applications. "
            "Authenticates, validates, normalises, and publishes to the Message Bus."
        ),
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/gateway/docs",
        redoc_url="/gateway/redoc",
        openapi_url="/gateway/openapi.json",
    )

    # CORS
    allow_origins = os.getenv("GATEWAY_ALLOW_ORIGINS", "*").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in allow_origins],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Global exception handler — always return JSON, never expose stack traces
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception on %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "code": "INTERNAL_ERROR", "message": "An internal error occurred"},
        )

    # Health check (no auth required)
    @app.get("/gateway/health", tags=["health"], summary="Gateway health check")
    async def health() -> dict:
        """
        Returns Gateway and Bus health status.

        WHAT IT IS DOING:
          Quick liveness check for load balancers and monitoring tools.
          Checks Redis reachability. Returns 200 if healthy, 503 if the Bus
          is unreachable.

        DIAGNOSTIC GUIDE:
          If this endpoint returns 503, the Gateway cannot publish to the Bus.
          All telemetry ingestion will fail until Redis is restored.
        """
        try:
            bus = get_bus_client()
            bus_ok = bus.ping()
        except Exception:
            bus_ok = False

        if not bus_ok:
            return JSONResponse(
                status_code=503,
                content={"status": "unhealthy", "bus": "unreachable"},
            )

        return {"status": "healthy", "bus": "connected"}

    # Register routers
    app.include_router(telemetry_router)
    app.include_router(connectors_router)
    app.include_router(read_api_router)

    return app
