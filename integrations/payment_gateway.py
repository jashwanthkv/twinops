"""
client/integrations/payment_gateway.py — External 3rd-Party Payment Client (e.g. Stripe/PayPal).

Measures external_api_latency_ms and simulates partner timeouts / 504 errors.
All network delays are simulated (no real external call) for safe demo use.
"""
from __future__ import annotations
import random
import time
import threading
from fastapi import HTTPException, status
from client.chaos_state import chaos


class ExternalPaymentGatewayClient:
    """Simulated HTTP client for an external payment processing partner.

    Responsibilities:
      - Simulate outbound payment API call with realistic latency
      - Apply chaos-injected failure ratio and extra delay
      - Track last-measured latency for the external_api_latency_ms metric
      - Expose stats and health for telemetry observability
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.last_measured_latency_ms: float = 35.0
        self._total_charges: int = 0
        self._total_failures: int = 0
        self._total_latency_ms: float = 0.0

    # ------------------------------------------------------------------ #
    # Core operation
    # ------------------------------------------------------------------ #

    def process_charge(self, amount: float, currency: str = "USD") -> dict:
        """Execute a simulated payment charge; raises 504 on partner timeout."""
        start_time = time.time()

        # Simulated external network round-trip delay
        delay_ms = chaos.payment_delay_ms + random.uniform(-5.0, 15.0)
        delay_ms = max(5.0, delay_ms)
        time.sleep(delay_ms / 1000.0)

        elapsed_ms = (time.time() - start_time) * 1000.0

        with self._lock:
            self.last_measured_latency_ms = round(elapsed_ms, 2)
            self._total_latency_ms += elapsed_ms
            self._total_charges += 1

        # Chaos: inject random payment gateway failures
        if random.random() < chaos.payment_fail_ratio:
            with self._lock:
                self._total_failures += 1
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"Payment Gateway Partner Timeout after {int(elapsed_ms)}ms",
            )

        return {
            "transaction_id": f"tx_{int(time.time() * 1000)}",
            "amount": amount,
            "currency": currency,
            "status": "APPROVED",
            "latency_ms": self.last_measured_latency_ms,
        }

    # ------------------------------------------------------------------ #
    # Observability helpers
    # ------------------------------------------------------------------ #

    def get_latency(self) -> float:
        """Return the most recently measured round-trip latency in ms."""
        return self.last_measured_latency_ms

    def get_stats(self) -> dict:
        """Return cumulative payment processing statistics."""
        with self._lock:
            avg_latency = (
                round(self._total_latency_ms / self._total_charges, 2)
                if self._total_charges > 0 else 0.0
            )
            return {
                "total_charges": self._total_charges,
                "total_failures": self._total_failures,
                "last_latency_ms": self.last_measured_latency_ms,
                "avg_latency_ms": avg_latency,
                "failure_rate": round(
                    self._total_failures / self._total_charges, 3
                ) if self._total_charges > 0 else 0.0,
            }

    def reset_stats(self) -> None:
        """Reset cumulative counters (used during profile transitions)."""
        with self._lock:
            self._total_charges = 0
            self._total_failures = 0
            self._total_latency_ms = 0.0
            self.last_measured_latency_ms = 35.0

    def is_healthy(self) -> bool:
        """Gateway is healthy when failure rate is below 50%."""
        with self._lock:
            if self._total_charges == 0:
                return True
            return (self._total_failures / self._total_charges) < 0.5


# Singleton
payment_gateway = ExternalPaymentGatewayClient()
