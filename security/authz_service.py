"""
client/security/authz_service.py — Role-Based Access Control Authorization (AuthZ/RBAC).

ARCHITECTURAL ROLE: Security Layer — Authorization
Enforces role-based permissions (RBAC) on all protected operations.
"""
from __future__ import annotations
import threading
from fastapi import HTTPException, status


class RBACAuthorizationService:
    """
    Role-Based Access Control (RBAC) Authorization service.
    Enforces role permissions and tracks authorization decisions.
    """

    _ROLE_HIERARCHY = {
        "guest":   0,
        "shopper": 1,
        "operator": 2,
        "admin":   3,
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total_checks: int = 0
        self._denied_count: int = 0
        self._allowed_count: int = 0

    def check_permission(self, user: dict, required_role: str) -> None:
        """Raise 403 if user lacks the required role level."""
        with self._lock:
            self._total_checks += 1

        user_role = user.get("role", "shopper")
        user_level = self._ROLE_HIERARCHY.get(user_role, 0)
        required_level = self._ROLE_HIERARCHY.get(required_role, 99)

        if user_level < required_level:
            with self._lock:
                self._denied_count += 1
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: '{required_role}' role required, got '{user_role}'",
            )
        with self._lock:
            self._allowed_count += 1

    def get_role_level(self, role: str) -> int:
        """Return the numeric level for a named role."""
        return self._ROLE_HIERARCHY.get(role, 0)

    def get_stats(self) -> dict:
        """Return authorization telemetry: check counts and denial rate."""
        with self._lock:
            total = self._total_checks
            return {
                "total_checks": total,
                "allowed": self._allowed_count,
                "denied": self._denied_count,
                "denial_rate": round(self._denied_count / total, 3) if total > 0 else 0.0,
            }

    def reset_stats(self) -> None:
        """Reset RBAC counters (for testing)."""
        with self._lock:
            self._total_checks = 0
            self._denied_count = 0
            self._allowed_count = 0

    def is_healthy(self) -> bool:
        """Returns True if authorization denial rate is under 20%."""
        stats = self.get_stats()
        return stats["denial_rate"] < 0.20


authz_service = RBACAuthorizationService()
