"""
twin/state.py

WHAT IT IS DOING:
  Defines the ApplicationTwinState — the live in-memory representation of one
  monitored application's current health. Implements a simple state machine:
  UNKNOWN → HEALTHY → DEGRADED → CRITICAL with transitions driven by anomaly
  events and telemetry metric readings.

HOW IT IS CODED:
  TwinHealth is a string enum: UNKNOWN, HEALTHY, DEGRADED, CRITICAL.
  ApplicationTwinState is a dataclass holding:
    - component health per component_id (a dict of ComponentHealth)
    - active anomalies list
    - latest metric snapshot (last 18 metric values)
    - latest forecast dict (metric → predicted_15min value)
    - latest analysis_id (from LLM)
    - last_seen timestamp
  transition() is a pure function that returns the new health given current
  health and an event (anomaly_detected, anomaly_resolved, telemetry_ok).

DIAGNOSTIC GUIDE:
  If a component is stuck in CRITICAL, check active_anomalies list. Each entry
  should auto-clear when the same metric returns to normal for 5 consecutive
  readings. HGET twin:{app_id}:state health in redis-cli shows current health.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

TwinHealth = Literal["UNKNOWN", "HEALTHY", "DEGRADED", "CRITICAL"]


@dataclass
class ComponentMetric:
    value: float
    unit: str
    ts: float = field(default_factory=time.time)


@dataclass
class ActiveAnomaly:
    metric: str
    score: float
    top_metric: str
    detected_at: float = field(default_factory=time.time)
    evaluator_verdict: str = "REAL"


@dataclass
class ApplicationTwinState:
    app_id: str
    health: TwinHealth = "UNKNOWN"
    last_seen: float = field(default_factory=time.time)
    metrics: dict[str, ComponentMetric] = field(default_factory=dict)
    active_anomalies: list[ActiveAnomaly] = field(default_factory=list)
    forecast: dict[str, float] = field(default_factory=dict)
    latest_analysis_id: str = ""
    consecutive_ok: int = 0  # consecutive telemetry batches with no anomaly

    def to_redis_dict(self) -> dict:
        """Serialize to flat string dict for Redis HSET."""
        import json
        return {
            "app_id": self.app_id,
            "health": self.health,
            "last_seen": str(self.last_seen),
            "metrics": json.dumps({
                k: {"value": v.value, "unit": v.unit, "ts": v.ts}
                for k, v in self.metrics.items()
            }),
            "active_anomalies": json.dumps([
                {"metric": a.metric, "score": a.score, "top_metric": a.top_metric,
                 "detected_at": a.detected_at, "evaluator_verdict": a.evaluator_verdict}
                for a in self.active_anomalies
            ]),
            "forecast": json.dumps(self.forecast),
            "latest_analysis_id": self.latest_analysis_id,
            "consecutive_ok": str(self.consecutive_ok),
        }

    @classmethod
    def from_redis_dict(cls, app_id: str, d: dict) -> "ApplicationTwinState":
        import json
        metrics_raw = json.loads(d.get("metrics", "{}"))
        metrics = {
            k: ComponentMetric(value=v["value"], unit=v.get("unit", ""), ts=v.get("ts", 0.0))
            for k, v in metrics_raw.items()
        }
        anomalies_raw = json.loads(d.get("active_anomalies", "[]"))
        anomalies = [
            ActiveAnomaly(
                metric=a["metric"], score=a["score"], top_metric=a.get("top_metric", ""),
                detected_at=a.get("detected_at", 0.0), evaluator_verdict=a.get("evaluator_verdict", "REAL"),
            )
            for a in anomalies_raw
        ]
        return cls(
            app_id=app_id,
            health=d.get("health", "UNKNOWN"),
            last_seen=float(d.get("last_seen", 0.0)),
            metrics=metrics,
            active_anomalies=anomalies,
            forecast=json.loads(d.get("forecast", "{}")),
            latest_analysis_id=d.get("latest_analysis_id", ""),
            consecutive_ok=int(d.get("consecutive_ok", 0)),
        )


OK_READINGS_TO_HEAL = 5  # consecutive clean readings before health improves


def transition(state: ApplicationTwinState, event: str, **kwargs) -> ApplicationTwinState:
    """
    Pure state transition. Events:
      "telemetry_ok"       → no anomaly detected; may heal
      "anomaly_detected"   → anomaly confirmed by evaluator
      "anomaly_resolved"   → metric returned to normal
    Returns the SAME state object (mutated in-place) for simplicity.
    """
    if event == "telemetry_ok":
        state.last_seen = time.time()
        state.consecutive_ok += 1
        if state.health == "UNKNOWN":
            state.health = "HEALTHY"
        elif state.health in ("DEGRADED", "CRITICAL") and state.consecutive_ok >= OK_READINGS_TO_HEAL:
            if not state.active_anomalies:
                state.health = "HEALTHY"
                state.consecutive_ok = 0
            else:
                state.health = "DEGRADED"

    elif event == "anomaly_detected":
        state.consecutive_ok = 0
        state.last_seen = time.time()
        metric = kwargs.get("metric", "unknown")
        score = float(kwargs.get("score", 0.0))
        top_metric = kwargs.get("top_metric", metric)
        # Add to active anomalies (avoid duplicates by metric name)
        existing = [a for a in state.active_anomalies if a.metric != metric]
        existing.append(ActiveAnomaly(metric=metric, score=score, top_metric=top_metric))
        state.active_anomalies = existing

        # Determine new health
        num_anomalies = len(state.active_anomalies)
        if num_anomalies >= 3 or score > 2.0:
            state.health = "CRITICAL"
        else:
            state.health = "DEGRADED"

    elif event == "anomaly_resolved":
        metric = kwargs.get("metric", "")
        state.active_anomalies = [a for a in state.active_anomalies if a.metric != metric]
        if not state.active_anomalies:
            state.consecutive_ok = 0  # reset; needs OK_READINGS_TO_HEAL more clean reads

    return state
