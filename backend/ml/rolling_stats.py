"""
ml/rolling_stats.py

WHAT IT IS DOING:
  Maintains per-(app_id) rolling statistics (mean, std, count) for the 18
  Isolation Forest features using Redis hashes. Used both by the anomaly
  detector (for top_metric z-score calculation) and the forecaster (window).

HOW IT IS CODED:
  Uses Redis HSET/HGET to persist float arrays as JSON strings under keys:
    ml:stats:{app_id}:mean  → JSON list of 18 floats
    ml:stats:{app_id}:std   → JSON list of 18 floats
    ml:stats:{app_id}:count → int
  Uses Welford's online algorithm for numerically stable mean/variance update.

DIAGNOSTIC GUIDE:
  If stats seem stale, check Redis key TTL — no TTL is set by default so
  they persist until Redis restart. Run HGETALL ml:stats:{app_id}:mean in
  redis-cli to inspect.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import redis

logger = logging.getLogger(__name__)
_N = 18  # number of features


def _stats_key(app_id: str, kind: str) -> str:
    return f"ml:stats:{app_id}:{kind}"


def load_stats(r: redis.Redis, app_id: str) -> tuple[np.ndarray, np.ndarray, int]:
    """Load (mean, std, count) from Redis. Returns zeros if not found."""
    raw_mean = r.get(_stats_key(app_id, "mean"))
    raw_m2 = r.get(_stats_key(app_id, "m2"))
    raw_count = r.get(_stats_key(app_id, "count"))

    count = int(raw_count) if raw_count else 0
    mean = np.array(json.loads(raw_mean), dtype=np.float64) if raw_mean else np.zeros(_N)
    m2 = np.array(json.loads(raw_m2), dtype=np.float64) if raw_m2 else np.zeros(_N)
    std = np.sqrt(m2 / max(count - 1, 1)) if count > 1 else np.ones(_N)
    return mean, std, count


def update_stats(r: redis.Redis, app_id: str, features: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """Update Welford online mean/variance with a new feature vector. Returns updated (mean, std, count)."""
    mean, std, count = load_stats(r, app_id)
    if count > 0:
        m2 = (std ** 2) * max(count - 1, 1)
    else:
        m2 = np.zeros(_N)

    count += 1
    delta = features - mean
    mean = mean + delta / count
    delta2 = features - mean
    m2 = m2 + delta * delta2
    std = np.sqrt(m2 / max(count - 1, 1)) if count > 1 else np.ones(_N)

    pipe = r.pipeline()
    pipe.set(_stats_key(app_id, "mean"), json.dumps(mean.tolist()))
    pipe.set(_stats_key(app_id, "m2"), json.dumps(m2.tolist()))
    pipe.set(_stats_key(app_id, "count"), str(count))
    pipe.execute()
    return mean, std, count
