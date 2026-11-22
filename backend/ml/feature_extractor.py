"""
ml/feature_extractor.py

WHAT IT IS DOING:
  Normalises incoming telemetry metric dicts into a fixed-order 18-element
  numpy array matching the Isolation Forest training schema. Missing metrics
  default to 0.0.

HOW IT IS CODED:
  FEATURE_NAMES list defines the canonical order. extract_features() iterates
  the metrics list and fills a float array by name. Any metric not in the
  schema is silently ignored (may be used by forecaster separately).

DIAGNOSTIC GUIDE:
  If Isolation Forest results seem off, print the feature vector and check
  which metrics defaulted to 0.0 — the SDK may not be sending them.
"""
from __future__ import annotations

import numpy as np

FEATURE_NAMES: list[str] = [
    "cpu_usage_pct", "process_cpu_pct", "memory_usage_pct", "heap_usage_pct",
    "request_rate_rps", "response_time_mean_ms", "response_time_p95_ms", "error_rate_ratio",
    "active_requests", "process_threads", "db_pool_active", "db_pool_idle", "db_pool_pending",
    "db_pool_saturation_ratio", "gc_pause_ms", "cache_hit_ratio", "external_api_latency_ms",
    "worker_queue_depth",
]

def extract_features(metrics: list[dict]) -> np.ndarray:
    """Return a (18,) float64 array from a telemetry metrics list."""
    lookup: dict[str, float] = {}
    for m in metrics:
        name = m.get("name", "")
        val = m.get("value")
        if name and val is not None:
            try:
                lookup[name] = float(val)
            except (TypeError, ValueError):
                pass
    return np.array([lookup.get(f, 0.0) for f in FEATURE_NAMES], dtype=np.float64)

def top_deviated_metric(features: np.ndarray, baseline_mean: np.ndarray, baseline_std: np.ndarray) -> str:
    """Return the feature name with the highest z-score deviation from baseline."""
    std_safe = np.where(baseline_std < 1e-9, 1.0, baseline_std)
    z_scores = np.abs((features - baseline_mean) / std_safe)
    idx = int(np.argmax(z_scores))
    return FEATURE_NAMES[idx]
