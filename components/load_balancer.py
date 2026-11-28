"""
client/components/load_balancer.py — Ingress Routing and Active Concurrency Tracker.
"""
from __future__ import annotations
import threading
from contextlib import contextmanager

class LoadBalancer:
    def __init__(self) -> None:
        self._active_requests: int = 0
        self._total_requests: int = 0
        self._lock = threading.Lock()

    @contextmanager
    def track_request(self):
        with self._lock:
            self._active_requests += 1
            self._total_requests += 1
        try:
            yield
        finally:
            with self._lock:
                self._active_requests = max(0, self._active_requests - 1)

    @property
    def active_requests(self) -> int:
        return self._active_requests

    @property
    def total_requests(self) -> int:
        return self._total_requests

load_balancer = LoadBalancer()
