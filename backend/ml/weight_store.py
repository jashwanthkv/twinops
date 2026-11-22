"""
ml/weight_store.py

WHAT IT IS DOING:
  Persists forecaster model metadata (training count, last updated, metric,
  app_id) to MongoDB collection `ml_models`. Provides load/save helpers used
  by the Forecaster to record weight checkpoint history.

HOW IT IS CODED:
  Synchronous pymongo client. Called from the asyncio forecast consumer via
  asyncio.to_thread() to avoid blocking the event loop.

DIAGNOSTIC GUIDE:
  MongoDB ml_models collection: each document is {app_id, metric, update_count,
  saved_at, weight_path}. Query it to audit model training progress.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pymongo

logger = logging.getLogger(__name__)
_MONGO_URI = os.getenv("MONGODBURI", "")
_DB_NAME = os.getenv("MONGODB_DATABASE", "twinops_aie")


def save_model_metadata(app_id: str, metric: str, update_count: int, weight_path: str) -> None:
    """Upsert model training metadata to MongoDB."""
    try:
        client = pymongo.MongoClient(_MONGO_URI)
        col = client[_DB_NAME]["ml_models"]
        col.update_one(
            {"app_id": app_id, "metric": metric},
            {"$set": {
                "app_id": app_id,
                "metric": metric,
                "update_count": update_count,
                "weight_path": weight_path,
                "saved_at": datetime.now(tz=timezone.utc).isoformat(),
            }},
            upsert=True,
        )
        client.close()
    except Exception as exc:
        logger.error("Failed to save model metadata for %s/%s: %s", app_id, metric, exc)
