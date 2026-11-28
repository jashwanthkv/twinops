"""
client/components/rate_limiter.py — Token Bucket / Sliding Window Rate Limiter.
"""
from __future__ import annotations
import time
import threading
from fastapi import HTTPException, status
from client.chaos_state import chaos

class RateLimiter:
    def __init__(self, normal_limit_rps: int = 60, choked_limit_rps: int = 3) -> None:
        self.normal_limit_rps = normal_limit_rps
        self.choked_limit_rps = choked_limit_rps
        self._history: list[float] = []
        self._lock = threading.Lock()

    def check(self, client_id: str = "default") -> None:
        now = time.time()
        effective_limit = self.choked_limit_rps if chaos.rate_limit_choke else self.normal_limit_rps
        with self._lock:
            # Prune entries older than 1 second
            self._history = [t for t in self._history if now - t <= 1.0]
            if len(self._history) >= effective_limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded (Max {effective_limit} RPS)",
                )
            self._history.append(now)

rate_limiter = RateLimiter()
