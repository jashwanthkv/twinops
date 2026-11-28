"""
client/routes/admin_chaos.py — Chaos and Fault Injection Controls for Evaluator Showcase.
Allows switching profiles between normal, average, and worst.
"""
from __future__ import annotations
from typing import Literal
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from client.chaos_state import chaos, ProfileMode

router = APIRouter(prefix="/api/admin", tags=["admin_chaos"])

class ProfileRequest(BaseModel):
    mode: ProfileMode = Field(..., description="normal | average | worst")

class FaultToggleRequest(BaseModel):
    fault: str = Field(..., description="db_leak | cache_bust | rate_limit_choke | worker_stall | reset")
    enabled: bool = Field(default=True)
    delay_ms: int = Field(default=0)

@router.get("/profile")
async def get_profile():
    return chaos.to_dict()

@router.post("/profile")
async def set_profile(req: ProfileRequest):
    chaos.set_profile(req.mode)
    return {
        "status": "profile_updated",
        "active_profile": req.mode,
        "state": chaos.to_dict(),
    }

@router.post("/fault")
async def toggle_fault(req: FaultToggleRequest):
    if req.fault == "reset":
        chaos.reset()
    elif req.fault == "db_leak":
        chaos.db_leak_enabled = req.enabled
    elif req.fault == "db_delay":
        chaos.db_extra_delay_ms = req.delay_ms
    elif req.fault == "cache_bust":
        chaos.cache_bust_enabled = req.enabled
    elif req.fault == "rate_limit_choke":
        chaos.rate_limit_choke = req.enabled
    elif req.fault == "worker_stall":
        chaos.worker_stalled = req.enabled
    elif req.fault == "payment_delay":
        chaos.payment_delay_ms = req.delay_ms
    return {"status": "ok", "state": chaos.to_dict()}
