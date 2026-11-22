"""
gateway/auth.py

Authentication and connector registry for the TwinOps Gateway.

WHAT IT IS DOING:
  Every request to the Gateway must carry an X-TwinOps-API-Key header.
  This module verifies that key against the hash stored in Redis
  (gateway:connectors:{app_id}) and returns the ConnectorRecord so the
  route handler has trusted tenant_id and connector_id without reading
  anything from the request body.

HOW IT IS CODED:
  ConnectorRegistry stores ConnectorRecord objects as Redis hashes.
  API keys are generated with secrets.token_urlsafe(32) on registration and
  immediately hashed with bcrypt — the raw key is returned once to the caller
  and never stored. Verification uses bcrypt.checkpw which is constant-time.

  GATEWAY_MASTER_KEY (env var) is a bootstrap key that lets the first
  connector be registered without an existing key. Remove it in production
  or scope it to an internal network.

DIAGNOSTIC GUIDE:
  401 → key missing or not found in Redis; check header spelling and key value.
  403 → connector exists but is_active=False; re-activate or re-register.
  Redis connection failure at auth time → Gateway returns 503; check Redis.
  Slow auth → bcrypt rounds too high; default is 12, reduce if p99 auth > 50ms.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Annotated

import redis
from fastapi import Depends, Header, HTTPException, status

from gateway.models import ConnectorRecord

logger = logging.getLogger(__name__)

BCRYPT_ROUNDS = int(os.getenv("GATEWAY_BCRYPT_ROUNDS", "12"))
MASTER_KEY = os.getenv("GATEWAY_MASTER_KEY", "")  # Bootstrap key; empty = disabled

# Redis key patterns
_CONNECTOR_KEY = "gateway:connectors:{app_id}"
_API_KEY_INDEX = "gateway:apikey_index:{key_hash}"  # maps key_hash → app_id


def _hash_key(raw_key: str) -> str:
    """SHA-256 hash for index lookups (fast, not for auth — bcrypt does auth)."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _bcrypt_hash(raw_key: str) -> str:
    """bcrypt hash for secure storage."""
    try:
        import bcrypt
        return bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt(BCRYPT_ROUNDS)).decode()
    except ImportError:
        # Fallback if bcrypt not installed — use SHA-256 (less secure, OK for dev)
        logger.warning("bcrypt not installed; falling back to SHA-256 for key hashing (install bcrypt for production)")
        return "sha256:" + _hash_key(raw_key)


def _bcrypt_verify(raw_key: str, stored_hash: str) -> bool:
    """Verify raw_key against bcrypt or SHA-256 fallback hash."""
    if stored_hash.startswith("sha256:"):
        return secrets.compare_digest(stored_hash[7:], _hash_key(raw_key))
    try:
        import bcrypt
        return bcrypt.checkpw(raw_key.encode(), stored_hash.encode())
    except ImportError:
        return False


class ConnectorRegistry:
    """CRUD for ConnectorRecord objects stored as Redis hashes.

    HOW IT IS CODED:
      Each connector is stored as a flat Redis hash at
      gateway:connectors:{app_id}.  A secondary index
      gateway:apikey_index:{sha256(key)} → app_id allows O(1) lookup
      without iterating all connectors.

    WHAT IT IS DOING:
      Gateway uses this registry to authenticate requests and retrieve
      the trusted tenant_id / connector_id for each API key.

    DIAGNOSTIC GUIDE:
      Call registry.list_connectors() to dump all registered apps.
      If a connector is missing, re-register — there is no restore path
      unless you have Redis persistence or a backup.
    """

    def __init__(self, redis_client: redis.Redis) -> None:
        self._r = redis_client

    def register(self, record: ConnectorRecord, raw_api_key: str) -> None:
        """Store the connector record and build the lookup index."""
        key = _CONNECTOR_KEY.format(app_id=record.application_id)
        data = record.model_dump(mode="json")
        # Store tags as JSON string in the hash
        data["tags"] = json.dumps(data.get("tags", {}))
        data["registered_at"] = record.registered_at.isoformat()
        data["last_seen_at"] = record.last_seen_at.isoformat() if record.last_seen_at else ""
        data["is_active"] = "1" if record.is_active else "0"
        self._r.hset(key, mapping={str(k): str(v) for k, v in data.items()})

        # Secondary index: sha256(key) → app_id
        idx_key = _API_KEY_INDEX.format(key_hash=_hash_key(raw_api_key))
        self._r.set(idx_key, record.application_id)
        logger.info("Registered connector app_id=%s connector_id=%s", record.application_id, record.connector_id)

    def get_by_app_id(self, app_id: str) -> ConnectorRecord | None:
        """Fetch connector record by application_id."""
        key = _CONNECTOR_KEY.format(app_id=app_id)
        data = self._r.hgetall(key)
        if not data:
            return None
        return self._parse_record(data)

    def get_app_id_for_key(self, raw_key: str) -> str | None:
        """Look up which application_id owns this raw API key."""
        idx_key = _API_KEY_INDEX.format(key_hash=_hash_key(raw_key))
        return self._r.get(idx_key)

    def touch_last_seen(self, app_id: str) -> None:
        """Update last_seen_at timestamp (called on every authenticated request)."""
        key = _CONNECTOR_KEY.format(app_id=app_id)
        self._r.hset(key, "last_seen_at", datetime.now(tz=timezone.utc).isoformat())

    def exists(self, app_id: str) -> bool:
        return bool(self._r.exists(_CONNECTOR_KEY.format(app_id=app_id)))

    def set_active(self, app_id: str, active: bool) -> None:
        key = _CONNECTOR_KEY.format(app_id=app_id)
        self._r.hset(key, "is_active", "1" if active else "0")

    def list_connectors(self) -> list[str]:
        """Return all registered application_ids."""
        pattern = _CONNECTOR_KEY.format(app_id="*")
        return [k.split(":")[-1] for k in self._r.keys(pattern)]

    def _parse_record(self, data: dict) -> ConnectorRecord:
        return ConnectorRecord(
            connector_id=data.get("connector_id", ""),
            application_id=data.get("application_id", ""),
            display_name=data.get("display_name", ""),
            environment=data.get("environment", "production"),
            owner_email=data.get("owner_email") or None,
            tags=json.loads(data.get("tags", "{}")),
            tenant_id=data.get("tenant_id", "default"),
            api_key_hash=data.get("api_key_hash", ""),
            registered_at=datetime.fromisoformat(data["registered_at"]) if data.get("registered_at") else datetime.now(tz=timezone.utc),
            last_seen_at=datetime.fromisoformat(data["last_seen_at"]) if data.get("last_seen_at") else None,
            is_active=data.get("is_active", "1") == "1",
        )


def generate_api_key() -> tuple[str, str]:
    """Generate a raw API key and its bcrypt hash.

    Returns (raw_key, hashed_key). Store only hashed_key; return raw_key to caller once.
    """
    raw = "tsk_" + secrets.token_urlsafe(32)
    hashed = _bcrypt_hash(raw)
    return raw, hashed


# ──────────────────────────────────────────────────────────────────────────── #
# FastAPI dependency
# ──────────────────────────────────────────────────────────────────────────── #

_registry: ConnectorRegistry | None = None


def get_registry() -> ConnectorRegistry:
    global _registry
    if _registry is None:
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        r = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=5)
        _registry = ConnectorRegistry(r)
    return _registry


async def require_connector(
    x_twinops_api_key: Annotated[str | None, Header()] = None,
    registry: ConnectorRegistry = Depends(get_registry),
) -> ConnectorRecord:
    """FastAPI dependency — authenticate and return the ConnectorRecord.

    HOW IT IS CODED:
      1. Reads X-TwinOps-API-Key header (None → 401).
      2. Checks master key bypass (dev only).
      3. Looks up app_id via SHA-256 index (O(1) Redis GET).
      4. Fetches ConnectorRecord (Redis HGETALL).
      5. bcrypt-verifies the raw key against stored hash.
      6. Checks is_active flag.
      7. Updates last_seen_at and returns the record.

    WHAT IT IS DOING:
      Provides a trusted ConnectorRecord to every route handler so the
      handler can attach tenant_id and connector_id to canonical events
      without reading anything from the untrusted request body.

    DIAGNOSTIC GUIDE:
      401 "Missing API key" → header not set by SDK; check SDK config.
      401 "Unknown API key" → key not in index; connector may be unregistered.
      401 "Invalid API key" → key found but bcrypt mismatch; wrong key.
      403 "Connector inactive" → set_active(app_id, True) to restore.
    """
    if not x_twinops_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "MISSING_API_KEY", "message": "X-TwinOps-API-Key header is required"},
        )

    # Master key bypass (bootstrap / dev only)
    if MASTER_KEY and secrets.compare_digest(x_twinops_api_key, MASTER_KEY):
        # Return a synthetic master connector record
        return ConnectorRecord(
            application_id="__master__",
            display_name="Master Key",
            environment="internal",
            tenant_id="system",
            api_key_hash="__master__",
        )

    # Look up by key hash
    try:
        app_id = registry.get_app_id_for_key(x_twinops_api_key)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "REDIS_UNAVAILABLE", "message": "Redis cache is offline; please start Redis"},
        )

    if not app_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNKNOWN_API_KEY", "message": "API key not recognised"},
        )

    try:
        record = registry.get_by_app_id(app_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "REDIS_UNAVAILABLE", "message": "Redis cache is offline; please start Redis"},
        )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "CONNECTOR_NOT_FOUND", "message": "Connector record missing"},
        )

    if not _bcrypt_verify(x_twinops_api_key, record.api_key_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_API_KEY", "message": "API key is invalid"},
        )

    if not record.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "CONNECTOR_INACTIVE", "message": "This connector has been deactivated"},
        )

    registry.touch_last_seen(app_id)
    return record
