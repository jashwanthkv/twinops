"""
aie/persistence/services/persistence_service.py — Orchestrates Multi-Tier Knowledge Persistence.

Coordinates atomic saving of completed AIE analysis runs across:
1. MongoDB — Permanent, durable source of truth with immutable version history.
2. Redis — In-memory active cache with graph adjacency lists for SLM fast lookup.
3. ChromaDB — Semantic vector index over component knowledge chunks.
"""

from __future__ import annotations

import logging
from typing import Any

from aie.models import (
    AnalysisRun,
    DiscoveredComponent,
    DiscoveredRelationship,
    FileEntry,
    TechnologyProfile,
)
from aie.persistence.mongodb.client import mongo_client
from aie.persistence.mongodb.repositories import (
    AnalysisRunRepository,
    ApplicationRepository,
    ComponentRepository,
    ContractRepository,
    FileInventoryRepository,
    GraphRepository,
    RelationshipRepository,
    TechnologyProfileRepository,
)
from aie.persistence.redis.client import redis_client
from aie.persistence.redis.graph_store import RedisGraphStore
from aie.persistence.redis.knowledge_store import RedisKnowledgeStore
from aie.persistence.vector.chroma_store import chroma_store

logger = logging.getLogger(__name__)


class KnowledgePersistenceService:
    """Coordinates persistence across MongoDB, Redis, and ChromaDB."""

    def __init__(self) -> None:
        self.mongo_client = mongo_client
        self.app_repo = ApplicationRepository()
        self.run_repo = AnalysisRunRepository()
        self.profile_repo = TechnologyProfileRepository()
        self.comp_repo = ComponentRepository()
        self.rel_repo = RelationshipRepository()
        self.graph_repo = GraphRepository()
        self.contract_repo = ContractRepository()
        self.file_repo = FileInventoryRepository()

        self.redis_client = redis_client
        self.redis_knowledge = RedisKnowledgeStore()
        self.redis_graph = RedisGraphStore()
        self.chroma = chroma_store

    async def save_completed_analysis(
        self,
        run: AnalysisRun,
        inventory: list[FileEntry] | list[dict[str, Any]],
        profile: TechnologyProfile | dict[str, Any],
        components: list[DiscoveredComponent] | list[dict[str, Any]],
        relationships: list[DiscoveredRelationship] | list[dict[str, Any]],
        graph: Any,
        descriptor: dict[str, Any],
        capabilities: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically persist a completed analysis run across MongoDB, Redis, and ChromaDB."""
        run_id = run.run_id
        repo_url = getattr(run, "repo_url", "")
        branch = getattr(run, "branch", None) or getattr(run, "repo_ref", "main")
        commit_hash = getattr(run, "commit_hash", None)

        from aie.utils import extract_project_from_url

        owner, project_name = extract_project_from_url(repo_url)
        db_name = project_name
        app_id = (
            getattr(run, "application_id", None)
            or project_name
            or descriptor.get("application_id")
            or run_id
        )

        # ── 1. Ensure Connections ─────────────────────────────────────────
        await self.mongo_client.connect()
        await self.redis_client.connect()

        # ── 2. Determine Next Analysis Version ─────────────────────────────
        version = await self.app_repo.get_next_version(app_id, db_name=db_name)
        run.version = version  # type: ignore[attr-defined]

        logger.info(
            "Persisting completed analysis run '%s' as Version %d for application '%s' in DB '%s'...",
            run_id,
            version,
            app_id,
            db_name,
        )

        # Normalize data models to dictionaries for MongoDB
        profile_dict = profile.model_dump() if hasattr(profile, "model_dump") else (dict(profile) if profile else {})
        comp_dicts = [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in components]
        rel_dicts = [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in relationships]
        file_dicts = [f.model_dump() if hasattr(f, "model_dump") else dict(f) for f in inventory]
        graph_dict = graph.model_dump() if hasattr(graph, "model_dump") else (dict(graph) if graph else {"nodes": [], "edges": []})
        run_dict = run.model_dump() if hasattr(run, "model_dump") else dict(run)

        run_dict["version"] = version
        run_dict["is_active"] = True

        # ── 3. MongoDB: Permanent Source of Truth ─────────────────────────
        try:
            # Ensure indexes in target database
            await self.mongo_client.create_indexes_for_db(db_name)

            # 3.1 Save immutable analysis run
            await self.run_repo.save(run_dict, db_name=db_name)

            # 3.2 Save application registry & promote active version
            app_name = descriptor.get("name") or getattr(profile, "app_name", None) or app_id
            await self.app_repo.upsert_application(
                app_id=app_id,
                name=app_name,
                repo_url=repo_url,
                default_branch=branch or "main",
                active_run_id=run_id,
                version=version,
                db_name=db_name,
            )
            await self.app_repo.set_active_version(app_id, run_id, version, db_name=db_name)

            # 3.3 Save profile
            await self.profile_repo.save(app_id, run_id, version, profile_dict, db_name=db_name)

            # 3.4 Save components with complete knowledge
            await self.comp_repo.save_batch(app_id, run_id, version, comp_dicts, is_active=True, db_name=db_name)

            # 3.5 Save relationships
            await self.rel_repo.save_batch(app_id, run_id, version, rel_dicts, db_name=db_name)

            # 3.6 Save graph
            await self.graph_repo.save_graph(app_id, run_id, version, graph_dict, db_name=db_name)

            # 3.7 Save Contracts B and C
            await self.contract_repo.save_contract_b(app_id, run_id, version, descriptor, db_name=db_name)
            await self.contract_repo.save_contract_c(app_id, run_id, version, capabilities, db_name=db_name)

            # 3.8 Save file inventory
            await self.file_repo.save_inventory(app_id, run_id, version, file_dicts, db_name=db_name)

            logger.info("MongoDB: Successfully persisted Version %d for app '%s' in DB '%s'", version, app_id, db_name)
        except Exception as exc:
            logger.warning("MongoDB persistence encountered an error: %s", exc)

        # ── 4. Redis: Active In-Memory Cache & Graph Adjacency Lists ──────
        try:
            # 4.1 Clear old active keys to prevent cross-run pollution
            await self.redis_knowledge.clear_app(app_id)
            await self.redis_graph.clear_graph(app_id)

            # 4.2 Set active version pointer
            await self.redis_knowledge.set_active_version(app_id, run_id, version, commit_hash)

            # 4.3 Set metadata and profile
            metadata = {
                "application_id": app_id,
                "name": app_name,
                "repo_url": repo_url,
                "branch": branch,
                "commit_hash": commit_hash,
                "version": version,
                "run_id": run_id,
                "file_count": len(file_dicts),
                "component_count": len(comp_dicts),
                "relationship_count": len(rel_dicts),
                "action_count": len(capabilities.get("actions", [])),
            }
            await self.redis_knowledge.set_metadata(app_id, metadata)
            await self.redis_knowledge.set_technology_profile(app_id, profile_dict)

            # 4.4 Store components
            await self.redis_knowledge.store_components_batch(app_id, comp_dicts)

            # 4.5 Store Contract B & C
            await self.redis_knowledge.store_contract_b(app_id, descriptor)
            await self.redis_knowledge.store_contract_c(app_id, capabilities)

            # 4.6 Store graph and build bidirectional adjacency sets
            nodes = graph_dict.get("nodes") or [{"id": c.get("component_id"), "name": c.get("name"), "type": c.get("component_type")} for c in comp_dicts]
            edges = graph_dict.get("edges") or [{"source": r.get("source_component_id"), "target": r.get("target_component_id"), "label": r.get("relation_type")} for r in rel_dicts]
            await self.redis_graph.store_graph(app_id, nodes, edges)

            logger.info("Redis: Loaded active Version %d knowledge and adjacency graph for app '%s'", version, app_id)
        except Exception as exc:
            logger.warning("Redis caching encountered an error: %s", exc)

        # ── 5. ChromaDB: Semantic Retrieval Index ─────────────────────────
        try:
            indexed_chunks = self.chroma.index_component_knowledge(
                app_id=app_id,
                run_id=run_id,
                version=version,
                components=components,
            )
            logger.info("ChromaDB: Indexed %d semantic chunks for app '%s' (v%d)", indexed_chunks, app_id, version)
        except Exception as exc:
            logger.warning("ChromaDB indexing encountered an error: %s", exc)

        return {
            "application_id": app_id,
            "run_id": run_id,
            "version": version,
            "status": "persisted",
            "components_count": len(comp_dicts),
            "relationships_count": len(rel_dicts),
        }


# Singleton service
persistence_service = KnowledgePersistenceService()
