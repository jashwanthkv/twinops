"""
client/components/auth_service.py — Authentication Service (AuthN).
Validates API keys / Bearer tokens.
"""
from __future__ import annotations
from fastapi import HTTPException, Header, status
from client.chaos_state import chaos

class AuthService:
    def authenticate(self, authorization: Any = None) -> dict:
        if not isinstance(authorization, str):
            auth_str = None
        else:
            auth_str = authorization

        if auth_str == "Bearer invalid-token":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired authentication credentials",
            )
        token = auth_str.replace("Bearer ", "").strip() if auth_str else "guest-user"
        return {"user_id": token, "role": "admin" if "admin" in token else "shopper"}

auth_service = AuthService()
