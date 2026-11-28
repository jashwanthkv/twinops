"""
client/components — The 8 Core Architecture Components of the Client Application.

Each component now lives in its own named subpackage so AIE can detect
them individually. This module re-exports singletons for backward compatibility
with routes and twinops_agent.
"""
# Ingress / Middleware
from client.middleware.load_balancer import load_balancer
from client.middleware.rate_limiter import rate_limiter

# Security
from client.security.auth_service import auth_service
from client.security.authz_service import authz_service

# Cache
from client.cache.cache_service import cache_service

# Database
from client.db.db_service import db_service

# External Integrations
from client.integrations.payment_gateway import payment_gateway

# Background Workers
from client.workers.worker_queue import worker_queue

__all__ = [
    "load_balancer",
    "rate_limiter",
    "auth_service",
    "authz_service",
    "cache_service",
    "db_service",
    "payment_gateway",
    "worker_queue",
]
