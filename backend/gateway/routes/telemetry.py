"""
gateway/routes/telemetry.py

Route handlers for telemetry ingestion endpoints.

WHAT IT IS DOING:
  Implements the two primary ingestion endpoints:
    POST /api/v1/telemetry          — accept a telemetry batch
    POST /api/v1/telemetry/heartbeat — accept an agent heartbeat

  Both endpoints:
    1. Authenticate via require_connector dependency (API key → ConnectorRecord)
    2. Rate-limit the authenticated application
    3. Validate the request body (Pydantic does this automatically)
    4. Normalise to CanonicalEvent
    5. Publish to the Bus (XADD)
    6. Return 202 Accepted only after the Bus has accepted the event

  The handler returns 202 (not 200) because the event is queued for async
  processing — the Gateway makes no promise about when downstream consumers
  will process it.

HOW IT IS CODED:
  All dependencies (auth, rate limiter, bus client, normalizer) are injected
  via FastAPI's Depends() so they can be replaced in tests without changing
  route logic. The Bus publish call is synchronous (redis-py) but wrapped in
  asyncio.to_thread() to avoid blocking the event loop.

DIAGNOSTIC GUIDE:
  422 Unprocessable Entity → Pydantic validation failed; check request body
    against TelemetryBatch schema (especially timestamp format and metric names).
  401/403 → see gateway/auth.py diagnostic notes.
  429 → see gateway/rate_limiter.py diagnostic notes.
  503 Service Unavailable → Bus (Redis) is down; Gateway cannot accept events
    until Redis is restored. The 503 body contains a "BUS_UNAVAILABLE" code.
  Slow endpoint (p99 > 20ms) → check Redis latency; the pipeline and XADD
    should complete in < 2ms on a local Redis.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from bus.streams import STREAM_APP_EVENTS, STREAM_TELEMETRY, get_bus_client
from gateway.auth import ConnectorRecord, get_registry, require_connector
from gateway.models import (
    GatewayAccepted,
    HeartbeatPayload,
    TelemetryBatch,
)
from gateway.normalizer import get_normalizer
from gateway.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/telemetry", tags=["telemetry"])


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=GatewayAccepted,
    summary="Receive a telemetry batch from the SDK",
)
async def ingest_telemetry(
    batch: TelemetryBatch,
    connector: ConnectorRecord = Depends(require_connector),
) -> GatewayAccepted:
    """Accept a telemetry batch, normalise it, and publish to the Bus.

    HOW IT IS CODED:
      1. require_connector authenticates X-TwinOps-API-Key and returns the
         trusted ConnectorRecord (tenant_id, connector_id, environment).
      2. Rate limiter checks per-app quota (sliding window in Redis).
      3. Pydantic already validated the batch body before this handler runs.
      4. Normalizer converts the batch to CanonicalEvent, attaching server
         metadata the client cannot forge.
      5. Bus XADD publishes the event; if Redis is unreachable a 503 is raised
         so the SDK can retry — the event is NOT silently dropped.
      6. 202 Accepted is returned with event_id and batch_id for correlation.

    WHAT IT IS DOING:
      This is the hot path — every SDK flush lands here. Keep it fast:
      no DB writes, no ML, no twin updates, no LLM calls happen here.
      Those are downstream consumers' responsibility.

    DIAGNOSTIC GUIDE:
      Duplicate batch_id → client sent the same batch twice; consumers must
      deduplicate by batch_id. This is expected during retries.
      High latency → profile Redis XADD time; consider reducing batch size
      or increasing SDK flush interval.
    """
    # Rate limit
    rl = get_rate_limiter()
    rl.check(connector.application_id)

    # Normalise
    normalizer = get_normalizer()
    event = normalizer.normalize(batch, connector)

    # Publish to Bus (run in thread to avoid blocking event loop)
    bus = get_bus_client()
    try:
        msg_id = await asyncio.to_thread(bus.publish, STREAM_TELEMETRY, event.to_bus_dict())
    except Exception as exc:
        logger.error("Bus publish failed for batch %s: %s", batch.batch_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "BUS_UNAVAILABLE",
                "message": "Message bus is unreachable; please retry",
            },
        )

    logger.info(
        "Telemetry accepted app=%s batch=%s metrics=%d bus_id=%s",
        connector.application_id,
        batch.batch_id,
        len(batch.metrics),
        msg_id,
    )
    return GatewayAccepted(event_id=event.event_id, batch_id=batch.batch_id)


@router.post(
    "/heartbeat",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=GatewayAccepted,
    summary="Receive a heartbeat from the SDK agent",
)
async def ingest_heartbeat(
    payload: HeartbeatPayload,
    connector: ConnectorRecord = Depends(require_connector),
) -> GatewayAccepted:
    """Accept a heartbeat and publish an application.events entry to the Bus.

    HOW IT IS CODED:
      Heartbeats carry no metrics — just a liveness signal and optional status.
      The ConnectorRegistry.touch_last_seen() call already happened inside
      require_connector so the connector record is up-to-date before we
      publish to the bus.

    WHAT IT IS DOING:
      Lets the Digital Twin track agent liveness. If heartbeats stop arriving,
      the Twin should raise an alert after (2 × expected interval).

    DIAGNOSTIC GUIDE:
      Heartbeat latency > 50ms → Redis latency issue or gateway overload.
      Missing heartbeats in Twin state → check application.events consumer
      (twin-group) for processing errors.
    """
    rl = get_rate_limiter()
    rl.check(connector.application_id, limit=120)  # more lenient limit for heartbeats

    normalizer = get_normalizer()
    hb_dict = normalizer.normalize_heartbeat(
        app_id=connector.application_id,
        hb_status=payload.status,
        record=connector,
        metadata=payload.metadata,
    )

    bus = get_bus_client()
    try:
        await asyncio.to_thread(bus.publish, STREAM_APP_EVENTS, hb_dict)
    except Exception as exc:
        logger.error("Bus publish failed for heartbeat app=%s: %s", connector.application_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "BUS_UNAVAILABLE", "message": "Message bus is unreachable; please retry"},
        )

    logger.info("Heartbeat accepted app=%s status=%s", connector.application_id, payload.status)
    return GatewayAccepted(
        event_id=hb_dict["event_id"],
        batch_id=hb_dict["event_id"],
        message="Heartbeat accepted",
    )
