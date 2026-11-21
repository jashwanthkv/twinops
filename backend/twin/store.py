"""
twin/store.py

WHAT IT IS DOING:
  Persists ApplicationTwinState to two locations:
    1. Redis HASH twin:{app_id}:state — always-current hot state for fast UI reads
    2. MongoDB twin_snapshots collection — point-in-time snapshots every SNAPSHOT_INTERVAL_S

HOW IT IS CODED:
  TwinStore.save_hot(state) → synchronous Redis HSET call.
  TwinStore.load_hot(app_id) → Redis HGETALL → ApplicationTwinState.from_redis_dict().
  TwinStore.maybe_snapshot(state) → async MongoDB insert if enough time has passed.
  Snapshot interval controlled by TWIN_SNAPSHOT_INTERVAL_S env var (default 60s).

DIAGNOSTIC GUIDE:
  If Redis shows stale state, check that TwinConsumer is running and XACKing messages.
  HGETALL twin:{app_id}:state in redis-cli inspects current state.
  If MongoDB snapshots are missing, check MONGODBURI and that the event loop is running.
"""
from __future__ import annotations

import json
import logging
import os
import time

import motor.motor_asyncio
import redis

from env_loader import get_mongo_database, get_mongo_uri, get_redis_url
from twin.state import ApplicationTwinState

logger = logging.getLogger(__name__)

_MONGO_URI = get_mongo_uri()
_DB_NAME = get_mongo_database()
_SNAPSHOT_INTERVAL = int(os.getenv("TWIN_SNAPSHOT_INTERVAL_S", "60"))
REDIS_URL = get_redis_url()


def _state_key(app_id: str) -> str:
    return f"twin:{app_id}:state"


class TwinStore:
    """Persists twin state to Redis (hot) and MongoDB (snapshots)."""

    def __init__(self) -> None:
        self._r = redis.from_url(REDIS_URL, decode_responses=True)
        self._mongo = motor.motor_asyncio.AsyncIOMotorClient(_MONGO_URI)
        self._col = self._mongo[_DB_NAME]["twin_snapshots"]
        self._last_snapshot: dict[str, float] = {}

    def save_hot(self, state: ApplicationTwinState) -> None:
        """Write current state to Redis HASH."""
        key = _state_key(state.app_id)
        self._r.hset(key, mapping=state.to_redis_dict())

    def load_hot(self, app_id: str) -> ApplicationTwinState | None:
        """Load state from Redis. Returns None if not found."""
        key = _state_key(app_id)
        raw = self._r.hgetall(key)
        if not raw:
            return None
        return ApplicationTwinState.from_redis_dict(app_id, raw)

    async def maybe_snapshot(self, state: ApplicationTwinState) -> None:
        """Write a MongoDB snapshot if SNAPSHOT_INTERVAL has passed."""
        now = time.time()
        last = self._last_snapshot.get(state.app_id, 0.0)
        if now - last < _SNAPSHOT_INTERVAL:
            return
        self._last_snapshot[state.app_id] = now
        try:
            doc = {
                "app_id": state.app_id,
                "health": state.health,
                "snapshot_at": now,
                "metrics": {k: {"value": v.value, "unit": v.unit} for k, v in state.metrics.items()},
                "active_anomalies": [
                    {"metric": a.metric, "score": a.score, "top_metric": a.top_metric}
                    for a in state.active_anomalies
                ],
                "forecast": state.forecast,
            }
            await self._col.insert_one(doc)
            logger.debug("Twin snapshot saved for app=%s health=%s", state.app_id, state.health)
        except Exception as exc:
            logger.error("Failed to save twin snapshot for %s: %s", state.app_id, exc)
