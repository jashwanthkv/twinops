"""
client/routes — API route modules for the client app.
"""
from client.routes.store import router as store_router
from client.routes.admin_chaos import router as admin_chaos_router

__all__ = ["store_router", "admin_chaos_router"]
