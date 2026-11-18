"""
aie/persistence/redis/graph_store.py — In-Memory Graph Representation & Adjacency Lists.

Maintains bidirectional adjacency lists using Redis Sets for fast neighbour lookup
and multi-hop architectural dependency traversal without requiring a native graph database.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from typing import Any

from aie.persistence.redis.client import RedisClient, redis_client

logger = logging.getLogger(__name__)


class RedisGraphStore:
    """Manages graph nodes, edges, and incoming/outgoing adjacency sets in Redis."""

    def __init__(self, client: RedisClient | None = None) -> None:
        self._client_mgr = client or redis_client

    async def _get_conn(self) -> Any:
        await self._client_mgr.connect()
        return self._client_mgr.client

    # ── Graph Storage ────────────────────────────────────────────────────

    async def store_graph(
        self,
        app_id: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        """Store nodes, edges, and construct bidirectional adjacency sets."""
        conn = await self._get_conn()

        # Clear existing graph keys for this app
        await self.clear_graph(app_id)

        # 1. Store Nodes
        for node in nodes:
            node_id = str(node.get("id") or node.get("name") or node.get("component_id"))
            if not node_id:
                continue

            node_key = f"aie:graph:{app_id}:node:{node_id}"
            await conn.set(node_key, json.dumps(node))
            await conn.sadd(f"aie:graph:{app_id}:nodes", node_id)

        # 2. Store Edges & Adjacency Sets
        for idx, edge in enumerate(edges):
            src = str(edge.get("source") or edge.get("source_component_id"))
            tgt = str(edge.get("target") or edge.get("target_component_id"))
            rel = str(edge.get("label") or edge.get("relation_type") or "calls")
            edge_id = str(edge.get("id") or edge.get("relationship_id") or f"e-{idx:03d}")

            if not src or not tgt:
                continue

            edge_doc = {
                "id": edge_id,
                "source": src,
                "target": tgt,
                "relation_type": rel,
                "confidence": edge.get("confidence", "confirmed"),
            }
            # Save edge
            await conn.set(f"aie:graph:{app_id}:edge:{edge_id}", json.dumps(edge_doc))

            # Outgoing adjacency: src -> tgt
            out_member = f"{tgt}:{rel}:{edge_id}"
            await conn.sadd(f"aie:graph:{app_id}:adj:{src}:out", out_member)

            # Incoming adjacency: tgt <- src
            in_member = f"{src}:{rel}:{edge_id}"
            await conn.sadd(f"aie:graph:{app_id}:adj:{tgt}:in", in_member)

        logger.debug(
            "Loaded graph into Redis for app '%s': %d nodes, %d edges",
            app_id,
            len(nodes),
            len(edges),
        )

    # ── Neighbour Lookup ─────────────────────────────────────────────────

    async def get_neighbours(
        self,
        app_id: str,
        node_id: str,
        direction: str = "all",
    ) -> dict[str, list[dict[str, str]]]:
        """Retrieve immediate incoming and/or outgoing neighbours."""
        conn = await self._get_conn()
        res: dict[str, list[dict[str, str]]] = {"outgoing": [], "incoming": []}

        if direction in ("out", "all"):
            out_members = await conn.smembers(f"aie:graph:{app_id}:adj:{node_id}:out")
            for m in out_members:
                parts = m.split(":", 2)
                if len(parts) >= 2:
                    tgt = parts[0]
                    rel = parts[1]
                    eid = parts[2] if len(parts) > 2 else ""
                    res["outgoing"].append({"target": tgt, "relation": rel, "edge_id": eid})

        if direction in ("in", "all"):
            in_members = await conn.smembers(f"aie:graph:{app_id}:adj:{node_id}:in")
            for m in in_members:
                parts = m.split(":", 2)
                if len(parts) >= 2:
                    src = parts[0]
                    rel = parts[1]
                    eid = parts[2] if len(parts) > 2 else ""
                    res["incoming"].append({"source": src, "relation": rel, "edge_id": eid})

        return res

    # ── Multi-hop Traversal ──────────────────────────────────────────────

    async def traverse_paths(
        self,
        app_id: str,
        start_node_id: str,
        direction: str = "out",
        max_depth: int = 3,
    ) -> list[dict[str, Any]]:
        """Breadth-first search traversal starting from a node up to max_depth."""
        conn = await self._get_conn()
        visited: set[str] = {start_node_id}
        queue: deque[tuple[str, int, list[dict[str, str]]]] = deque([(start_node_id, 0, [])])
        paths: list[dict[str, Any]] = []

        while queue:
            curr_node, depth, current_path = queue.popleft()
            if depth >= max_depth:
                continue

            adj_key = f"aie:graph:{app_id}:adj:{curr_node}:{direction}"
            members = await conn.smembers(adj_key)

            for m in members:
                parts = m.split(":", 2)
                if len(parts) < 2:
                    continue
                next_node = parts[0]
                rel = parts[1]
                edge_id = parts[2] if len(parts) > 2 else ""

                step = {
                    "from": curr_node,
                    "to": next_node,
                    "relation": rel,
                    "edge_id": edge_id,
                    "depth": depth + 1,
                }
                new_path = list(current_path) + [step]
                paths.append({
                    "start": start_node_id,
                    "destination": next_node,
                    "depth": depth + 1,
                    "path": new_path,
                })

                if next_node not in visited and depth + 1 < max_depth:
                    visited.add(next_node)
                    queue.append((next_node, depth + 1, new_path))

        return paths

    # ── Full Graph Retrieval ─────────────────────────────────────────────

    async def get_graph(self, app_id: str) -> dict[str, Any]:
        """Reconstruct entire visual architecture graph from Redis."""
        conn = await self._get_conn()
        node_ids = await conn.smembers(f"aie:graph:{app_id}:nodes")
        nodes = []
        for nid in sorted(node_ids):
            raw = await conn.get(f"aie:graph:{app_id}:node:{nid}")
            if raw:
                nodes.append(json.loads(raw))

        edge_keys = await conn.keys(f"aie:graph:{app_id}:edge:*")
        edges = []
        for ek in sorted(edge_keys):
            raw = await conn.get(ek)
            if raw:
                edges.append(json.loads(raw))

        return {
            "application_id": app_id,
            "nodes": nodes,
            "edges": edges,
        }

    # ── Graph Eviction ───────────────────────────────────────────────────

    async def clear_graph(self, app_id: str) -> int:
        """Remove all graph keys for an application."""
        conn = await self._get_conn()
        keys_to_del = await conn.keys(f"aie:graph:{app_id}:*")
        if keys_to_del:
            return await conn.delete(*keys_to_del)
        return 0
