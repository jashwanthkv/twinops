"""
ml/consumer_anomaly.py

WHAT IT IS DOING:
  Reads telemetry events from Redis Streams (ml-anomaly-group), extracts the
  18-feature vector, runs Isolation Forest inference, and when alert=True
  calls the EvaluatorGate. If the evaluator confirms REAL, publishes to the
  anomaly.events stream for the Master LLM consumer.

HOW IT IS CODED:
  AnomalyConsumer.run() loops with XREADGROUP. For each event:
    1. Parse metrics JSON from the bus event fields.
    2. extract_features() → 18-feature numpy array.
    3. update_stats() → Welford online mean/std update + returns current stats.
    4. detector.predict() → AnomalyResult.
    5. If alert=True → evaluator.evaluate() → verdict.
    6. If REAL → bus.publish(STREAM_ANOMALY, anomaly_event_dict).
    7. XACK the message.

DIAGNOSTIC GUIDE:
  If anomaly.events stream is empty despite alerts being logged, check that
  STREAM_ANOMALY consumer groups were created (gateway startup creates them).
  XLEN twinops:anomaly.events in redis-cli shows pending events.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

import redis as _redis

from bus.streams import STREAM_ANOMALY, STREAM_TELEMETRY, BusClient, get_bus_client, get_clean_redis_url
from evaluator.evaluator import EvaluatorGate
from ml.anomaly_detector import AnomalyDetector
from ml.feature_extractor import extract_features
from ml.rolling_stats import load_stats, update_stats
from ml.rolling_window import FORECAST_METRICS, get_window_values, push_metric

logger = logging.getLogger(__name__)

_GROUP = "ml-anomaly-group"
_CONSUMER = "anomaly-worker-1"
REDIS_URL = get_clean_redis_url()


class AnomalyConsumer:
    """Reads telemetry, runs Isolation Forest, routes confirmed anomalies to bus."""

    def __init__(self) -> None:
        self._bus: BusClient = get_bus_client()
        self._r = _redis.from_url(REDIS_URL, decode_responses=True)
        self._detector = AnomalyDetector(self._r)
        self._evaluator = EvaluatorGate()

    async def run(self) -> None:
        # Ensure consumer groups exist for anomaly stream
        try:
            self._bus.create_groups()
        except Exception:
            pass
        logger.info("AnomalyConsumer started — %s / %s", STREAM_TELEMETRY, _GROUP)
        while True:
            try:
                messages = await self._bus.read_async(STREAM_TELEMETRY, _GROUP, _CONSUMER, count=10, block_ms=2000)
                for msg_id, fields in messages:
                    await self._process(msg_id, fields)
            except Exception as exc:
                logger.error("AnomalyConsumer loop error: %s", exc)
                await asyncio.sleep(2)

    async def _process(self, msg_id: str, fields: dict) -> None:
        app_id = fields.get("application_id", "unknown")
        try:
            metrics_raw: list[dict] = json.loads(fields.get("metrics", "[]"))
            features = extract_features(metrics_raw)

            # Update rolling stats (Welford online)
            mean, std, count = update_stats(self._r, app_id, features)

            # Also push forecast metrics into rolling window
            metric_lookup = {m.get("name"): m.get("value") for m in metrics_raw if m.get("name")}
            ts = time.time()
            for fm in FORECAST_METRICS:
                if fm in metric_lookup and metric_lookup[fm] is not None:
                    push_metric(self._r, app_id, fm, float(metric_lookup[fm]), ts)

            # Anomaly inference
            result = self._detector.predict(app_id, features, mean, std)

            if not result.alert:
                self._bus.ack(STREAM_TELEMETRY, _GROUP, msg_id)
                return

            logger.info(
                "Anomaly detected app=%s score=%.4f top=%s — sending to evaluator",
                app_id, result.score, result.top_metric,
            )
            print(f"[ML Engine] ⚠️ Isolation Forest anomaly detected for '{app_id}' on metric '{result.top_metric}' (score: {result.score:.3f}). Sending to Evaluator Gate...", flush=True)

            # Build snapshot for evaluator (top 10 non-zero metrics)
            snapshot = {
                name: float(val)
                for name, val in zip(
                    ["cpu_usage_pct","memory_usage_pct","response_time_p95_ms",
                     "error_rate_ratio","db_pool_pending","worker_queue_depth",
                     "request_rate_rps","heap_usage_pct","cache_hit_ratio","gc_pause_ms"],
                    features[[0,2,6,7,12,17,4,3,15,14]]
                )
                if val != 0.0
            }

            # Get recent trend for top_metric from window
            window_vals = get_window_values(self._r, app_id, result.top_metric)
            trend = window_vals.tolist() if window_vals is not None else []

            verdict, reason = await asyncio.to_thread(
                self._evaluator.evaluate,
                result.top_metric, result.score, snapshot, trend,
            )

            if verdict == "FALSE_POSITIVE":
                logger.info("Evaluator: FALSE_POSITIVE for %s — %s", app_id, reason)
                print(f"[Evaluator Gate] 🛡️ False positive rejected for '{app_id}' ({result.top_metric}): {reason}", flush=True)
                self._bus.ack(STREAM_TELEMETRY, _GROUP, msg_id)
                return

            # Publish confirmed anomaly to bus
            anomaly_event = {
                "event_type": "anomaly.detected",
                "app_id": app_id,
                "application_id": app_id,
                "tenant_id": fields.get("tenant_id", "default"),
                "timestamp": result.timestamp,
                "score": str(result.score),
                "top_metric": result.top_metric,
                "verdict": verdict,
                "evaluator_reason": reason,
                "trigger": "isolation_forest",
                "metric_snapshot": json.dumps(snapshot),
                "trend": json.dumps(trend),
                "source_event_id": fields.get("event_id", ""),
            }
            self._bus.publish(STREAM_ANOMALY, anomaly_event)
            logger.info("Confirmed anomaly published to %s for app=%s", STREAM_ANOMALY, app_id)
            print(f"[Evaluator Gate] 🚨 CONFIRMED ANOMALY on '{app_id}' ({result.top_metric})! Verdict: {verdict}. Publishing to bus...", flush=True)

        except Exception as exc:
            logger.error("AnomalyConsumer._process failed for msg %s: %s", msg_id, exc)
        finally:
            self._bus.ack(STREAM_TELEMETRY, _GROUP, msg_id)
