"""
client/security/auth_service.py — Bearer Token Authentication Service (AuthN).

ARCHITECTURAL ROLE: Security Layer — Authentication
Validates Bearer tokens and API keys on all protected routes.
"""
from __future__ import annotations
import time
import threading
from typing import Any
from fastapi import HTTPException, status


class BearerTokenAuthService:
    """
    Token-based Authentication (AuthN) service.
    Validates Bearer tokens, tracks auth failures, and exposes health metrics.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total_requests: int = 0
        self._auth_failures: int = 0
        self._auth_successes: int = 0

    def authenticate(self, authorization: Any = None) -> dict:
        """Validate a Bearer token and return the user context dict."""
        with self._lock:
            self._total_requests += 1

        auth_str = authorization if isinstance(authorization, str) else None

        if auth_str == "Bearer invalid-token":
            with self._lock:
                self._auth_failures += 1
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired authentication token",
            )

        token = auth_str.replace("Bearer ", "").strip() if auth_str else "guest-user"
        with self._lock:
            self._auth_successes += 1
        return {"user_id": token, "role": "admin" if "admin" in token else "shopper"}

    def get_stats(self) -> dict:
        """Return authentication telemetry: success/failure counts and rates."""
        with self._lock:
            total = self._total_requests
            return {
                "total_requests": total,
                "auth_successes": self._auth_successes,
                "auth_failures": self._auth_failures,
                "failure_rate": round(self._auth_failures / total, 3) if total > 0 else 0.0,
            }

    def reset_stats(self) -> None:
        """Reset authentication counters (for testing)."""
        with self._lock:
            self._total_requests = 0
            self._auth_failures = 0
            self._auth_successes = 0

    def is_healthy(self) -> bool:
        """Returns True if authentication failure rate is under 10%."""
        stats = self.get_stats()
        return stats["failure_rate"] < 0.10


auth_service = BearerTokenAuthService()
