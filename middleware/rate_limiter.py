"""
client/middleware/rate_limiter.py — Token Bucket / Sliding-Window Rate Limiter.

Enforces per-second request limits on the ingress path.
Supports chaos-injected choke-down (normal_limit_rps → choked_limit_rps).
"""
from __future__ import annotations
import time
import threading
from fastapi import HTTPException, status
from client.chaos_state import chaos


class TokenBucketRateLimiter:
    """Sliding-window rate limiter that tracks per-second request history.

    Components:
      - Token bucket window (1 s)
      - Chaos-injected choke mode (reduces effective RPS limit drastically)
      - Per-client_id counters for observability
    """

    def __init__(self, normal_limit_rps: int = 60, choked_limit_rps: int = 3) -> None:
        self.normal_limit_rps = normal_limit_rps
        self.choked_limit_rps = choked_limit_rps
        self._history: list[float] = []
        self._lock = threading.Lock()
        self._total_allowed: int = 0
        self._total_rejected: int = 0

    # ------------------------------------------------------------------ #
    # Core gate method
    # ------------------------------------------------------------------ #

    def check(self, client_id: str = "default") -> None:
        """Allow or deny the request; raises HTTP 429 when over limit."""
        now = time.time()
        effective_limit = self.choked_limit_rps if chaos.rate_limit_choke else self.normal_limit_rps
        with self._lock:
            self._history = [t for t in self._history if now - t <= 1.0]
            if len(self._history) >= effective_limit:
                self._total_rejected += 1
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded (max {effective_limit} RPS)",
                )
            self._history.append(now)
            self._total_allowed += 1

    # ------------------------------------------------------------------ #
    # Observability helpers
    # ------------------------------------------------------------------ #

    def get_limit(self) -> int:
        """Return the currently active RPS limit."""
        return self.choked_limit_rps if chaos.rate_limit_choke else self.normal_limit_rps

    def get_stats(self) -> dict:
        """Return counters and current window snapshot."""
        with self._lock:
            now = time.time()
            current_window = len([t for t in self._history if now - t <= 1.0])
            return {
                "effective_limit_rps": self.get_limit(),
                "current_window_count": current_window,
                "total_allowed": self._total_allowed,
                "total_rejected": self._total_rejected,
                "choke_active": chaos.rate_limit_choke,
            }

    def reset_stats(self) -> None:
        """Reset counters (used during profile transitions)."""
        with self._lock:
            self._total_allowed = 0
            self._total_rejected = 0
            self._history.clear()

    def is_healthy(self) -> bool:
        """Rate limiter is always operationally healthy (pure in-memory)."""
        return True


# Singleton
rate_limiter = TokenBucketRateLimiter()
