"""
client/routes/store.py — E-commerce Store Endpoints.
Routes all requests through the 8 components.
"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from client.components import (
    load_balancer,
    rate_limiter,
    auth_service,
    authz_service,
    cache_service,
    db_service,
    payment_gateway,
    worker_queue,
)

router = APIRouter(prefix="/api/v1/store", tags=["store"])

class CheckoutRequest(BaseModel):
    product_id: str = Field(default="prod-1")
    quantity: int = Field(default=1, ge=1)
    currency: str = Field(default="USD")

@router.get("/health")
async def health():
    return {"status": "ok", "service": "Python Client Store Target"}

@router.get("/products")
async def list_products(client_ip: str = "127.0.0.1"):
    with load_balancer.track_request():
        rate_limiter.check(client_ip)
        # 1. Check Cache
        cached = cache_service.get("catalog_products")
        if cached:
            return {"source": "cache", "products": cached}

        # 2. Cache Miss -> Query Database
        products = db_service.get_products()
        cache_service.set("catalog_products", products, ttl_seconds=30)
        return {"source": "database", "products": products}

@router.post("/checkout")
async def checkout(
    req: CheckoutRequest,
    authorization: str | None = Header(None),
    client_ip: str = "127.0.0.1",
):
    with load_balancer.track_request():
        # 1. Rate Limiting
        rate_limiter.check(client_ip)

        # 2. Authentication (AuthN)
        user = auth_service.authenticate(authorization)

        # 3. Authorization (AuthZ)
        authz_service.check_permission(user, required_role="shopper")

        # 4. Database Order Creation
        order_record = db_service.create_order({
            "user_id": user["user_id"],
            "product_id": req.product_id,
            "quantity": req.quantity,
            "status": "PROCESSING",
        })

        # 5. External 3rd-Party Payment Gateway
        total_amount = round(req.quantity * 199.99, 2)
        payment_result = payment_gateway.process_charge(total_amount, currency=req.currency)

        # 6. Async Background Worker Queue
        worker_queue.enqueue("send_email_receipt", {
            "order_id": order_record["order_id"],
            "user_id": user["user_id"],
            "amount": total_amount,
        })

        return {
            "status": "COMPLETED",
            "order": order_record,
            "payment": payment_result,
        }
