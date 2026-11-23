"""
llm/consumer.py

WHAT IT IS DOING:
  The Master LLM Consumer listens for high-severity events on the Redis Streams bus:
    1. Confirmed anomalies from STREAM_ANOMALY (ml-anomaly-group + evaluator gate)
    2. Severe 15-minute forecast degradation events from STREAM_ANALYSIS (ml-forecast-group)
  It invokes the ContextAssembler to pull complete AIE repository intelligence,
  runs MasterLLMAnalyzer to diagnose root cause and remediation, persists the result to
  MongoDB (`analysis_results`), caches the latest analysis in Redis, and updates the Twin.

HOW IT IS CODED:
  LLMConsumer runs two concurrent consumer loops:
    - _consume_anomalies: XREADGROUP on STREAM_ANOMALY / llm-group
    - _consume_forecasts: XREADGROUP on STREAM_ANALYSIS / llm-group
  Uses asyncio.to_thread() for LLM inference so the event loop remains responsive.
  ACKs each message upon completion.

DIAGNOSTIC GUIDE:
  If incidents are detected but no analyses appear in MongoDB:
  1. Check if llm-group exists on STREAM_ANOMALY (created at startup).
  2. Inspect logs for "Master LLM successfully generated analysis".
  3. Query MongoDB collection `analysis_results` or check key `twin:analysis:{app_id}:latest`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

import motor.motor_asyncio
import redis

from bus.streams import (
    STREAM_ANALYSIS,
    STREAM_ANOMALY,
    BusClient,
    get_bus_client,
    get_clean_redis_url,
)
from env_loader import get_mongo_client_kwargs, get_mongo_database, get_mongo_uri, get_redis_url
from llm.analyzer import MasterLLMAnalyzer
from llm.context_assembler import ContextAssembler

logger = logging.getLogger(__name__)

_MONGO_URI = get_mongo_uri()
_DB_NAME = get_mongo_database()
_REDIS_URL = get_redis_url()
_GROUP = "llm-group"


class LLMConsumer:
    """Consumes confirmed anomalies and forecast degradation events, running Master LLM analysis."""

    def __init__(self) -> None:
        self.bus: BusClient = get_bus_client()
        self.r = redis.from_url(_REDIS_URL, decode_responses=True)
        self.mongo = motor.motor_asyncio.AsyncIOMotorClient(_MONGO_URI, **get_mongo_client_kwargs())
        self.col = self.mongo[_DB_NAME]["analysis_results"]
        self.assembler = ContextAssembler()
        self.analyzer = MasterLLMAnalyzer()
        self._last_analysis_time: dict[str, float] = {}

    async def run(self) -> None:
        logger.info("LLMConsumer started — listening on %s and %s for group '%s'", STREAM_ANOMALY, STREAM_ANALYSIS, _GROUP)
        await asyncio.gather(
            self._consume_anomalies(),
            self._consume_forecasts(),
        )

    async def _consume_anomalies(self) -> None:
        consumer_name = "llm-anomaly-worker-1"
        while True:
            try:
                messages = await self.bus.read_async(STREAM_ANOMALY, _GROUP, consumer_name, count=5, block_ms=2000)
                for msg_id, fields in messages:
                    await self._process_anomaly(msg_id, fields)
            except Exception as exc:
                logger.error("LLMConsumer anomaly loop error: %s", exc)
                await asyncio.sleep(2)

    async def _consume_forecasts(self) -> None:
        consumer_name = "llm-forecast-worker-1"
        while True:
            try:
                messages = await self.bus.read_async(STREAM_ANALYSIS, _GROUP, consumer_name, count=5, block_ms=2000)
                for msg_id, fields in messages:
                    # Only process forecast degradation triggers to avoid infinite loops with analysis.completed
                    if fields.get("event_type") == "forecast.degradation":
                        await self._process_forecast(msg_id, fields)
                    else:
                        self.bus.ack(STREAM_ANALYSIS, _GROUP, msg_id)
            except Exception as exc:
                logger.error("LLMConsumer forecast loop error: %s", exc)
                await asyncio.sleep(2)

    async def _process_anomaly(self, msg_id: str, fields: dict) -> None:
        app_id = fields.get("application_id") or fields.get("app_id", "unknown")
        try:
            now = time.time()
            if now - self._last_analysis_time.get(app_id, 0) < 15:
                logger.debug("Skipping anomaly Master LLM for '%s' (cooldown active)", app_id)
                return
            self._last_analysis_time[app_id] = now

            logger.info("LLMConsumer triggered by confirmed anomaly on app='%s' (metric: %s)", app_id, fields.get("top_metric"))
            print(f"[Master LLM] 🧠 Triggered by confirmed anomaly on '{app_id}' ({fields.get('top_metric')}). Assembling AIE architecture context...", flush=True)

            trigger_payload = {
                "top_metric": fields.get("top_metric"),
                "score": float(fields.get("score", 0.0)),
                "verdict": fields.get("verdict"),
                "evaluator_reason": fields.get("evaluator_reason"),
                "metric_snapshot": json.loads(fields.get("metric_snapshot", "{}")),
                "trend": json.loads(fields.get("trend", "[]")),
                "timestamp": fields.get("timestamp"),
            }

            # 1. Assemble complete context
            context = await self.assembler.assemble_context(
                app_id=app_id,
                trigger_type="ANOMALY_CONFIRMED",
                trigger_payload=trigger_payload,
            )

            # 2. Run Master LLM in thread
            analysis = await asyncio.to_thread(self.analyzer.analyze, context)

            # 3. Persist and update
            await self._persist_analysis(app_id, analysis, trigger_type="ANOMALY", trigger_id=msg_id)
            print(f"[Master LLM] ✅ Analysis complete for '{app_id}'. Root cause: {analysis.get('root_cause_component_id', 'unknown')} | Severity: {analysis.get('severity', 'HIGH')} | Persisted to Twin & MongoDB.", flush=True)

        except Exception as exc:
            logger.error("LLMConsumer._process_anomaly failed for msg %s: %s", msg_id, exc)
        finally:
            self.bus.ack(STREAM_ANOMALY, _GROUP, msg_id)

    async def _process_forecast(self, msg_id: str, fields: dict) -> None:
        app_id = fields.get("application_id") or fields.get("app_id", "unknown")
        try:
            now = time.time()
            if now - self._last_analysis_time.get(app_id, 0) < 15:
                logger.debug("Skipping forecast Master LLM for '%s' (cooldown active)", app_id)
                return
            self._last_analysis_time[app_id] = now

            escalations = json.loads(fields.get("escalations", "[]"))
            logger.warning("LLMConsumer triggered by forecast degradation on app='%s' (%d metrics)", app_id, len(escalations))
            print(f"[Master LLM] 🔮 Triggered by forecast degradation on '{app_id}' ({len(escalations)} metrics). Assembling AIE architecture context...", flush=True)

            trigger_payload = {
                "escalations": escalations,
                "timestamp": fields.get("timestamp"),
            }

            # 1. Assemble context
            context = await self.assembler.assemble_context(
                app_id=app_id,
                trigger_type="FORECAST_DEGRADATION",
                trigger_payload=trigger_payload,
            )

            # 2. Run Master LLM in thread
            analysis = await asyncio.to_thread(self.analyzer.analyze, context)

            # 3. Persist and update
            await self._persist_analysis(app_id, analysis, trigger_type="FORECAST", trigger_id=msg_id)
            print(f"[Master LLM] ✅ Predictive analysis complete for '{app_id}'. Preventive mitigation persisted.", flush=True)

        except Exception as exc:
            logger.error("LLMConsumer._process_forecast failed for msg %s: %s", msg_id, exc)
        finally:
            self.bus.ack(STREAM_ANALYSIS, _GROUP, msg_id)

    async def _persist_analysis(
        self,
        app_id: str,
        analysis: dict,
        trigger_type: str,
        trigger_id: str,
    ) -> None:
        """Persists analysis to MongoDB, updates Redis hot cache, and publishes event."""
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        incident_id = analysis.get("incident_id", f"inc-{app_id}")

        doc = {
            **analysis,
            "application_id": app_id,
            "trigger_type": trigger_type,
            "trigger_id": trigger_id,
            "created_at": now_iso,
        }

        # 1. Save to MongoDB
        await self.col.insert_one(doc)

        # 2. Save hot record in Redis
        hot_key = f"twin:analysis:{app_id}:latest"
        self.r.set(hot_key, json.dumps(doc))

        # 3. Publish analysis completed event
        completed_event = {
            "event_type": "analysis.completed",
            "application_id": app_id,
            "incident_id": incident_id,
            "root_cause_component_id": analysis.get("root_cause_component_id", ""),
            "root_cause_summary": analysis.get("root_cause_summary", ""),
            "created_at": now_iso,
        }
        self.bus.publish(STREAM_ANALYSIS, completed_event)
        logger.info("Master LLM analysis persisted for app='%s' (incident: %s)", app_id, incident_id)
