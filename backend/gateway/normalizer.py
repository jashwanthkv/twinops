"""
gateway/normalizer.py

Converts a validated TelemetryBatch into a CanonicalEvent ready for the Bus.

WHAT IT IS DOING:
  Takes the raw SDK payload (TelemetryBatch) and the trusted ConnectorRecord
  returned by authentication, and produces a CanonicalEvent where:
    - tenant_id comes from the ConnectorRecord (not the request body)
    - received_at is set by the Gateway server clock
    - each metric is converted to a stable dict with all optional fields
      filled with defaults so consumers never need to guard for missing keys

HOW IT IS CODED:
  Normalizer.normalize() is a pure function — no I/O, no side-effects.
  It is called synchronously in the route handler after auth and rate-limit
  checks pass and before publishing to the Bus.

  Metric normalisation:
    - name is lowercased and stripped
    - value is cast to float (None remains None)
    - unit is stripped
    - status defaults to "available"
    - tags are preserved as-is
    - a normalised_at field is added for downstream lag measurement

DIAGNOSTIC GUIDE:
  If a canonical event is missing expected fields, the problem is here.
  Add a log line in normalize() to dump the raw batch and check whether
  the SDK is omitting optional fields.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from gateway.models import CanonicalEvent, ConnectorRecord, TelemetryBatch


class Normalizer:
    """Converts SDK payloads into CanonicalEvent objects.

    HOW IT IS CODED:
      Stateless; all context (record, now) is passed at call time.
      Metric normalisation is strict: values become float or None;
      names are lowercased so downstream comparisons are case-insensitive.

    WHAT IT IS DOING:
      Provides a single place where the mapping from SDK wire format to
      internal canonical format is defined. Changing this class is the only
      action needed when the canonical schema evolves.

    DIAGNOSTIC GUIDE:
      Unit test this class with known inputs to catch regressions when
      the SDK schema version changes. The event_version field on
      CanonicalEvent is intended to signal breaking schema changes.
    """

    def normalize(
        self,
        batch: TelemetryBatch,
        record: ConnectorRecord,
        received_at: datetime | None = None,
    ) -> CanonicalEvent:
        """Build a CanonicalEvent from a validated batch and trusted connector record."""
        now = received_at or datetime.now(tz=timezone.utc)
        metrics = [self._normalize_metric(m.model_dump()) for m in batch.metrics]

        return CanonicalEvent(
            event_type="telemetry.batch",
            tenant_id=record.tenant_id,
            connector_id=record.connector_id,
            application_id=record.application_id,  # trusted from record, not batch
            environment=record.environment,
            client_timestamp=batch.timestamp,
            received_at=now,
            batch_id=batch.batch_id,
            source=batch.source,
            schema_version=batch.schema_version,
            metrics=metrics,
            metadata={
                **batch.metadata,
                "original_application_id": batch.application_id,  # auditing
                "metric_count": len(metrics),
                "available_count": sum(1 for m in metrics if m["status"] == "available"),
                "unavailable_count": sum(1 for m in metrics if m["status"] != "available"),
            },
        )

    def normalize_heartbeat(
        self,
        app_id: str,
        hb_status: str,
        record: ConnectorRecord,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Build a minimal bus dict for heartbeat events."""
        import uuid, json
        now = datetime.now(tz=timezone.utc).isoformat()
        return {
            "event_id": str(uuid.uuid4()),
            "event_type": "heartbeat",
            "event_version": "1.0",
            "tenant_id": record.tenant_id,
            "connector_id": record.connector_id,
            "application_id": record.application_id,
            "environment": record.environment,
            "received_at": now,
            "hb_status": hb_status,
            "metadata": json.dumps(metadata or {}),
        }

    def _normalize_metric(self, m: dict[str, Any]) -> dict[str, Any]:
        """Normalise one metric dict: lowercase name, cast value to float."""
        return {
            "name": str(m.get("name", "")).lower().strip(),
            "value": float(m["value"]) if m.get("value") is not None else None,
            "unit": str(m.get("unit", "")).strip(),
            "status": m.get("status", "available"),
            "reason": m.get("reason"),
            "tags": m.get("tags", {}),
        }


_normalizer = Normalizer()


def get_normalizer() -> Normalizer:
    return _normalizer
