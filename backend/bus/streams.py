"""
bus/streams.py

Redis Streams message bus for TwinOps.

WHAT IT IS DOING:
  Defines the stream names, consumer group names, and all read/write helpers
  that Gateway, ML Engine, Digital Twin, and LLM use to exchange events.
  Uses Redis Streams (XADD / XREADGROUP / XACK) so each consumer group
  receives every message independently and unprocessed messages are retried.

HOW IT IS CODED:
  BusClient wraps a redis.Redis connection. It is instantiated once at
  startup (get_bus_client()) and re-used across requests.
  - publish()       — XADD to a stream with MAXLEN trimming
  - create_groups() — XGROUP CREATE for all defined consumer groups
  - read()          — XREADGROUP for a named group and consumer
  - ack()           — XACK after successful processing
  - pending()       — XPENDING to surface stuck messages
  Dead-letter logic: messages that exceed MAX_DELIVERY_COUNT are moved to
  the matching *.dead stream by the consumer before XACK.

DIAGNOSTIC GUIDE:
  Bus not reachable → check REDIS_URL in .env and that Redis is running.
  Consumer group already exists error on startup is safe (MKSTREAM / SETID).
  High pending count on a stream means a consumer is crashing after read
  but before ack — check consumer logs and idempotency guards.
  Use XPENDING <stream> <group> - + 10 in redis-cli to inspect stuck msgs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import redis

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────── #
# Stream and consumer group names (single source of truth)
# ──────────────────────────────────────────────────────────────────────────── #

# MVP streams — keep this list small until you need more
STREAM_TELEMETRY  = "twinops:telemetry.events"
STREAM_ANOMALY    = "twinops:anomaly.events"
STREAM_ANALYSIS   = "twinops:analysis.events"
STREAM_APP_EVENTS = "twinops:application.events"

# Dead-letter streams — failed messages land here after MAX_DELIVERY_COUNT
STREAM_TELEMETRY_DL  = "twinops:telemetry.events.dead"
STREAM_ANOMALY_DL    = "twinops:anomaly.events.dead"
STREAM_ANALYSIS_DL   = "twinops:analysis.events.dead"

# Consumer groups per stream
# Each group receives every message independently (fan-out)
CONSUMER_GROUPS: dict[str, list[str]] = {
    STREAM_TELEMETRY: [
        "storage-group",      # persists telemetry to MongoDB
        "twin-group",         # updates Digital Twin state
        "ml-forecast-group",  # feeds Linear Regression forecast path
        "ml-anomaly-group",   # feeds Isolation Forest anomaly path
    ],
    STREAM_ANOMALY: [
        "twin-group",      # updates anomaly state in Twin
        "llm-group",       # triggers root-cause analysis
        "storage-group",   # persists anomaly record
    ],
    STREAM_ANALYSIS: [
        "storage-group",   # persists LLM analysis
        "llm-group",       # Master LLM consumes forecast/anomaly triggers
    ],
    STREAM_APP_EVENTS: [
        "twin-group",      # twin tracks app lifecycle
        "storage-group",
    ],
}

# Trim streams to avoid unbounded growth
STREAM_MAXLEN = int(os.getenv("BUS_STREAM_MAXLEN", "50000"))
MAX_DELIVERY_COUNT = int(os.getenv("BUS_MAX_DELIVERY_COUNT", "5"))


# ──────────────────────────────────────────────────────────────────────────── #
# Client
# ──────────────────────────────────────────────────────────────────────────── #

_bus_client: "BusClient | None" = None


from env_loader import get_redis_url

def get_clean_redis_url() -> str:
    return get_redis_url()


def get_bus_client() -> "BusClient":
    """Return the singleton BusClient, creating it on first call."""
    global _bus_client
    if _bus_client is None:
        _bus_client = BusClient.from_env()
    return _bus_client


class BusClient:
    """Thin wrapper around redis.Redis that speaks the TwinOps stream protocol."""

    def __init__(self, redis_client: redis.Redis) -> None:
        self._r = redis_client

    @classmethod
    def from_env(cls) -> "BusClient":
        url = get_clean_redis_url()
        r = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=5)
        try:
            r.ping()
            logger.info("BusClient connected to Redis at %s", url)
        except redis.ConnectionError as exc:
            logger.error("BusClient cannot reach Redis at %s: %s", url, exc)
            raise
        return cls(r)

    # ── Publishing ──────────────────────────────────────────────────────── #

    def publish(
        self,
        stream: str,
        fields: dict[str, str],
        maxlen: int = STREAM_MAXLEN,
    ) -> str:
        """XADD to stream, return the Redis message ID.

        Uses approximate trimming (~ maxlen) for performance.
        All field values must be strings — callers should serialise complex
        values to JSON before calling publish().
        """
        msg_id = self._r.xadd(stream, fields, maxlen=maxlen, approximate=True)  # type: ignore[arg-type]
        logger.debug("Published to %s id=%s", stream, msg_id)
        return msg_id  # type: ignore[return-value]

    # ── Consumer group setup ─────────────────────────────────────────────── #

    def create_groups(self) -> None:
        """Ensure every stream and consumer group exists.

        Called once at Gateway startup. Safe to call repeatedly:
        BUSYGROUP errors (group already exists) are silently ignored.

        HOW IT IS CODED:
          Iterates CONSUMER_GROUPS dict and calls XGROUP CREATE … $ MKSTREAM
          for each (stream, group) pair. The $ start-id means only new
          messages are delivered to freshly created groups; existing data in
          the stream (if any) is not replayed.
        """
        for stream, groups in CONSUMER_GROUPS.items():
            for group in groups:
                try:
                    self._r.xgroup_create(stream, group, id="$", mkstream=True)
                    logger.info("Created consumer group '%s' on stream '%s'", group, stream)
                except redis.exceptions.ResponseError as e:
                    if "BUSYGROUP" in str(e):
                        logger.debug("Group '%s' on '%s' already exists — skipping", group, stream)
                    else:
                        raise

    # ── Consuming ───────────────────────────────────────────────────────── #

    def read(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int = 10,
        block_ms: int = 2000,
    ) -> list[tuple[str, dict[str, str]]]:
        """XREADGROUP, return list of (message_id, fields) tuples.

        HOW IT IS CODED:
          Calls XREADGROUP GROUP <group> <consumer> COUNT <count>
          BLOCK <block_ms> STREAMS <stream> >
          The '>' special ID means only undelivered messages are fetched.
          Returns an empty list on timeout.

        DIAGNOSTIC GUIDE:
          If you see the same message_id repeatedly without an ack, the
          consumer is crashing after read. Add idempotency logic and check
          exception handling in the consumer loop.
        """
        result = self._r.xreadgroup(
            groupname=group,
            consumername=consumer,
            streams={stream: ">"},
            count=count,
            block=block_ms,
        )
        if not result:
            return []
        messages: list[tuple[str, dict[str, str]]] = []
        for _stream_name, entries in result:
            for msg_id, fields in entries:
                messages.append((msg_id, fields))
        return messages

    async def read_async(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int = 10,
        block_ms: int = 2000,
    ) -> list[tuple[str, dict[str, str]]]:
        """Non-blocking async wrapper that delegates synchronous XREADGROUP to a background thread."""
        return await asyncio.to_thread(self.read, stream, group, consumer, count, block_ms)

    def ack(self, stream: str, group: str, *message_ids: str) -> int:
        """XACK one or more message IDs after successful processing."""
        return self._r.xack(stream, group, *message_ids)  # type: ignore[return-value]

    def move_to_dead_letter(
        self,
        source_stream: str,
        message_id: str,
        fields: dict[str, str],
        reason: str,
    ) -> None:
        """Move a failed message to the dead-letter stream and ACK the original.

        HOW IT IS CODED:
          Publishes the original fields plus a 'dead_letter_reason' and
          'dead_letter_source' to the *.dead stream, then ACKs the original
          message in the source stream so it is not redelivered.

        WHAT IT IS DOING:
          Prevents poison-pill messages from blocking a consumer group
          indefinitely. Operators can inspect *.dead streams to diagnose
          systematic processing failures.
        """
        dl_stream = source_stream + ".dead"
        dl_fields = {
            **fields,
            "dead_letter_reason": reason,
            "dead_letter_source": source_stream,
            "dead_letter_original_id": message_id,
            "dead_letter_at": str(time.time()),
        }
        self._r.xadd(dl_stream, dl_fields)  # type: ignore[arg-type]
        logger.warning(
            "Message %s from %s moved to dead-letter: %s", message_id, source_stream, reason
        )

    def pending_count(self, stream: str, group: str) -> int:
        """Return number of pending (unacknowledged) messages for a group."""
        info = self._r.xpending(stream, group)
        return info.get("pending", 0) if isinstance(info, dict) else 0

    def ping(self) -> bool:
        """Check Redis connectivity."""
        try:
            return self._r.ping()
        except Exception:
            return False
