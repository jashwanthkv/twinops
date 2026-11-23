"""
ml/consumer_forecast.py

WHAT IT IS DOING:
  Reads telemetry events from Redis Streams (ml-forecast-group), maintains the
  10-minute rolling window for each of the 6 bottleneck metrics, runs the online
  SGDRegressor to predict the value 15 minutes ahead, and escalates to the Master
  LLM via the analysis.events stream if the prediction indicates degradation.

HOW IT IS CODED:
  ForecastConsumer.run() loops with XREADGROUP on STREAM_TELEMETRY / ml-forecast-group.
  For each event:
    1. Parse metrics JSON.
    2. For each of the 6 FORECAST_METRICS: push_metric() to Redis window.
    3. Get the last 10 values via get_window_values().
    4. If window has 10+ readings:
       a. Compute y_target as the NEXT actual value (lagged update) OR use
          the average of the last 3 values shifted by the observed slope.
       b. forecaster.update(app_id, metric, window, y_target) — incremental fit.
       c. predicted = forecaster.predict(app_id, metric, window).
       d. Check degradation_ratio vs threshold. If breached → publish trigger.
    5. XACK message.

DIAGNOSTIC GUIDE:
  If no forecasts are published, check that the window is filling:
    ZCARD ml:window:{app_id}:{metric} in redis-cli.
  If predictions diverge badly, the model may not have enough history.
  Check update_count in MongoDB ml_models collection.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import numpy as np
import redis as _redis

from bus.streams import STREAM_ANALYSIS, STREAM_TELEMETRY, BusClient, get_bus_client, get_clean_redis_url
from ml.forecaster import Forecaster
from ml.rolling_window import FORECAST_METRICS, get_window, get_window_values, push_metric

logger = logging.getLogger(__name__)

_GROUP = "ml-forecast-group"
_CONSUMER = "forecast-worker-1"
REDIS_URL = get_clean_redis_url()

# Degradation thresholds: if predicted value exceeds these, escalate
DEGRADATION_THRESHOLDS: dict[str, float] = {
    "cpu_usage_pct":        0.30,  # 30% relative increase
    "memory_usage_pct":     0.25,
    "heap_usage_pct":       0.25,
    "response_time_p95_ms": 1.0,   # 100% increase or crossing 1000ms
    "db_pool_pending":      5.0,   # absolute: >5 pending
    "worker_queue_depth":   50.0,  # absolute: >50 queued
}

ABSOLUTE_METRICS = {"db_pool_pending", "worker_queue_depth", "response_time_p95_ms"}


def _is_degraded(metric: str, current: float, predicted: float) -> bool:
    threshold = DEGRADATION_THRESHOLDS.get(metric, 0.25)
    if metric in ABSOLUTE_METRICS:
        # For absolute metrics, check if predicted > threshold directly
        return predicted > threshold and predicted > current
    else:
        # Relative: check if predicted is threshold% worse
        if current < 1e-9:
            return False
        ratio = (predicted - current) / current
        return ratio > threshold


class ForecastConsumer:
    """Reads telemetry, maintains 10-min window, predicts +15min, escalates if worse."""

    def __init__(self) -> None:
        self._bus: BusClient = get_bus_client()
        self._r = _redis.from_url(REDIS_URL, decode_responses=True)
        self._forecaster = Forecaster()
        # Track last window state per (app_id, metric) to get y_target for update
        self._last_windows: dict[str, list[float]] = {}

    async def run(self) -> None:
        try:
            self._bus.create_groups()
        except Exception:
            pass
        logger.info("ForecastConsumer started — %s / %s", STREAM_TELEMETRY, _GROUP)
        while True:
            try:
                messages = await self._bus.read_async(STREAM_TELEMETRY, _GROUP, _CONSUMER, count=10, block_ms=2000)
                for msg_id, fields in messages:
                    await self._process(msg_id, fields)
            except Exception as exc:
                logger.error("ForecastConsumer loop error: %s", exc)
                await asyncio.sleep(2)

    async def _process(self, msg_id: str, fields: dict) -> None:
        app_id = fields.get("application_id", "unknown")
        try:
            metrics_raw: list[dict] = json.loads(fields.get("metrics", "[]"))
            metric_lookup: dict[str, float] = {}
            for m in metrics_raw:
                name = m.get("name", "")
                val = m.get("value")
                if name and val is not None:
                    try:
                        metric_lookup[name] = float(val)
                    except (TypeError, ValueError):
                        pass

            ts = time.time()
            escalations: list[dict] = []

            for metric in FORECAST_METRICS:
                if metric not in metric_lookup:
                    continue
                current_val = metric_lookup[metric]
                push_metric(self._r, app_id, metric, current_val, ts)

                window = get_window_values(self._r, app_id, metric)
                if window is None:
                    continue

                # Update model: use the current reading as the y_target for the previous window
                prev_key = f"{app_id}:{metric}"
                prev_window = self._last_windows.get(prev_key)
                if prev_window is not None and len(prev_window) >= 15:
                    pw = np.array(prev_window[-15:], dtype=np.float64)
                    await asyncio.to_thread(
                        self._forecaster.update, app_id, metric, pw, current_val
                    )

                self._last_windows[prev_key] = window[-15:].tolist()

                # Predict using strictly the last 15 values
                predicted = await asyncio.to_thread(
                    self._forecaster.predict, app_id, metric, window[-15:]
                )
                if predicted is None:
                    continue

                if _is_degraded(metric, current_val, predicted):
                    escalations.append({
                        "metric": metric,
                        "current": round(current_val, 4),
                        "predicted_15min": round(predicted, 4),
                    })
                    logger.warning(
                        "Forecast escalation: app=%s metric=%s current=%.2f predicted=%.2f",
                        app_id, metric, current_val, predicted,
                    )
                    print(f"[Forecaster] 📉 Projected degradation for '{app_id}' on metric '{metric}': current={current_val:.2f} -> projected={predicted:.2f} in +15m", flush=True)

            if escalations:
                forecast_event = {
                    "event_type": "forecast.degradation",
                    "application_id": app_id,
                    "app_id": app_id,
                    "tenant_id": fields.get("tenant_id", "default"),
                    "timestamp": fields.get("client_timestamp", ""),
                    "trigger": "linear_regression_forecast",
                    "escalations": json.dumps(escalations),
                    "source_event_id": fields.get("event_id", ""),
                }
                self._bus.publish(STREAM_ANALYSIS, forecast_event)
                logger.info("Forecast degradation event published for app=%s (%d metrics)", app_id, len(escalations))

        except Exception as exc:
            logger.error("ForecastConsumer._process failed for msg %s: %s", msg_id, exc)
        finally:
            self._bus.ack(STREAM_TELEMETRY, _GROUP, msg_id)
