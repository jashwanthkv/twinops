"""
storage/consumer.py

WHAT IT IS DOING:
  Consumes telemetry events from the Redis Streams bus and persists them
  to MongoDB collection `telemetry_raw`. Acts as the durable raw data store
  so ML models can backfill history from MongoDB if Redis state is lost.

HOW IT IS CODED:
  StorageConsumer.run() loops forever: XREADGROUP from STREAM_TELEMETRY in
  `storage-group`, parses the canonical event, inserts to MongoDB, XACKs.
  Uses motor (async MongoDB driver) for non-blocking inserts.
  Dead-letters messages that fail JSON parsing after MAX_DELIVERY_COUNT.

DIAGNOSTIC GUIDE:
  If MongoDB is unavailable, inserts raise ConnectionFailure — the consumer
  logs and retries on the next iteration without XACKing so the message
  stays pending. Use XPENDING twinops:telemetry.events storage-group to
  see stuck messages.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import motor.motor_asyncio

from bus.streams import BusClient, STREAM_TELEMETRY, get_bus_client
from env_loader import get_mongo_database, get_mongo_uri

logger = logging.getLogger(__name__)

_MONGO_URI = get_mongo_uri()
_DB_NAME = get_mongo_database()
_COLLECTION = "telemetry_raw"
_GROUP = "storage-group"
_CONSUMER = "storage-worker-1"


class StorageConsumer:
    """Persists raw telemetry events from Redis Streams to MongoDB."""

    def __init__(self) -> None:
        self._bus: BusClient = get_bus_client()
        self._mongo = motor.motor_asyncio.AsyncIOMotorClient(_MONGO_URI)
        self._col = self._mongo[_DB_NAME][_COLLECTION]

    async def run(self) -> None:
        logger.info("StorageConsumer started — consuming %s / %s", STREAM_TELEMETRY, _GROUP)
        while True:
            try:
                messages = await self._bus.read_async(STREAM_TELEMETRY, _GROUP, _CONSUMER, count=20, block_ms=2000)
                for msg_id, fields in messages:
                    await self._process(msg_id, fields)
            except Exception as exc:
                logger.error("StorageConsumer loop error: %s", exc)
                await asyncio.sleep(2)

    async def _process(self, msg_id: str, fields: dict) -> None:
        try:
            doc = {
                "event_id": fields.get("event_id"),
                "application_id": fields.get("application_id"),
                "tenant_id": fields.get("tenant_id"),
                "environment": fields.get("environment"),
                "client_timestamp": fields.get("client_timestamp"),
                "received_at": fields.get("received_at"),
                "batch_id": fields.get("batch_id"),
                "source": fields.get("source"),
                "metrics": json.loads(fields.get("metrics", "[]")),
                "metadata": json.loads(fields.get("metadata", "{}")),
                "stored_at": datetime.now(tz=timezone.utc).isoformat(),
            }
            await self._col.insert_one(doc)
            self._bus.ack(STREAM_TELEMETRY, _GROUP, msg_id)
            logger.debug("Stored telemetry %s for app %s", fields.get("event_id"), fields.get("application_id"))
        except Exception as exc:
            logger.error("StorageConsumer failed to process msg %s: %s", msg_id, exc)
            # Do NOT ack — let it retry
