"""
client/components/authz_service.py — Authorization Service (AuthZ / RBAC).
Enforces role-based permissions.
"""
from __future__ import annotations
from fastapi import HTTPException, status

class AuthZService:
    def check_permission(self, user: dict, required_role: str) -> None:
        user_role = user.get("role", "shopper")
        if required_role == "admin" and user_role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Admin privileges required",
            )

authz_service = AuthZService()
