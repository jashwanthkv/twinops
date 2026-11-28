"""
client/main.py — FastAPI Entrypoint for Python Client Target Application.
Runs on port 8090.
"""
from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from client.config import settings
from client.twinops_agent import agent, TwinOpsTelemetryMiddleware
from client.routes import store_router, admin_chaos_router
from client.chaos_state import chaos

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("client.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: Launch the TwinOps Telemetry Agent
    logger.info("Starting Python Client Application on port %d...", settings.app_port)
    agent.start()
    yield
    # Shutdown
    agent._running = False
    logger.info("Python Client Application stopped.")


app = FastAPI(
    title="TwinOps Client Target — Python Store",
    description="Live target application with 8 modular components for TwinOps monitoring.",
    version="1.0.0",
    lifespan=lifespan,
)

# 1. CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Telemetry Middleware (Tracks all HTTP request latencies and errors)
app.add_middleware(TwinOpsTelemetryMiddleware)

# 3. Mount Application Routes
app.include_router(store_router)
app.include_router(admin_chaos_router)


# 4. Direct Inspection Endpoints
@app.get("/twinops/v1/metrics", tags=["TwinOps Diagnostics"])
async def direct_metrics():
    """Directly inspect current live readings of the 18 canonical features."""
    return {
        "application_id": settings.app_id,
        "profile": chaos.profile,
        "features": agent.collector.snapshot_and_reset(),
    }


@app.get("/twinops/v1/topology", tags=["TwinOps Diagnostics"])
async def topology():
    """Describes the 8 components and dependency relationships."""
    return {
        "application_id": settings.app_id,
        "components": [
            {"id": "load_balancer", "name": "Ingress Router", "type": "GATEWAY"},
            {"id": "rate_limiter", "name": "Token Bucket Rate Limiter", "type": "MIDDLEWARE"},
            {"id": "auth_service", "name": "Authentication (AuthN)", "type": "SECURITY"},
            {"id": "authz_service", "name": "Authorization (AuthZ)", "type": "SECURITY"},
            {"id": "cache_service", "name": "Redis Cache Layer", "type": "CACHE"},
            {"id": "db_service", "name": "MongoDB Pool Service", "type": "DATABASE"},
            {"id": "payment_gateway", "name": "External Payment API", "type": "EXTERNAL_API"},
            {"id": "worker_queue", "name": "Async Task Worker", "type": "QUEUE"},
        ],
        "edges": [
            {"source": "load_balancer", "target": "rate_limiter"},
            {"source": "rate_limiter", "target": "auth_service"},
            {"source": "auth_service", "target": "authz_service"},
            {"source": "authz_service", "target": "cache_service"},
            {"source": "cache_service", "target": "db_service"},
            {"source": "db_service", "target": "payment_gateway"},
            {"source": "payment_gateway", "target": "worker_queue"},
        ]
    }


if __name__ == "__main__":
    uvicorn.run(
        "client.main:app",
        host="0.0.0.0",
        port=settings.app_port,
        reload=False,
        log_level="info",
    )
