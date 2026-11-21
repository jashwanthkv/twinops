"""
gateway/routes/read_api.py

WHAT IT IS DOING:
  Provides query and observability endpoints for the Digital Twin, Anomaly alerts,
  Linear Regression forecasts, and Master LLM root-cause analysis results.
  Used by developers, monitoring dashboards, and future UI components.

ENDPOINTS:
  GET /api/v1/twin/{app_id}                  — Current Digital Twin health and metric snapshot
  GET /api/v1/analysis/{app_id}/latest       — Latest Master LLM analysis and remediation report
  GET /api/v1/forecast/{app_id}/{metric}    — 10-min history and 15-min linear regression forecast
  GET /api/v1/anomalies/{app_id}             — Active anomaly alerts for the application

HOW IT IS CODED:
  Reads sub-millisecond hot state from Redis, falling back to MongoDB where applicable.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException
import motor.motor_asyncio
import redis

from env_loader import get_mongo_database, get_mongo_uri, get_redis_url
from ml.forecaster import Forecaster
from ml.rolling_window import get_window, get_window_values
from twin.store import TwinStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["observability"])

_REDIS_URL = get_redis_url()
_MONGO_URI = get_mongo_uri()
_DB_NAME = get_mongo_database()

_twin_store = TwinStore()
_forecaster = Forecaster()
_r = redis.from_url(_REDIS_URL, decode_responses=True)
_mongo = motor.motor_asyncio.AsyncIOMotorClient(_MONGO_URI)
_analysis_col = _mongo[_DB_NAME]["analysis_results"]


@router.get("/twin/{app_id}", summary="Get live Digital Twin state")
async def get_twin_state(app_id: str) -> dict[str, Any]:
    """Returns the current real-time health, metric snapshot, and active anomalies for an app."""
    state = _twin_store.load_hot(app_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"No digital twin state found for '{app_id}'")

    return {
        "app_id": state.app_id,
        "health": state.health,
        "last_seen": state.last_seen,
        "consecutive_ok": state.consecutive_ok,
        "latest_analysis_id": state.latest_analysis_id,
        "metrics": {
            k: {"value": v.value, "unit": v.unit, "ts": v.ts}
            for k, v in state.metrics.items()
        },
        "active_anomalies": [
            {
                "metric": a.metric,
                "score": a.score,
                "top_metric": a.top_metric,
                "detected_at": a.detected_at,
                "evaluator_verdict": a.evaluator_verdict,
            }
            for a in state.active_anomalies
        ],
        "forecast": state.forecast,
    }


@router.get("/analysis/{app_id}/latest", summary="Get latest LLM analysis and remediation")
async def get_latest_analysis(app_id: str) -> dict[str, Any]:
    """Returns the most recent Master LLM root-cause analysis report for an application."""
    # Try Redis hot cache first (<1ms)
    hot_key = f"twin:analysis:{app_id}:latest"
    cached = _r.get(hot_key)
    if cached:
        try:
            return json.loads(cached)
        except Exception:
            pass

    # Fallback to MongoDB
    doc = await _analysis_col.find_one(
        {"application_id": app_id},
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    if not doc:
        # Also check app_id field
        doc = await _analysis_col.find_one(
            {"app_id": app_id},
            {"_id": 0},
            sort=[("created_at", -1)],
        )

    if not doc:
        raise HTTPException(status_code=404, detail=f"No analysis reports found for '{app_id}'")

    return doc


@router.get("/forecast/{app_id}/{metric}", summary="Get metric forecast")
async def get_metric_forecast(app_id: str, metric: str) -> dict[str, Any]:
    """Returns the 10-minute historical window and projected value at t+15 min."""
    window_pairs = get_window(_r, app_id, metric)
    window_vals = get_window_values(_r, app_id, metric)

    current_val = window_pairs[-1][1] if window_pairs else None
    predicted_15min = None

    if window_vals is not None:
        predicted_15min = _forecaster.predict(app_id, metric, window_vals)

    return {
        "app_id": app_id,
        "metric": metric,
        "current_value": current_val,
        "predicted_15min": round(predicted_15min, 4) if predicted_15min is not None else None,
        "window_points_count": len(window_pairs),
        "window_history": [{"timestamp": ts, "value": val} for ts, val in window_pairs],
    }


@router.get("/anomalies/{app_id}", summary="Get active anomalies")
async def get_active_anomalies(app_id: str) -> dict[str, Any]:
    """Returns active anomalies for an app from the Digital Twin."""
    state = _twin_store.load_hot(app_id)
    anomalies = []
    if state:
        anomalies = [
            {
                "metric": a.metric,
                "score": a.score,
                "top_metric": a.top_metric,
                "detected_at": a.detected_at,
                "evaluator_verdict": a.evaluator_verdict,
            }
            for a in state.active_anomalies
        ]
    return {
        "app_id": app_id,
        "health": state.health if state else "UNKNOWN",
        "active_anomalies": anomalies,
    }
