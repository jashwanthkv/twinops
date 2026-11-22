"""
ml/rolling_window.py

WHAT IT IS DOING:
  Maintains a time-ordered sliding window of metric values in Redis for the
  6 bottleneck metrics used by the Linear Regression forecaster. Keeps only
  observations from the last 10 minutes (or last 10 readings, whichever is
  larger).

HOW IT IS CODED:
  Uses Redis ZADD (score = epoch_seconds) and ZRANGEBYSCORE to retrieve the
  window. Trims old entries with ZREMRANGEBYSCORE after each push.
  Key pattern: ml:window:{app_id}:{metric_name}
  Each member is a JSON-encoded {"ts": float, "value": float} string.

DIAGNOSTIC GUIDE:
  If the window is empty on the first few minutes, it means no data has
  arrived yet — normal. ZCARD ml:window:{app_id}:{metric} in redis-cli shows
  current window size.
"""
from __future__ import annotations

import json
import logging
import time

import numpy as np
import redis

logger = logging.getLogger(__name__)

FORECAST_METRICS: list[str] = [
    "cpu_usage_pct", "memory_usage_pct", "heap_usage_pct",
    "response_time_p95_ms", "db_pool_pending", "worker_queue_depth",
]
WINDOW_SECONDS = 600   # 10 minutes
MIN_WINDOW_SIZE = 10   # need at least 10 readings to forecast


def _window_key(app_id: str, metric: str) -> str:
    return f"ml:window:{app_id}:{metric}"


def push_metric(r: redis.Redis, app_id: str, metric: str, value: float, ts: float | None = None) -> None:
    """Add a new observation to the sliding window and trim old entries."""
    ts = ts or time.time()
    key = _window_key(app_id, metric)
    member = json.dumps({"ts": ts, "value": value})
    r.zadd(key, {member: ts})
    # Trim entries older than WINDOW_SECONDS
    r.zremrangebyscore(key, "-inf", ts - WINDOW_SECONDS)


def get_window(r: redis.Redis, app_id: str, metric: str) -> list[tuple[float, float]]:
    """Return (ts, value) pairs in ascending time order for the window."""
    key = _window_key(app_id, metric)
    now = time.time()
    raw = r.zrangebyscore(key, now - WINDOW_SECONDS, "+inf")
    result = []
    for item in raw:
        try:
            d = json.loads(item)
            result.append((float(d["ts"]), float(d["value"])))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return result


def get_window_values(r: redis.Redis, app_id: str, metric: str) -> np.ndarray | None:
    """Return numpy array of the last 10 values, or None if window not full."""
    pairs = get_window(r, app_id, metric)
    if len(pairs) < MIN_WINDOW_SIZE:
        return None
    values = np.array([v for _, v in pairs[-MIN_WINDOW_SIZE:]], dtype=np.float64)
    return values
