"""
client/components/payment_gateway.py — External 3rd-Party Payment Client (e.g. Stripe/PayPal).
Measures external_api_latency_ms and simulates partner timeouts / 504 errors.
"""
from __future__ import annotations
import random
import time
from fastapi import HTTPException, status
from client.chaos_state import chaos

class PaymentGateway:
    def __init__(self) -> None:
        self.last_measured_latency_ms: float = 35.0

    def process_charge(self, amount: float, currency: str = "USD") -> dict:
        start_time = time.time()
        # Simulated external API network delay
        delay_ms = chaos.payment_delay_ms + random.uniform(-5.0, 10.0)
        delay_ms = max(5.0, delay_ms)
        time.sleep(delay_ms / 1000.0)

        elapsed_ms = (time.time() - start_time) * 1000.0
        self.last_measured_latency_ms = round(elapsed_ms, 2)

        # Fault: simulated external payment gateway outage
        if random.random() < chaos.payment_fail_ratio:
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

payment_gateway = PaymentGateway()
