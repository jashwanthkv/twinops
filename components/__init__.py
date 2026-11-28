"""
client/components — The 8 Core Architecture Components of the Client Application.
"""
from client.components.load_balancer import load_balancer
from client.components.rate_limiter import rate_limiter
from client.components.auth_service import auth_service
from client.components.authz_service import authz_service
from client.components.cache_service import cache_service
from client.components.db_service import db_service
from client.components.payment_gateway import payment_gateway
from client.components.worker_queue import worker_queue

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
