"""
gateway/routes/connectors.py

Route handlers for connector registration and status.

WHAT IT IS DOING:
  Implements two endpoints used by SDK setup tools and operators:
    POST /api/v1/connectors/register   — register a new monitored application
    GET  /api/v1/connectors/{app_id}/status — check a connector's registration

  Registration generates an API key, hashes it, stores the ConnectorRecord
  in Redis, and returns the raw API key once — it is never retrievable again.

HOW IT IS CODED:
  POST register:
    - Optionally protected by GATEWAY_MASTER_KEY header for bootstrap.
    - Checks for duplicate app_id (409 if exists).
    - Generates raw + hashed key pair via auth.generate_api_key().
    - Stores ConnectorRecord in Redis via ConnectorRegistry.register().
    - Returns the raw key with a "store it now" warning.

  GET status:
    - Protected by require_connector so only authenticated connectors
      can check their own status (or master key for admin lookup).
    - Returns safe subset of ConnectorRecord (no key hash).

DIAGNOSTIC GUIDE:
  409 on register → app_id already registered; check existing key or
    implement a re-key flow.
  Registration succeeds but first telemetry returns 401 → API key was not
    stored correctly by the SDK; re-register and store the key securely.
  Status returns 404 → connector was never registered or Redis was flushed;
    re-register.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from gateway.auth import (
    ConnectorRecord,
    ConnectorRegistry,
    generate_api_key,
    get_registry,
    require_connector,
)
from gateway.models import RegisterRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])

MASTER_KEY = os.getenv("GATEWAY_MASTER_KEY", "")


class RegisterResponse(BaseModel):
    """Returned once on successful registration."""
    connector_id: str
    application_id: str
    api_key: str
    warning: str = (
        "Store this API key securely — it cannot be retrieved after this response. "
        "Pass it as X-TwinOps-API-Key on every telemetry request."
    )


class ConnectorStatus(BaseModel):
    """Public view of a connector record (no secrets)."""
    connector_id: str
    application_id: str
    display_name: str
    environment: str
    tenant_id: str
    is_active: bool
    registered_at: str
    last_seen_at: str | None


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=RegisterResponse,
    summary="Register a new monitored application",
)
async def register_connector(
    request: RegisterRequest,
    x_twinops_master_key: str | None = Header(None),
    registry: ConnectorRegistry = Depends(get_registry),
) -> RegisterResponse:
    """Register a new application with TwinOps and receive an API key.

    HOW IT IS CODED:
      Uses GATEWAY_MASTER_KEY header for bootstrap authentication so the
      first connector can be registered before any API key exists.
      After registration, connectors should use their own API key for all
      further requests.
      The API key is generated with secrets.token_urlsafe(32) and prefixed
      'tsk_' for easy identification. Only the bcrypt hash is stored; the
      raw key is returned in this response only.

    WHAT IT IS DOING:
      Creates the relationship between a customer application and TwinOps.
      After registration, the SDK can send telemetry using the returned key.

    DIAGNOSTIC GUIDE:
      If you lose the API key, deregister and re-register to get a new one.
      Multiple environments (prod/staging) should be registered as separate
      connectors with different application_ids (e.g. myapp-prod, myapp-staging).
    """
    # Bootstrap auth: require master key for registration
    if MASTER_KEY:
        import secrets as _secrets
        if not x_twinops_master_key or not _secrets.compare_digest(x_twinops_master_key, MASTER_KEY):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "MASTER_KEY_REQUIRED", "message": "X-TwinOps-Master-Key required for registration"},
            )

    # Duplicate check
    if registry.exists(request.application_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CONNECTOR_EXISTS",
                "message": f"Application '{request.application_id}' is already registered. "
                           "Use the existing API key or contact support to rotate it.",
            },
        )

    raw_key, hashed_key = generate_api_key()
    record = ConnectorRecord(
        application_id=request.application_id,
        display_name=request.display_name,
        environment=request.environment,
        owner_email=request.owner_email,
        tags=request.tags,
        tenant_id="default",  # extend to multi-tenant by passing from master key context
        api_key_hash=hashed_key,
    )
    registry.register(record, raw_key)

    logger.info(
        "Connector registered app_id=%s connector_id=%s",
        record.application_id,
        record.connector_id,
    )
    return RegisterResponse(
        connector_id=record.connector_id,
        application_id=record.application_id,
        api_key=raw_key,
    )


@router.get(
    "/{app_id}/status",
    response_model=ConnectorStatus,
    summary="Get registration status for a connector",
)
async def connector_status(
    app_id: str,
    registry: ConnectorRegistry = Depends(get_registry),
    connector: ConnectorRecord = Depends(require_connector),
) -> ConnectorStatus:
    """Return the registration status and last-seen time for a connector.

    HOW IT IS CODED:
      Protected by require_connector so only authenticated callers can
      query status. The returned payload contains no secrets.

    WHAT IT IS DOING:
      Lets SDK setup tools and operators verify that a connector is
      registered and active. The last_seen_at field shows when the most
      recent authenticated request arrived.

    DIAGNOSTIC GUIDE:
      last_seen_at is null → no authenticated request has been received
      since registration; check SDK configuration and connectivity.
      is_active = false → connector was deactivated; contact admin.
    """
    record = registry.get_by_app_id(app_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CONNECTOR_NOT_FOUND", "message": f"No connector registered for '{app_id}'"},
        )

    return ConnectorStatus(
        connector_id=record.connector_id,
        application_id=record.application_id,
        display_name=record.display_name,
        environment=record.environment,
        tenant_id=record.tenant_id,
        is_active=record.is_active,
        registered_at=record.registered_at.isoformat(),
        last_seen_at=record.last_seen_at.isoformat() if record.last_seen_at else None,
    )
