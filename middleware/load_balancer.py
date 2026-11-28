"""
client/middleware/load_balancer.py — Ingress Router and Active Concurrency Tracker.

Wraps every incoming request with an active-connection context manager so
the telemetry agent can observe concurrency at any instant.
"""
from __future__ import annotations
import threading
from contextlib import contextmanager


class IngressLoadBalancer:
    """Tracks active/total request concurrency for ingress-level observability.

    Responsibilities:
      - Increment/decrement active_requests atomically around each HTTP request
      - Maintain a lifetime total_requests counter
      - Expose per-instance stats and health for the telemetry agent
    """

    def __init__(self) -> None:
        self._active_requests: int = 0
        self._total_requests: int = 0
        self._peak_concurrent: int = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Context manager — called by route handlers
    # ------------------------------------------------------------------ #

    @contextmanager
    def track_request(self):
        """Context manager that bookends a request with counter updates."""
        with self._lock:
            self._active_requests += 1
            self._total_requests += 1
            if self._active_requests > self._peak_concurrent:
                self._peak_concurrent = self._active_requests
        try:
            yield
        finally:
            with self._lock:
                self._active_requests = max(0, self._active_requests - 1)

    # ------------------------------------------------------------------ #
    # Properties for the telemetry collector
    # ------------------------------------------------------------------ #

    @property
    def active_requests(self) -> int:
        """Current in-flight request count."""
        return self._active_requests

    @property
    def total_requests(self) -> int:
        """Lifetime request count since process start."""
        return self._total_requests

    # ------------------------------------------------------------------ #
    # Observability helpers
    # ------------------------------------------------------------------ #

    def get_stats(self) -> dict:
        """Return current concurrency snapshot for diagnostics."""
        with self._lock:
            return {
                "active_requests": self._active_requests,
                "total_requests": self._total_requests,
                "peak_concurrent": self._peak_concurrent,
            }

    def reset_stats(self) -> None:
        """Reset peak counter (totals are never reset — running since start)."""
        with self._lock:
            self._peak_concurrent = 0

    def is_healthy(self) -> bool:
        """Load balancer is healthy when active count is sane."""
        return self._active_requests >= 0


# Singleton
load_balancer = IngressLoadBalancer()
