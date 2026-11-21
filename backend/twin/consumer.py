"""
twin/consumer.py

WHAT IT IS DOING:
  The Digital Twin Consumer maintains the live health state of every monitored
  application. It reads from two streams:
    1. STREAM_TELEMETRY (twin-group) — updates metric snapshots and heals state
    2. STREAM_ANOMALY (twin-group) — records confirmed anomalies, degrades health

  For every incoming event it:
    1. Loads current ApplicationTwinState from Redis (or creates a new one).
    2. Applies the state transition (telemetry_ok or anomaly_detected).
    3. Updates metric snapshot from the telemetry.
    4. Saves hot state to Redis.
    5. Possibly writes a snapshot to MongoDB.

HOW IT IS CODED:
  TwinConsumer.run() runs two asyncio tasks concurrently — one for each stream.
  _process_telemetry() and _process_anomaly() handle the respective message types.
  State is loaded from Redis on each message so the consumer is stateless and
  can be restarted without losing progress.

DIAGNOSTIC GUIDE:
  If health never transitions from UNKNOWN, check that STREAM_TELEMETRY is
  receiving messages (XLEN twinops:telemetry.events). If CRITICAL never
  resolves, check that anomaly metrics are appearing in telemetry again.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from bus.streams import STREAM_ANOMALY, STREAM_TELEMETRY, BusClient, get_bus_client
from twin.state import ApplicationTwinState, ComponentMetric, transition
from twin.store import TwinStore

logger = logging.getLogger(__name__)


class TwinConsumer:
    """Maintains real-time ApplicationTwinState from telemetry + anomaly events."""

    def __init__(self) -> None:
        self._bus: BusClient = get_bus_client()
        self._store = TwinStore()

    async def run(self) -> None:
        logger.info("TwinConsumer started — monitoring telemetry + anomaly streams")
        await asyncio.gather(
            self._consume_telemetry(),
            self._consume_anomaly(),
        )

    async def _consume_telemetry(self) -> None:
        group = "twin-group"
        consumer = "twin-telemetry-worker"
        while True:
            try:
                messages = await self._bus.read_async(STREAM_TELEMETRY, group, consumer, count=10, block_ms=2000)
                for msg_id, fields in messages:
                    await self._process_telemetry(msg_id, fields)
            except Exception as exc:
                logger.error("TwinConsumer telemetry loop error: %s", exc)
                await asyncio.sleep(2)

    async def _consume_anomaly(self) -> None:
        group = "twin-group"
        consumer = "twin-anomaly-worker"
        while True:
            try:
                messages = await self._bus.read_async(STREAM_ANOMALY, group, consumer, count=10, block_ms=2000)
                for msg_id, fields in messages:
                    await self._process_anomaly(msg_id, fields)
            except Exception as exc:
                logger.error("TwinConsumer anomaly loop error: %s", exc)
                await asyncio.sleep(2)

    async def _process_telemetry(self, msg_id: str, fields: dict) -> None:
        app_id = fields.get("application_id", "unknown")
        try:
            state = self._store.load_hot(app_id) or ApplicationTwinState(app_id=app_id)
            metrics_raw: list[dict] = json.loads(fields.get("metrics", "[]"))
            for m in metrics_raw:
                name = m.get("name", "")
                val = m.get("value")
                unit = m.get("unit", "")
                if name and val is not None:
                    state.metrics[name] = ComponentMetric(
                        value=float(val), unit=unit, ts=time.time()
                    )
            transition(state, "telemetry_ok")
            self._store.save_hot(state)
            await self._store.maybe_snapshot(state)
        except Exception as exc:
            logger.error("TwinConsumer._process_telemetry failed %s: %s", msg_id, exc)
        finally:
            self._bus.ack(STREAM_TELEMETRY, "twin-group", msg_id)

    async def _process_anomaly(self, msg_id: str, fields: dict) -> None:
        app_id = fields.get("application_id") or fields.get("app_id", "unknown")
        try:
            state = self._store.load_hot(app_id) or ApplicationTwinState(app_id=app_id)
            score = float(fields.get("score", 0.0))
            top_metric = fields.get("top_metric", "unknown")
            transition(state, "anomaly_detected", metric=top_metric, score=score, top_metric=top_metric)
            self._store.save_hot(state)
            await self._store.maybe_snapshot(state)
            logger.info(
                "Twin state updated: app=%s health=%s anomaly=%s",
                app_id, state.health, top_metric,
            )
        except Exception as exc:
            logger.error("TwinConsumer._process_anomaly failed %s: %s", msg_id, exc)
        finally:
            self._bus.ack(STREAM_ANOMALY, "twin-group", msg_id)
