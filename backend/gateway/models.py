"""
gateway/models.py

All Pydantic models for Gateway request and response payloads.

INCOMING (from SDK middleware):
  TelemetryBatch    — the JSON body for POST /api/v1/telemetry
  Metric            — one metric observation inside a batch
  HeartbeatPayload  — POST /api/v1/telemetry/heartbeat
  RegisterRequest   — POST /api/v1/connectors/register

INTERNAL / OUTGOING:
  CanonicalEvent    — the normalised, trusted object the Gateway puts on the Bus
  ConnectorRecord   — stored registration entry for one monitored application
  GatewayResponse   — standard 202 / 4xx envelope sent back to the SDK
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# ──────────────────────────────────────────────────────────────────────────── #
# Incoming SDK payloads
# ──────────────────────────────────────────────────────────────────────────── #


class Metric(BaseModel):
    """One metric observation sent by the SDK.

    HOW IT IS CODED:
      A flat record with a name, numeric value (nullable when unavailable),
      a unit string, an availability status, and an optional reason string
      explaining why a metric could not be collected. The validator rejects
      names that contain spaces or start with a digit.

    WHAT IT IS DOING:
      Carries a single telemetry measurement (request_rate, heap_usage, …)
      from the customer application to the Gateway. The Gateway validates
      name/unit/value then includes it verbatim in the canonical event.

    DIAGNOSTIC GUIDE:
      If a metric arrives with status=unavailable the reason field tells you
      which SDK feature flag was not enabled. Missing 'name' or 'unit' fields
      produce a 422 that the SDK should log and surface to the operator.
    """

    name: str = Field(..., description="Metric name, snake_case recommended")
    value: float | int | None = Field(None, description="Numeric reading; None when unavailable")
    unit: str = Field(..., description="Unit string, e.g. 'ms', 'bytes', 'requests/second'")
    status: Literal["available", "unavailable", "stale"] = Field(
        default="available",
        description="Whether the value could be collected",
    )
    reason: str | None = Field(
        None,
        description="Machine-readable reason when status != available, e.g. RUNTIME_METRICS_NOT_ENABLED",
    )
    tags: dict[str, str] = Field(
        default_factory=dict,
        description="Optional key-value labels attached to this metric (e.g. endpoint, method)",
    )

    @field_validator("name")
    @classmethod
    def validate_metric_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Metric name must not be empty")
        if v[0].isdigit():
            raise ValueError(f"Metric name must not start with a digit: '{v}'")
        if len(v) > 128:
            raise ValueError(f"Metric name too long (max 128 chars): '{v}'")
        return v

    @field_validator("value")
    @classmethod
    def validate_value(cls, v: float | int | None) -> float | int | None:
        if v is not None and (v != v):  # NaN check
            raise ValueError("Metric value must not be NaN")
        return v


class TelemetryBatch(BaseModel):
    """Full batch payload sent by the SDK on POST /api/v1/telemetry.

    HOW IT IS CODED:
      Top-level envelope with schema_version for forward-compatibility, an
      application_id that identifies the monitored service, an optional
      batch_id for idempotency, a client-side timestamp, and a list of
      Metric objects. The validator requires at least one metric and rejects
      batches older than 10 minutes.

    WHAT IT IS DOING:
      Carries all metrics collected in one SDK flush cycle. The Gateway
      validates this object, attaches server-side trusted metadata
      (tenant_id, received_at, connector_id), and publishes the resulting
      CanonicalEvent to the telemetry.events Redis Stream.

    DIAGNOSTIC GUIDE:
      A 422 on this endpoint usually means schema_version mismatch or a
      metric with an invalid name/value. A 429 means the application is
      sending faster than the configured rate limit. A 503 means the Bus
      (Redis) is unreachable — check Redis health and REDIS_URL in .env.
    """

    schema_version: str = Field(default="1.0", description="SDK schema version")
    application_id: str = Field(..., description="Stable identifier for the monitored application")
    environment: Literal["production", "staging", "development", "testing"] = Field(
        default="production",
        description="Deployment environment",
    )
    source: str = Field(
        default="twinops-sdk",
        description="SDK or agent name that produced this batch",
    )
    batch_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Client-side unique ID for deduplication; auto-generated if omitted",
    )
    timestamp: datetime = Field(
        ...,
        description="Client-side collection timestamp in ISO-8601 / UTC",
    )
    metrics: list[Metric] = Field(..., min_length=1, description="One or more metric observations")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional free-form metadata (SDK version, host, region, …)",
    )

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: datetime) -> datetime:
        # Ensure timezone-aware
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        # Reject timestamps more than 10 minutes in the future
        now = datetime.now(tz=timezone.utc)
        drift_seconds = (v - now).total_seconds()
        if drift_seconds > 600:
            raise ValueError(
                f"Timestamp is {drift_seconds:.0f}s in the future (max 600s allowed)"
            )
        # Reject timestamps older than 1 hour
        if (now - v).total_seconds() > 3600:
            raise ValueError("Timestamp is more than 1 hour old; check system clock on agent")
        return v

    @field_validator("application_id")
    @classmethod
    def validate_app_id(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 128:
            raise ValueError("application_id must be 1–128 characters")
        return v


class HeartbeatPayload(BaseModel):
    """Heartbeat body for POST /api/v1/telemetry/heartbeat.

    HOW IT IS CODED:
      Minimal payload carrying only application_id, schema_version, and an
      optional status string. The Gateway records last_seen on the connector
      record and publishes an application.events entry.

    WHAT IT IS DOING:
      Lets the Digital Twin and monitoring layer know the agent is alive even
      when no metrics changed. If heartbeats stop arriving, the Twin can mark
      the application as potentially degraded.

    DIAGNOSTIC GUIDE:
      Missing heartbeats for >2× the expected interval should trigger an
      alert in the Twin. Check agent process logs and network connectivity
      when heartbeats disappear.
    """

    schema_version: str = Field(default="1.0")
    application_id: str = Field(...)
    status: str = Field(default="alive", description="Agent self-reported status")
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegisterRequest(BaseModel):
    """Registration body for POST /api/v1/connectors/register.

    HOW IT IS CODED:
      Declares a new monitored application to TwinOps. The Gateway stores a
      ConnectorRecord in Redis (hash gateway:connectors:{app_id}) and returns
      an API key the SDK must include in every subsequent request's
      X-TwinOps-API-Key header.

    WHAT IT IS DOING:
      Bootstraps the relationship between a customer application and TwinOps.
      After registration, the connector can send telemetry and heartbeats.
      The API key is a secret; it must be stored securely by the customer.

    DIAGNOSTIC GUIDE:
      409 Conflict means the application_id is already registered — use the
      existing API key or deregister first. 422 means a required field is
      missing or malformed.
    """

    application_id: str = Field(..., description="Stable, unique identifier for the application")
    display_name: str = Field(..., description="Human-readable application name")
    environment: str = Field(default="production")
    owner_email: str | None = Field(None, description="Contact for this application")
    tags: dict[str, str] = Field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────── #
# Internal / Bus models
# ──────────────────────────────────────────────────────────────────────────── #


class CanonicalEvent(BaseModel):
    """Normalised, trusted telemetry event published to the Bus.

    HOW IT IS CODED:
      The Gateway constructs this after validating a TelemetryBatch. It
      attaches server-side fields (tenant_id, connector_id, received_at,
      event_id) that the client cannot forge. The Bus, Digital Twin, ML
      Engine, and LLM all consume this format — never the raw batch.

    WHAT IT IS DOING:
      Provides a single, stable schema for all downstream consumers. Embedding
      received_at and event_id enables idempotent processing and lag
      measurement. The metrics list is preserved from the validated batch.

    DIAGNOSTIC GUIDE:
      If downstream consumers report schema errors, check that the Gateway
      version matches the consumer's expected CanonicalEvent version. Add
      event_version to the stream entry for future migration support.
    """

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: Literal["telemetry.batch", "heartbeat", "connector.registered"] = "telemetry.batch"
    event_version: str = "1.0"

    # Trusted identity — set by Gateway, never from request body
    tenant_id: str
    connector_id: str
    application_id: str
    environment: str

    # Timing
    client_timestamp: datetime
    received_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    # Payload
    batch_id: str
    source: str
    schema_version: str
    metrics: list[dict[str, Any]]  # Metric dicts (serialisable for Redis)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_bus_dict(self) -> dict[str, str]:
        """Flatten to string-only dict for Redis Streams XADD."""
        import json
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "event_version": self.event_version,
            "tenant_id": self.tenant_id,
            "connector_id": self.connector_id,
            "application_id": self.application_id,
            "environment": self.environment,
            "client_timestamp": self.client_timestamp.isoformat(),
            "received_at": self.received_at.isoformat(),
            "batch_id": self.batch_id,
            "source": self.source,
            "schema_version": self.schema_version,
            "metrics": json.dumps(self.metrics),
            "metadata": json.dumps(self.metadata),
        }


class ConnectorRecord(BaseModel):
    """Stored registration record for one monitored application.

    HOW IT IS CODED:
      Stored as a Redis hash under gateway:connectors:{application_id}.
      Contains the hashed API key (never the raw key), connector metadata,
      registration and last-seen timestamps, and a rate-limit budget.

    WHAT IT IS DOING:
      Acts as the authority record for authentication and application
      identification. The Gateway checks this record on every request to
      verify the API key and retrieve tenant_id.

    DIAGNOSTIC GUIDE:
      If a connector disappears from Redis (TTL expired or flush), the SDK
      will receive 401 until re-registered. Set an appropriate TTL or use
      Redis persistence (AOF/RDB) to avoid losing connector records.
    """

    connector_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    application_id: str
    display_name: str
    environment: str
    owner_email: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    tenant_id: str = Field(default="default")
    api_key_hash: str = Field(..., description="bcrypt hash of the API key — raw key never stored")
    registered_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    last_seen_at: datetime | None = None
    is_active: bool = True


# ──────────────────────────────────────────────────────────────────────────── #
# Standard response envelopes
# ──────────────────────────────────────────────────────────────────────────── #


class GatewayAccepted(BaseModel):
    """202 Accepted response after telemetry is published to the Bus."""

    status: Literal["accepted"] = "accepted"
    event_id: str
    batch_id: str
    message: str = "Telemetry accepted and queued"


class GatewayError(BaseModel):
    """4xx / 5xx error envelope."""

    status: Literal["error"] = "error"
    code: str
    message: str
    details: Any = None
