"""
aie/persistence/services/retrieval_service.py — High-Performance Knowledge Retrieval for TwinOps & SLM.

Provides fast, version-safe queries across Redis (sub-millisecond), MongoDB (source of truth),
and ChromaDB (semantic search). Supports automatic Redis recovery and graph traversal.
"""

from __future__ import annotations

import logging
from typing import Any

from aie.persistence.mongodb.client import mongo_client
from aie.persistence.mongodb.repositories import (
    AnalysisRunRepository,
    ApplicationRepository,
    ComponentRepository,
    ContractRepository,
    GraphRepository,
    RelationshipRepository,
    TechnologyProfileRepository,
)
from aie.persistence.redis.client import redis_client
from aie.persistence.redis.graph_store import RedisGraphStore
from aie.persistence.redis.knowledge_store import RedisKnowledgeStore
from aie.persistence.vector.chroma_store import chroma_store

logger = logging.getLogger(__name__)


class KnowledgeRetrievalService:
    """Unified retrieval interface prioritizing Redis, backed by MongoDB and ChromaDB."""

    def __init__(self) -> None:
        self.app_repo = ApplicationRepository()
        self.run_repo = AnalysisRunRepository()
        self.profile_repo = TechnologyProfileRepository()
        self.comp_repo = ComponentRepository()
        self.rel_repo = RelationshipRepository()
        self.graph_repo = GraphRepository()
        self.contract_repo = ContractRepository()

        self.redis_knowledge = RedisKnowledgeStore()
        self.redis_graph = RedisGraphStore()
        self.chroma = chroma_store

    # ── 1. Component Retrieval ───────────────────────────────────────────

    async def get_component(self, app_id: str, comp_id: str) -> dict[str, Any] | None:
        """Retrieve a specific component by ID (Redis with MongoDB fallback)."""
        # Try Redis first (<1ms)
        comp = await self.redis_knowledge.get_component(app_id, comp_id)
        if comp is not None:
            return comp

        # Fallback to MongoDB
        comp = await self.comp_repo.get_by_id(app_id, comp_id)
        if comp is not None:
            # Re-cache in Redis
            await self.redis_knowledge.store_component(app_id, comp)
        return comp

    async def get_all_components(self, app_id: str) -> list[dict[str, Any]]:
        """Retrieve all active components of an application."""
        comps = await self.redis_knowledge.get_all_components(app_id)
        if comps:
            return comps

        # Fallback to MongoDB
        comps = await self.comp_repo.get_all(app_id)
        if comps:
            await self.redis_knowledge.store_components_batch(app_id, comps)
        return comps

    # ── 2. Graph & Relationship Queries ──────────────────────────────────

    async def get_component_neighbours(
        self,
        app_id: str,
        comp_id: str,
        direction: str = "all",
    ) -> dict[str, list[dict[str, str]]]:
        """Retrieve immediate incoming and outgoing neighbours from Redis adjacency sets."""
        neighbours = await self.redis_graph.get_neighbours(app_id, comp_id, direction=direction)
        # If empty and Redis cold, rebuild and retry
        if not neighbours["incoming"] and not neighbours["outgoing"]:
            active = await self.redis_knowledge.get_active_version(app_id)
            if not active:
                await self.rebuild_redis_from_mongodb(app_id)
                neighbours = await self.redis_graph.get_neighbours(app_id, comp_id, direction=direction)
        return neighbours

    async def traverse_relationships(
        self,
        app_id: str,
        start_comp_id: str,
        direction: str = "out",
        max_depth: int = 3,
    ) -> list[dict[str, Any]]:
        """Traverse multi-hop dependency paths starting from a component."""
        return await self.redis_graph.traverse_paths(
            app_id=app_id,
            start_node_id=start_comp_id,
            direction=direction,
            max_depth=max_depth,
        )

    async def get_architecture_graph(self, app_id: str) -> dict[str, Any] | None:
        """Retrieve complete visual architecture graph."""
        graph = await self.redis_graph.get_graph(app_id)
        if graph and graph.get("nodes"):
            return graph
        return await self.graph_repo.get_graph(app_id)

    # ── 3. Contracts B & C ───────────────────────────────────────────────

    async def get_contract_b(self, app_id: str) -> dict[str, Any] | None:
        """Retrieve Contract B Application Descriptor for TwinOps Digital Twin."""
        desc = await self.redis_knowledge.get_contract_b(app_id)
        if desc is not None:
            return desc
        return await self.contract_repo.get_contract_b(app_id)

    async def get_contract_c(self, app_id: str) -> dict[str, Any] | None:
        """Retrieve Contract C Capability Catalog for TwinOps Operational Levers."""
        caps = await self.redis_knowledge.get_contract_c(app_id)
        if caps is not None:
            return caps
        return await self.contract_repo.get_contract_c(app_id)

    # ── 4. Semantic Search via ChromaDB ──────────────────────────────────

    async def semantic_search(
        self,
        app_id: str,
        query: str,
        component_id: str | None = None,
        content_type: str | None = None,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Perform semantic search scoped to an application and active analysis run."""
        active = await self.redis_knowledge.get_active_version(app_id)
        run_id = active.get("run_id") if active else None

        return self.chroma.semantic_search(
            app_id=app_id,
            query=query,
            run_id=run_id,
            component_id=component_id,
            content_type=content_type,
            n_results=n_results,
        )

    # ── 5. Rebuild Redis from MongoDB ────────────────────────────────────

    async def rebuild_redis_from_mongodb(self, app_id: str) -> bool:
        """Reconstruct in-memory active state and graph adjacency lists in Redis from MongoDB."""
        logger.info("Rebuilding Redis cache from MongoDB for application '%s'...", app_id)

        # 1. Fetch Application Record
        app_doc = await self.app_repo.get(app_id)
        if not app_doc:
            logger.warning("Cannot rebuild Redis: application '%s' not found in MongoDB", app_id)
            return False

        active_run_id = app_doc.get("active_run_id")
        version = app_doc.get("active_version", 1)

        # 2. Fetch Latest Successful Run if no active_run_id
        if not active_run_id:
            latest_run = await self.run_repo.get_latest_successful(app_id)
            if not latest_run:
                logger.warning("No successful analysis found in MongoDB for app '%s'", app_id)
                return False
            active_run_id = latest_run["run_id"]
            version = latest_run.get("version", 1)

        # 3. Clear existing Redis keys for this application
        await self.redis_knowledge.clear_app(app_id)
        await self.redis_graph.clear_graph(app_id)

        # 4. Set Active Version Pointer
        await self.redis_knowledge.set_active_version(app_id, active_run_id, version)

        # 5. Load Profile & Metadata
        profile = await self.profile_repo.get(app_id, active_run_id) or {}
        await self.redis_knowledge.set_technology_profile(app_id, profile)

        metadata = {
            "application_id": app_id,
            "name": app_doc.get("name", app_id),
            "repo_url": app_doc.get("repo_url", ""),
            "version": version,
            "run_id": active_run_id,
        }
        await self.redis_knowledge.set_metadata(app_id, metadata)

        # 6. Load Components
        components = await self.comp_repo.get_all(app_id, active_run_id)
        await self.redis_knowledge.store_components_batch(app_id, components)

        # 7. Load Contracts B & C
        contract_b = await self.contract_repo.get_contract_b(app_id, active_run_id)
        if contract_b:
            await self.redis_knowledge.store_contract_b(app_id, contract_b)

        contract_c = await self.contract_repo.get_contract_c(app_id, active_run_id)
        if contract_c:
            await self.redis_knowledge.store_contract_c(app_id, contract_c)

        # 8. Rebuild Graph Adjacency Lists
        graph = await self.graph_repo.get_graph(app_id, active_run_id)
        if graph:
            nodes = graph.get("nodes", [])
            edges = graph.get("edges", [])
            await self.redis_graph.store_graph(app_id, nodes, edges)
        else:
            # Construct from components and relationships
            relationships = await self.rel_repo.get_all(app_id, active_run_id)
            nodes = [{"id": c.get("component_id"), "name": c.get("name"), "type": c.get("component_type")} for c in components]
            edges = [{"source": r.get("source_component_id"), "target": r.get("target_component_id"), "label": r.get("relation_type")} for r in relationships]
            await self.redis_graph.store_graph(app_id, nodes, edges)

        logger.info("Successfully rebuilt Redis in-memory cache for application '%s'", app_id)
        return True

    # ── 6. Version Management & Replacement ──────────────────────────────

    async def get_latest_successful_analysis(self, app_id: str) -> dict[str, Any] | None:
        """Query MongoDB for the latest successful analysis run."""
        return await self.run_repo.get_latest_successful(app_id)

    async def delete_or_replace_version(self, app_id: str, run_id: str) -> bool:
        """Safely delete an analysis version and update active version if needed."""
        # 1. Clean ChromaDB embeddings for this version
        self.chroma.delete_run_embeddings(app_id, run_id)

        # 2. Check if this is the active version
        app_doc = await self.app_repo.get(app_id)
        if app_doc and app_doc.get("active_run_id") == run_id:
            # Promote previous successful version
            all_runs = await self.run_repo.list_runs(app_id)
            remaining_runs = [r for r in all_runs if r["run_id"] != run_id and r.get("status") == "complete"]
            if remaining_runs:
                prev_run = remaining_runs[0]
                await self.app_repo.set_active_version(app_id, prev_run["run_id"], prev_run.get("version", 1))
                await self.rebuild_redis_from_mongodb(app_id)
            else:
                await self.redis_knowledge.clear_app(app_id)
                await self.redis_graph.clear_graph(app_id)

        return True


# Singleton service
retrieval_service = KnowledgeRetrievalService()
