"""
aie/persistence/redis/knowledge_store.py — Fast In-Memory Knowledge Store.

Maintains active application components, profiles, and contracts in Redis
for sub-millisecond retrieval by TwinOps SLM and backend services.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aie.persistence.redis.client import RedisClient, redis_client

logger = logging.getLogger(__name__)


class RedisKnowledgeStore:
    """Manages active application knowledge documents in Redis."""

    def __init__(self, client: RedisClient | None = None) -> None:
        self._client_mgr = client or redis_client

    async def _get_conn(self) -> Any:
        await self._client_mgr.connect()
        return self._client_mgr.client

    # ── Active Version Tracking ──────────────────────────────────────────

    async def set_active_version(
        self,
        app_id: str,
        run_id: str,
        version: int,
        commit_hash: str | None = None,
    ) -> None:
        conn = await self._get_conn()
        key = f"aie:app:{app_id}:active"
        val = {
            "application_id": app_id,
            "run_id": run_id,
            "version": version,
            "commit_hash": commit_hash,
        }
        await conn.set(key, json.dumps(val))

    async def get_active_version(self, app_id: str) -> dict[str, Any] | None:
        conn = await self._get_conn()
        key = f"aie:app:{app_id}:active"
        raw = await conn.get(key)
        return json.loads(raw) if raw else None

    # ── Application Metadata & Profile ───────────────────────────────────

    async def set_metadata(self, app_id: str, metadata: dict[str, Any]) -> None:
        conn = await self._get_conn()
        key = f"aie:app:{app_id}:metadata"
        await conn.set(key, json.dumps(metadata))

    async def get_metadata(self, app_id: str) -> dict[str, Any] | None:
        conn = await self._get_conn()
        raw = await conn.get(f"aie:app:{app_id}:metadata")
        return json.loads(raw) if raw else None

    async def set_technology_profile(self, app_id: str, profile: dict[str, Any]) -> None:
        conn = await self._get_conn()
        key = f"aie:app:{app_id}:technology_profile"
        await conn.set(key, json.dumps(profile))

    async def get_technology_profile(self, app_id: str) -> dict[str, Any] | None:
        conn = await self._get_conn()
        raw = await conn.get(f"aie:app:{app_id}:technology_profile")
        return json.loads(raw) if raw else None

    # ── Component Storage & Retrieval ────────────────────────────────────

    async def store_component(self, app_id: str, component: dict[str, Any]) -> None:
        conn = await self._get_conn()
        comp_id = component.get("component_id") or component.get("id") or component.get("name")
        if not comp_id:
            return

        # Store component document
        doc_key = f"aie:app:{app_id}:component:{comp_id}"
        await conn.set(doc_key, json.dumps(component))

        # Add to components set
        set_key = f"aie:app:{app_id}:components"
        await conn.sadd(set_key, comp_id)

    async def store_components_batch(self, app_id: str, components: list[dict[str, Any]]) -> None:
        for comp in components:
            await self.store_component(app_id, comp)

    async def get_component(self, app_id: str, comp_id: str) -> dict[str, Any] | None:
        conn = await self._get_conn()
        raw = await conn.get(f"aie:app:{app_id}:component:{comp_id}")
        return json.loads(raw) if raw else None

    async def get_all_components(self, app_id: str) -> list[dict[str, Any]]:
        conn = await self._get_conn()
        set_key = f"aie:app:{app_id}:components"
        comp_ids = await conn.smembers(set_key)
        if not comp_ids:
            return []

        results = []
        for cid in sorted(comp_ids):
            raw = await conn.get(f"aie:app:{app_id}:component:{cid}")
            if raw:
                results.append(json.loads(raw))
        return results

    # ── Contract B & Contract C ──────────────────────────────────────────

    async def store_contract_b(self, app_id: str, descriptor: dict[str, Any]) -> None:
        conn = await self._get_conn()
        key = f"aie:app:{app_id}:contract_b"
        await conn.set(key, json.dumps(descriptor))

    async def get_contract_b(self, app_id: str) -> dict[str, Any] | None:
        conn = await self._get_conn()
        raw = await conn.get(f"aie:app:{app_id}:contract_b")
        return json.loads(raw) if raw else None

    async def store_contract_c(self, app_id: str, capabilities: dict[str, Any]) -> None:
        conn = await self._get_conn()
        key = f"aie:app:{app_id}:contract_c"
        await conn.set(key, json.dumps(capabilities))

    async def get_contract_c(self, app_id: str) -> dict[str, Any] | None:
        conn = await self._get_conn()
        raw = await conn.get(f"aie:app:{app_id}:contract_c")
        return json.loads(raw) if raw else None

    # ── Version Cleanup ──────────────────────────────────────────────────

    async def clear_app(self, app_id: str) -> int:
        """Clear all active knowledge keys for an application to prevent version mixing."""
        conn = await self._get_conn()
        keys_to_del = await conn.keys(f"aie:app:{app_id}:*")
        if keys_to_del:
            return await conn.delete(*keys_to_del)
        return 0
